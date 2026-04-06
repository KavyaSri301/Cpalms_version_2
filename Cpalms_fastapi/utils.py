"""
Utility functions, session management, and helper functions
"""
import re
import json
import asyncio
import pyodbc
import secrets
import uuid
import threading
from typing import List, Dict, Tuple
from datetime import datetime, timezone
from fastapi import Request
from rapidfuzz import fuzz

from config import (
    encoding, client, client_2, client_3, client_4, search_client, search_client_1,
    OPENAI_DEPLOYMENT_NAME, OPENAI_DEPLOYMENT_NAME_2, OPENAI_DEPLOYMENT_NAME_3, OPENAI_DEPLOYMENT_NAME_4,
    AZURE_SQL_CONNECTION, AZURE_BLOB_BASE_URL, AZURE_BLOB_SAS_TOKEN,
    FIRST_CHUNK_SIZE, MAX_REMAINING_TOKENS, openai_executor
)
from db_pool import db_pool
_semaphore_gpt52 = asyncio.Semaphore(20)
_semaphore_gpt51 = asyncio.Semaphore(10)
_semaphore_classify = asyncio.Semaphore(15)


class Session:
    """Session class to store conversation history and user details."""
    def __init__(self, token: str, session_id: str, user_id: str):
        self.history: List[Dict] = []
        self.token = token
        self.session_id = session_id
        self.user_id = user_id
        self.last_active = datetime.now(timezone.utc)

session_data: Dict[str, Session] = {}
store_lock = threading.Lock()  


def cleanup_old_sessions(max_age_minutes: int = 30):
    """Remove inactive sessions that have been idle for more than 30 minutes."""
    current_time = datetime.now(timezone.utc)
    with store_lock:
        keys_to_delete = []
        for session_key, session in session_data.items():
            time_diff = (current_time - session.last_active).total_seconds()
            if time_diff > max_age_minutes * 60:
                keys_to_delete.append(session_key)
        for key in keys_to_delete:
            del session_data[key]
        if keys_to_delete:
            print(f"🧹 Cleaned up {len(keys_to_delete)} expired sessions")


def get_or_create_session_key(request: Request, session_id: str, user_id: str) -> tuple:
    """Get existing session key from cookie or create a new one.
    Returns: (session_key, token, is_new_session)
    """
    cookie_session_key = request.cookies.get("conversation_session_key")
    cookie_token = request.cookies.get("conversation_token")

    if cookie_session_key and cookie_session_key in session_data:
        session = session_data[cookie_session_key]
        if (session.token == cookie_token and
            session.session_id == session_id and
            session.user_id == user_id):
            session.last_active = datetime.now(timezone.utc)
            return cookie_session_key, session.token, False
    new_key = f"{user_id}_{session_id}_{uuid.uuid4().hex[:8]}"
    new_token = secrets.token_hex(16)

    with store_lock:
        session_data[new_key] = Session(
            token=new_token,
            session_id=session_id,
            user_id=user_id
        )

    print(f"✨ New session created: {new_key}")
    return new_key, new_token, True


def _parse_json_column(value):
    """Safely parse a DB column that stores a JSON array. Returns a list."""
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else [parsed]
    except (json.JSONDecodeError, TypeError):
        return [value]


def get_conversation_history_from_db(session_id: str, user_id: str, resource_id: str, limit: int = 10) -> List[Dict]:
    """
    Retrieve the latest N conversation messages from SQL database.

    Schema: one row per (User_ID, Session_ID, resource_id).
    Every column stores a JSON array (one element per message).
    Slices to `limit` immediately after parsing — before building message dicts.
    """
    try:
        conn = db_pool.get_connection()
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT query, response, Timestamp, supporting_documents,
                       Benchmarks, Benchmarks_Long, Response_Type, worksheet
                FROM ChatLogs
                WHERE User_ID = ? AND Session_ID = ? AND resource_id = ?
                """,
                (user_id, session_id, resource_id)
            )
            row = cursor.fetchone()
            cursor.close()
        finally:
            db_pool.return_connection(conn)

        if not row:
            print(f"📚 No conversation history found in DB for session {session_id}")
            return []

        queries        = _parse_json_column(row.query)
        responses      = _parse_json_column(row.response)
        timestamps     = _parse_json_column(row.Timestamp)
        supp_docs      = _parse_json_column(row.supporting_documents)
        benchmarks_arr = _parse_json_column(row.Benchmarks)
        bench_long_arr = _parse_json_column(row.Benchmarks_Long)
        resp_types     = _parse_json_column(row.Response_Type)
        worksheets     = _parse_json_column(row.worksheet)

        total = max(len(queries), len(responses), len(timestamps), 1)

        start_idx = max(0, total - limit)

        def safe_get(lst, i, default=""):
            return lst[i] if i < len(lst) else default

        recent = []
        for i in range(start_idx, total):
            supp_raw = safe_get(supp_docs, i, "")
            recent.append({
                "query":                safe_get(queries, i),
                "response":             safe_get(responses, i),
                "timestamp":            safe_get(timestamps, i, datetime.now(timezone.utc).isoformat()),
                "supporting_documents": supp_raw.split(',') if supp_raw else [],
                "benchmarks":           safe_get(benchmarks_arr, i),
                "benchmarks_long":      safe_get(bench_long_arr, i),
                "response_type":        safe_get(resp_types, i, "plain text"),
                "worksheet":            safe_get(worksheets, i),
            })

        print(f"📚 Retrieved {len(recent)} of {total} messages from DB for session {session_id}")
        return recent

    except Exception as e:
        print(f"❌ Error retrieving conversation history from DB: {str(e)}")
        return []


def get_conversation_history_from_memory(session_key: str, session_id: str, user_id: str) -> List[Dict]:
    """Retrieve conversation history from in-memory storage and filter entries older than 30 minutes."""
    if not session_key or session_key not in session_data:
        return []

    try:
        with store_lock:
            session = session_data.get(session_key)
            if not session:
                return []

            if session.session_id != session_id or session.user_id != user_id:
                return []

            current_time = datetime.now(timezone.utc)
            filtered_history = []
            
            for item in session.history:
                item_time = datetime.fromisoformat(item["timestamp"].replace('Z', '+00:00'))
                if item_time.tzinfo is None:
                    item_time = item_time.replace(tzinfo=timezone.utc)
                
                time_diff = (current_time - item_time).total_seconds()
                if time_diff <= 1800:  
                    filtered_history.append(item)

            session.history = filtered_history
            session.last_active = current_time
            return filtered_history
            
    except Exception as e:
        print(f"Error retrieving conversation history: {e}")
        return []


def get_combined_conversation_history(
    session_key: str,
    session_id: str,
    user_id: str,
    resource_id: str,
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """
    Get conversation history from memory first, fallback to database if empty.

    Returns:
        history_for_openai        – queries for THIS resource_id only (for OpenAI context)
        full_history_for_response – ALL queries across all resource_ids (for previous_response API field)
        resource_history          – queries for THIS resource_id only (for classify/followup index lookup)
    """
    full_history = get_conversation_history_from_memory(session_key, session_id, user_id)
    resource_history = [
        item for item in full_history
        if item.get("resource_id") == resource_id
    ]

    if not resource_history:
        print("💾 No history in memory for this resource, fetching from database...")
        resource_history = get_conversation_history_from_db(session_id, user_id, resource_id, limit=5)

        if resource_history and session_key in session_data:
            with store_lock:
                for item in resource_history:
                    session_data[session_key].history.append({
                        "timestamp": item["timestamp"],
                        "query": item["query"],
                        "response": item["response"],
                        "resource_id": resource_id,
                        "response_type": item.get("response_type", "plain text"),
                        "session_id": session_id,
                        "user_id": user_id,
                        "supporting_documents": item.get("supporting_documents", []),
                        "benchmarks": item.get("benchmarks", ""),
                        "benchmarks_long": item.get("benchmarks_long", ""),
                        "worksheet": item.get("worksheet", "")
                    })
                print(f"✅ Populated in-memory cache with {len(resource_history)} conversations from DB")

    history_for_openai = [
        {"query": item["query"], "response": item["response"]}
        for item in resource_history
    ]
    full_history_for_response = full_history if full_history else resource_history

    print(f"📊 History stats: {len(full_history_for_response)} total, {len(history_for_openai)} for OpenAI, {len(resource_history)} for this resource")
    return history_for_openai, full_history_for_response, resource_history


def add_to_conversation_history_in_memory(session_key, query, response, resource_id,
                                           response_type, session_id, user_id,
                                           supporting_documents, benchmarks,
                                           worksheet):
    """Add a new entry to conversation history in memory storage."""
    new_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": "user",
        "query": query,
        "response": response,
        "resource_id": resource_id,
        "response_type": response_type,
        "session_id": session_id,
        "user_id": user_id,
        "supporting_documents": supporting_documents,
        "benchmarks": benchmarks,
        "worksheet": worksheet
    }

    with store_lock:
        if session_key in session_data:
            session_data[session_key].history.append(new_entry)
            session_data[session_key].last_active = datetime.now(timezone.utc)
        else:
            new_token = secrets.token_hex(16)
            session_data[session_key] = Session(
                token=new_token,
                session_id=session_id,
                user_id=user_id
            )
            session_data[session_key].history.append(new_entry)




def format_benchmarks_from_dict(benchmark_dict: dict) -> str:
    formatted = []
    for code, details in benchmark_dict.items():
        benchmark_id = details.get("benchmark_id")
        if benchmark_id:
            formatted.append(f"{code}-{benchmark_id}")
    return ", ".join(formatted)

import html
import re
def get_all_benchmark_descriptions(benchmark_dict: dict) -> str:
    formatted_list = []
    for code, details in benchmark_dict.items():
        description = details.get("description", "").strip()
        if description:
            description = html.unescape(description)
            description = re.sub(r"\s+", " ", description)
            formatted_list.append(f"{code}: {description}")
    return "\n\n".join(formatted_list)


async def _search(search_client_obj, **kwargs):
    """Run a blocking Azure Search call off the event loop to avoid blocking all concurrent requests."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: list(search_client_obj.search(**kwargs)))


async def async_azure_openai_call(messages, temperature=None, model=OPENAI_DEPLOYMENT_NAME):
    """Call Azure OpenAI API with per-model semaphores and proactive gpt5.1 overflow.

    Routing logic for gpt5.2 (OPENAI_DEPLOYMENT_NAME_3):
      - gpt5.2 has free slots  → use gpt5.2 directly
      - gpt5.2 is full + gpt5.1 has free slots → immediately route to gpt5.1 (no exception needed)
      - both full → wait for gpt5.2 (primary model)

    Classification/summary models use independent semaphores so they never
    compete with the main response calls for capacity.
    """
    loop = asyncio.get_event_loop()
    api_params = {"model": model, "messages": messages}
    if temperature is not None:
        api_params["temperature"] = temperature

    if model == OPENAI_DEPLOYMENT_NAME_3:
        if _semaphore_gpt52._value > 0 or _semaphore_gpt51._value == 0:
            async with _semaphore_gpt52:
                try:
                    return await loop.run_in_executor(
                        openai_executor, lambda: client_3.chat.completions.create(**api_params)
                    )
                except Exception as e:
                    print(f"[gpt5.2] error: {e} — falling back to gpt5.1")
                    params = {**api_params, "model": OPENAI_DEPLOYMENT_NAME}
                    async with _semaphore_gpt51:
                        return await loop.run_in_executor(
                            openai_executor, lambda: client.chat.completions.create(**params)
                        )
        else:
            print("[overflow] gpt5.2 at capacity, routing to gpt5.1 proactively")
            params = {**api_params, "model": OPENAI_DEPLOYMENT_NAME}
            async with _semaphore_gpt51:
                try:
                    return await loop.run_in_executor(
                        openai_executor, lambda: client.chat.completions.create(**params)
                    )
                except Exception as e:
                    print(f"[gpt5.1 overflow] error: {e} — retrying on gpt5.2")
                    async with _semaphore_gpt52:
                        return await loop.run_in_executor(
                            openai_executor, lambda: client_3.chat.completions.create(**api_params)
                        )

    elif model == OPENAI_DEPLOYMENT_NAME:
        async with _semaphore_gpt51:
            try:
                return await loop.run_in_executor(
                    openai_executor, lambda: client.chat.completions.create(**api_params)
                )
            except Exception as e:
                print(f"[gpt5.1] error: {e} — falling back to gpt5.2")
                params = {**api_params, "model": OPENAI_DEPLOYMENT_NAME_3}
                async with _semaphore_gpt52:
                    return await loop.run_in_executor(
                        openai_executor, lambda: client_3.chat.completions.create(**params)
                    )

    elif model == OPENAI_DEPLOYMENT_NAME_4:
        async with _semaphore_classify:
            try:
                return await loop.run_in_executor(
                    openai_executor, lambda: client_4.chat.completions.create(**api_params)
                )
            except Exception as e:
                print(f"[summary model] error: {e} — falling back to gpt5.1")
                params = {**api_params, "model": OPENAI_DEPLOYMENT_NAME}
                async with _semaphore_gpt51:
                    return await loop.run_in_executor(
                        openai_executor, lambda: client.chat.completions.create(**params)
                    )

    else:
        async with _semaphore_classify:
            try:
                return await loop.run_in_executor(
                    openai_executor, lambda: client_2.chat.completions.create(**api_params)
                )
            except Exception as e:
                print(f"[classify model] error: {e} — retrying")
                raise



def extract_worksheet_content(response_text: str) -> Tuple[str, str]:
    """Extract worksheet content from response text."""
    pattern = r'<!--\s*DOCUMENT_CONTENT_START\s*-->(.*?)<!--\s*DOCUMENT_CONTENT_END\s*-->'
    match = re.search(pattern, response_text, re.DOTALL | re.IGNORECASE)
    
    if match:
        worksheet_content = match.group(1).strip()
        cleaned_response = re.sub(pattern, '', response_text, flags=re.DOTALL | re.IGNORECASE).strip()
        return cleaned_response, worksheet_content
    
    return response_text, ""


def normalize_benchmarks(value):
    """Normalize benchmark values."""
    if not value:
        return []
    return [b.strip() for b in value.split(",") if b.strip()]


def clean_file_paths(paths: list) -> list:
    """Clean file paths."""
    cleaned = []
    for item in paths:
        if item.startswith("/protected/"):
            cleaned.append(item.split("|")[0])
    return cleaned

def format_benchmarks(text: str) -> str:
    matches = re.findall(
        r'BenchmarkCode:([^,]+).*?BenchmarkId:(\d+)',
        text,
        re.DOTALL
    )
    return ", ".join([f"{code}-{bid}" for code, bid in matches])


def get_benchmark_id(benchmark_description: str, benchmark_code: str):
    """Get benchmark ID from description."""
    pattern = rf"BenchmarkCode:{re.escape(benchmark_code)},.*?BenchmarkId:(\d+)"
    match = re.search(pattern, benchmark_description)
    return match.group(1) if match else None


def format_benchmark_resource_ids(benchmark_to_resource_ids):
    """Format benchmark to resource IDs."""
    lines = []
    for benchmark, resource_ids in benchmark_to_resource_ids.items():
        if resource_ids:
            line = f"{benchmark}: {', '.join(resource_ids)}"
            lines.append(line)
        else:
            lines.append(f"{benchmark}: None")
    return "\n".join(lines)

def detect_response_type(query: str) -> str:
    """
    Detects the type of response based on the query content.

    Improvements over original:
    - Matches multi-word keywords (e.g. "lesson plan") as phrases, not split words
    - Uses fuzz.partial_ratio for substring/partial matches
    - Uses fuzz.token_set_ratio to handle word-order variations
    - Applies a lower threshold for multi-word phrases (more forgiving)
    - Falls back gracefully to "plain text"
    """
    q = query.lower()

    keywords = {
        "question-answer": [
            "assessment", "quiz", "exit ticket", "question",
            "practice", "worksheet", "test","add 2 more"
        ],
        "letter": [
            "letter", "parent letter", "communication letter"
        ],
        "lesson plan": [
            "lesson plan", "lesson"
        ]
    }

    single_word_threshold = 80
    multi_word_threshold = 75

    best_match_type = None
    best_score = 0

    for response_type, keyword_list in keywords.items():
        for keyword in keyword_list:
            is_multi_word = len(keyword.split()) > 1
            threshold = multi_word_threshold if is_multi_word else single_word_threshold

            if is_multi_word:
                score = max(
                    fuzz.partial_ratio(keyword, q),
                    fuzz.token_set_ratio(keyword, q)
                )
            else:
                score = max(
                    (fuzz.ratio(keyword, word) for word in q.split()),
                    default=0
                )
                score = max(score, fuzz.partial_ratio(keyword, q))

            if score > threshold and score > best_score:
                best_score = score
                best_match_type = response_type

    return best_match_type if best_match_type else "plain text"



async def generate_blob_urls(relative_paths: list) -> list:
    """Generate full blob URLs with SAS token from relative paths."""
    urls = []
    for relative_path in relative_paths:
        relative_path = relative_path.split("|")[0].strip()
        if relative_path.startswith("/"):
            relative_path = relative_path[1:]
        full_url = f"{AZURE_BLOB_BASE_URL}/{relative_path}?{AZURE_BLOB_SAS_TOKEN}"
        urls.append(full_url)
    return urls


from prompts import classify_query, get_fields_from_index
_resource_doc_cache: Dict[str, Tuple[dict, float]] = {}
_resource_doc_cache_ttl = 60.0

async def run_parallel_calls(query, resource_id, recent_queries=None):
    import time as _time
    now = _time.monotonic()

    expired = [k for k, v in _resource_doc_cache.items() if (now - v[1]) >= _resource_doc_cache_ttl]
    for k in expired:
        _resource_doc_cache.pop(k, None)

    cached = _resource_doc_cache.get(resource_id)
    if cached and (now - cached[1]) < _resource_doc_cache_ttl:
        resource_doc = cached[0]
    else:
        search_results = await _search(search_client, search_text="*", filter=f"id eq '{resource_id}'", top=1)
        resource_doc = search_results[0] if search_results else {}
        _resource_doc_cache[resource_id] = (resource_doc, now)
    lesson_plan_desc = resource_doc.get("Description", "")

    classification_messages = classify_query(query, lesson_plan_desc, recent_queries or [])
    field_messages = get_fields_from_index(query)

    classification_response, required_fields_response = await asyncio.gather(
        async_azure_openai_call(classification_messages, model=OPENAI_DEPLOYMENT_NAME_2),
        async_azure_openai_call(field_messages, model=OPENAI_DEPLOYMENT_NAME_2)
    )
    return classification_response, required_fields_response, resource_doc


def process_lesson_content_tokens(lesson_content: str, query: str) -> Tuple[str, str]:
    """Split lesson content into (part1, part2) based on token limits."""
    tokens = encoding.encode(lesson_content)
    token_count = len(tokens)

    print(f"Token count of lesson_content: {token_count}")

    if token_count <= FIRST_CHUNK_SIZE:
        return lesson_content, ""

    part_1_tokens = tokens[:FIRST_CHUNK_SIZE]
    remaining_tokens = tokens[FIRST_CHUNK_SIZE:]

    if len(remaining_tokens) > MAX_REMAINING_TOKENS:
        part_2_tokens = remaining_tokens[:MAX_REMAINING_TOKENS]
    else:
        part_2_tokens = remaining_tokens

    part_1_text = encoding.decode(part_1_tokens)
    part_2_text = encoding.decode(part_2_tokens)

    print(f"Part 1 tokens: {len(part_1_tokens)}")
    print(f"Part 2 tokens: {len(part_2_tokens)}")

    return part_1_text, part_2_text


def search_and_extract_documents(resource_id: str, search_client_1) -> Tuple[List[str], str]:
    """Search for document chunks and extract file paths."""
    file_paths = []
    combined_chunks = ""
    
    search_results_1 = search_client_1.search(search_text=resource_id, top=10)
    for doc in search_results_1:
        path = doc.get("metadata_storage_path", "")
        match = re.search(r"/(\d{3,6})/", path)
        if match and match.group(1) == resource_id:
            chunk = doc.get("chunk", "")
            file_paths.append(doc.get("metadata_storage_name"))
            combined_chunks += chunk + "\n\n"
    
    return file_paths, combined_chunks

def get_benchmark_description(benchmark_description: str, benchmark_code: str) -> str:
    """
    Extracts the description for a given benchmark code from a combined benchmark string.
    """
    pattern = rf"BenchmarkCode:{re.escape(benchmark_code)}, Description:(.*?)(?:, BenchmarkId:|\| BenchmarkCode:|$)"
    
    match = re.search(pattern, benchmark_description, re.DOTALL)
    
    if match:
        return match.group(1).strip()
    
    return ""