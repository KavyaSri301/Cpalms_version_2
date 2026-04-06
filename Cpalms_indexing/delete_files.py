import os
import asyncio
from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from logs_to_blob import log_query_to_blob



load_dotenv()

def _delete_resource_files_sync(resource_id):
    """Sync helper to delete blobs belonging to a specific resource_id folder"""
    account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
    container_name = os.getenv("STAGING_CONTAINER_NAME")

    credential = DefaultAzureCredential()
    blob_service_client = BlobServiceClient(account_url=account_url, credential=credential)
    container_client = blob_service_client.get_container_client(container_name)

    resource_prefix = f"{resource_id}/"
    blobs = container_client.list_blobs(name_starts_with=resource_prefix)
    deleted_count = 0

    for blob in blobs:
        container_client.delete_blob(blob.name)
        deleted_count += 1

    return deleted_count


async def delete_resource_files_in_container(resource_id):
    """Delete only blobs belonging to a specific resource_id folder in the staging container"""
    deleted_count = await asyncio.to_thread(_delete_resource_files_sync, resource_id)

    if deleted_count == 0:
        log_query_to_blob(f"ℹ️  No files to delete for ResourceID {resource_id}")
    else:
        log_query_to_blob(f"✅ Deleted {deleted_count} file(s) for ResourceID {resource_id}")


# For testing
if __name__ == "__main__":
    import asyncio
    asyncio.run(delete_resource_files_in_container("test_resource_id"))