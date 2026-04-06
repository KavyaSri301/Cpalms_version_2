import aiohttp
import asyncio
from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential
import os
from dotenv import load_dotenv
from document_index import index_blob_documents
from delete_files import delete_resource_files_in_container
import time
from logs_to_blob import log_query_to_blob
import random

# Semaphore to ensure only one resource runs blob indexing at a time
# This prevents Azure Search indexer contention (only one indexer can run at a time)
_blob_indexing_semaphore = asyncio.Semaphore(1)

load_dotenv()

# Reusable aiohttp session for connection pooling
_aiohttp_session = None

# Retry configuration
MAX_DOWNLOAD_RETRIES = 3
INITIAL_RETRY_DELAY = 1  # seconds
MAX_RETRY_DELAY = 30  # seconds

async def retry_with_backoff(func, *args, max_retries=MAX_DOWNLOAD_RETRIES, initial_delay=INITIAL_RETRY_DELAY, **kwargs):
    """
    Retry an async function with exponential backoff and jitter.

    Args:
        func: Async function to retry
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay between retries in seconds

    Returns:
        Result of the function if successful

    Raises:
        Last exception if all retries fail
    """
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            if attempt < max_retries:
                # Exponential backoff with jitter
                delay = min(initial_delay * (2 ** attempt) + random.uniform(0, 1), MAX_RETRY_DELAY)
                log_query_to_blob(f"⚠️ Attempt {attempt + 1}/{max_retries + 1} failed: {str(e)[:100]}. Retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
            else:
                log_query_to_blob(f"❌ All {max_retries + 1} attempts failed: {str(e)[:100]}")

    raise last_exception

async def get_aiohttp_session():
    """Get or create a reusable aiohttp session"""
    global _aiohttp_session
    if _aiohttp_session is None or _aiohttp_session.closed:
        timeout = aiohttp.ClientTimeout(total=120)  # Increased timeout to 120 seconds
        connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
        _aiohttp_session = aiohttp.ClientSession(timeout=timeout, connector=connector)
    return _aiohttp_session

async def generate_blob_urls(relative_paths: list) -> list:
    """Generate full blob URLs with SAS token from relative paths"""
    base_url = "https://cpalmsmediaprod.blob.core.windows.net"
    sas_token = "sv=2024-11-04&ss=b&srt=o&sp=r&se=2026-12-04T12:08:48Z&st=2025-12-04T03:53:48Z&spr=https&sig=KEnN%2FNfFPpZg72Oqs76aDt6r5sB2C7NCWKpBX48xNYw%3D"
    
    urls = []
    for relative_path in relative_paths:
        relative_path=relative_path.split("|")[0]
        relative_path = relative_path.strip()
        if relative_path.startswith("/"):
            relative_path = relative_path[1:]
        full_url = f"{base_url}/{relative_path}?{sas_token}"
        urls.append(full_url)
    
    return urls

def write_to_file(file_name, lines):
    """Helper function to write lines to a file"""
    if not lines:
        return
    with open(file_name, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


async def upload_documents_to_resource_folder(resource_id, file_urls):
    """
    Upload documents from URLs to Azure Blob Storage under resource_id folder.
    Uses async HTTP for faster downloads. Processes files in parallel.
    Includes retry logic with exponential backoff for failed downloads.
    """
    account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
    container_name = os.getenv("STAGING_CONTAINER_NAME")

    credential = DefaultAzureCredential()
    blob_service_client = BlobServiceClient(account_url=account_url, credential=credential)
    container_client = blob_service_client.get_container_client(container_name)

    resource_folder = f"{resource_id}/"
    processed_count = 0
    error_count = 0
    failed_urls = []  # Track failed URLs for reporting

    session = await get_aiohttp_session()

    async def download_and_upload_single(url, file_path, file_path_from_url):
        """Download a single file and upload to blob - inner function for retry"""
        async with session.get(url) as response:
            if response.status == 200:
                content = await response.read()
                blob_client = container_client.get_blob_client(file_path)
                await asyncio.to_thread(blob_client.upload_blob, content, overwrite=True)
                return True
            elif response.status == 404:
                # File not found - don't retry
                raise FileNotFoundError(f"File not found (404): {file_path_from_url}")
            else:
                raise Exception(f"HTTP {response.status} for {file_path_from_url}")

    async def download_and_upload(url):
        """Download a single file and upload to blob with retry logic"""
        nonlocal processed_count, error_count, failed_urls
        from urllib.parse import urlparse
        parsed_url = urlparse(url)
        file_path_from_url = parsed_url.path
        file_path = resource_folder + file_path_from_url.lstrip('/')

        try:
            # Use retry_with_backoff for resilient downloads
            await retry_with_backoff(
                download_and_upload_single,
                url, file_path, file_path_from_url,
                max_retries=MAX_DOWNLOAD_RETRIES
            )
            processed_count += 1
            return True
        except FileNotFoundError as e:
            # Don't retry 404 errors
            log_query_to_blob(f"⚠️ Skipped (not found): {file_path_from_url}")
            error_count += 1
            failed_urls.append({"url": url, "error": "File not found (404)"})
            return False
        except Exception as e:
            log_query_to_blob(f"❌ Failed after {MAX_DOWNLOAD_RETRIES + 1} attempts: {file_path_from_url}: {str(e)[:100]}")
            error_count += 1
            failed_urls.append({"url": url, "error": str(e)[:200]})
            return False

    # Download all files in parallel (max 5 concurrent)
    semaphore = asyncio.Semaphore(5)

    async def limited_download(url):
        async with semaphore:
            return await download_and_upload(url)

    await asyncio.gather(*[limited_download(url) for url in file_urls], return_exceptions=True)

    log_query_to_blob(f"📊 Upload: {processed_count} ok, {error_count} errors")

    if failed_urls:
        log_query_to_blob(f"⚠️ Failed URLs ({len(failed_urls)}):")
        for failed in failed_urls[:10]:  # Log first 10 failures
            log_query_to_blob(f"   - {failed['error'][:80]}")
        if len(failed_urls) > 10:
            log_query_to_blob(f"   ... and {len(failed_urls) - 10} more")

    return processed_count, error_count


def filter_files(files_list):
    # Extract file paths and filter out files with no extension or excluded extensions
    NON_INDEXABLE_EXTENSIONS = { 
    'sb3', 'sb2', 'zip', 'ggb', 'mp4', 'mp3', 'sbx', 'aia', 'pub', 'mov', 'gcode', 'stl', 
    'pages', 'notebook', 'ppsx', 'p12', 'svg', 'nex' 
            }
    filtered_files = []
    non_indexed_files = []
    
    for path in files_list:
        clean_path = path.split('|')[0]  # Remove everything after '|'
        
        # Check if file has no extension
        if '.' not in clean_path:
            non_indexed_files.append(clean_path)
        # Check if file has excluded extension
        elif clean_path.split('.')[-1].lower() in NON_INDEXABLE_EXTENSIONS:
            non_indexed_files.append(clean_path)
        else:
            filtered_files.append(clean_path)
    
    return filtered_files, non_indexed_files



async def index_blob_documents_1(resource_id, paths):
    """
    Main function to:
    1. Generate blob URLs
    2. Upload documents to staging container
    3. Index the documents with retry logic
    4. Delete files after indexing

    Runs completely before returning.
    Includes robust retry mechanism for document indexing.
    """
    max_indexing_retries = 3

    try:
        paths, non_indexed_files = filter_files(paths)
        if len(non_indexed_files) > 0:
            log_query_to_blob(f"\n Skipped files are: {non_indexed_files}")
            print(f"\n Skipped files are: {non_indexed_files}")

        log_query_to_blob(f"\n📂 Processing ResourceID: {resource_id}")
        log_query_to_blob(f"📄 Total files to process: {len(paths)}\n")

        if not paths or len(paths) == 0:
            log_query_to_blob(f"ℹ️ No files to process for ResourceID {resource_id}")
            log_query_to_blob(f"✅ Blob indexing completed (no files) for ResourceID: {resource_id}")
            return {"status": "success", "processed": 0, "message": "No files to process"}

        # Step 1: Generate URLs
        start_time = time.time()
        urls = await generate_blob_urls(paths)
        log_query_to_blob(f"⏱️  Generated URLs in {time.time() - start_time:.2f} seconds\n")

        # Step 2: Upload documents with retry
        start_time = time.time()
        processed, errors = await upload_documents_to_resource_folder(resource_id, urls)
        log_query_to_blob(f"⏱️  Uploaded documents in {time.time() - start_time:.2f} seconds\n")

        if processed == 0:
            log_query_to_blob(f"⚠️ No documents were uploaded for ResourceID {resource_id}")
            log_query_to_blob(f"✅ Blob indexing completed (no uploads) for ResourceID: {resource_id}")
            # Clean up even if no uploads succeeded
            try:
                await delete_resource_files_in_container(resource_id)
            except Exception:
                pass
            return {"status": "success", "processed": 0, "errors": errors, "message": "No uploads succeeded"}

        # Step 3: Index documents with retry logic (serialized via semaphore)
        # Only one resource can run the Azure Search indexer at a time
        async with _blob_indexing_semaphore:
            start_time = time.time()
            indexing_success = False
            last_error = None

            for attempt in range(max_indexing_retries):
                try:
                    log_query_to_blob(f"🔄 Indexing attempt {attempt + 1}/{max_indexing_retries} for ResourceID {resource_id}...")
                    await index_blob_documents()
                    indexing_success = True
                    break
                except Exception as e:
                    last_error = e
                    log_query_to_blob(f"⚠️ Indexing attempt {attempt + 1} failed: {str(e)[:100]}")
                    if attempt < max_indexing_retries - 1:
                        wait_time = 5 * (attempt + 1)
                        log_query_to_blob(f"⏳ Waiting {wait_time}s before retry...")
                        await asyncio.sleep(wait_time)

            if not indexing_success:
                log_query_to_blob(f"❌ Indexing failed after {max_indexing_retries} attempts for ResourceID {resource_id}")

            log_query_to_blob(f"⏱️  Indexed documents in {time.time() - start_time:.2f} seconds\n")

            # Step 4: Clean up only this resource's files from staging container
            start_time = time.time()
            try:
                await delete_resource_files_in_container(resource_id)
                log_query_to_blob(f"⏱️  Deleted files for ResourceID {resource_id} in {time.time() - start_time:.2f} seconds\n")
            except Exception as cleanup_error:
                log_query_to_blob(f"⚠️ Cleanup failed (non-critical): {str(cleanup_error)[:100]}")

        if indexing_success:
            log_query_to_blob(f"✅ Blob indexing completed for ResourceID: {resource_id}")
            print(f"✅ Blob indexing completed for ResourceID: {resource_id}")
            return {"status": "success", "processed": processed, "errors": errors}
        else:
            error_msg = f"Indexing failed after retries: {str(last_error)[:100]}"
            log_query_to_blob(f"⚠️ Blob indexing partially completed for ResourceID {resource_id}: {error_msg}")
            return {"status": "partial", "processed": processed, "errors": errors, "message": error_msg}

    except Exception as e:
        error_msg = f"❌ Blob indexing failed for ResourceID {resource_id}: {str(e)}"
        log_query_to_blob(error_msg)
        print(error_msg)
        # Re-raise the exception so it's caught by asyncio.gather
        raise Exception(error_msg) from e

# For testing
if __name__ == "__main__":
    import asyncio
    
    test_paths = [
        "protected/uploads/resources/29886/successfulmathcenters2023.pptx"    ]
    test_resource_id = "90727"
    
    asyncio.run(index_blob_documents_1(test_resource_id, test_paths))