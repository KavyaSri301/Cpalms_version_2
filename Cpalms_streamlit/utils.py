"""
utils.py — Utility and helper functions for CPALMS AI Customization Generator.
Covers: session state, history, UI helpers, text processing, search/retrieval helpers.
"""

import re
import os
import asyncio
import threading
from io import BytesIO
from datetime import datetime
from urllib.parse import urlparse, urlunparse, quote

import streamlit as st
import tiktoken
from azure.search.documents import SearchClient
# from azure.identity import DefaultAzureCredential
from azure.identity import DefaultAzureCredential, get_bearer_token_provider    
from azure.core.pipeline.policies import RetryPolicy
from openai import AzureOpenAI
from dotenv import load_dotenv
import pyodbc

from dataformatting import (
    convert_markdown_to_bold_html_1,
    convert_markdown_to_clean_text,
    convert_markdown_to_clean_text_for_docs,
    normalize_empty_lines,
)
from docx_formatting import generate_docx_file, make_docx_link

encoding = tiktoken.get_encoding("cl100k_base")

load_dotenv()

retry_policy = RetryPolicy(retry_total=2, timeout=120)

AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX")
AZURE_SEARCH_INDEX_NAME_1 = os.getenv("AZURE_SEARCH_INDEX_1")
OPENAI_API_VERSION = os.getenv("OPENAI_API_VERSION")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE")
OPENAI_DEPLOYMENT_NAME = os.getenv("OPENAI_DEPLOYMENT_NAME")

OPENAI_API_VERSION_4 = os.getenv("OPENAI_API_VERSION_4")
OPENAI_API_BASE_4 = os.getenv("OPENAI_API_BASE_4")
OPENAI_DEPLOYMENT_NAME_4 = os.getenv("OPENAI_DEPLOYMENT_NAME_4")

AZURE_SQL_CONNECTION = os.getenv("AZURE_SQL_CONNECTION_STRING")

_mi_credential = DefaultAzureCredential()

search_client = SearchClient(
    endpoint=AZURE_SEARCH_ENDPOINT,
    index_name=AZURE_SEARCH_INDEX_NAME,
    credential=_mi_credential,
    retry_policy=retry_policy,
)

search_client_1 = SearchClient(
    endpoint=AZURE_SEARCH_ENDPOINT,
    index_name=AZURE_SEARCH_INDEX_NAME_1,
    credential=_mi_credential,
    retry_policy=retry_policy,
)

_openai_token_provider = get_bearer_token_provider(
    _mi_credential, "https://cognitiveservices.azure.com/.default"
)

client = AzureOpenAI(
    azure_ad_token_provider=_openai_token_provider,
    api_version=OPENAI_API_VERSION,
    azure_endpoint=OPENAI_API_BASE,
)

client_2 = AzureOpenAI(
    azure_ad_token_provider=_openai_token_provider,
    api_version=os.getenv("OPENAI_API_VERSION_2"),
    azure_endpoint=os.getenv("OPENAI_API_BASE_2"),
)

client_3 = AzureOpenAI(
    azure_ad_token_provider=_openai_token_provider,
    api_version=os.getenv("OPENAI_API_VERSION_3"),
    azure_endpoint=os.getenv("OPENAI_API_BASE_3"),
)

client_4 = AzureOpenAI(
    azure_ad_token_provider=_openai_token_provider,
    api_version=OPENAI_API_VERSION_4,
    azure_endpoint=OPENAI_API_BASE_4,
)

OPENAI_DEPLOYMENT_NAME_2 = os.getenv("OPENAI_DEPLOYMENT_NAME_2")

OPENAI_DEPLOYMENT_NAME_3 = os.getenv("OPENAI_DEPLOYMENT_NAME_3")

_openai_semaphore = threading.Semaphore(10)

async def async_azure_openai_call(messages, temperature=None, model=OPENAI_DEPLOYMENT_NAME):
    loop = asyncio.get_event_loop()
    api_params = {"model": model, "messages": messages}
    if temperature is not None:
        api_params["temperature"] = temperature

    def _call(client_obj, params):
        with _openai_semaphore:
            return client_obj.chat.completions.create(**params)

    def _pick_client(m):
        if m == OPENAI_DEPLOYMENT_NAME:
            return client
        if m == OPENAI_DEPLOYMENT_NAME_3:
            return client_3
        if m == OPENAI_DEPLOYMENT_NAME_4:
            return client_4
        return client_2

    def _fallback_model(m):
        if m == OPENAI_DEPLOYMENT_NAME:
            return OPENAI_DEPLOYMENT_NAME_3
        if m == OPENAI_DEPLOYMENT_NAME_3:
            return OPENAI_DEPLOYMENT_NAME
        if m == OPENAI_DEPLOYMENT_NAME_4:
            return OPENAI_DEPLOYMENT_NAME
        return OPENAI_DEPLOYMENT_NAME_3

    try:
        chosen_client = _pick_client(model)
        return await loop.run_in_executor(None, lambda: _call(chosen_client, api_params))
    except Exception as e:
        print(f"⚠️ OpenAI API error on first attempt (model={model}): {e}")
        print("🔄 Retrying with fallback model…")
        fallback = _fallback_model(model)
        api_params["model"] = fallback
        try:
            fallback_client = _pick_client(fallback)
            return await loop.run_in_executor(None, lambda: _call(fallback_client, api_params))
        except Exception as retry_error:
            print(f"❌ OpenAI API failed after retry (fallback={fallback}): {retry_error}")
            raise



def initialize_session_state():
    """Initialise all session-state variables with safe defaults."""
    defaults = {
        "ai_content": "",
        "edit_mode": False,
        "copy_success": False,
        "last_processed_query": "",
        "user_history": [],
        "last_query_key": "",
        "last_resource_id": "",
        "recommended_questions": "",
        "query_input": "",
        "resource_id_input": "",
        "previous_response_for_display": "",
        "unrelated_query_flag": False,
        "query_type": "",
        "formatted_benchmarks": "",
        "benchmark_to_resource_ids": {},
        "resource_id_lists": "",
        "document_links_md": "",
        "query_type": "",
        "benchmark_desc_text": "",
    }
    for key, default in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default


def initialize_session_history():
    """Ensure user_history list exists in session state."""
    if "user_history" not in st.session_state:
        st.session_state.user_history = []


def reset_session_state():
    """Reset transient session-state for a new query."""
    st.session_state.ai_content = ""
    st.session_state.edit_mode = False
    st.session_state.copy_success = False
    st.session_state.previous_response_for_display = ""
    st.session_state.query_type = ""
    st.session_state.formatted_benchmarks = ""
    st.session_state.benchmark_to_resource_ids = {}
    st.session_state.resource_id_lists = ""
    st.session_state.document_links_md = ""
    st.session_state.query_type = ""


def should_process_new_query(query: str, resource_id: str) -> bool:
    """Return True when the query+resource combination has changed."""
    current_key = f"{query}_{resource_id}"
    if "last_query_key" not in st.session_state:
        st.session_state.last_query_key = ""
    if st.session_state.last_query_key != current_key:
        st.session_state.last_query_key = current_key
        reset_session_state()
        return True
    return False



def add_to_history(query: str, resource_id: str, ai_output: str, formatted_benchmarks: str = "", benchmark_to_resource_ids: dict = None, query_type: str = "", resource_id_lists: str = "", benchmark_desc_text: str = ""):
    """Append an entry to user history (capped at 10)."""
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "query": query,
        "resource_id": resource_id,
        "ai_output": ai_output,
        "formatted_benchmarks": formatted_benchmarks,
        "benchmark_to_resource_ids": benchmark_to_resource_ids,
        "query_type": query_type,
        "resource_id_lists": resource_id_lists,
        "benchmark_desc_text": benchmark_desc_text,
    }
    st.session_state.user_history.append(entry)
    st.session_state.user_history = st.session_state.user_history[-10:]


def get_previous_response_for_resource(resource_id: str):
    """Return the most-recent AI output stored for *resource_id*, or None."""
    current_session = [
        e for e in st.session_state.user_history if e["resource_id"] == resource_id
    ]
    return current_session[-1]["ai_output"] if current_session else None


def check_query_in_history(query: str, resource_id: str):
    """
    Check if the exact query has been answered before for this resource.
    Returns (ai_output, previous_query) if found, otherwise (None, None).
    """
    normalized = query.lower().strip()
    for idx, entry in enumerate(st.session_state.user_history):
        if (
            entry["resource_id"] == resource_id
            and entry["query"].lower().strip() == normalized
        ):
            previous_query = None
            if idx > 0:
                prev = st.session_state.user_history[idx - 1]
                if prev["resource_id"] == resource_id:
                    previous_query = prev["query"]
            return entry["ai_output"], previous_query
    return None, None


def format_user_edits(text: str) -> str:
    """Bold ALL-CAPS words (converting to Title Case) and add blank lines before all-caps headings."""
    lines = text.splitlines()
    formatted = []
    for line in lines:
        stripped = line.strip()
        if stripped == "📘 PREVIOUS RESPONSE:":
            formatted.append(stripped)
            continue
        letters_only = re.sub(r"[^A-Za-z]", "", stripped)
        if letters_only and letters_only.isupper():
            if formatted and formatted[-1] != "":
                formatted.append("")

        def bold_title(match):
            return f"**{match.group(1).title()}**"

        line = re.sub(r"\b(?!\*\*)([A-Z]{2,})(?!\*\*)\b", bold_title, line)
        formatted.append(line)
    return "\n".join(formatted)


def remove_markdown(text: str) -> str:
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"`(.*?)`", r"\1", text)
    return text


def remove_inline_download_links(text: str) -> str:
    """Strip any inline base64 DOCX links from text."""
    return re.sub(
        r"📄.*?\(data:application\/vnd\.openxmlformats-officedocument"
        r"\.wordprocessingml\.document;base64,[^)]+\)",
        "",
        text,
    )


def normalize_benchmarks(value: str) -> list:
    if not value:
        return []
    return [b.strip() for b in value.split(",") if b.strip()]


def format_benchmarks(text: str) -> str:
    matches = re.findall(
        r"BenchmarkCode:([^,]+).*?BenchmarkId:(\d+)", text, re.DOTALL
    )
    return ", ".join([f"{code}-{bid}" for code, bid in matches])


def extract_benchmark_code_id(text: str) -> list:
    """Return list of (BenchmarkCode, BenchmarkId) tuples."""
    return re.findall(r"BenchmarkCode:([^,]+).*?BenchmarkId:(\d+)", text)


def format_benchmarks_from_dict(benchmark_dict: dict) -> str:
    formatted = []

    for code, details in benchmark_dict.items():
        benchmark_id = details.get("benchmark_id")

        if benchmark_id is not None:
            formatted.append(f"{code}-{benchmark_id}")
        else:
            formatted.append(code)

    return ", ".join(formatted)

import html

def get_all_benchmark_descriptions(benchmark_dict: dict) -> str:
    formatted_list = []

    for code, details in benchmark_dict.items():
        description = details.get("description", "").strip()

        if description:
            description = html.unescape(description)
            description = re.sub(r"\s+", " ", description)

            formatted_list.append(f"{code}: {description}")

    return "\n\n".join(formatted_list)



def get_benchmark_id(benchmark_description: str, benchmark_code: str):
    pattern = rf"BenchmarkCode:{re.escape(benchmark_code)},.*?BenchmarkId:(\d+)"
    match = re.search(pattern, benchmark_description)
    return match.group(1) if match else None


def get_benchmark_description(benchmark_description: str, benchmark_code: str) -> str:
    pattern = (
        rf"BenchmarkCode:{re.escape(benchmark_code)}, Description:(.*?)"
        r"(?:, BenchmarkId:|\| BenchmarkCode:|$)"
    )
    match = re.search(pattern, benchmark_description, re.DOTALL)
    return match.group(1).strip() if match else ""


def clean_file_paths(paths: list) -> list:
    return [item.split("|")[0] for item in paths if item.startswith("/protected/")]


def extract_document_content(ai_response: str) -> tuple:
    """
    Split AI response into (ui_content, document_content).
    document_content is None when the delimiters are absent.
    """
    doc_start = "<!-- DOCUMENT_CONTENT_START -->"
    doc_end = "<!-- DOCUMENT_CONTENT_END -->"
    if doc_start in ai_response and doc_end in ai_response:
        parts = ai_response.split(doc_start)
        ui_content = parts[0].strip()
        doc_parts = parts[1].split(doc_end)
        document_content = doc_parts[0].strip()
        if len(doc_parts) > 1 and doc_parts[1].strip():
            ui_content += "\n\n" + doc_parts[1].strip()
        return ui_content, document_content
    return ai_response, None


def extract_test_or_worksheet_section(text: str) -> str:
    """Extract a worksheet/quiz/test section from AI output."""
    doc_start = "<!-- DOCUMENT_CONTENT_START -->"
    doc_end = "<!-- DOCUMENT_CONTENT_END -->"
    if doc_start in text and doc_end in text:
        start = text.find(doc_start) + len(doc_start)
        end = text.find(doc_end)
        return text[start:end].strip()
    pattern = r"(##\s*(Worksheet|Quiz|Test)[\s\S]*?)(?=\n##|\Z)"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    question_lines = [
        l for l in text.splitlines()
        if any(q in l.lower() for q in ["question", "?", "1.", "a)", "b)", "answer"])
    ]
    return "\n".join(question_lines).strip()


def replace_generate_docx_link(markdown_text: str, doc_buffer) -> str:
    """Replace #GENERATE_DOCX_LINK placeholders with base64 data URIs."""
    data_uri = make_docx_link(doc_buffer)
    return re.sub(
        r"\[(.*?)\]\(#GENERATE_DOCX_LINK\)", rf"[\1]({data_uri})", markdown_text
    )



def urls_to_clickable_filenames(urls: list) -> str:
    links = []
    for url in urls:
        parsed = urlparse(url)
        encoded_path = quote(parsed.path)
        encoded_url = urlunparse(
            (parsed.scheme, parsed.netloc, encoded_path, parsed.params, parsed.query, parsed.fragment)
        )
        filename = os.path.basename(parsed.path)
        links.append(f"- [{filename}]({encoded_url})")
    return "\n".join(links)


async def generate_blob_urls(relative_paths: list) -> list:
    """Build full blob URLs with SAS token from relative paths."""
    base_url = "https://cpalmsmediaprod.blob.core.windows.net"
    sas_token = os.getenv("AZURE_BLOB_SAS_TOKEN")
    urls = []
    for rp in relative_paths:
        rp = rp.split("|")[0].strip().lstrip("/")
        urls.append(f"{base_url}/{rp}?{sas_token}")
    return urls


async def fetch_recommended_questions(lesson_content: str) -> str:
    from recommendation import generate_recommended_questions

    messages = generate_recommended_questions(lesson_content)
    response = await async_azure_openai_call(messages, model=OPENAI_DEPLOYMENT_NAME_2)
    return response.choices[0].message.content


def display_recommended_questions():
    """Render clickable recommended-question buttons (no auto-submit)."""
    if not st.session_state.recommended_questions:
        return

    st.markdown("### 💡 Recommended Questions")
    st.markdown(
        """
        <style>
        div[data-testid="stHorizontalBlock"] button {
            height: 55px !important; min-height: 55px !important; max-height: 55px !important;
            white-space: normal !important; text-align: left !important;
            padding: 8px 15px !important; overflow: hidden !important;
            font-size: 16px !important; line-height: 1.3 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    questions_list = [
        line.strip()
        for line in st.session_state.recommended_questions.strip().split("\n")
        if re.match(r"^\d+\.", line.strip())
    ]

    for i in range(0, len(questions_list), 2):
        col1, col2 = st.columns(2)
        with col1:
            q1 = questions_list[i]
            q1_text = re.sub(r"^\d+\.\s*", "", q1.strip())
            if st.button(
                q1,
                key=f"rec_q_{i}_1_{st.session_state.last_resource_id}",
                use_container_width=True,
                help="Click to use this question",
            ):
                st.session_state.query_input = q1_text
                st.rerun()
        if i + 1 < len(questions_list):
            with col2:
                q2 = questions_list[i + 1]
                q2_text = re.sub(r"^\d+\.\s*", "", q2.strip())
                if st.button(
                    q2,
                    key=f"rec_q_{i}_2_{st.session_state.last_resource_id}",
                    use_container_width=True,
                    help="Click to use this question",
                ):
                    st.session_state.query_input = q2_text

    st.markdown("---")


def show_history():
    """Render the query history expanders."""
    if not st.session_state.user_history:
        return

    st.markdown("### 📚 Previous Queries")
    for i, entry in enumerate(reversed(st.session_state.user_history), 1):
        with st.expander(
            f"Query {i}: {entry['timestamp']} - Resource ID: {entry['resource_id']}"
        ):
            st.markdown("**Query:**")
            st.write(entry["query"])
            st.markdown("**✨ AI Customization**")

            ui_content, document_content = extract_document_content(entry["ai_output"])
            if "#GENERATE_DOCX_LINK" in entry["ai_output"]:
                if document_content:
                    worksheet_clean = convert_markdown_to_clean_text_for_docs(document_content)
                else:
                    worksheet_section = extract_test_or_worksheet_section(entry["ai_output"])
                    worksheet_clean = convert_markdown_to_clean_text_for_docs(worksheet_section)

                doc = generate_docx_file(worksheet_clean, title="Student Worksheet")
                doc_io = BytesIO()
                doc.save(doc_io)
                doc_io.seek(0)
                ui_content = replace_generate_docx_link(ui_content, doc_io)
                st.write(ui_content)
            else:
                st.write(entry["ai_output"])