from azure.storage.blob import BlobServiceClient
from datetime import datetime, timezone
import os
import threading
import atexit
from dotenv import load_dotenv
import logging

load_dotenv()

# Reuse a single BlobServiceClient to avoid creating hundreds of connections
_blob_service_client = None
_current_blob_name = None
_current_blob_client = None

# Buffer to batch multiple log lines into a single append_block call
# This prevents hitting the 50,000 block limit on append blobs
_log_buffer = []
_buffer_lock = threading.Lock()
BUFFER_FLUSH_SIZE = 50  # Flush every 50 log lines (reduces blocks by 50x)

from azure.identity import DefaultAzureCredential

_blob_service_client = None

def _get_blob_service_client():
    global _blob_service_client

    if _blob_service_client is None:
        account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
        if not account_url:
            raise ValueError("AZURE_STORAGE_ACCOUNT_URL not found.")

        credential = DefaultAzureCredential()
        _blob_service_client = BlobServiceClient(
            account_url=account_url,
            credential=credential
        )

    return _blob_service_client



def _get_or_create_blob_client(blob_name):
    """Get or create the blob client, creating the append blob if needed."""
    global _current_blob_name, _current_blob_client

    container_name = "datastorage"
    blob_service = _get_blob_service_client()

    if _current_blob_client is None or _current_blob_name != blob_name:
        _current_blob_client = blob_service.get_blob_client(
            container=container_name,
            blob=blob_name
        )
        _current_blob_name = blob_name

        if not _current_blob_client.exists():
            _current_blob_client.create_append_blob()

    return _current_blob_client


def _flush_buffer():
    """Flush buffered log entries as a single append_block call."""
    global _log_buffer, _current_blob_client, _current_blob_name

    with _buffer_lock:
        if not _log_buffer:
            return

        entries = _log_buffer.copy()
        _log_buffer = []

    # Determine blob name from current time
    now = datetime.now(timezone.utc)
    date = now.strftime("%Y-%m-%d")
    hour = now.strftime("%H")
    # Use hourly log files to further spread blocks across files
    blob_name = f"Indexing logs version 2/indexing_logs_{date}_{hour}h.txt"

    try:
        blob_client = _get_or_create_blob_client(blob_name)
        combined = "\n".join(entries)
        blob_client.append_block(combined.encode("utf-8"))
    except Exception as e:
        logging.error(f"Error flushing log buffer to blob: {e}")
        _current_blob_client = None
        _current_blob_name = None


def log_query_to_blob(text):
    """
    Buffer log entries and flush as a single block every BUFFER_FLUSH_SIZE lines.

    This reduces append_block calls by ~50x, preventing the 50,000 block limit.
    With 50 lines per block and hourly log files, each file supports
    50,000 * 50 = 2.5 million log lines before hitting the limit.
    """
    with _buffer_lock:
        _log_buffer.append(text)
        should_flush = len(_log_buffer) >= BUFFER_FLUSH_SIZE

    if should_flush:
        _flush_buffer()


def flush_logs():
    """Explicitly flush any remaining buffered logs. Call at end of batch processing."""
    _flush_buffer()


# Flush remaining logs when the process exits
atexit.register(_flush_buffer)
