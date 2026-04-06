import os
from dotenv import load_dotenv
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient, SearchIndexerClient
from azure.search.documents.indexes.models import (
    SearchIndex, SearchField, SearchFieldDataType, VectorSearch,
    VectorSearchProfile, HnswAlgorithmConfiguration,
    SearchIndexerDataSourceConnection, SearchIndexerDataContainer
)
from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from logs_to_blob import log_query_to_blob
from urllib.parse import quote

import base64
import hashlib
from urllib.parse import unquote
import requests
import json
import asyncio
import random

load_dotenv()

# Retry configuration for document indexing
MAX_INDEXER_RETRIES = 5  # Maximum retries for indexer runs
MAX_MISSING_DOC_RETRIES = 3  # Maximum retries for missing documents
INITIAL_RETRY_DELAY = 2  # Initial delay in seconds
MAX_RETRY_DELAY = 60  # Maximum delay in seconds


async def async_retry_with_backoff(func, *args, max_retries=3, initial_delay=2, operation_name="operation", **kwargs):
    """
    Retry an async function with exponential backoff and jitter.

    Args:
        func: Async function to retry
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay between retries in seconds
        operation_name: Name for logging purposes

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
                delay = min(initial_delay * (2 ** attempt) + random.uniform(0, 1), MAX_RETRY_DELAY)
                log_query_to_blob(f"⚠️ {operation_name} attempt {attempt + 1}/{max_retries + 1} failed: {str(e)[:100]}. Retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
            else:
                log_query_to_blob(f"❌ {operation_name} failed after {max_retries + 1} attempts: {str(e)[:100]}")

    raise last_exception


class AzureSearchIndexer:
    def __init__(self):
        self.search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
        self.index_name = os.getenv("AZURE_SEARCH_INDEX_NAME_2")
        self.openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        # No API key needed — skillset uses the search service's managed identity
        self.embedding_deployment = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
        self.model_name = os.getenv("AZURE_MODEL_NAME")
        # self.cognitive_services_key = None  # Not needed — using managed identity via AIServicesByIdentity

        # Managed Identity credential for Azure Search SDK clients
        _search_credential = DefaultAzureCredential()
        self.credential = _search_credential

        # Bearer token provider for REST API calls (skillset/indexer creation)
        self._token_provider = get_bearer_token_provider(
            _search_credential,
            "https://search.azure.com/.default"
        )

        self.index_client = SearchIndexClient(self.search_endpoint, self.credential)
        self.indexer_client = SearchIndexerClient(self.search_endpoint, self.credential)
        self.search_client = SearchClient(self.search_endpoint, self.index_name, self.credential)
        
        self.data_source_name = "cpalms-stagingblob-datasource"
        self.skillset_name = "cpalms-document-skillset"
        self.indexer_name = "cpalms-document-indexer"
        self.container_name = "stagingblob"
        
        # Track documents throughout the process
        self.manually_indexed_docs = []
        self.missing_docs_found = []
        self.final_unindexed_docs = []

        missing_settings = [
            name for name, value in {
                "AZURE_SEARCH_ENDPOINT": self.search_endpoint,
                "AZURE_SEARCH_INDEX_NAME_2": self.index_name,
                "AZURE_OPENAI_ENDPOINT": self.openai_endpoint,
                "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": self.embedding_deployment,
                "AZURE_MODEL_NAME": self.model_name,
            }.items() if not value
        ]
        if missing_settings:
            raise ValueError(f"Missing required settings: {', '.join(missing_settings)}")
    
    def setup_initial(self, connection_string):
        """One-time setup: Create data source, index, skillset, and indexer"""
        log_query_to_blob("\n=== INITIAL SETUP (Run once) ===\n")
        
        # Data source
        log_query_to_blob("[1/4] Creating data source...")
        data_source = SearchIndexerDataSourceConnection(
            name=self.data_source_name,
            type="azureblob",
            connection_string=connection_string,
            container=SearchIndexerDataContainer(name=self.container_name)
        )
        self.indexer_client.create_or_update_data_source_connection(data_source)
        log_query_to_blob("✓ Data source created")
        
        # Index - REMOVED chunk_index field
        log_query_to_blob("[2/4] Creating index...")
        fields = [
            SearchField(name="id", type=SearchFieldDataType.String, key=True, 
                    filterable=True, analyzer_name="keyword"),
            SearchField(name="parent_id", type=SearchFieldDataType.String, filterable=True),
            SearchField(name="chunk", type=SearchFieldDataType.String, searchable=True),
            # REMOVED: SearchField(name="chunk_index", type=SearchFieldDataType.Int32, filterable=True),
            SearchField(name="metadata_storage_name", type=SearchFieldDataType.String, 
                    searchable=True, filterable=True, facetable=True),
            SearchField(name="metadata_storage_path", type=SearchFieldDataType.String, 
                    filterable=True),
            SearchField(name="text_vector", 
                    type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                    vector_search_dimensions=3072, vector_search_profile_name="vector-profile")
        ]
        
        vector_search = VectorSearch(
            profiles=[VectorSearchProfile(name="vector-profile", 
                                        algorithm_configuration_name="hnsw-config")],
            algorithms=[HnswAlgorithmConfiguration(name="hnsw-config")]
        )
        
        index = SearchIndex(name=self.index_name, fields=fields, vector_search=vector_search)
        self.index_client.create_or_update_index(index)
        log_query_to_blob("✓ Index created")        
        log_query_to_blob("[3/4] Creating skillset with text chunking...")

        skills = [
            {
                "@odata.type": "#Microsoft.Skills.Vision.OcrSkill",
                "name": "#1",
                "context": "/document/normalized_images/*",
                "lineEnding": "Space",
                "defaultLanguageCode": "en",
                "detectOrientation": True,
                "inputs": [{"name": "image", "source": "/document/normalized_images/*"}],
                "outputs": [{"name": "text", "targetName": "text"}]
            },
            {
                "@odata.type": "#Microsoft.Skills.Text.MergeSkill",
                "name": "#2",
                "context": "/document",
                "insertPreTag": " ",
                "insertPostTag": " ",
                "inputs": [
                    {"name": "text", "source": "/document/content"},
                    {"name": "itemsToInsert", "source": "/document/normalized_images/*/text"},
                    {"name": "offsets", "source": "/document/normalized_images/*/contentOffset"}
                ],
                "outputs": [{"name": "mergedText", "targetName": "mergedText"}]
            },
            {
                "@odata.type": "#Microsoft.Skills.Text.SplitSkill",
                "name": "#3",
                "context": "/document",
                "textSplitMode": "pages",
                "maximumPageLength": 8000,  # Stay well under 8000 token limit
                "pageOverlapLength": 500,    # Overlap to maintain context
                "inputs": [
                    {"name": "text", "source": "/document/mergedText"}
                ],
                "outputs": [
                    {"name": "textItems", "targetName": "pages"}
                ]
            },
            {
                "@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
                "name": "#4",
                "context": "/document/pages/*",
                "resourceUri": self.openai_endpoint.rstrip('/'),
                "deploymentId": self.embedding_deployment,
                "modelName": self.model_name,
                "dimensions": 3072,
                "inputs": [
                    {"name": "text", "source": "/document/pages/*"}
                ],
                "outputs": [
                    {"name": "embedding", "targetName": "text_vector"}
                ]
            }
        ]
        
        skillset_dict = {
            "name": self.skillset_name,
            "description": "Extract text from images, chunk it, and generate embeddings",
            "skills": skills,
            "cognitiveServices": {
                "@odata.type": "#Microsoft.Azure.Search.AIServicesByIdentity"
            },
            "indexProjections": {
                "selectors": [{
                    "targetIndexName": self.index_name,
                    "parentKeyFieldName": "parent_id",
                    "sourceContext": "/document/pages/*",
                    "mappings": [
                        {"name": "text_vector", "source": "/document/pages/*/text_vector"},
                        {"name": "chunk", "source": "/document/pages/*"},
                        {"name": "metadata_storage_name", "source": "/document/metadata_storage_name"},
                        {"name": "metadata_storage_path", "source": "/document/metadata_storage_path"}
                    ]
                }],
                "parameters": {"projectionMode": "skipIndexingParentDocuments"}
            }
        }
        
        url = f"{self.search_endpoint}/skillsets/{self.skillset_name}?api-version=2024-07-01"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token_provider()}"
        }
        response = requests.put(url, headers=headers, data=json.dumps(skillset_dict))
        
        if response.status_code not in [200, 201, 204]:
            raise Exception(f"Skillset failed: {response.text}")
        log_query_to_blob("✓ Skillset created with text chunking")
        
        # Indexer
        log_query_to_blob("[4/4] Creating indexer...")
        indexer_dict = {
            "name": self.indexer_name,
            "dataSourceName": self.data_source_name,
            "targetIndexName": self.index_name,
            "skillsetName": self.skillset_name,
            "parameters": {
                "configuration": {
                    "imageAction": "generateNormalizedImages",
                    "dataToExtract": "contentAndMetadata",
                    "failOnUnsupportedContentType": False,
                    "failOnUnprocessableDocument": False
                }
            }
        }
        
        url = f"{self.search_endpoint}/indexers/{self.indexer_name}?api-version=2024-07-01"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token_provider()}"
        }
        response = requests.put(url, headers=headers, data=json.dumps(indexer_dict))
        
        if response.status_code not in [200, 201, 204]:
            raise Exception(f"Indexer failed: {response.text}")
        log_query_to_blob("✓ Indexer created")
        
        log_query_to_blob("\n✓✓✓ Setup complete!\n")
    
    async def run_indexer_async(self):
        """Run the indexer to process new documents in the container (async)"""
        log_query_to_blob("\n=== RUNNING INDEXER ===\n")
        try:
            await asyncio.to_thread(self.indexer_client.run_indexer, self.indexer_name)
            log_query_to_blob("✓ Indexer started - processing documents...")
        except Exception as e:
            if "is already running" in str(e):
                log_query_to_blob("⚠ Indexer is already running. Please wait for it to complete.")
            else:
                raise
    
    async def get_status_async(self):
        """Check indexer status with detailed warnings and errors (async)"""
        status = await asyncio.to_thread(self.indexer_client.get_indexer_status, self.indexer_name)
        
        log_query_to_blob(f"\n📊 Overall Status: {status.status}")
        
        failed_docs = []
        
        if status.last_result:
            log_query_to_blob(f"\n🔄 Last Execution:")
            log_query_to_blob(f"   Status: {status.last_result.status}")
            
            items_processed = getattr(status.last_result, 'items_processed', 
                                    getattr(status.last_result, 'itemsProcessed', 'N/A'))
            items_failed = getattr(status.last_result, 'items_failed', 
                                getattr(status.last_result, 'itemsFailed', 'N/A'))
            log_query_to_blob(f"   Items Processed: {items_processed}")
            log_query_to_blob(f"   Items Failed: {items_failed}")
            
            if hasattr(status.last_result, 'start_time'):
                log_query_to_blob(f"   Start Time: {status.last_result.start_time}")
            if hasattr(status.last_result, 'end_time'):
                log_query_to_blob(f"   End Time: {status.last_result.end_time}")
            
            # ERRORS
            errors = getattr(status.last_result, 'errors', [])
            if errors:
                log_query_to_blob(f"\n❌ ERRORS ({len(errors)}):")
                print(f"\n❌ ERRORS ({len(errors)}):")
                for i, error in enumerate(errors, 1):
                    log_query_to_blob(f"\n   Error {i}:")
                    error_msg = getattr(error, 'error_message', 
                                    getattr(error, 'message', 'Unknown error'))
                    log_query_to_blob(f"      Message: {error_msg}")
                    doc_key = getattr(error, 'key', 
                                    getattr(error, 'document_key', 
                                            getattr(error, 'name', 'Unknown')))
                    log_query_to_blob(f"      Document: {doc_key}")
            
            # WARNINGS
            warnings = getattr(status.last_result, 'warnings', [])
            if warnings:
                log_query_to_blob(f"\n⚠️  WARNINGS ({len(warnings)}):")
                print(f"\n⚠️  WARNINGS ({len(warnings)}):")
                for i, warning in enumerate(warnings, 1):
                    log_query_to_blob(f"\n   Warning {i}:")
                    print(f"\n   Warning {i}:")
                    warning_msg = getattr(warning, 'message', 
                                        getattr(warning, 'warning_message', 'Unknown warning'))
                    log_query_to_blob(f"      Message: {warning_msg}")
                    print(f"      Message: {warning_msg}")
                    doc_key = getattr(warning, 'key', 
                                    getattr(warning, 'document_key', 
                                            getattr(warning, 'name', 'Unknown')))
                    log_query_to_blob(f"      Document: {doc_key}")
                    
                    # Check if this is a text extraction failure
                    if "Could not execute skill because one or more skill input was invalid" in warning_msg:
                        failed_docs.append(doc_key)
            
            if not errors and not warnings:
                log_query_to_blob(f"\n✅ No errors or warnings!")
        
        return status, failed_docs
    

    async def manually_index_empty_documents(self, failed_doc_urls):
        """Manually index documents that failed text extraction with empty placeholders"""
        if not failed_doc_urls:
            log_query_to_blob("\n✅ No documents need manual indexing.")
            return
        
        unique_doc_urls = list(set(failed_doc_urls))
        
        log_query_to_blob(f"📝 MANUALLY INDEXING {len(unique_doc_urls)} EMPTY DOCUMENTS")
        
        zero_vector = [0.0] * 3072
        documents_to_upload = []
        
        for doc_url in unique_doc_urls:
            try:
                if "documentKey=" in doc_url:
                    encoded_url = doc_url.split("documentKey=")[1]
                elif "localId=" in doc_url:
                    encoded_url = doc_url.split("localId=")[1].split("&")[0]
                else:
                    encoded_url = doc_url
                
                decoded_url = unquote(unquote(encoded_url))
                filename = decoded_url.split("/")[-1]
                
                parent_id = base64.b64encode(decoded_url.encode('utf-8')).decode('utf-8').rstrip('=')
                hash_obj = hashlib.md5(decoded_url.encode('utf-8'))
                doc_hash = hash_obj.hexdigest()[:12]
                doc_id = f"{doc_hash}_{parent_id}_pages_0"
                
                # REMOVED chunk_index field
                doc = {
                    "id": doc_id,
                    "parent_id": parent_id,
                    "chunk": "",
                    # REMOVED: "chunk_index": None,
                    "metadata_storage_name": filename,
                    "metadata_storage_path": decoded_url,
                    "text_vector": zero_vector
                }
                
                documents_to_upload.append(doc)
                self.manually_indexed_docs.append(filename)
                log_query_to_blob(f"   ✓ Prepared: {filename}")
                
            except Exception as e:
                log_query_to_blob(f"Error preparing document {doc_url}: {e}")
                log_query_to_blob(f"   ✗ Failed to prepare: {doc_url}: {e}")
        
        if documents_to_upload:
            try:
                result = await asyncio.to_thread(
                    self.search_client.upload_documents,
                    documents=documents_to_upload
                )
                log_query_to_blob(f"\n✅ Successfully indexed {len(documents_to_upload)} empty documents")
                log_query_to_blob(f"Manually indexed {len(documents_to_upload)} empty documents")
            except Exception as e:
                log_query_to_blob(f"Error uploading empty documents: {e}")
                log_query_to_blob(f"\n❌ Error uploading documents: {e}")
        
        log_query_to_blob(f"{'='*60}\n")
    
    async def find_missing_documents(self):
        """Compare blob storage files with indexed documents to find missing ones.
        Includes retry logic for search operations.
        """
        log_query_to_blob("\n=== COMPARING BLOB STORAGE WITH SEARCH INDEX ===\n")

        account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
        credential = DefaultAzureCredential()
        blob_service = BlobServiceClient(account_url=account_url, credential=credential)
        container_client = blob_service.get_container_client(self.container_name)

        blob_files = {}
        log_query_to_blob("📦 Fetching blob files...")

        try:
            for blob in container_client.list_blobs():
                blob_url = f"https://{blob_service.account_name}.blob.core.windows.net/{self.container_name}/{blob.name}"
                blob_files[blob_url] = blob.name
        except Exception as e:
            log_query_to_blob(f"❌ Error listing blobs: {str(e)}")
            return []

        log_query_to_blob(f"   Found {len(blob_files)} files in blob storage")
        print(f"   Found {len(blob_files)} files in blob storage")

        if not blob_files:
            log_query_to_blob("   No files in blob storage to check")
            return []

        indexed_paths = set()
        missing_blobs = []
        search_errors = 0

        log_query_to_blob("🔍 Checking each blob in search index...")

        for blob_path, blob_name in blob_files.items():
            max_search_retries = 3
            found = False

            for attempt in range(max_search_retries):
                try:
                    # Try raw path first (spaces unencoded), then encoded
                    for path_variant in [blob_path, quote(blob_path, safe=":/()"), quote(blob_path, safe="")]:
                        results = self.search_client.search(
                            search_text="",
                            filter=f"metadata_storage_path eq '{path_variant}'",
                            select=["metadata_storage_path"],
                            top=1
                        )
                        for result in results:
                            found = True
                            indexed_paths.add(blob_path)
                            break
                        if found:
                            break

                    break  # Search succeeded, exit retry loop

                except Exception as e:
                    search_errors += 1
                    if attempt < max_search_retries - 1:
                        delay = 1 * (attempt + 1)  # 1s, 2s, 3s
                        log_query_to_blob(f"   ⚠ Search error for {blob_name} (attempt {attempt + 1}): {str(e)[:50]}. Retrying...")
                        await asyncio.sleep(delay)
                    else:
                        log_query_to_blob(f"   ⚠ Search failed for {blob_name} after {max_search_retries} attempts: {str(e)[:50]}")
                        # Mark as missing if we can't verify
                        missing_blobs.append({
                            'name': blob_name,
                            'path': blob_path,
                            'error': 'search_failed'
                        })
                        self.missing_docs_found.append(blob_name)
                        found = True  # Set to True to skip adding below

            if not found:
                missing_blobs.append({
                    'name': blob_name,
                    'path': blob_path
                })
                self.missing_docs_found.append(blob_name)

        log_query_to_blob(f"   Found {len(indexed_paths)} files indexed")
        if search_errors > 0:
            log_query_to_blob(f"   ⚠️ Encountered {search_errors} search errors during verification")

        if missing_blobs:
            log_query_to_blob(f"\n⚠️  Found {len(missing_blobs)} files NOT indexed:")
            print(f"\n⚠️  Found {len(missing_blobs)} files NOT indexed:")
            for blob_info in sorted(missing_blobs, key=lambda x: x['name'])[:20]:  # Show first 20
                log_query_to_blob(f"   - {blob_info['name']}")
            if len(missing_blobs) > 20:
                log_query_to_blob(f"   ... and {len(missing_blobs) - 20} more")
            return missing_blobs
        else:
            log_query_to_blob("\n✓ All blob files are indexed!")
            return []
    
    def check_setup_exists(self):
        """Check if setup already exists"""
        try:
            self.indexer_client.get_indexer(self.indexer_name)
            return True
        except:
            return False
    
    def log_query_to_blob_final_summary(self):
        """log_query_to_blob final summary of all indexing operations"""
        log_query_to_blob("\n" + "="*70)
        log_query_to_blob("📊 FINAL INDEXING SUMMARY")
        log_query_to_blob("="*70)
        
        if self.manually_indexed_docs:
            log_query_to_blob(f"\n✅ Manually Indexed Documents (Empty/No Text): {len(self.manually_indexed_docs)}")
            for doc in sorted(set(self.manually_indexed_docs)):
                log_query_to_blob(f"   - {doc}")
        
        if self.missing_docs_found:
            log_query_to_blob(f"\n🔍 Documents Found Missing from Index: {len(self.missing_docs_found)}")
            for doc in sorted(set(self.missing_docs_found)):
                log_query_to_blob(f"   - {doc}")
        
        if self.final_unindexed_docs:
            log_query_to_blob(f"\n❌ Documents Still Not Indexed After All Attempts: {len(self.final_unindexed_docs)}")
            print(f"\n❌ Documents Still Not Indexed After All Attempts: {len(self.final_unindexed_docs)}")
            for doc in sorted(set(self.final_unindexed_docs)):
                log_query_to_blob(f"   - {doc}")
                print(f"   - {doc}")
        else:
            log_query_to_blob("\n✅✅✅ All documents have been successfully indexed!")
            print("\n✅✅✅ All documents have been successfully indexed!")
        
        log_query_to_blob("\n" + "="*70 + "\n")
        
        # Log to blob
        log_query_to_blob(f"Final Summary - Manually indexed: {len(self.manually_indexed_docs)}, "
                         f"Missing found: {len(self.missing_docs_found)}, "
                         f"Still unindexed: {len(self.final_unindexed_docs)}")


async def index_blob_documents():
    """Main async function to index blob documents with robust retry logic"""
    log_query_to_blob("🔍 === Azure AI Search Document Indexer ===\n")

    indexer = AzureSearchIndexer()
    storage_resource_id = os.getenv("AZURE_STORAGE_RESOURCE_ID")
    if not storage_resource_id:
        log_query_to_blob("❌ AZURE_STORAGE_RESOURCE_ID not found in .env")
        raise ValueError("AZURE_STORAGE_RESOURCE_ID not found")

    # Azure Search expects a semicolon-terminated ResourceId connection string
    # when using managed identity for the data source.
    managed_identity_conn_str = f"ResourceId={storage_resource_id};"

    # STEP 1: Initial Setup (if needed) and First Indexing
    if indexer.check_setup_exists():
        log_query_to_blob("✓ Setup already exists. Refreshing datasource connection string...\n")
        print("✓ Setup already exists. Refreshing datasource connection string...\n")
        # Refresh the datasource with the managed identity connection string
        data_source = SearchIndexerDataSourceConnection(
            name=indexer.data_source_name,
            type="azureblob",
            connection_string=managed_identity_conn_str,
            container=SearchIndexerDataContainer(name=indexer.container_name)
        )
        await asyncio.to_thread(indexer.indexer_client.create_or_update_data_source_connection, data_source)
        log_query_to_blob("✓ Datasource connection string refreshed. Running indexer...\n")
        await indexer.run_indexer_async()
    else:
        log_query_to_blob("⚠ Setup not found. Running initial setup...\n")
        await asyncio.to_thread(indexer.setup_initial, managed_identity_conn_str)
        log_query_to_blob("\nRunning indexer for the first time...")
        print("\nRunning indexer for the first time...")
        await indexer.run_indexer_async()

    # Wait and check status
    log_query_to_blob("\n⏳ Waiting for indexer to process documents...")
    await asyncio.sleep(5)

    status, failed_docs = await indexer.get_status_async()
    all_failed_docs = list(failed_docs)

    # STEP 2: Retry indexing if not successful (with exponential backoff)
    retry_count = 0
    running_wait_count = 0
    max_running_waits = 12  # Max 12 waits for "running" state (~2 min total) to prevent infinite loop
    max_initial_retries = MAX_INDEXER_RETRIES

    while retry_count < max_initial_retries:
        # Check if indexer completed successfully
        if status.last_result and status.last_result.status == "success":
            log_query_to_blob(f"✓ Indexer completed successfully on attempt {retry_count + 1}")
            break

        # Check if indexer is still running (with a max wait limit to prevent infinite loop)
        if status.status == "running":
            running_wait_count += 1
            if running_wait_count > max_running_waits:
                log_query_to_blob(f"⚠️ Indexer stuck in 'running' state for {running_wait_count} checks - moving on")
                break

            wait_time = min(10 * (running_wait_count), 60)
            log_query_to_blob(f"⏳ Indexer still running, waiting {wait_time}s... (check {running_wait_count}/{max_running_waits})")
            await asyncio.sleep(wait_time)
            status, additional_failed = await indexer.get_status_async()
            all_failed_docs.extend(additional_failed)
            continue

        running_wait_count = 0  # Reset when indexer is no longer running

        # Retry if failed or transient error
        retry_count += 1
        if retry_count < max_initial_retries:
            delay = min(INITIAL_RETRY_DELAY * (2 ** (retry_count - 1)) + random.uniform(0, 2), MAX_RETRY_DELAY)
            log_query_to_blob(f"\n🔄 Retry attempt {retry_count}/{max_initial_retries}, waiting {delay:.1f}s...")
            await asyncio.sleep(delay)

            try:
                await indexer.run_indexer_async()
                await asyncio.sleep(5)
                status, additional_failed = await indexer.get_status_async()
                all_failed_docs.extend(additional_failed)
            except Exception as e:
                log_query_to_blob(f"⚠️ Retry {retry_count} failed: {str(e)[:100]}")
        else:
            log_query_to_blob(f"⚠️ Indexer did not complete successfully after {max_initial_retries} retries")

    # STEP 3: Handle documents with warnings (no text extraction)
    unique_failed_docs = list(set(all_failed_docs))
    if unique_failed_docs:
        log_query_to_blob(f"\n🔧 Found {len(unique_failed_docs)} documents with text extraction failures")
        await indexer.manually_index_empty_documents(unique_failed_docs)
        await asyncio.sleep(2)

    # STEP 4: Compare blob storage with index to find missing documents
    log_query_to_blob("\n" + "=" * 70)

    missing_docs = await indexer.find_missing_documents()

    # STEP 5: Retry for missing documents with exponential backoff
    missing_doc_retry = 0

    while missing_docs and missing_doc_retry < MAX_MISSING_DOC_RETRIES:
        missing_doc_retry += 1
        delay = min(INITIAL_RETRY_DELAY * (2 ** (missing_doc_retry - 1)) + random.uniform(0, 2), MAX_RETRY_DELAY)

        log_query_to_blob(f"\n🔄 Re-running indexer for {len(missing_docs)} missing documents (attempt {missing_doc_retry}/{MAX_MISSING_DOC_RETRIES})...")
        log_query_to_blob(f"⏳ Waiting {delay:.1f}s before retry...")
        await asyncio.sleep(delay)

        try:
            await indexer.run_indexer_async()
            # Wait for indexer to complete with status checks
            for check in range(6):  # Check up to 6 times (30s total)
                await asyncio.sleep(5)
                status, _ = await indexer.get_status_async()
                if status.last_result and status.last_result.status != "inProgress":
                    break

            # Check for still-missing documents
            missing_docs = await indexer.find_missing_documents()

            if not missing_docs:
                log_query_to_blob("\n✅ All documents indexed successfully!")
                break
            else:
                log_query_to_blob(f"⚠️ Still {len(missing_docs)} documents missing after retry {missing_doc_retry}")

        except Exception as e:
            log_query_to_blob(f"⚠️ Retry {missing_doc_retry} failed: {str(e)[:100]}")

    # STEP 6: Try manual indexing for any remaining missing documents
    if missing_docs:
        log_query_to_blob(f"\n🔧 Attempting manual indexing for {len(missing_docs)} remaining documents...")
        print(f"\n🔧 Attempting manual indexing for {len(missing_docs)} remaining documents...")
        missing_urls = [doc['path'] for doc in missing_docs if 'path' in doc]
        if missing_urls:
            await indexer.manually_index_empty_documents(missing_urls)
            await asyncio.sleep(2)

            # Final check
            final_missing = await indexer.find_missing_documents()
            if final_missing:
                indexer.final_unindexed_docs = [doc['name'] for doc in final_missing]
                log_query_to_blob(f"\n⚠️ {len(final_missing)} documents still not indexed after all attempts")
            else:
                log_query_to_blob("\n✅ All documents indexed after manual indexing!")
    else:
        log_query_to_blob("\n✅ All documents indexed successfully!")

    # STEP 7: Log final summary
    indexer.log_query_to_blob_final_summary()

    log_query_to_blob("💡 Check Azure Portal for additional details if needed.")
    log_query_to_blob("=" * 70 + "\n")

    # Return summary for tracking
    return {
        "manually_indexed": len(indexer.manually_indexed_docs),
        "missing_found": len(indexer.missing_docs_found),
        "final_unindexed": len(indexer.final_unindexed_docs)
    }


# For standalone testing
if __name__ == "__main__":
    import sys
    
    asyncio.run(index_blob_documents())
