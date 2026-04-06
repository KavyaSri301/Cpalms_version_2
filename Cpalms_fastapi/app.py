"""
FastAPI application with all API endpoints
"""
from fastapi import FastAPI, HTTPException, BackgroundTasks, Response, Request, Security, status
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
import re
import json
import time
import pyodbc
from datetime import datetime, timezone
import asyncio
import uuid
import html

from recommendation import generate_recommended_questions
from validation import validate_educational_query
from logs import log_query_to_blob
from logs_sql import log_query_to_sql

from config import (
    search_client, search_client_1, encoding,
    OPENAI_DEPLOYMENT_NAME, OPENAI_DEPLOYMENT_NAME_2, OPENAI_DEPLOYMENT_NAME_3, OPENAI_DEPLOYMENT_NAME_4,
    AZURE_SQL_CONNECTION, SQL_SERVER, SQL_DATABASE, SQL_USERNAME, SQL_PASSWORD,
    VALID_API_KEYS
)
from db_pool import db_pool
from models import (
    ChatRequest, ChatResponse, PreviousResponseItem,
    RecommendationRequest, RecommendationResponse,
    SidebarRequest, SidebarResponse, SessionResourceCombo, ResourceTitleCombo,
    PreviousHistoryRequest, PreviousHistoryResponse, HistoryItem
)
from utils import (
    cleanup_old_sessions, get_or_create_session_key,
    get_combined_conversation_history, add_to_conversation_history_in_memory,
    async_azure_openai_call, extract_worksheet_content,
    normalize_benchmarks, clean_file_paths, format_benchmarks, get_benchmark_id,
    format_benchmark_resource_ids, detect_response_type,
    run_parallel_calls, search_and_extract_documents, get_benchmark_description, 
    format_benchmarks_from_dict, get_all_benchmark_descriptions, _search
)
from prompts import (
    add_html_tags, generate_summary_for_primary_benchmarks,
    generate_creative_response, generate_creative_response_for_reference
)


app = FastAPI(
    title="CPALMS AI Customization API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
    timeout=180
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import threading
def periodic_session_cleanup():
    """Run session cleanup every 5 minutes"""
    import time
    while True:
        time.sleep(300)
        try:
            cleanup_old_sessions(max_age_minutes=30)
        except Exception:
            pass

cleanup_thread = threading.Thread(target=periodic_session_cleanup, daemon=True)
cleanup_thread.start()

@app.middleware("http")
async def add_request_logging(request: Request, call_next):
    """Log detailed request timing and concurrency metrics"""
    request_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    from db_pool import db_pool
    pool_stats = db_pool.get_stats()
    try:
        response = await call_next(request)
        duration = (time.time() - start_time) * 1000

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = f"{duration:.2f}ms"

        return response

    except Exception as e:
        duration = (time.time() - start_time) * 1000
        raise

api_key_header = APIKeyHeader(name="X-API-Key")

async def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key not in VALID_API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key"
        )
    return api_key


@app.post("/recommendation", response_model=RecommendationResponse)
async def recommendation_endpoint(request: RecommendationRequest, api_key: str = Security(verify_api_key)):
    """
    Generate recommended questions for a resource
    """
    resource_id = request.resource_id.strip()
    session_id = request.Session_ID
    user_id = request.User_ID

    if not re.fullmatch(r'\d{2,7}', resource_id):
        raise HTTPException(status_code=400, detail="Resource ID must be a 2-7 digit number")

    try:
        search_results = await _search(search_client, search_text="*", filter=f"id eq '{resource_id}'", top=1)

        if not search_results:
            raise HTTPException(
                status_code=404,
                detail=f"No lesson content found for Resource ID: {resource_id}"
            )
        
        async def check_resource_deleted():
            loop = asyncio.get_event_loop()
            def _check():
                conn = db_pool.get_connection()
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT IsDeleted FROM ResourceCore WHERE ResourceId = ?", (resource_id,))
                    rows = cursor.fetchall()
                    cursor.close()
                    return any(row[0] for row in rows)
                finally:
                    db_pool.return_connection(conn)
            return await loop.run_in_executor(None, _check)
  
        is_deleted = await check_resource_deleted()
        if is_deleted:
            raise HTTPException(status_code=400, detail=f"Resource ID {resource_id} has been deleted.")
    
        excluded_fields = [
            "BenchmarkIds", "BenchmarkCodes", "Benchmark_Description",
            "SpecialMaterialsNeeded", "Files", "text",
            "ResourceUrl", "PublishedDate", "ResourceTypeId",
            "PrimaryResourceICT", "PrimaryResourceICTId"
        ]
        filtered_doc = {k: v for k, v in search_results[0].items() if k not in excluded_fields and v is not None}
        lesson_content = str(filtered_doc)

        messages = generate_recommended_questions(lesson_content)
        response = await async_azure_openai_call(messages, model=OPENAI_DEPLOYMENT_NAME_2)
        recommended_questions_text = response.choices[0].message.content
        questions_list = []
        for line in recommended_questions_text.strip().split('\n'):
            line = line.strip()
            if re.match(r'^\d+\.', line):
                question = re.sub(r'^\d+\.\s*', '', line.strip())
                questions_list.append(question)

        return RecommendationResponse(
            recommendation_questions=questions_list,
            Session_ID=session_id,
            User_ID=user_id,
            resource_id=resource_id
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.post("/chat")
async def chat_endpoint(
    chat_request: ChatRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    api_key: str = Security(verify_api_key)
):
    """
    Process educational query and generate AI customization
    """

    resource_id = chat_request.resource_id.strip()
    query = chat_request.query.strip()
    session_id = chat_request.Session_ID
    user_id = chat_request.User_ID
    request_timestamp = datetime.now(timezone.utc).isoformat()

    timings = {
        'validation': 0,
        'session_lookup': 0,
        'classification': 0,
        'db_queries': 0,
        'azure_search': 0,
        'openai_calls': 0,
        'total': 0
    }

    if not re.fullmatch(r'\d{2,7}', resource_id):
        raise HTTPException(status_code=400, detail="Resource ID must be a 2-7 digit number")
    if not session_id:
        raise HTTPException(status_code=400, detail="Session_ID cannot be empty")
    if not user_id:
        raise HTTPException(status_code=400, detail="User_ID cannot be empty")
    if not resource_id:
        raise HTTPException(status_code=400, detail="Resource ID cannot be empty")
    if not query:
        raise HTTPException(status_code=400, detail="Please enter the query")

    async def check_resource_deleted():
        loop = asyncio.get_event_loop()
        def _check():
            conn = db_pool.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT IsDeleted FROM ResourceCore WHERE ResourceId = ?", (resource_id,))
                rows = cursor.fetchall()
                cursor.close()
                return any(row[0] for row in rows)
            finally:
                db_pool.return_connection(conn)
        return await loop.run_in_executor(None, _check)

    try:
        is_deleted = await check_resource_deleted()
        if is_deleted:
            raise HTTPException(status_code=400, detail=f"Resource ID {resource_id} has been deleted.")
    except HTTPException:
        raise
    except Exception:
        pass

    val_start = time.time()
    is_valid_query, error_message = validate_educational_query(query)
    timings['validation'] = (time.time() - val_start) * 1000


    if not is_valid_query:
        background_tasks.add_task(
            log_query_to_blob,
            resource_id=resource_id,
            query=query,
            processing_time=0.00,
            ai_output="❌ This query doesn't appear to be education-related.",
            recommended_questions="No recommendations generated"
        )
        background_tasks.add_task(
            log_query_to_sql,
            resource_id=resource_id,
            benchmarks="no benchmarks",
            benchmarks_long="",
            query=query,
            response="❌ This query doesn't appear to be education-related.",
            response_type="plain text",
            session_id=session_id,
            user_id=user_id,
            server=SQL_SERVER,
            database=SQL_DATABASE,
            username=SQL_USERNAME,
            password=SQL_PASSWORD,
            worksheet=""
        )
        raise HTTPException(status_code=400, detail=error_message)

    start_time = time.time()

    try:
        # Get session
        sess_start = time.time()
        session_key, token, is_new_session = get_or_create_session_key(request, session_id, user_id)
        timings['session_lookup'] = (time.time() - sess_start) * 1000


        loop = asyncio.get_event_loop()
        history_for_openai, full_history_for_response, resource_history = await loop.run_in_executor(
            None,
            lambda: get_combined_conversation_history(
                session_key=session_key,
                session_id=session_id,
                user_id=user_id,
                resource_id=resource_id,
            )
        )

        previous_responses_for_api = [
            PreviousResponseItem(
                timestamp=item.get("timestamp", datetime.now(timezone.utc).isoformat()),
                query=item["query"],
                response=item["response"],
                resource_id=item.get("resource_id", resource_id),
                response_type=item.get("response_type", "plain text"),
                supporting_documents=item.get("supporting_documents", []),
                benchmarks=item.get("benchmarks", ""),
                worksheet=item.get("worksheet", "")
            )
            for item in full_history_for_response[-10:]
        ][::-1]

        recent_queries = [
            {
                "query": item["query"],
                "response_type": item.get("response_type", "")
            }
            for item in resource_history
        ]


        class_start = time.time()
        classification_response, required_fields_response, resource_doc = await run_parallel_calls(query, resource_id, recent_queries)
        timings['classification'] = (time.time() - class_start) * 1000

        if "unrelated" in classification_response.choices[0].message.content.lower():
            raise HTTPException(
                status_code=400,
                detail="I can only answer educational content and lesson planning related queries."
            )
        if "vague" in classification_response.choices[0].message.content.lower():
            raise HTTPException(
                status_code=400,
                detail="Could you please provide more details about your request? Your input seems a bit unclear."
            )

        query_type = classification_response.choices[0].message.content.strip()

        raw_text = required_fields_response.choices[0].message.content
        try:
            raw_fields_clean = re.sub(r"^```(?:json)?|```$", "", raw_text.strip(), flags=re.MULTILINE)
            fields_list = [item["field"] for item in json.loads(raw_fields_clean)]
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400,
                detail="JSON error while getting the required fields."
            )

        combined_chunks = ""
        lesson_content = ""
        files = ""
        lesson_plan = ""
        benchmark_to_resource_ids = {}
        grade_levels = ""
        formatted_benchmarks = ""
        supporting_documents = []

        primary_benchmarks = set()
        secondary_benchmarks = set()
        primary_docs = []
        doc_benchmarks = None
        all_benchmarks_description = ""
        benchmark_desc_text = ""
        deleted_resource_ids_list = []

        if query_type == "normal":

            async def fetch_benchmarks():
                loop = asyncio.get_event_loop()
                def _fetch():
                    conn = db_pool.get_connection()
                    try:
                        cursor = conn.cursor()
                        check_query = "select Code, RelationshipId from ResourceBenchmarks where ResourceId=?"
                        cursor.execute(check_query, (resource_id,))
                        rows = cursor.fetchall()
                        cursor.close()
                        return rows
                    finally:
                        db_pool.return_connection(conn)
                return await loop.run_in_executor(None, _fetch)

            try:
                rows = await fetch_benchmarks()
            except Exception as _e:
                print(f"⚠️ fetch_benchmarks failed, continuing without: {_e}")
                rows = []
            for row in rows:
                if row.RelationshipId == 1:
                    primary_benchmarks.add(row.Code)
                elif row.RelationshipId == 2:
                    secondary_benchmarks.add(row.Code)


            deleted_resource_ids_list = []
            if resource_doc:
                lesson_plan = str(resource_doc)
                doc_benchmarks = resource_doc.get("BenchmarkCodes")
                files = resource_doc.get("Files")
                all_benchmarks_description = resource_doc.get("Benchmark_Description", "")
                grade_levels = resource_doc.get("GradeLevelNames", "")

            benchmarks_code_set = set(normalize_benchmarks(doc_benchmarks))

            if benchmarks_code_set:
                async def fetch_deleted_resources():
                    loop = asyncio.get_event_loop()
                    def _fetch():
                        conn = db_pool.get_connection()
                        try:
                            placeholders = ",".join(["?"] * len(benchmarks_code_set))
                            list_benchmarks = f"""
                            SELECT ResourceId
                            FROM ResourceBenchmarks
                            WHERE Code IN ({placeholders})
                            AND IsDeleted = 1
                            """
                            cursor = conn.cursor()
                            cursor.execute(list_benchmarks, tuple(benchmarks_code_set))
                            rows = cursor.fetchall()
                            cursor.close()
                            return [str(row.ResourceId) for row in rows]
                        finally:
                            db_pool.return_connection(conn)
                    return await loop.run_in_executor(None, _fetch)

                try:
                    deleted_resource_ids_list = await fetch_deleted_resources()
                except Exception as _e:
                    print(f"⚠️ fetch_deleted_resources failed, continuing without: {_e}")
                    deleted_resource_ids_list = []

            if primary_benchmarks:
                primary_benchmarks = list(primary_benchmarks)
            else:
                primary_benchmarks = normalize_benchmarks(doc_benchmarks)

            if primary_benchmarks:
                set_rid = set()
                for doc in await _search(search_client, search_text=" ".join(primary_benchmarks), top=500):
                    r_id = doc.get("id", "")
                    if r_id in deleted_resource_ids_list or r_id in set_rid:
                        continue
                    rid_list = normalize_benchmarks(doc.get("BenchmarkCodes"))
                    matched_benchmarks = set(primary_benchmarks) & set(rid_list)
                    if matched_benchmarks:
                        filtered_doc = {f: doc.get(f, "") for f in fields_list if doc.get(f, "")}
                        filtered_doc["id"] = r_id
                        primary_docs.append(filtered_doc)
                        set_rid.add(r_id)

            if files:
                all_file_paths = clean_file_paths(re.findall(r"\(([^)]+)\)", files))

            if doc_benchmarks:
                formatted_benchmarks = format_benchmarks(all_benchmarks_description)

        elif query_type.startswith("reference"):
            parts = query_type.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                raise HTTPException(
                    status_code=400,
                    detail="Reference query must specify a benchmark code, e.g., 'reference MAFS.5.NBT.1.1'"
                )
            benchmark_codes_raw = parts[1].strip().upper()

            if resource_doc:
                lesson_plan = str(resource_doc)
                doc_benchmarks = resource_doc.get("BenchmarkCodes")
                files = resource_doc.get("Files")
                all_benchmarks_description = resource_doc.get("Benchmark_Description", "")
                grade_levels = resource_doc.get("GradeLevelNames", "")

            if benchmark_codes_raw:
                benchmark_list = normalize_benchmarks(benchmark_codes_raw)

                async def fetch_deleted_benchmarks():
                    loop = asyncio.get_event_loop()
                    def _fetch():
                        conn = db_pool.get_connection()
                        try:
                            placeholders = ",".join(["?"] * len(benchmark_list))
                            cursor = conn.cursor()
                            cursor.execute(
                                f"SELECT ResourceId FROM ResourceBenchmarks WHERE Code IN ({placeholders}) AND IsDeleted=1",
                                tuple(benchmark_list),
                            )
                            result = [str(r.ResourceId) for r in cursor.fetchall()]
                            cursor.close()
                            return result
                        finally:
                            db_pool.return_connection(conn)
                    return await loop.run_in_executor(None, _fetch)

                try:
                    deleted_resource_ids_list = await fetch_deleted_benchmarks()
                except Exception as _e:
                    print(f"⚠️ fetch_deleted_benchmarks failed, continuing without: {_e}")
                    deleted_resource_ids_list = []

                search_start = time.time()
                benchmark_info = {
                    b: {"description": "", "benchmark_id": "", "found_desc": False}
                    for b in benchmark_list
                }

                set_rid = set()
                for doc in await _search(search_client, search_text=benchmark_codes_raw, top=500):
                    r_id = doc.get("id", "")
                    if r_id in deleted_resource_ids_list or r_id in set_rid:
                        continue
                    rid_list = normalize_benchmarks(doc.get("BenchmarkCodes"))
                    matched_benchmarks = set(benchmark_list) & set(rid_list)
                    if matched_benchmarks:
                        filtered_doc = {f: doc.get(f, "") for f in fields_list if doc.get(f, "")}
                        filtered_doc["id"] = r_id
                        primary_docs.append(filtered_doc)
                        set_rid.add(r_id)
                    for bm in matched_benchmarks:
                        if not benchmark_info[bm]["found_desc"]:
                            raw_desc_field = doc.get("Benchmark_Description", "")
                            benchmark_info[bm]["benchmark_id"]=get_benchmark_id(raw_desc_field,bm)
                            benchmark_info[bm]["description"]=get_benchmark_description(raw_desc_field,bm)
                            benchmark_info[bm]["found_desc"] = True


            if benchmark_info:
                formatted_benchmarks = format_benchmarks_from_dict(benchmark_info)
                benchmark_desc_text = get_all_benchmark_descriptions(benchmark_info)

            if files:
                all_file_paths = clean_file_paths(re.findall(r"\(([^)]+)\)", files))

        elif query_type.startswith("followup"):
            if resource_doc:
                lesson_plan = str(resource_doc)
                doc_benchmarks = resource_doc.get("BenchmarkCodes")
                files = resource_doc.get("Files")
                grade_levels = resource_doc.get("GradeLevelNames", "")
                all_benchmarks_description = resource_doc.get("Benchmark_Description", "")

            if files:
                all_file_paths = clean_file_paths(re.findall(r"\(([^)]+)\)", files))

            if "reference" in query_type:
                qt_stripped = re.sub(r'\s*-\d+$', '', query_type).strip()
                ref_parts = qt_stripped.split(maxsplit=2)
                benchmark_codes_raw = ref_parts[2].strip().upper() if len(ref_parts) >= 3 else ""


                benchmark_list = []
                if benchmark_codes_raw:
                    benchmark_list = normalize_benchmarks(benchmark_codes_raw)

                    async def fetch_deleted_followup():
                        loop = asyncio.get_event_loop()
                        def _fetch():
                            conn = db_pool.get_connection()
                            try:
                                placeholders = ",".join(["?"] * len(benchmark_list))
                                cursor = conn.cursor()
                                cursor.execute(
                                    f"SELECT ResourceId FROM ResourceBenchmarks WHERE Code IN ({placeholders}) AND IsDeleted=1",
                                    tuple(benchmark_list),
                                )
                                result = [str(r.ResourceId) for r in cursor.fetchall()]
                                cursor.close()
                                return result
                            finally:
                                db_pool.return_connection(conn)
                        return await loop.run_in_executor(None, _fetch)

                    try:
                        deleted_resource_ids_list = await fetch_deleted_followup()
                    except Exception as _e:
                        print(f"⚠️ fetch_deleted_followup failed, continuing without: {_e}")
                        deleted_resource_ids_list = []

                if benchmark_list:
                    benchmark_to_resource_ids = {b: [] for b in benchmark_list}
                    benchmark_info = {
                        b: {"description": "", "benchmark_id": "", "found_desc": False}
                        for b in benchmark_list
                    }

                    for doc in await _search(search_client, search_text=benchmark_codes_raw, top=200):
                        r_id = doc.get("id", "")
                        if r_id in deleted_resource_ids_list:
                            continue
                        rid_list = normalize_benchmarks(doc.get("BenchmarkCodes"))
                        matched_benchmarks = set(benchmark_list) & set(rid_list)

                        for bm in matched_benchmarks:
                            benchmark_to_resource_ids[bm].append(r_id)

                            if not benchmark_info[bm]["found_desc"]:
                                raw_desc_field = doc.get("Benchmark_Description", "")
                                benchmark_info[bm]["benchmark_id"] = get_benchmark_id(raw_desc_field, bm)
                                benchmark_info[bm]["description"] = get_benchmark_description(raw_desc_field, bm)
                                benchmark_info[bm]["found_desc"] = True


                    if benchmark_info:
                        formatted_benchmarks = format_benchmarks_from_dict(benchmark_info)
                        benchmark_desc_text = get_all_benchmark_descriptions(benchmark_info)


                matched_docs = ""
                query_type = "followup_reference"

                lesson_content = ""
                combined_chunks = ""
                primary_docs = []

            else:
                if all_benchmarks_description:
                    formatted_benchmarks = format_benchmarks(all_benchmarks_description)

                query_type = "followup_normal"
                lesson_content = ""
                combined_chunks = ""

        else:
            query_type = "normal"
            if resource_doc:
                lesson_plan = str(resource_doc)
                doc_benchmarks = resource_doc.get("BenchmarkCodes")
                files = resource_doc.get("Files")
                all_benchmarks_description = resource_doc.get("Benchmark_Description", "")
                grade_levels = resource_doc.get("GradeLevelNames", "")

            if files:
                all_file_paths = clean_file_paths(re.findall(r"\(([^)]+)\)", files))

        file_paths = []
        if files:
            all_file_paths = re.findall(r'\(([^)]+)\)', files)
            all_file_paths = clean_file_paths(all_file_paths)

        file_paths, combined_chunks = await loop.run_in_executor(
            None, lambda: search_and_extract_documents(resource_id, search_client_1)
        )

        if files:
            matched_paths = []
            file_name_set = set(file_paths)

            for path in all_file_paths:
                filename = path.split("/")[-1]
                if filename in file_name_set:
                    matched_paths.append(path)

            if matched_paths:
                supporting_documents = matched_paths

        if query_type.startswith("normal") or query_type.startswith("followup_normal"):
            lesson_content = "\n".join([str(doc) for doc in primary_docs])

            tokens = encoding.encode(lesson_content)
            tokens_for_docs = encoding.encode(combined_chunks)
            token_count = len(tokens)
            token_count_for_docs = len(tokens_for_docs)


            FIRST_CHUNK_SIZE = 1000
            MAX_REMAINING_TOKENS = 100_000
            part_1_tokens = []
            part_2_tokens = []
            part_1_doc_tokens = []
            part_2_doc_tokens = []

            if token_count <= FIRST_CHUNK_SIZE:
                part_1_tokens = tokens
            else:
                part_2_tokens = tokens[:MAX_REMAINING_TOKENS]

            part_1_text = encoding.decode(part_1_tokens)
            part_2_text = encoding.decode(part_2_tokens)


            if token_count_for_docs <= FIRST_CHUNK_SIZE:
                part_1_doc_tokens = tokens_for_docs
            else:
                part_2_doc_tokens = tokens_for_docs[:MAX_REMAINING_TOKENS]

            part_1_doc_text = encoding.decode(part_1_doc_tokens)
            part_2_doc_text = encoding.decode(part_2_doc_tokens)


            summary_tasks = []

            if part_2_text:
                summary_tasks.append(
                    async_azure_openai_call(
                        generate_summary_for_primary_benchmarks(query, part_2_text),
                        model=OPENAI_DEPLOYMENT_NAME_4
                    )
                )

            if part_2_doc_text:
                summary_tasks.append(
                    async_azure_openai_call(
                        generate_summary_for_primary_benchmarks(query, part_2_doc_text),
                        model=OPENAI_DEPLOYMENT_NAME
                    )
                )

            if summary_tasks:
                responses = await asyncio.gather(*summary_tasks)

                idx = 0
                if part_2_text:
                    summary_output = responses[idx].choices[0].message.content
                    lesson_content = summary_output
                    idx += 1
                else:
                    lesson_content = part_1_text

                if part_2_doc_text:
                    docs_summary_output = responses[idx].choices[0].message.content
                    combined_chunks = docs_summary_output
                else:
                    combined_chunks = part_1_doc_text


            messages = generate_creative_response(
                query=query,
                resource_id=resource_id,
                lesson_content=lesson_content,
                combined_chunks=combined_chunks,
                grade_levels=grade_levels,
                lesson_plan=lesson_plan,
                conversation_history=history_for_openai,
                all_benchmarks_description=all_benchmarks_description,
                query_type="followup" if "followup" in query_type else ""
            )

        elif query_type.startswith("reference") or query_type.startswith("followup_reference"):
            lesson_content = "\n".join([str(doc) for doc in primary_docs])

            tokens = encoding.encode(lesson_content)
            tokens_for_docs = encoding.encode(combined_chunks)
            token_count = len(tokens)
            token_count_for_docs = len(tokens_for_docs)


            FIRST_CHUNK_SIZE = 1000
            MAX_REMAINING_TOKENS = 100_000
            part_1_tokens = []
            part_2_tokens = []
            part_1_doc_tokens = []
            part_2_doc_tokens = []

            if token_count <= FIRST_CHUNK_SIZE:
                part_1_tokens = tokens
            else:
                part_2_tokens = tokens[:MAX_REMAINING_TOKENS]

            part_1_text = encoding.decode(part_1_tokens)
            part_2_text = encoding.decode(part_2_tokens)


            if token_count_for_docs <= FIRST_CHUNK_SIZE:
                part_1_doc_tokens = tokens_for_docs
            else:
                part_2_doc_tokens = tokens_for_docs[:MAX_REMAINING_TOKENS]

            part_1_doc_text = encoding.decode(part_1_doc_tokens)
            part_2_doc_text = encoding.decode(part_2_doc_tokens)


            summary_tasks = []

            if part_2_text:
                summary_tasks.append(
                    async_azure_openai_call(
                        generate_summary_for_primary_benchmarks(query, part_2_text),
                        model=OPENAI_DEPLOYMENT_NAME_4
                    )
                )

            if part_2_doc_text:
                summary_tasks.append(
                    async_azure_openai_call(
                        generate_summary_for_primary_benchmarks(query, part_2_doc_text),
                        model=OPENAI_DEPLOYMENT_NAME
                    )
                )

            if summary_tasks:
                responses = await asyncio.gather(*summary_tasks)

                idx = 0
                if part_2_text:
                    summary_output = responses[idx].choices[0].message.content
                    lesson_content = summary_output
                    idx += 1
                else:
                    lesson_content = part_1_text

                if part_2_doc_text:
                    docs_summary_output = responses[idx].choices[0].message.content
                    combined_chunks = docs_summary_output
                else:
                    combined_chunks = part_1_doc_text

            messages = generate_creative_response_for_reference(
                query, resource_id, lesson_content, combined_chunks, lesson_plan,
                conversation_history=history_for_openai,
                benchmark_description=benchmark_desc_text,
                query_type="followup" if "followup" in query_type else ""
            )
        openai_start = time.time()
        ai_response = await async_azure_openai_call(messages, model=OPENAI_DEPLOYMENT_NAME_3)
        ai_output = ai_response.choices[0].message.content
        usage = ai_response.usage
        timings['openai_calls'] = (time.time() - openai_start) * 1000

        ai_output = re.sub(r'^\s*-{2,}\s*$\n?', '', ai_output, flags=re.MULTILINE)
        ai_output, worksheet_content = extract_worksheet_content(ai_output)

        processing_time = time.time() - start_time
        timings['total'] = processing_time * 1000

        detected_response_type = detect_response_type(query)
        benchmarks_output = format_benchmark_resource_ids(benchmark_to_resource_ids)

        add_to_conversation_history_in_memory(
            session_key=session_key,
            query=query,
            response=ai_output,
            resource_id=resource_id,
            response_type=detected_response_type,
            session_id=session_id,
            user_id=user_id,
            supporting_documents=supporting_documents,
            benchmarks=formatted_benchmarks if formatted_benchmarks else "",
            worksheet=worksheet_content
        )

        background_tasks.add_task(
            log_query_to_blob,
            resource_id=resource_id,
            query=query,
            processing_time=processing_time,
            ai_output=ai_output,
            recommended_questions="Generated via API"
        )

        benchmarks_str = formatted_benchmarks


        background_tasks.add_task(
            log_query_to_sql,
            resource_id=resource_id,
            benchmarks=benchmarks_str,
            benchmarks_long="",
            query=query,
            response=ai_output,
            response_type=detected_response_type,
            session_id=session_id,
            user_id=user_id,
            server=SQL_SERVER,
            database=SQL_DATABASE,
            username=SQL_USERNAME,
            password=SQL_PASSWORD,
            supporting_documents=supporting_documents,
            worksheet=worksheet_content
        )

        response.set_cookie(
            key="conversation_session_key",
            value=session_key,
            max_age=1800,
            httponly=True,
            samesite="none",
            secure=True
        )
        response.set_cookie(
            key="conversation_token",
            value=token,
            max_age=1800,
            httponly=True,
            samesite="none",
            secure=True
        )

        chat_response = ChatResponse(
            supporting_documents=supporting_documents,
            benchmarks=formatted_benchmarks if formatted_benchmarks else "",
            response=ai_output,
            worksheet=worksheet_content,
            query=query,
            Session_ID=session_id,
            User_ID=user_id,
            resource_id=resource_id,
            response_type=detected_response_type,
            timestamp=request_timestamp,
            previous_response=previous_responses_for_api
        )

        return chat_response

    except HTTPException as http_exc:
        raise
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.post("/sidebar", response_model=SidebarResponse)
async def sidebar_endpoint(sidebar_request: SidebarRequest, api_key: str = Security(verify_api_key)):
    """
    Get all unique session-resource combinations and resource-title combinations for a given user.
    """
    user_id = sidebar_request.User_ID.strip()

    if not user_id:
        raise HTTPException(status_code=400, detail="User_ID cannot be empty")

    async def fetch_sidebar_data():
        loop = asyncio.get_event_loop()
        def _fetch():
            conn = db_pool.get_connection()
            try:
                cursor = conn.cursor()

                session_resource_query = """
                    SELECT DISTINCT
                        c.Session_Id,
                        c.Resource_Id
                    FROM chatlogs c
                    WHERE c.User_Id = ?
                        AND c.Session_Id IS NOT NULL
                        AND c.Resource_Id IS NOT NULL
                    ORDER BY c.Session_Id DESC, c.Resource_Id DESC
                """
                cursor.execute(session_resource_query, (user_id,))
                session_resource_rows = cursor.fetchall()

                resource_title_query = """
                    SELECT DISTINCT
                        c.Resource_Id,
                        COALESCE(r.Title, 'Untitled Resource') AS Title
                    FROM chatlogs c
                    LEFT JOIN [dbo].[ResourceCore] r ON c.Resource_Id = r.ResourceID
                    WHERE c.User_Id = ?
                        AND c.Resource_Id IS NOT NULL
                    ORDER BY c.Resource_Id DESC
                """
                cursor.execute(resource_title_query, (user_id,))
                resource_title_rows = cursor.fetchall()

                cursor.close()
                return session_resource_rows, resource_title_rows
            finally:
                db_pool.return_connection(conn)
        return await loop.run_in_executor(None, _fetch)

    try:
        session_resource_rows, resource_title_rows = await fetch_sidebar_data()

        session_resource_combinations = []
        for row in session_resource_rows:
            session_resource_combinations.append(SessionResourceCombo(
                Session_ID=row.Session_Id if row.Session_Id else "",
                resource_id=row.Resource_Id if row.Resource_Id else ""
            ))

        resource_title_combinations = []
        for row in resource_title_rows:
            resource_title_combinations.append(ResourceTitleCombo(
                resource_id=row.Resource_Id if row.Resource_Id else "",
                title=row.Title if row.Title else "Untitled Resource"
            ))

        return SidebarResponse(
            User_ID=user_id,
            session_resource_combinations=session_resource_combinations,
            resource_title_combinations=resource_title_combinations
        )

    except pyodbc.Error as db_error:
        raise HTTPException(status_code=500, detail=f"Database error: {str(db_error)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.post("/previous_history", response_model=PreviousHistoryResponse)
async def previous_history_endpoint(history_request: PreviousHistoryRequest, api_key: str = Security(verify_api_key)):
    """
    Return the top 10 most recent chat messages for a (User_ID, Session_ID, resource_id),
    newest first.
    """
    resource_id = history_request.resource_id.strip()
    session_id  = history_request.Session_ID.strip()
    user_id     = history_request.User_ID.strip()

    if not re.fullmatch(r'\d{2,7}', resource_id):
        raise HTTPException(status_code=400, detail="Resource ID must be a 2-7 digit number")
    if not session_id:
        raise HTTPException(status_code=400, detail="Session_ID cannot be empty")
    if not user_id:
        raise HTTPException(status_code=400, detail="User_ID cannot be empty")

    def _parse(value):
        if not value:
            return []
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else [parsed]
        except (json.JSONDecodeError, TypeError):
            return [value]

    async def fetch_history_data():
        loop = asyncio.get_event_loop()
        def _fetch():
            conn = db_pool.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT query, response, Timestamp, supporting_documents,
                           Benchmarks, Response_Type, worksheet
                    FROM ChatLogs
                    WHERE User_ID = ? AND Session_ID = ? AND resource_id = ?
                    """,
                    (user_id, session_id, resource_id)
                )
                row = cursor.fetchone()
                cursor.close()
                return row
            finally:
                db_pool.return_connection(conn)
        return await loop.run_in_executor(None, _fetch)

    try:
        row = await fetch_history_data()

        history = []

        if row:
            queries        = _parse(row.query)
            responses      = _parse(row.response)
            timestamps     = _parse(row.Timestamp)
            supp_docs      = _parse(row.supporting_documents)
            benchmarks_arr = _parse(row.Benchmarks)
            resp_types     = _parse(row.Response_Type)
            worksheets     = _parse(row.worksheet)

            total = max(len(queries), len(responses), len(timestamps), 1)
            start_idx = max(0, total - 10)

            def safe_get(lst, i, default=""):
                return lst[i] if i < len(lst) else default

            all_messages = []
            for i in range(start_idx, total):
                all_messages.append(HistoryItem(
                    query_text=safe_get(queries, i),
                    response_text=safe_get(responses, i),
                    timestamp=safe_get(timestamps, i) or None,
                    response_type=safe_get(resp_types, i) or None,
                    supporting_documents=safe_get(supp_docs, i),
                    benchmarks=safe_get(benchmarks_arr, i) or None,
                    worksheet=safe_get(worksheets, i) or None
                ))

            history = list(reversed(all_messages))

        return PreviousHistoryResponse(
            resource_id=resource_id,
            Session_ID=session_id,
            User_ID=user_id,
            history=history
        )

    except pyodbc.Error as db_error:
        raise HTTPException(status_code=500, detail=f"Database error: {str(db_error)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)