"""
main.py — CPALMS AI Customization Generator (Streamlit entry point).
Imports all helpers from utils.py and all prompt builders from prompts.py.
"""
import asyncio
import json
import os
import re
import time
from io import BytesIO
import pyodbc
import streamlit as st

from dataformatting import (
    convert_markdown_to_bold_html_1,
    convert_markdown_to_clean_text,
    convert_markdown_to_clean_text_for_docs,
    normalize_empty_lines,
)
from convert_to_pdf import generate_structured_pdf
from docx_formatting import generate_docx_file, generate_docx_file_for_download
from logs import log_query_to_blob
from validation import validate_educational_query

from utils import (
    AZURE_SQL_CONNECTION,
    OPENAI_DEPLOYMENT_NAME,
    OPENAI_DEPLOYMENT_NAME_2,
    OPENAI_DEPLOYMENT_NAME_3,
    OPENAI_DEPLOYMENT_NAME_4,
    async_azure_openai_call,
    encoding,
    search_client,
    search_client_1,
    initialize_session_state,
    initialize_session_history,
    reset_session_state,
    should_process_new_query,
    add_to_history,
    get_previous_response_for_resource,
    check_query_in_history,
    format_user_edits,
    remove_inline_download_links,
    normalize_benchmarks,
    format_benchmarks,
    get_benchmark_id,
    get_benchmark_description,
    clean_file_paths,
    extract_document_content,
    extract_test_or_worksheet_section,
    replace_generate_docx_link,
    format_benchmarks_from_dict,
    get_all_benchmark_descriptions,
    urls_to_clickable_filenames,
    generate_blob_urls,
    fetch_recommended_questions,
    display_recommended_questions,
    show_history,
)

from prompts import (
    classify_query,
    get_fields_from_index,
    generate_summary_for_primary_benchmarks,
    generate_creative_response,
    generate_creative_response_for_reference,
)


st.set_page_config(
    page_title="CPALMS AI Customization Generator",
    layout="wide",
    initial_sidebar_state="collapsed",
    page_icon="📘",
)


st.markdown(
    """
<style>
    .stApp { background-color: #f8f9fa !important; }

    .main-header {
        text-align: center; padding: 40px 20px 20px 20px;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white; border-radius: 15px; margin: 20px 0 30px 0;
        box-shadow: 0 10px 30px rgba(0,0,0,0.15);
    }
    .main-title { font-size: 2.8rem; font-weight: 700; margin-bottom: 10px;
                  text-shadow: 0 2px 4px rgba(0,0,0,0.3); }
    .main-subtitle { font-size: 1.3rem; opacity: 0.9; font-weight: 300; }

    input[aria-label="Resource ID"] { text-align: center !important; }

    .stTextInput > div > div > input {
        background-color: white; border: 2px solid #e1e5e9;
        border-radius: 10px; padding: 15px 20px; font-size: 16px;
        transition: all 0.3s ease; box-shadow: 0 2px 5px rgba(0,0,0,0.05);
    }
    .stTextInput > div > div > input:focus {
        border-color: #667eea; box-shadow: 0 0 0 3px rgba(102,126,234,0.1);
    }

    .stButton > button {
        border-radius: 25px !important; font-weight: 600 !important;
        transition: all 0.2s ease !important; border: none !important;
        box-shadow: 0 4px 15px rgba(0,0,0,0.1) !important;
    }
    .stButton > button:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 8px 25px rgba(0,0,0,0.15) !important;
    }

    .stDownloadButton > button {
        background: linear-gradient(135deg, #FF9800, #F57C00) !important;
        color: white !important; border-radius: 25px !important; font-weight: 600 !important;
    }

    .ai-container {
        background: white; border-radius: 12px; padding: 25px;
        box-shadow: 0 5px 20px rgba(0,0,0,0.08); border: 2px solid #e6e6e6;
        margin: 30px auto !important; max-width: 1550px; width: 90%;
        display: block; font-size: 17px;
    }
    .ai-label { font-weight: bold; font-size: 22px; margin-bottom: 10px; color: #764ba2; }
    .new-content-box { background: #e8f4fd; padding: 18px; border-radius: 10px; margin-bottom: 20px; }
    .previous-content-box { background: #fffce0; padding: 18px; border-radius: 10px; }

    div[data-testid="stButton"] button[key^="rec_q_"],
    div[data-testid="column"] button[key^="rec_q_"],
    button[key^="rec_q_"] {
        background: white !important; color: #333 !important;
        border: 2px solid #e1e5e9 !important; border-radius: 10px !important;
        padding: 8px 15px !important; text-align: left !important;
        font-weight: 400 !important; font-size: 16px !important;
        line-height: 1.3 !important; margin-bottom: 10px !important;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05) !important;
        transition: all 0.2s ease !important; height: 55px !important;
        min-height: 55px !important; max-height: 55px !important;
        width: 100% !important; white-space: normal !important;
        word-wrap: break-word !important; overflow: hidden !important;
        display: flex !important; align-items: center !important;
        justify-content: flex-start !important;
    }
    div[data-testid="stButton"] button[key^="rec_q_"]:hover,
    div[data-testid="column"] button[key^="rec_q_"]:hover,
    button[key^="rec_q_"]:hover {
        background: #f8f9fa !important; border-color: #667eea !important;
        transform: translateX(5px) !important;
        box-shadow: 0 4px 10px rgba(102,126,234,0.2) !important;
    }
</style>
""",
    unsafe_allow_html=True,
)

initialize_session_state()
initialize_session_history()


st.markdown(
    """
<div class="main-header">
    <div class="main-title">🎓 CPALMS AI Customization Generator</div>
    <div class="main-subtitle">Generate AI-powered educational content</div>
</div>
""",
    unsafe_allow_html=True,
)

with st.container(border=True):
    st.markdown("<h3 style='text-align: center;'>🔢 Enter Resource ID</h3>", unsafe_allow_html=True)
    st.markdown(
        "<style>input[aria-label='Resource ID']{text-align:center!important;}</style>",
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        resource_id_input = st.text_input(
            "Resource ID",
            value=st.session_state.resource_id_input,
            placeholder="Please enter a Resource ID. Example: 176009",
            key="resource_id_field",
            label_visibility="collapsed",
        )
    if (
        resource_id_input.strip()
        and resource_id_input.strip() != st.session_state.last_resource_id
    ):
        if re.fullmatch(r"\d{2,7}", resource_id_input.strip()):
            st.session_state.resource_id_input = resource_id_input.strip()

            check_deleted_sql = (
                "SELECT IsDeleted FROM ResourceCore WHERE ResourceId = "
                + resource_id_input.strip()
            )
            conn = pyodbc.connect(AZURE_SQL_CONNECTION)
            cursor = conn.cursor()
            cursor.execute(check_deleted_sql)
            rows = cursor.fetchall()
            is_deleted = any(row[0] for row in rows)
            cursor.close()
            conn.close()

            if is_deleted:
                st.warning(f"⚠️ Resource ID {resource_id_input.strip()} has been deleted.")
                st.session_state.recommended_questions = ""
                st.session_state.last_resource_id = ""
                st.stop()

            with st.spinner("🔍 Loading recommended questions…"):
                try:
                    search_results = search_client.search(
                        search_text=resource_id_input.strip(), top=3
                    )
                    lesson_content = ""
                    for doc in search_results:
                        if str(doc.get("id")) == resource_id_input.strip():
                            excluded = {
                                "BenchmarkIds", "BenchmarkCodes", "Benchmark_Description",
                                "SpecialMaterialsNeeded", "Files", "text",
                                "ResourceUrl", "PublishedDate", "ResourceTypeId",
                                "PrimaryResourceICT", "PrimaryResourceICTId",
                            }
                            lesson_content = str(
                                {k: v for k, v in doc.items() if k not in excluded and v is not None}
                            )

                    if lesson_content:
                        recommended_questions = asyncio.run(
                            fetch_recommended_questions(lesson_content)
                        )
                        st.session_state.recommended_questions = recommended_questions
                        st.session_state.last_resource_id = resource_id_input.strip()
                        st.rerun()
                    else:
                        st.warning(
                            f"⚠️ No lesson content found for Resource ID: {resource_id_input.strip()}"
                        )
                        st.session_state.recommended_questions = ""
                        st.session_state.last_resource_id = ""
                except Exception as e:
                    st.warning(f"Could not load recommended questions: {str(e)}")
                    st.session_state.recommended_questions = ""
        
        else:
            if resource_id_input.strip():
                st.warning("⚠️ Resource ID must be a 2-7 digit number")

    if st.session_state.recommended_questions and st.session_state.last_resource_id:
        display_recommended_questions()

    if st.session_state.last_resource_id:
        st.markdown("### 📝 Your Query")
        query_input = st.text_input(
            "Enter your educational query",
            value=st.session_state.query_input,
            placeholder="Example: Generate teaching phase and guiding questions…",
            key="query_text_input",
            label_visibility="collapsed",
        )
        if query_input != st.session_state.query_input:
            st.session_state.query_input = query_input

        col_submit = st.columns([3, 2, 3])[1]
        with col_submit:
            submit_button = st.button(
                "🚀 Generate AI Customization",
                use_container_width=True,
                type="primary",
                key="submit_btn",
            )
    else:
        submit_button = False


async def run_parallel_calls(query: str, resource_id: str, previous_query: str = ""):
    lesson_plan_text = ""
    for doc in search_client.search(search_text=resource_id, top=1):
        lesson_plan_text = doc.get("Description", "")

    classification_messages = classify_query(query, lesson_plan_text, previous_query)
    field_messages = get_fields_from_index(query)

    classification_response, required_fields_response = await asyncio.gather(
        async_azure_openai_call(classification_messages, model=OPENAI_DEPLOYMENT_NAME_2),
        async_azure_openai_call(field_messages, model=OPENAI_DEPLOYMENT_NAME_2),
    )
    return classification_response, required_fields_response



should_submit = submit_button

if should_submit:
    resource_id = resource_id_input.strip()
    query = query_input.strip()

    if not all([resource_id, query]):
        st.error("⚠️ Both Resource ID and Query are required.")
        st.stop()

    if not re.fullmatch(r"\d{2,7}", resource_id):
        st.error("❌ Resource ID must be a 2 to 7-digit number.")
        st.stop()

    is_valid_query, error_message = validate_educational_query(query)
    if not is_valid_query:
        log_query_to_blob(
            resource_id=resource_id,
            query=query,
            processing_time=0.00,
            ai_output=(
                "❌ This query doesn't appear to be education-related. "
                "Please ask about lesson plans, teaching strategies, or assessments."
            ),
            recommended_questions="No recommendations generated",
        )
        st.error(error_message)
        st.stop()

if not should_submit and not st.session_state.ai_content:
    st.stop()

resource_id = resource_id_input.strip()


if should_submit and (
    should_process_new_query(query, resource_id) or not st.session_state.ai_content
):
    cached_response, previous_query_from_history = check_query_in_history(query, resource_id)
    if cached_response:
        st.session_state.ai_content = cached_response
        st.session_state.last_processed_query = query
        if previous_query_from_history:
            previous_response = next(
                (
                    e["ai_output"]
                    for e in st.session_state.user_history
                    if e["resource_id"] == resource_id
                    and e["query"] == previous_query_from_history
                ),
                None,
            )
            st.session_state.previous_response_for_display = previous_response
        else:
            st.session_state.previous_response_for_display = None
        st.rerun()
        st.stop()
    with st.spinner("🔍 Analysing your query…"):
        previous_query_ctx = st.session_state.get("last_processed_query", "")
        classification_response, required_fields_response = asyncio.run(
            run_parallel_calls(query, resource_id, previous_query_ctx)
        )

    classification_text = classification_response.choices[0].message.content.strip().lower()

    if classification_text == "unrelated" or classification_text=="vague":
        ai_output=""
        if classification_text == "unrelated":
            ai_output="❌ This query doesn't appear to be education-related.Please ask about lesson plans, teaching strategies, or assessments."
        else:
            ai_output="Could you please provide more details about your request? Your input seems a bit unclear."
        log_query_to_blob(
            resource_id=resource_id,
            query=query,
            processing_time=0.00,
            ai_output=ai_output,
            recommended_questions="No recommendations generated",
        )
        st.error(ai_output)
        st.session_state.unrelated_query_flag = True
        st.stop()

    with st.spinner("🔄 Processing your request…"):
        start_time = time.time()
        query_type = classification_text

        raw_fields = required_fields_response.choices[0].message.content
        try:
            raw_fields_clean = re.sub(r"^```(?:json)?|```$", "", raw_fields.strip(), flags=re.MULTILINE)
            fields_list = [item["field"] for item in json.loads(raw_fields_clean)]
        except json.JSONDecodeError as e:
            print(f"JSON decode error for fields: {e}, raw: {raw_fields}")
            st.error("Something went wrong while processing your request. Please try again.")
            st.stop()

        combined_chunks = ""
        lesson_content = ""
        files = ""
        lesson_plan = ""
        benchmark_to_resource_ids = {}
        grade_levels = ""
        primary_benchmarks = set()
        secondary_benchmarks = set()
        primary_docs = []
        doc_benchmarks = None
        all_benchmarks_description = ""
        all_file_paths = []
        last_query_type = ""
        benchmark_desc_text = ""
        deleted_resource_ids_list = []

        st.session_state["query_type"] = query_type


        if query_type == "normal":
            sql_benchmarks = (
                "SELECT Code, RelationshipId FROM ResourceBenchmarks WHERE ResourceId='"
                + resource_id
                + "'"
            )
            conn = pyodbc.connect(AZURE_SQL_CONNECTION)
            cursor = conn.cursor()
            cursor.execute(sql_benchmarks)
            for row in cursor.fetchall():
                (primary_benchmarks if row.RelationshipId == 1 else secondary_benchmarks).add(row.Code)
            cursor.close()
            conn.close()
            
            search_results = search_client.search(search_text=resource_id, top=3)
            for doc in search_results:
                if str(doc.get("id", "")) == resource_id:
                    lesson_plan = str(doc)
                    doc_benchmarks = doc.get("BenchmarkCodes")
                    files = doc.get("Files")
                    all_benchmarks_description = doc.get("Benchmark_Description", "")
                    grade_levels = doc.get("GradeLevelNames", "")
            benchmarks_code_set = set(normalize_benchmarks(doc_benchmarks))
            if benchmarks_code_set:

                placeholders = ",".join(["?"] * len(benchmarks_code_set))

                list_benchmarks = f"""
                SELECT ResourceId
                FROM ResourceBenchmarks
                WHERE Code IN ({placeholders})
                AND IsDeleted = 1
                """
                conn = pyodbc.connect(AZURE_SQL_CONNECTION)
                cursor = conn.cursor()
                cursor.execute(list_benchmarks, tuple(benchmarks_code_set))
                rows = cursor.fetchall()
                
                for row in rows:
                    deleted_resource_ids_list.append(str(row.ResourceId))
                cursor.close()
                conn.close()

            if primary_benchmarks:
                primary_benchmarks = list(primary_benchmarks)
            else:
                primary_benchmarks = normalize_benchmarks(doc_benchmarks)
            if primary_benchmarks:
                set_rid = set()
                for doc in search_client.search(search_text="".join(map(str, primary_benchmarks)), top=500):
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
            
            if doc_benchmarks:
                doc_list = normalize_benchmarks(doc_benchmarks)
                benchmark_to_resource_ids = {b: [] for b in doc_list}
                for doc in search_client.search(search_text=doc_benchmarks, top=1700):
                    r_id = doc.get("id", "")
                    if r_id in deleted_resource_ids_list:
                        continue
                    rid_list = normalize_benchmarks(doc.get("BenchmarkCodes"))
                    matched_benchmarks = set(doc_list) & set(rid_list)
                    for bm in matched_benchmarks:
                        benchmark_to_resource_ids[bm].append(r_id)

            if files:
                all_file_paths = clean_file_paths(re.findall(r"\(([^)]+)\)", files))

            if doc_benchmarks:
                formatted_benchmarks = format_benchmarks(all_benchmarks_description)
                st.session_state["formatted_benchmarks"] = formatted_benchmarks

            if benchmark_to_resource_ids:
                st.session_state["benchmark_to_resource_ids"] = benchmark_to_resource_ids


        elif query_type.startswith("reference"):
            parts = query_type.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                st.error(
                    "Could not identify the benchmark reference. Please include the benchmark "
                    "code in your query (e.g., 'refer to benchmark MA.K.NSO.1.1')."
                )
                st.stop()

            benchmark_codes_raw = parts[1].strip().upper()

            search_results = search_client.search(search_text=resource_id, top=3)
            for doc in search_results:
                if str(doc.get("id", "")) == resource_id:
                    lesson_plan = str(doc)
                    doc_benchmarks = doc.get("BenchmarkCodes")
                    files = doc.get("Files")
                    all_benchmarks_description = doc.get("Benchmark_Description", "")
                    grade_levels = doc.get("GradeLevelNames", "")


            if benchmark_codes_raw:
                benchmark_list=normalize_benchmarks(benchmark_codes_raw)


                placeholders = ",".join(["?"] * len(benchmark_list))
                conn = pyodbc.connect(AZURE_SQL_CONNECTION)
                cursor = conn.cursor()
                cursor.execute(
                        f"SELECT ResourceId FROM ResourceBenchmarks WHERE Code IN ({placeholders}) AND IsDeleted=1",
                        tuple(benchmark_list),
                    )
                deleted_resource_ids_list = [str(r.ResourceId) for r in cursor.fetchall()]
                cursor.close()
                conn.close()

                
                set_rid = set()
                for doc in search_client.search(search_text=benchmark_codes_raw, top=500):
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

            if benchmark_list:
                benchmark_to_resource_ids = {b: [] for b in benchmark_list}
                benchmark_info = {
                    b: {"description": "", "benchmark_id": "", "found_desc": False}
                    for b in benchmark_list
                }

                for doc in search_client.search(search_text=benchmark_codes_raw, top=1700):
                    r_id = doc.get("id", "")
                    if r_id in deleted_resource_ids_list:
                        continue

                    rid_list = normalize_benchmarks(doc.get("BenchmarkCodes"))
                    matched_benchmarks = set(benchmark_list) & set(rid_list)

                    for bm in matched_benchmarks:
                        benchmark_to_resource_ids[bm].append(r_id)

                        if not benchmark_info[bm]["found_desc"]:
                            raw_desc_field = doc.get("Benchmark_Description", "")
                            benchmark_info[bm]["benchmark_id"]=get_benchmark_id(raw_desc_field,bm)
                            benchmark_info[bm]["description"]=get_benchmark_description(raw_desc_field,bm)
                            benchmark_info[bm]["found_desc"] = True
            if benchmark_info:
                formatted_benchmarks = format_benchmarks_from_dict(benchmark_info)
                st.session_state["formatted_benchmarks"] = formatted_benchmarks
                benchmark_desc_text = get_all_benchmark_descriptions(benchmark_info)
                st.session_state["benchmark_desc_text"] = benchmark_desc_text


            if benchmark_to_resource_ids:
                st.session_state["benchmark_to_resource_ids"] = benchmark_to_resource_ids
            
            
            if files:
                all_file_paths = clean_file_paths(re.findall(r"\(([^)]+)\)", files))


        
        elif query_type == "followup":

            previous_entries = [
                e for e in st.session_state.user_history
                if e["resource_id"] == resource_id
            ]
            if previous_entries:
                last_entry = previous_entries[-1]
                last_query_type = last_entry.get("query_type", "")
                if last_query_type == "normal":
                    search_results = search_client.search(search_text=resource_id, top=3)
                    for doc in search_results:
                        if str(doc.get("id", "")) == resource_id:
                            lesson_plan = str(doc)
                            doc_benchmarks = doc.get("BenchmarkCodes")
                            files = doc.get("Files")
                            all_benchmarks_description = doc.get("Benchmark_Description", "")
                            grade_levels = doc.get("GradeLevelNames", "")
                    st.session_state["formatted_benchmarks"] = last_entry.get("formatted_benchmarks", "")
                    st.session_state["benchmark_to_resource_ids"] = last_entry.get("benchmark_to_resource_ids", {})
                elif last_query_type.startswith("reference"):

                    parts = last_query_type.split(maxsplit=1)
                    benchmark_code = parts[1].strip().upper()
                    benchmark_desc_text = last_entry.get("benchmark_desc_text", "")

                    for doc in search_client.search(search_text=resource_id, top=3):
                        if str(doc.get("id", "")) == resource_id:
                            lesson_plan = str(doc)
                            files = doc.get("Files")
                            grade_levels = doc.get("GradeLevelNames", "")

                    st.session_state["resource_id_lists"] = last_entry.get("resource_id_lists", "")
                else:
                    query_type = "normal"
                    for doc in search_client.search(search_text=resource_id, top=3):
                        if str(doc.get("id", "")) == resource_id:
                            lesson_plan = str(doc)
                            doc_benchmarks = doc.get("BenchmarkCodes")
                            files = doc.get("Files")
                            all_benchmarks_description = doc.get("Benchmark_Description", "")
                            grade_levels = doc.get("GradeLevelNames", "")

            else:
                query_type = "normal"
                for doc in search_client.search(search_text=resource_id, top=3):
                    if str(doc.get("id", "")) == resource_id:
                        lesson_plan = str(doc)
                        doc_benchmarks = doc.get("BenchmarkCodes")
                        files = doc.get("Files")
                        all_benchmarks_description = doc.get("Benchmark_Description", "")
                        grade_levels = doc.get("GradeLevelNames", "")
                


            if files:
                all_file_paths = clean_file_paths(re.findall(r"\(([^)]+)\)", files))

            if doc_benchmarks:
                formatted_benchmarks = format_benchmarks(all_benchmarks_description)
                st.session_state["formatted_benchmarks"] = formatted_benchmarks

            if benchmark_to_resource_ids:
                st.session_state["benchmark_to_resource_ids"] = benchmark_to_resource_ids

        else:
            query_type = "normal"
            for doc in search_client.search(search_text=resource_id, top=1):
                lesson_plan = str(doc)
                doc_benchmarks = doc.get("BenchmarkCodes")
                files = doc.get("Files")
                all_benchmarks_description = doc.get("Benchmark_Description", "")
                grade_levels = doc.get("GradeLevelNames", "")

        file_paths = []
        for doc in search_client_1.search(search_text=resource_id, top=10):
            path = doc.get("metadata_storage_path", "")
            match = re.search(r"/(\d{3,6})/", path)
            if match and match.group(1) == resource_id:
                combined_chunks += doc.get("chunk", "") + "\n\n"
                file_paths.append(doc.get("metadata_storage_name"))

        if files:
            file_name_set = set(file_paths)
            matched_paths = [
                p for p in all_file_paths if p.split("/")[-1] in file_name_set
            ]
            document_links = asyncio.run(generate_blob_urls(matched_paths))
            st.session_state["document_links_md"] = urls_to_clickable_filenames(document_links)

        st.session_state["retrieved_attachments"] = []

        lesson_content = "\n".join([str(doc) for doc in primary_docs])
        tokens = encoding.encode(lesson_content)
        tokens_for_docs=encoding.encode(combined_chunks)
        token_count = len(tokens)
        token_count_for_docs=len(tokens_for_docs)

        FIRST_CHUNK_SIZE = 1000
        MAX_REMAINING_TOKENS = 100_000
        part_1_tokens=[]
        part_2_tokens=[]
        part_1_doc_tokens=[]
        part_2_doc_tokens=[]
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
            async def _run_summary():
                return await asyncio.gather(*summary_tasks)
            responses = asyncio.run(_run_summary())


            idx = 0
            if part_2_text:
                lesson_content = responses[idx].choices[0].message.content
                idx += 1
            else:
                lesson_content = part_1_text

            if part_2_doc_text:
                combined_chunks = responses[idx].choices[0].message.content
            else:
                combined_chunks = part_1_doc_text

        previous_response_ctx = get_previous_response_for_resource(resource_id)
        if previous_response_ctx:
            previous_response_ctx = remove_inline_download_links(previous_response_ctx)
        previous_query_ctx = st.session_state.get("last_processed_query", "")

        if query_type == "normal":
            messages = generate_creative_response(
                query=query,
                resource_id=resource_id,
                lesson_content=lesson_content,
                combined_chunks=combined_chunks,
                grade_levels=grade_levels,
                lesson_plan=lesson_plan,
                all_benchmarks_description=all_benchmarks_description,
                previous_response=previous_response_ctx,
                previous_query=previous_query_ctx,
            )
        elif query_type.startswith("reference"):
            messages = generate_creative_response_for_reference(
                query=query,
                resource_id=resource_id,
                lesson_content=lesson_content,
                combined_chunks=combined_chunks,
                lesson_plan=lesson_plan,
                benchmark_description=benchmark_desc_text,
                previous_response=previous_response_ctx,
                previous_query=previous_query_ctx,
            )
        else:
            if last_query_type == "normal":
                messages=generate_creative_response(
                    query=query,
                    resource_id=resource_id,
                    lesson_content="",
                    combined_chunks="",
                    grade_levels=grade_levels,
                    lesson_plan=lesson_plan,
                    all_benchmarks_description=all_benchmarks_description,
                    previous_response=previous_response_ctx,
                    previous_query=previous_query_ctx,
                )
            else:
                messages = generate_creative_response_for_reference(
                    query=query,
                    resource_id=resource_id,
                    lesson_content="",
                    combined_chunks="",
                    lesson_plan=lesson_plan,
                    benchmark_description=benchmark_desc_text,
                    previous_response=previous_response_ctx,
                    previous_query=previous_query_ctx,
                )


        response = asyncio.run(
            async_azure_openai_call(messages, model=OPENAI_DEPLOYMENT_NAME_3)
        )
        ai_output = response.choices[0].message.content
        ai_output = re.sub(r"^\s*-{2,}\s*$\n?", "", ai_output, flags=re.MULTILINE)

        previous_response = get_previous_response_for_resource(resource_id)
        ui_content, document_content = extract_document_content(ai_output)

        if "#GENERATE_DOCX_LINK" in ai_output:
            worksheet_clean = convert_markdown_to_clean_text_for_docs(
                document_content
                if document_content
                else extract_test_or_worksheet_section(ai_output)
            )
            doc = generate_docx_file(worksheet_clean, title="Student Worksheet")
            doc_io = BytesIO()
            doc.save(doc_io)
            doc_io.seek(0)
            ui_content = replace_generate_docx_link(ui_content, doc_io)
            st.session_state["worksheet_docx"] = doc_io
            ai_output = ui_content
        else:
            ai_output = ui_content

        processing_time = time.time() - start_time
        st.session_state.ai_content = ai_output
        st.session_state.previous_response_for_display = previous_response

        add_to_history(query=query, resource_id=resource_id, ai_output=st.session_state.ai_content,formatted_benchmarks=st.session_state.get("formatted_benchmarks", ""),
                       benchmark_to_resource_ids=st.session_state.get("benchmark_to_resource_ids", {}),query_type=st.session_state.get("query_type", ""),resource_id_lists=st.session_state.get("resource_id_lists", ""), benchmark_desc_text=st.session_state.get("benchmark_desc_text", ""))
        st.session_state.last_processed_query = query

        try:
            log_query_to_blob(
                resource_id=resource_id,
                query=query,
                processing_time=processing_time,
                ai_output=ai_output,
                recommended_questions=st.session_state.recommended_questions
                or "No recommendations generated",
            )
            print(f"Processing time: {processing_time:.2f}s  ✅ Logged to Azure Blob Storage")
        except Exception as log_error:
            print(f"⚠️ Failed to log to blob storage: {log_error}")


if st.session_state.get("formatted_benchmarks") or st.session_state.get("benchmark_to_resource_ids"):

    if st.session_state.get("formatted_benchmarks"):
        st.markdown("### Supporting Benchmarks List")
        st.markdown(st.session_state["formatted_benchmarks"])

    if st.session_state.get("benchmark_to_resource_ids"):
        bm_map = st.session_state["benchmark_to_resource_ids"]
        lines = [
            f"**{bm}** - {', '.join(rids)}"
            for bm, rids in bm_map.items()
            if rids
        ]
        if lines:
            with st.expander("Show All Benchmarks and Resource IDs"):
                for line in lines:
                    st.markdown(line)
else:
    if st.session_state.get("resource_id_lists"):
        st.markdown("### Supporting Resource IDs")
        with st.expander("Show Resource IDs for the Benchmark"):
            st.markdown(st.session_state["resource_id_lists"])

if st.session_state.get("document_links_md"):
    st.markdown("### Supporting Documents")
    st.markdown(st.session_state["document_links_md"])

if st.session_state.ai_content:
    if st.session_state.get("retrieved_attachments") and any(
        lnk.strip() for lnk in st.session_state["retrieved_attachments"]
    ):
        n = len(st.session_state["retrieved_attachments"])
        st.markdown(f"**📁 Retrieved data from {n} attachment(s):**")
        for lnk in st.session_state["retrieved_attachments"]:
            st.markdown(f"- {lnk}", unsafe_allow_html=True)

    formatted_ai = remove_inline_download_links(
        convert_markdown_to_clean_text(st.session_state.ai_content)
    )
    formatted_ai_for_docs = remove_inline_download_links(
        convert_markdown_to_clean_text_for_docs(st.session_state.ai_content)
    )

    col1, col2, col3 = st.columns([1, 1, 0.5])

    with col1:
        if st.button("✏️ Edit", use_container_width=True, key="edit_btn"):
            st.session_state.edit_mode = not st.session_state.edit_mode
            st.rerun()

    with col3:
        download_format = st.radio(
            "📁", ["DOCX", "PDF"], horizontal=True, label_visibility="collapsed"
        )

    with col2:
        if download_format == "DOCX":
            doc = generate_docx_file_for_download(formatted_ai_for_docs)
            doc_io = BytesIO()
            doc.save(doc_io)
            doc_io.seek(0)
            st.download_button(
                label="⬇️ Download",
                data=doc_io,
                file_name=f"cpalms_ai_customization_{resource_id}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )
        else:
            pdf_buffer = generate_structured_pdf(formatted_ai, resource_id)
            st.download_button(
                label="⬇️ Download",
                data=pdf_buffer,
                file_name=f"cpalms_ai_customization_{resource_id}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )

    if st.session_state.edit_mode:
        st.markdown("### ✏️ Edit Mode")
        edited_ai = st.text_area(
            "Edit AI Customization Output:",
            value=remove_inline_download_links(
                convert_markdown_to_clean_text(st.session_state.ai_content)
            ),
            height=600,
            key="edit_ai_customization",
        )
        if st.button("💾 Save Changes", use_container_width=True):
            st.session_state.ai_content = format_user_edits(edited_ai)
            st.session_state.edit_mode = False
            st.rerun()

    else:
        if st.session_state.previous_response_for_display:
            st.markdown(
                f"""
<div class="ai-container">
    <div class="ai-label">✨ AI Customization</div>
    <div class="new-content-box">
        <strong>✨ Latest Customization:</strong>
        <div>{normalize_empty_lines(convert_markdown_to_bold_html_1(st.session_state.ai_content))}</div>
    </div>
    <div class="previous-content-box">
        <strong>📘 Previous Response:</strong>
        <div>{normalize_empty_lines(convert_markdown_to_bold_html_1(st.session_state.previous_response_for_display))}</div>
    </div>
</div>
""",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"""
<div class="ai-container">
    <div class="ai-label">✨ AI Customization</div>
    <div>{normalize_empty_lines(convert_markdown_to_bold_html_1(st.session_state.ai_content))}</div>
</div>
""",
                unsafe_allow_html=True,
            )

    st.markdown("---")
    show_history()