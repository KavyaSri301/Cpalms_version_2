import asyncio
import os
import json
import random
from dotenv import load_dotenv
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from azure.core.exceptions import ResourceNotFoundError
from typing import Dict
from logs_to_blob import log_query_to_blob
import time
# Load .env environment variables
load_dotenv()

# Retry configuration
MAX_UPSERT_RETRIES = 3
INITIAL_RETRY_DELAY = 1  # seconds
MAX_RETRY_DELAY = 30  # seconds



# Import your indexer functions
from indexer1 import create_index_if_not_exists, prepare_document, generate_embedding

def build_text_for_embedding(final_doc: dict) -> str:
        parts = []

        for key, value in final_doc.items():
            if key.lower() == "embedding":
                continue

            if value is None:
                continue

            # Handle lists
            if isinstance(value, list):
                value = " ".join(map(str, value))

            value = str(value).strip()
            if not value:
                continue

            parts.append(value)

        return " ".join(parts)


class AzureSearchIndexer:
    """Azure Search indexer for managing and uploading documents"""
    
    def __init__(self):
        log_query_to_blob(f"\n{'='*60}")
        log_query_to_blob(f"INITIALIZING AZURE SEARCH INDEXER")
        log_query_to_blob(f"{'='*60}")
        log_query_to_blob("Initializing Azure Search Indexer")       

        
        # Load credentials from .env
        self.service_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
        self.index_name = os.getenv("AZURE_SEARCH_INDEX_NAME_1")

        # Validate credentials
        if not all([self.service_endpoint, self.index_name]):
            log_query_to_blob(f"\n✗ ERROR: Missing Azure Search credentials in .env file")
            log_query_to_blob(f"  • Service Endpoint: {'✓' if self.service_endpoint else '✗'}")
            log_query_to_blob(f"  • Index Name: {'✓' if self.index_name else '✗'}")
            log_query_to_blob(f"{'='*60}\n")
            log_query_to_blob("Missing Azure Search credentials in .env file")
            raise ValueError("Missing Azure Search credentials in .env file")

        # Managed Identity credential (async) — stored so it can be closed in __aexit__
        self.credential = DefaultAzureCredential()

        # Initialize ASYNC clients
        log_query_to_blob(f"\n→ Initializing Azure Search clients...")
        log_query_to_blob("Initializing Azure Search clients...")
        try:
            self.index_client = SearchIndexClient(
                endpoint=self.service_endpoint,
                credential=self.credential
            )
            log_query_to_blob(f"  ✓ Index client initialized")
            print(f"✓ Index client initialized")
            log_query_to_blob("Index client initialized")

            self.search_client = SearchClient(
                endpoint=self.service_endpoint,
                index_name=self.index_name,
                credential=self.credential
            )
            log_query_to_blob(f"  ✓ Search client initialized")
            log_query_to_blob(f"{'='*60}\n")
            log_query_to_blob("Search client initialized")

        except Exception as e:
            log_query_to_blob(f"\n✗ ERROR: Failed to initialize clients: {str(e)}")
            log_query_to_blob(f"Failed to initialize clients: {str(e)}")
            log_query_to_blob(f"{'='*60}\n")
            log_query_to_blob("Failed to initialize clients")
            raise

    async def __aenter__(self):
        """Async context manager entry"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - close clients and credential"""
        await self.search_client.close()
        await self.index_client.close()
        await self.credential.close()

    


    async def smart_upsert_document(self, new_document: Dict) -> Dict:
        """
        Intelligently upsert document - merge with existing data if present, otherwise insert new.
        Includes robust retry logic with exponential backoff.
        """
        try:
            document_id = new_document.get('id')
            log_query_to_blob(f"\n{'='*60}")
            log_query_to_blob(f"SMART UPSERT - Document ID: {document_id}")
            log_query_to_blob(f"{'='*60}")
            log_query_to_blob(f"Smart upsert for Document ID: {document_id}")

            # Check if document exists
            try:
                existing_doc = await self.search_client.get_document(key=document_id)
                log_query_to_blob(f"✓ Document exists - merging data...")
                log_query_to_blob(f"Document exists - merging data for ID: {document_id}")

                # Merge: keep existing if new is None/empty
                final_doc = {}
                for key in set(list(existing_doc.keys()) + list(new_document.keys())):
                    new_val = new_document.get(key)
                    existing_val = existing_doc.get(key)

                    # Always update embedding
                    if key == 'embedding':
                        final_doc[key] = new_val if new_val else existing_val
                    # Use new value if it's not empty, otherwise keep existing
                    elif new_val not in (None, "", [], "None"):
                        final_doc[key] = new_val
                    elif existing_val is not None:
                        final_doc[key] = existing_val
                text_for_embedding = build_text_for_embedding(final_doc)
                final_doc["embedding"] = generate_embedding(text_for_embedding)
                log_query_to_blob("Updated embeddings")

                # Show what's being updated
                updated_fields = [k for k in new_document.keys()
                                if k in existing_doc and new_document[k] not in (None, "", [], "None")
                                and new_document[k] != existing_doc.get(k)]
                if updated_fields:
                    log_query_to_blob(f"→ Updating fields: {', '.join(updated_fields)}")
                    log_query_to_blob(f"Updating fields for ID {document_id}: {', '.join(updated_fields)}")

                operation = "UPDATE"

            except ResourceNotFoundError:
                log_query_to_blob(f"✓ New document - inserting...")
                log_query_to_blob(f"New document - inserting ID: {document_id}")
                final_doc = new_document
                operation = "INSERT"

            # Upload document with retry logic
            start = time.time()
            last_error = None

            for attempt in range(MAX_UPSERT_RETRIES + 1):
                try:
                    result = await self.search_client.merge_or_upload_documents(documents=[final_doc])
                    elapsed = time.time() - start

                    if result[0].succeeded:
                        retry_suffix = f"_RETRY_{attempt}" if attempt > 0 else ""
                        log_query_to_blob(f"✓ {operation}{retry_suffix} completed in {elapsed:.2f}s")
                        log_query_to_blob(f"{'='*60}\n")
                        log_query_to_blob(f"{operation} completed in {elapsed:.2f}s")
                        return {"success": True, "operation": operation + retry_suffix, "document_id": document_id}
                    else:
                        last_error = result[0].error_message if hasattr(result[0], 'error_message') else 'Unknown'
                        log_query_to_blob(f"✗ Attempt {attempt + 1} failed: {last_error}")

                        if attempt < MAX_UPSERT_RETRIES:
                            delay = min(INITIAL_RETRY_DELAY * (2 ** attempt) + random.uniform(0, 1), MAX_RETRY_DELAY)
                            log_query_to_blob(f"🔄 Retrying in {delay:.1f}s... (attempt {attempt + 2}/{MAX_UPSERT_RETRIES + 1})")
                            await asyncio.sleep(delay)

                except Exception as e:
                    last_error = str(e)
                    log_query_to_blob(f"⚠️ Exception on attempt {attempt + 1}: {last_error[:100]}")

                    if attempt < MAX_UPSERT_RETRIES:
                        delay = min(INITIAL_RETRY_DELAY * (2 ** attempt) + random.uniform(0, 1), MAX_RETRY_DELAY)
                        log_query_to_blob(f"🔄 Retrying in {delay:.1f}s...")
                        await asyncio.sleep(delay)

            # All retries exhausted
            log_query_to_blob(f"✗ All {MAX_UPSERT_RETRIES + 1} attempts failed for document ID {document_id}")
            log_query_to_blob(f"{'='*60}\n")
            return {"success": False, "operation": operation, "document_id": document_id, "error": last_error}

        except Exception as e:
            log_query_to_blob(f"✗ Error: {str(e)}")
            log_query_to_blob(f"{'='*60}\n")
            raise

    async def index_data(self, resource_json: Dict):
        """Create index if needed, prepare and upload/update document"""
        try:
            log_query_to_blob(f"\n{'#'*60}")
            log_query_to_blob(f"# STARTING INDEXING PROCESS")
            log_query_to_blob(f"{'#'*60}\n")
            
            start_time = time.time()
            
            # Step 1: Create index if not present
            log_query_to_blob(f"[STEP 1/3] Index Creation")
            log_query_to_blob("Creating index if not present...")
            await create_index_if_not_exists(self)

            # Step 2: Prepare document
            log_query_to_blob(f"[STEP 2/3] Document Preparation")
            log_query_to_blob("Preparing document...")
            prep_start = time.time()
            document = prepare_document(self, resource_json)
            prep_elapsed = time.time() - prep_start
            log_query_to_blob(f"✓ Document preparation completed in {prep_elapsed:.2f}s\n")
            log_query_to_blob(f"Document preparation completed in {prep_elapsed:.2f}s")

            # Step 3: Smart Upsert
            log_query_to_blob(f"[STEP 3/3] Smart Upsert")
            log_query_to_blob(f"→ Upserting document with ID...")
            upsert_result = await self.smart_upsert_document(document)
            
            total_elapsed = time.time() - start_time
            
            # Check result
            if upsert_result['success']:
                log_query_to_blob(f"{'#'*60}")
                log_query_to_blob(f"# ✓ INDEXING COMPLETED SUCCESSFULLY")
                log_query_to_blob("Indexing completed successfully")
                log_query_to_blob(f"# Operation: {upsert_result['operation']}")
                log_query_to_blob(f"Operation: {upsert_result['operation']}")
                log_query_to_blob(f"# Document ID: {upsert_result['document_id']}")
                
                log_query_to_blob(f"Document ID: {upsert_result['document_id']}")
                log_query_to_blob(f"# Total Duration: {total_elapsed:.2f}s")
                log_query_to_blob(f"Total Duration: {total_elapsed:.2f}s")
                log_query_to_blob(f"{'#'*60}\n")
            else:
                log_query_to_blob(f"{'#'*60}")
                log_query_to_blob(f"# ✗ INDEXING FAILED")
                log_query_to_blob("Indexing failed")
                log_query_to_blob(f"# Document ID: {upsert_result['document_id']}")
                log_query_to_blob(f"Document ID: {upsert_result['document_id']}")
                log_query_to_blob(f"# Error: {upsert_result.get('error', 'Unknown error')}")
                log_query_to_blob(f"Error: {upsert_result.get('error', 'Unknown error')}")
                log_query_to_blob(f"{'#'*60}\n")

        except Exception as e:
            log_query_to_blob(f"\n{'#'*60}")
            log_query_to_blob(f"# ✗ INDEXING FAILED")
            log_query_to_blob("Indexing failed")
            log_query_to_blob(f"# Error: {str(e)}")
            log_query_to_blob(f"Error: {str(e)}")
            log_query_to_blob(f"{'#'*60}\n")
            raise


async def json_indexer(resource_json_file: Dict):
    """Main function to index a resource JSON"""
    try:
        log_query_to_blob(f"* JSON INDEXER STARTED")
        log_query_to_blob(f"→ Input type: {type(resource_json_file).__name__}")
        
        if isinstance(resource_json_file, dict):
            log_query_to_blob(f"→ Keys in JSON: {len(resource_json_file)}")
            if 'ResourceID' in resource_json_file:
                log_query_to_blob(f"→ Resource ID: {resource_json_file.get('ResourceID')}")
        
        log_query_to_blob(f"{'*'*60}\n")
        
        # Instantiate indexer with async context manager
        async with AzureSearchIndexer() as indexer:
            # Start indexing
            await indexer.index_data(resource_json_file)
        
        log_query_to_blob(f"\n{'*'*60}")
        log_query_to_blob(f"* JSON INDEXER COMPLETED")
        log_query_to_blob(f"{'*'*60}\n")
        
    except Exception as e:
        log_query_to_blob(f"\n{'*'*60}")
        log_query_to_blob(f"* JSON INDEXER FAILED")
        log_query_to_blob(f"* Error: {str(e)}")
        log_query_to_blob(f"{'*'*60}\n")
        raise


# Optional: For direct script execution
if __name__ == "__main__":
    log_query_to_blob("\n" + "="*60)
    log_query_to_blob("DIRECT SCRIPT EXECUTION")