import azure.functions as func
import logging
import json
import os

# Create the function app
app = func.FunctionApp()

# Try to import dependencies
try:
    from dotenv import load_dotenv
    from urllib.parse import quote_plus
    from sqlalchemy import create_engine, text, bindparam
    from datetime import datetime, timezone
    from data_formatting import consolidate_resource_json
    from store_in_blob import index_blob_documents_1
    from main_index import json_indexer
    from logs_to_blob import log_query_to_blob, flush_logs
    import asyncio
    import time

    load_dotenv()


    IMPORTS_SUCCESSFUL = True
    IMPORT_ERROR = None
except Exception as e:
    logging.error(f"Failed to import dependencies: {str(e)}")
    IMPORTS_SUCCESSFUL = False
    IMPORT_ERROR = str(e)

    # Define fallback functions when imports fail
    def log_query_to_blob(msg):
        logging.info(msg)
    def flush_logs():
        pass

# Database connection setup - reuse engine across batches to avoid connection pool churn
_db_engine = None


def _get_required_setting(name):
    value = os.getenv(name)
    if value:
        return value
    raise ValueError(f"Missing required setting: {name}")

def get_db_engine():
    """Create and return database engine (reused across calls)"""
    global _db_engine
    if _db_engine is not None:
        return _db_engine

    server = _get_required_setting("AZURE_SQL_SERVER")
    database = _get_required_setting("AZURE_SQL_DATABASE")
    username = _get_required_setting("AZURE_SQL_USERNAME")
    password = _get_required_setting("AZURE_SQL_PASSWORD")
    driver = "ODBC Driver 18 for SQL Server"

    password_quoted = quote_plus(password)
    connection_string = (
        f"mssql+pyodbc://{username}:{password_quoted}"
        f"@{server}.database.windows.net:1433/{database}"
        f"?driver={quote_plus(driver)}"
    )

    _db_engine = create_engine(connection_string, pool_pre_ping=True, pool_recycle=300)
    return _db_engine


BATCH_SIZE = 100  # Process 100 resources per Azure Function invocation
MAX_FUNCTION_RUNTIME_SECONDS = 540  # 9 minutes - leave buffer before 10 min timeout
PARALLEL_WORKERS = 10  # Process 10 resources in parallel
MAX_RESOURCE_RETRIES = 2  # Maximum retries for failed resources

def convert_rows_to_list(table_name, rows):
    """Convert database rows to list of dictionaries"""
    output = []

    if table_name == "ResourceBenchmarks":
        if rows:
            output.append({"ResourceID": rows[0].ResourceID})
        for r in rows:
            bm_entry = {
                "BenchmarkID": r.BenchmarkID,
                "Code": r.Code
            }
            output.append(bm_entry)
        return output

    for r in rows:
        entry = {}
        for key, value in dict(r._mapping).items():
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            entry[key] = value
        output.append(entry)

    return output


def get_total_pending_count(engine):
    """Get count of resources needing indexing - uses fresh connection"""
    with engine.connect() as conn:
        count_query = text("""
            SELECT COUNT(*) as total
            FROM ResourceCore
            WHERE isDeleted=0 and (LastIndexed IS NULL
               OR LastIndexed < LastUpdated)
        """)
        result = conn.execute(count_query).fetchone()
        return result.total if result else 0


def get_batch_of_resources(engine, batch_size, offset=0):
    """Fetch a batch of resources using pagination - uses fresh connection"""
    with engine.connect() as conn:
        # Use OFFSET/FETCH for pagination - much more memory efficient
        query = text("""
            SELECT *
            FROM ResourceCore
            WHERE isDeleted=0 and (LastIndexed IS NULL
               OR LastIndexed < LastUpdated)
            ORDER BY ResourceID
            OFFSET :offset ROWS
            FETCH NEXT :batch_size ROWS ONLY
        """)
        result = conn.execute(query, {"offset": offset, "batch_size": batch_size}).fetchall()
        return result


async def process_single_resource_with_new_connection(engine, resource_id):
    """Process a single resource with a fresh database connection"""
    try:
        with engine.connect() as conn:
            # Fetch the resource
            q0 = text("SELECT * FROM ResourceCore WHERE ResourceID = :rid")
            row = conn.execute(q0, {"rid": resource_id}).fetchone()

            if not row:
                raise Exception(f"Resource {resource_id} not found in ResourceCore")

            # Fetch related data
            q1 = text("SELECT * FROM LessonPlanTemplate WHERE ResourceID = :rid")
            lesson_rows = conn.execute(q1, {"rid": resource_id}).fetchall()

            q2 = text("SELECT * FROM ResourceBenchmarks WHERE ResourceID = :rid")
            benchmark_rows = conn.execute(q2, {"rid": resource_id}).fetchall()

            q3 = text("SELECT * FROM ResourceFiles WHERE ResourceID = :rid")
            file_rows = conn.execute(q3, {"rid": resource_id}).fetchall()

            benchmark_list = convert_rows_to_list("ResourceBenchmarks", benchmark_rows)
            benchmark_ids = [item['BenchmarkID'] for item in benchmark_list if 'BenchmarkID' in item]
            benchmark_codes = [item['Code'] for item in benchmark_list if 'Code' in item]

            if benchmark_ids and benchmark_codes:
                q = text("""
                SELECT BenchmarkID, BenchmarkCode, Descriptor
                FROM Benchmarks
                WHERE BenchmarkID IN :benchmark_ids
                OR BenchmarkCode IN :benchmark_codes
                """).bindparams(
                    bindparam("benchmark_ids", expanding=True),
                    bindparam("benchmark_codes", expanding=True)
                )

                rows = conn.execute(
                    q,
                    {
                        "benchmark_ids": tuple(benchmark_ids),
                        "benchmark_codes": tuple(benchmark_codes)
                    }
                ).fetchall()
                benchmark_des = convert_rows_to_list("Benchmarks", rows)
            else:
                benchmark_des = []

            lesson_list = convert_rows_to_list("LessonPlanTemplate", lesson_rows)
            files_list = convert_rows_to_list("ResourceFiles", file_rows)
            resource_core_list = convert_rows_to_list("ResourceCore", [row])

            final_json = consolidate_resource_json(
                resource_core_list,
                lesson_list,
                benchmark_list,
                files_list,
                benchmark_des
            )

            file_paths = [f.get("FinalPath") for f in files_list if f.get("FinalPath")]
            filtered_files = file_paths

    except Exception as db_error:
        raise Exception(f"DB fetch failed for ResourceID {resource_id}: {str(db_error)}") from db_error

    # Run parallel indexing OUTSIDE the connection context
    log_query_to_blob(f"Starting JSON + Blob indexing for ResourceID {resource_id}...")

    json_task = asyncio.create_task(json_indexer(final_json[0]))
    blob_task = asyncio.create_task(index_blob_documents_1(resource_id, filtered_files))

    results = await asyncio.gather(json_task, blob_task, return_exceptions=True)

    task_names = ["JSON Index", "Blob Index"]
    success_count = 0
    error_details = []

    for idx, result in enumerate(results):
        if isinstance(result, Exception):
            error_details.append(f"{task_names[idx]}: {str(result)}")
            log_query_to_blob(f"{task_names[idx]} failed for ResourceID {resource_id}: {str(result)}")
        else:
            success_count += 1

    if success_count == 0:
        raise Exception(f"Both indexing tasks failed for ResourceID {resource_id} — {' | '.join(error_details)}")

    if success_count < 2:
        # Only one task succeeded — don't update LastIndexed so this resource
        # is retried in the next batch to complete the failed part.
        log_query_to_blob(f"⚠️ Partial success for ResourceID {resource_id} ({success_count}/2) — "
                          f"NOT updating LastIndexed so it will be retried. Failures: {' | '.join(error_details)}")
        return False

    # Update LastIndexed ONLY when both tasks succeeded
    try:
        with engine.connect() as conn:
            timestamp = datetime.now(timezone.utc)
            update_query = text("""
                UPDATE ResourceCore
                SET LastIndexed = :timestamp
                WHERE ResourceID = :rid
            """)
            conn.execute(update_query, {"timestamp": timestamp, "rid": resource_id})
            conn.commit()
    except Exception as update_error:
        log_query_to_blob(f"⚠️ LastIndexed update failed for ResourceID {resource_id}: {str(update_error)}")
        # Don't re-raise - the indexing succeeded, just the timestamp update failed

    return True


async def process_resources(limit=None):
    """
    Process and index resources from database with SAFE batch processing.

    Key improvements for Azure Functions:
    - Uses pagination (OFFSET/FETCH) instead of loading all rows
    - Creates fresh DB connections for each resource
    - Tracks time to avoid timeout
    - Returns progress for resume capability

    Args:
        limit: Max resources to process in this invocation (default: BATCH_SIZE)

    Returns:
        Dict with status, counts, and has_more flag
    """
    log_query_to_blob("="*80)
    log_query_to_blob("STARTING RESOURCE INDEXING (AZURE FUNCTION SAFE MODE)")
    log_query_to_blob(f"Batch size: {limit or BATCH_SIZE}")
    log_query_to_blob("="*80)

    if not IMPORTS_SUCCESSFUL:
        log_query_to_blob(f"Cannot start indexing - imports failed: {IMPORT_ERROR}")
        return {"status": "error", "message": f"Import failed: {IMPORT_ERROR}", "count": 0}

    engine = get_db_engine()

    # Get total count of pending resources
    total_pending = get_total_pending_count(engine)
    log_query_to_blob(f"✓ Total resources needing indexing: {total_pending}")

    if total_pending == 0:
        log_query_to_blob("="*80)
        log_query_to_blob("No resources need indexing - database is up to date")
        log_query_to_blob("="*80)
        return {
            "status": "complete",
            "message": "No data to index",
            "count": 0,
            "total_pending": 0,
            "has_more": False
        }

    # Determine batch size for this run
    batch_size = min(limit or BATCH_SIZE, BATCH_SIZE)

    # Fetch batch using pagination
    log_query_to_blob(f"Fetching batch of {batch_size} resources...")
    # Always offset 0 — already-indexed resources are excluded by the WHERE clause
    batch = get_batch_of_resources(engine, batch_size, offset=0)

    if not batch:
        log_query_to_blob("No resources in current batch")
        return {
            "status": "complete",
            "message": "No more resources to process",
            "count": 0,
            "total_pending": total_pending,
            "has_more": False
        }

    log_query_to_blob(f"✓ Fetched {len(batch)} resources for processing")
    log_query_to_blob(f"⚡ Using {PARALLEL_WORKERS} parallel workers for speed")
    log_query_to_blob("="*80)

    indexed_count = 0
    failed_count = 0
    failed_resource_ids = []
    retry_queue = []  # Queue for resources that need retry
    start_time = time.time()

    # Process in parallel chunks
    skipped_on_timeout = 0
    for chunk_start in range(0, len(batch), PARALLEL_WORKERS):
        # Check if we're approaching timeout
        elapsed = time.time() - start_time
        if elapsed > MAX_FUNCTION_RUNTIME_SECONDS:
            remaining_in_batch = len(batch) - chunk_start
            skipped_on_timeout = remaining_in_batch
            log_query_to_blob(f"⚠️ Approaching timeout ({elapsed:.0f}s), skipping {remaining_in_batch} unprocessed resources")
            break

        chunk_end = min(chunk_start + PARALLEL_WORKERS, len(batch))
        chunk = batch[chunk_start:chunk_end]
        resource_ids = [row.ResourceID for row in chunk]

        log_query_to_blob(f"\n⚡ Processing {len(chunk)} resources in parallel: {resource_ids}")

        # Create tasks for parallel execution
        tasks = [
            process_single_resource_with_new_connection(engine, row.ResourceID)
            for row in chunk
        ]

        # Run all tasks in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        for idx, result in enumerate(results):
            resource_id = resource_ids[idx]
            if isinstance(result, Exception):
                log_query_to_blob(f"❌ Failed ResourceID {resource_id}: {str(result)}")
                retry_queue.append({"resource_id": resource_id, "attempt": 1})
            else:
                indexed_count += 1
                status = "✅" if result else "⚠️"
                log_query_to_blob(f"{status} ResourceID {resource_id}")

        log_query_to_blob(f"Chunk complete: {indexed_count} indexed, {len(retry_queue)} pending retry")

    # RETRY PHASE: Process failed resources with exponential backoff
    if retry_queue:
        log_query_to_blob(f"\n🔄 === RETRY PHASE: {len(retry_queue)} resources to retry ===")

        for retry_attempt in range(MAX_RESOURCE_RETRIES):
            if not retry_queue:
                break

            # Check timeout before retry — count remaining as failed
            elapsed = time.time() - start_time
            if elapsed > MAX_FUNCTION_RUNTIME_SECONDS:
                log_query_to_blob(f"⚠️ Timeout reached during retry phase, marking {len(retry_queue)} as failed")
                for item in retry_queue:
                    failed_count += 1
                    failed_resource_ids.append(item["resource_id"])
                retry_queue = []
                break

            # Wait before retry with exponential backoff
            wait_time = 5 * (retry_attempt + 1)  # 5s, 10s
            log_query_to_blob(f"\n⏳ Waiting {wait_time}s before retry attempt {retry_attempt + 1}/{MAX_RESOURCE_RETRIES}...")
            await asyncio.sleep(wait_time)

            current_retry_batch = retry_queue.copy()
            retry_queue = []

            log_query_to_blob(f"🔄 Retrying {len(current_retry_batch)} resources...")

            # Process retries in smaller chunks (half the normal parallel workers)
            retry_chunk_size = max(PARALLEL_WORKERS // 2, 2)

            for chunk_start in range(0, len(current_retry_batch), retry_chunk_size):
                chunk_end = min(chunk_start + retry_chunk_size, len(current_retry_batch))
                chunk = current_retry_batch[chunk_start:chunk_end]

                tasks = [
                    process_single_resource_with_new_connection(engine, item["resource_id"])
                    for item in chunk
                ]

                results = await asyncio.gather(*tasks, return_exceptions=True)

                for idx, result in enumerate(results):
                    resource_id = chunk[idx]["resource_id"]
                    attempt = chunk[idx]["attempt"]

                    if isinstance(result, Exception):
                        if attempt < MAX_RESOURCE_RETRIES:
                            log_query_to_blob(f"⚠️ Retry {attempt} failed for ResourceID {resource_id}: {str(result)[:80]}")
                            retry_queue.append({"resource_id": resource_id, "attempt": attempt + 1})
                        else:
                            log_query_to_blob(f"❌ Final failure for ResourceID {resource_id} after {attempt + 1} attempts")
                            failed_count += 1
                            failed_resource_ids.append(resource_id)
                    else:
                        indexed_count += 1
                        log_query_to_blob(f"✅ ResourceID {resource_id} succeeded on retry {attempt}")

        # Any remaining in retry queue are final failures
        for item in retry_queue:
            failed_count += 1
            failed_resource_ids.append(item["resource_id"])
            log_query_to_blob(f"❌ ResourceID {item['resource_id']} failed after all retries")

    if failed_resource_ids:
        log_query_to_blob(f"\n⚠️ Total failed resource IDs: {failed_resource_ids[:20]}")
        if len(failed_resource_ids) > 20:
            log_query_to_blob(f"   ... and {len(failed_resource_ids) - 20} more")

    # Get updated count of remaining resources
    remaining_count = get_total_pending_count(engine)

    # Final summary
    elapsed_time = time.time() - start_time
    log_query_to_blob("\n" + "="*80)
    log_query_to_blob("BATCH COMPLETED")
    log_query_to_blob("="*80)
    log_query_to_blob(f"Processed in this batch: {indexed_count + failed_count}")
    log_query_to_blob(f"Successfully indexed: {indexed_count}")
    log_query_to_blob(f"Failed: {failed_count}")
    if skipped_on_timeout > 0:
        log_query_to_blob(f"Skipped (timeout): {skipped_on_timeout}")
    log_query_to_blob(f"Time elapsed: {elapsed_time:.1f}s")
    log_query_to_blob(f"Remaining to process: {remaining_count}")
    if indexed_count + failed_count > 0:
        log_query_to_blob(f"Avg time per resource: {elapsed_time/(indexed_count + failed_count):.1f}s")
    log_query_to_blob("="*80)
    flush_logs()  # Flush buffered logs before returning

    has_more = remaining_count > 0

    return {
        "status": "success" if failed_count == 0 else "partial",
        "message": f"Indexed {indexed_count}, failed {failed_count}, remaining {remaining_count}",
        "count": indexed_count,
        "failed": failed_count,
        "failed_resource_ids": failed_resource_ids,
        "total_processed": indexed_count + failed_count,
        "remaining": remaining_count,
        "has_more": has_more,
        "elapsed_seconds": round(elapsed_time, 2),
        "next_action": "Call /api/indexer again to continue" if has_more else "All done!"
    }

# Health check endpoint
@app.route(route="health", auth_level=func.AuthLevel.ANONYMOUS)
def health_check(req: func.HttpRequest) -> func.HttpResponse:
    """Simple health check endpoint"""
    status = {
        "status": "healthy",
        "service": "CPALMS Indexer",
        "imports": "successful" if IMPORTS_SUCCESSFUL else f"failed: {IMPORT_ERROR}"
    }
    return func.HttpResponse(
        json.dumps(status),
        mimetype="application/json",
        status_code=200
    )


# Diagnostic endpoint to check database
@app.route(route="check-db", auth_level=func.AuthLevel.ANONYMOUS)
def check_database(req: func.HttpRequest) -> func.HttpResponse:
    """Check database for resources needing indexing"""
    try:
        if not IMPORTS_SUCCESSFUL:
            return func.HttpResponse(
                json.dumps({"error": f"Imports failed: {IMPORT_ERROR}"}),
                mimetype="application/json",
                status_code=500
            )

        engine = get_db_engine()

        with engine.connect() as conn:
            # Count total resources needing indexing
            count_query = text("""
                SELECT COUNT(*) as total
                FROM ResourceCore
                WHERE isDeleted=0 and (LastIndexed IS NULL OR LastIndexed < LastUpdated)
            """)
            total_result = conn.execute(count_query).fetchone()
            total_count = total_result.total if total_result else 0

            # Get top 5 resources needing indexing
            query = text("""
                SELECT TOP 5
                    ResourceID,
                    Title,
                    LastIndexed,
                    LastUpdated,
                    CASE
                        WHEN LastIndexed IS NULL THEN 'Never indexed'
                        WHEN LastIndexed < LastUpdated THEN 'Updated since last index'
                        ELSE 'Already indexed'
                    END AS Status
                FROM ResourceCore
                WHERE isDeleted=0 and (LastIndexed IS NULL OR LastIndexed < LastUpdated)
                ORDER BY ResourceID DESC
            """)

            result = conn.execute(query).fetchall()

            resources = []
            for row in result:
                resources.append({
                    "ResourceID": row.ResourceID,
                    "Title": row.Title[:100] if row.Title else None,
                    "LastIndexed": str(row.LastIndexed) if row.LastIndexed else None,
                    "LastUpdated": str(row.LastUpdated) if row.LastUpdated else None,
                    "Status": row.Status
                })

            # Get most recent 3 resources by ID
            recent_query = text("""
                SELECT TOP 3
                    ResourceID,
                    Title,
                    LastIndexed,
                    LastUpdated
                FROM ResourceCore
                ORDER BY ResourceID DESC
            """)

            recent_result = conn.execute(recent_query).fetchall()
            recent_resources = []
            for row in recent_result:
                recent_resources.append({
                    "ResourceID": row.ResourceID,
                    "Title": row.Title[:100] if row.Title else None,
                    "LastIndexed": str(row.LastIndexed) if row.LastIndexed else None,
                    "LastUpdated": str(row.LastUpdated) if row.LastUpdated else None
                })

            response = {
                "total_needing_indexing": total_count,
                "resources_needing_indexing": resources,
                "most_recent_resources": recent_resources,
                "database": os.getenv("AZURE_SQL_DATABASE"),
                "server": os.getenv("AZURE_SQL_SERVER")
            }

            return func.HttpResponse(
                json.dumps(response, indent=2),
                mimetype="application/json",
                status_code=200
            )

    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            mimetype="application/json",
            status_code=500
        )


# Check if resource exists in Azure Search
@app.route(route="check-search", auth_level=func.AuthLevel.ANONYMOUS)
async def check_search(req: func.HttpRequest) -> func.HttpResponse:
    """Check if a resource exists in Azure Search"""
    try:
        if not IMPORTS_SUCCESSFUL:
            return func.HttpResponse(
                json.dumps({"error": f"Imports failed: {IMPORT_ERROR}"}),
                mimetype="application/json",
                status_code=500
            )

        # Get ResourceID from query params
        resource_id = req.params.get('resource_id')
        if not resource_id:
            return func.HttpResponse(
                json.dumps({"error": "Please provide resource_id parameter"}),
                mimetype="application/json",
                status_code=400
            )

        from azure.search.documents.aio import SearchClient
        from azure.identity.aio import DefaultAzureCredential

        search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
        json_index = os.getenv("AZURE_SEARCH_INDEX_NAME_1")
        blob_index = os.getenv("AZURE_SEARCH_INDEX_NAME_2")

        credential = DefaultAzureCredential()
        results = {}

        # Check JSON index
        try:
            async with SearchClient(
                endpoint=search_endpoint,
                index_name=json_index,
                credential=credential
            ) as client:
                search_results = await client.search(
                    search_text=f"id:{resource_id}",
                    select=["id", "Title", "Description"],
                    top=1
                )

                json_docs = []
                async for doc in search_results:
                    json_docs.append({
                        "id": doc.get("id"),
                        "Title": doc.get("Title", "")[:100],
                        "Description": doc.get("Description", "")[:200]
                    })

                results["json_index"] = {
                    "index_name": json_index,
                    "found": len(json_docs) > 0,
                    "documents": json_docs
                }
        except Exception as e:
            results["json_index"] = {"error": str(e)}

        # Check Blob index
        try:
            async with SearchClient(
                endpoint=search_endpoint,
                index_name=blob_index,
                credential=credential
            ) as client:
                search_results = await client.search(
                    search_text=f"{resource_id}",
                    select=["id", "parent_id", "chunk", "metadata_storage_name"],
                    top=3
                )

                blob_docs = []
                async for doc in search_results:
                    blob_docs.append({
                        "id": doc.get("id"),
                        "parent_id": doc.get("parent_id"),
                        "metadata_storage_name": doc.get("metadata_storage_name"),
                        "chunk_preview": doc.get("chunk", "")[:100]
                    })

                results["blob_index"] = {
                    "index_name": blob_index,
                    "found": len(blob_docs) > 0,
                    "document_count": len(blob_docs),
                    "documents": blob_docs
                }
        except Exception as e:
            results["blob_index"] = {"error": str(e)}

        return func.HttpResponse(
            json.dumps(results, indent=2),
            mimetype="application/json",
            status_code=200
        )

    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            mimetype="application/json",
            status_code=500
        )


# DAILY TIMER - Runs at 06:30 AM UTC every day
# Processes ALL pending resources until database is fully indexed
@app.timer_trigger(
    schedule="0 30 6 * * *",  # 06:30 AM UTC daily
    arg_name="myTimer",
    run_on_startup=False,  # Only run on schedule, not on deployment
    use_monitor=False
)
async def indexer_daily_timer(myTimer: func.TimerRequest) -> None:
    """
    Daily timer trigger that runs at 06:30 AM UTC.
    Processes ALL pending resources in batches until everything is indexed.

    This will keep running batches until:
    - All resources where LastIndexed IS NULL are indexed
    - All resources where LastIndexed < LastUpdated are re-indexed
    """
    if not IMPORTS_SUCCESSFUL:
        log_query_to_blob(f"⏰ Daily timer cannot run - imports failed: {IMPORT_ERROR}")
        return

    try:
        engine = get_db_engine()
        initial_pending = get_total_pending_count(engine)

        if initial_pending == 0:
            log_query_to_blob("⏰ Daily Timer (05:01 AM UTC): No resources pending - nothing to do")
            return

        log_query_to_blob("="*80)
        log_query_to_blob(f"⏰ DAILY INDEXING STARTED - 05:01 AM UTC")
        log_query_to_blob(f"⏰ Total resources to index: {initial_pending}")
        log_query_to_blob("="*80)

        total_indexed = 0
        total_failed = 0
        batch_number = 0
        no_progress_count = 0
        previous_remaining = initial_pending
        start_time = time.time()

        # Keep processing batches until all resources are indexed
        while True:
            batch_number += 1

            # Check remaining resources
            remaining = get_total_pending_count(engine)

            if remaining == 0:
                log_query_to_blob(f"✅ All resources indexed! No more pending.")
                break

            log_query_to_blob(f"\n⏰ Batch {batch_number}: {remaining} resources remaining...")

            # Process one batch
            try:
                result = await process_resources()
            except Exception as batch_error:
                log_query_to_blob(f"❌ BATCH {batch_number} CRASHED: {str(batch_error)}")
                no_progress_count += 1
                if no_progress_count >= 3:
                    log_query_to_blob("❌ 3 consecutive batch crashes - stopping")
                    break
                continue

            batch_indexed = result.get('count', 0)
            batch_failed = result.get('failed', 0)

            total_indexed += batch_indexed
            total_failed += batch_failed

            log_query_to_blob(f"⏰ Batch {batch_number} done: +{batch_indexed} indexed, +{batch_failed} failed")

            # Safety check - if remaining count didn't decrease, we're stuck on
            # permanently failing resources. Count consecutive stalls and stop after 3.
            new_remaining = get_total_pending_count(engine)
            if new_remaining >= previous_remaining:
                no_progress_count += 1
                log_query_to_blob(f"⚠️ No reduction in pending count (still {new_remaining}) — stall {no_progress_count}/3")
                if no_progress_count >= 3:
                    log_query_to_blob(f"⚠️ Pending count stuck at {new_remaining} for 3 consecutive batches — "
                                      f"remaining resources are likely permanently failing. Stopping.")
                    break
            else:
                no_progress_count = 0

            previous_remaining = new_remaining

            # Check if we've been running too long (8 hours max)
            elapsed_hours = (time.time() - start_time) / 3600
            if elapsed_hours > 8:
                log_query_to_blob(f"⚠️ Running for {elapsed_hours:.1f} hours - stopping for safety")
                break

        # Final summary
        elapsed_time = time.time() - start_time
        final_remaining = get_total_pending_count(engine)

        log_query_to_blob("\n" + "="*80)
        log_query_to_blob("⏰ DAILY INDEXING COMPLETED")
        log_query_to_blob("="*80)
        log_query_to_blob(f"Started with: {initial_pending} pending")
        log_query_to_blob(f"Total indexed: {total_indexed}")
        log_query_to_blob(f"Total failed: {total_failed}")
        log_query_to_blob(f"Still remaining: {final_remaining}")
        log_query_to_blob(f"Total batches: {batch_number}")
        log_query_to_blob(f"Total time: {elapsed_time/60:.1f} minutes ({elapsed_time/3600:.1f} hours)")
        log_query_to_blob("="*80)
        flush_logs()  # Flush buffered logs at end of daily timer

    except Exception as e:
        import traceback
        log_query_to_blob(f"❌ DAILY TIMER CRASHED: {str(e)}")
        log_query_to_blob(f"Full traceback:\n{traceback.format_exc()}")
        flush_logs()





# HTTP trigger - Progress check endpoint
@app.route(route="indexer/status", auth_level=func.AuthLevel.FUNCTION)
def indexer_status(req: func.HttpRequest) -> func.HttpResponse:
    """
    Check indexing progress - how many resources still need indexing.

    Returns:
    - total_pending: Number of resources still needing indexing
    - estimated_batches: How many more /api/indexer calls needed
    """
    if not IMPORTS_SUCCESSFUL:
        return func.HttpResponse(
            json.dumps({"error": f"Imports failed: {IMPORT_ERROR}"}),
            mimetype="application/json",
            status_code=500
        )

    try:
        engine = get_db_engine()
        total_pending = get_total_pending_count(engine)

        estimated_batches = (total_pending + BATCH_SIZE - 1) // BATCH_SIZE if total_pending > 0 else 0

        return func.HttpResponse(
            json.dumps({
                "total_pending": total_pending,
                "batch_size": BATCH_SIZE,
                "estimated_batches_remaining": estimated_batches,
                "status": "complete" if total_pending == 0 else "pending",
                "message": f"{total_pending} resources need indexing" if total_pending > 0 else "All resources indexed!"
            }, indent=2),
            mimetype="application/json",
            status_code=200
        )
    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            mimetype="application/json",
            status_code=500
        )


# HTTP trigger - Main indexer
@app.route(route="indexer", auth_level=func.AuthLevel.FUNCTION)
async def indexer_http(req: func.HttpRequest) -> func.HttpResponse:
    """
    HTTP trigger for SAFE batch indexing (Azure Function optimized).

    IMPORTANT: This endpoint processes resources in small batches to avoid
    Azure Function timeout. Call it repeatedly until has_more=false.

    How to use:
    1. Call /api/indexer - processes up to 50 resources
    2. Check response: if has_more=true, call again
    3. Repeat until has_more=false or remaining=0

    For automation, use a loop or Azure Logic App to call repeatedly.

    Parameters:
    - limit (optional): Override batch size (max 50 recommended for safety)

    Example:
    - Process batch: /api/indexer
    - Smaller batch: /api/indexer?limit=10
    - Check status: /api/indexer/status
    """
    if not IMPORTS_SUCCESSFUL:
        return func.HttpResponse(
            json.dumps({"error": f"Imports failed: {IMPORT_ERROR}"}),
            mimetype="application/json",
            status_code=500
        )

    log_query_to_blob("HTTP trigger started - Batch indexing request")

    limit = req.params.get('limit')

    if limit:
        try:
            limit = int(limit)
            # Cap at BATCH_SIZE for safety
            if limit > BATCH_SIZE:
                log_query_to_blob(f"Limit {limit} exceeds safe batch size, using {BATCH_SIZE}")
                limit = BATCH_SIZE
            log_query_to_blob(f"Limit parameter set to: {limit}")
        except ValueError:
            return func.HttpResponse(
                json.dumps({"error": "Invalid limit parameter - must be an integer"}),
                mimetype="application/json",
                status_code=400
            )

    result = await process_resources(limit=limit)

    # Always return 200 for partial success so automation can continue
    status_code = 200 if result["status"] in ["success", "partial", "complete"] else 500

    return func.HttpResponse(
        json.dumps(result, indent=2),
        mimetype="application/json",
        status_code=status_code
    )
