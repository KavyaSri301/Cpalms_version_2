from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential
from datetime import datetime, timezone
import re

def convert_markdown_to_clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'(?<!\n)\s*(^#{1,6}\s*)', r'\n\n\1', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*#{1,6}\s*(.*)', lambda m: f"{m.group(1).strip().upper()}:", text, flags=re.MULTILINE)
    text = re.sub(r'(?<!\n)\n?(?=^\s*\*\*[^\n]+?:\*\*)', r'\n\n', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.*?)\*\*', lambda m: m.group(1).upper(), text)
    text = re.sub(r'(?<!\n)\n?(?=^[A-Z\s]+:)', r'\n\n', text, flags=re.MULTILINE)
    text = re.sub(r'\[(.*?)\]\((.*?)\)', r'\1 (\2)', text)
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text).strip()
    text = re.sub(r'\n\s*\n+', '\n\n', text)
    text = re.sub(r'\n---\n\s*\n', '\n\n', text)
    text = text.replace("*", "").replace("#", "").strip()
    text = text.replace("---", "")
    return text

def remove_inline_download_links(text: str) -> str:
    return re.sub(
        r'📄.*?\(data:application\/vnd\.openxmlformats-officedocument\.wordprocessingml\.document;base64,[^)]+\)',
        '',
        text
    )

def log_query_to_blob(
    resource_id,
    query,
    processing_time,
    ai_output,
    recommended_questions
):
    credential = DefaultAzureCredential()

    account_url = "https://cpalmsaifiles.blob.core.windows.net"

    container_name = "datastorage"
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    date = now.strftime("%Y-%m-%d")

    blob_name = f"logs version 2/lesson_logs_{date}.txt"

    ai_output = remove_inline_download_links(ai_output)
    formatted_ai = convert_markdown_to_clean_text(ai_output)

    log_entry = f"""Time: {timestamp}
Resource ID: {resource_id}
Query: {query}
Processing Time: {processing_time:.2f} seconds

🧠 Recommended Questions:
{recommended_questions}

✨ AI Customization:
{formatted_ai}

------------------------------------------------------------------------------------------------------------
"""

    try:
        blob_service_client = BlobServiceClient(
            account_url=account_url,
            credential=credential
        )

        blob_client = blob_service_client.get_blob_client(
            container=container_name,
            blob=blob_name
        )

        if not blob_client.exists():
            blob_client.create_append_blob()

        blob_client.append_block(log_entry.encode("utf-8"))

        print("✅ Log entry appended successfully (Managed Identity).")

    except Exception as e:
        print(f"❌ Error appending to blob: {e}")