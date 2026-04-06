# CPALMS Azure Search Indexer

> Automated dual-index pipeline for CPALMS educational resources using Azure Cognitive Search and Azure Functions.

---

## Overview

This Azure Function automatically indexes educational resources from a SQL database into **Azure Cognitive Search**. It performs dual indexing for each resource:

| Index | Target | Content |
|-------|--------|---------|
| **JSON Index** | `json-final` | Metadata — titles, descriptions, benchmarks, lesson plans |
| **Blob Index** | `blob-attachments-final` | File attachments — PDFs and documents |

**Schedule:** Runs automatically every day at **4:08 PM UTC** (12:08 AM EST)

---

## Features

- **Daily Automated Indexing** — CRON-triggered at 4:08 PM UTC
- **Processes Until Complete** — Continues until all pending resources are indexed
- **Parallel Processing** — Handles 10 resources simultaneously
- **Async File Downloads** — Concurrent downloads via `aiohttp`
- **Batch Processing** — 100 resources per batch
- **Progress Tracking** — Real-time status via HTTP endpoint
- **Error Resilience** — Skips failed resources and continues

---

## Project Structure

```
├── function_app.py        # Main Azure Function entry point
├── host.json              # Azure Functions host configuration
├── requirements.txt       # Python dependencies
│
├── data_formatting.py     # Consolidates resource data from multiple SQL tables
├── main_index.py          # JSON indexer for resource metadata
├── indexer1.py            # Index schema and embedding generation
├── store_in_blob.py       # Async blob document indexer
├── document_index.py      # Azure Search indexer for blob documents
├── delete_files.py        # Cleanup utility for staging container
└── logs_to_blob.py        # Logging to Azure Blob Storage
```

---

## How It Works

```
Timer (4:08 PM UTC)
       │
       ▼
Check pending resources
(LastIndexed IS NULL OR LastIndexed < LastUpdated)
       │
       ▼
Process in batches of 100
  ├── JSON Indexer  →  json-final index
  └── Blob Indexer  →  blob-attachments-final index
       │
       ▼
Update LastIndexed in SQL
       │
       ▼
Repeat until all pending resources are done (max 8 hours)
```

---

## API Endpoints

All endpoints require a `code` query parameter (Azure Function Key stored in Azure App Settings).

### Check Indexing Status
```http
GET /api/indexer/status?code=<FUNCTION_KEY>
```
```json
{
  "total_pending": 150,
  "batch_size": 100,
  "estimated_batches_remaining": 2,
  "status": "pending",
  "message": "150 resources need indexing"
}
```

### Trigger Manual Indexing (One Batch)
```http
GET /api/indexer?code=<FUNCTION_KEY>
```
```json
{
  "status": "success",
  "message": "Indexed 95, failed 5, remaining 50",
  "count": 95,
  "failed": 5,
  "remaining": 50,
  "has_more": true,
  "elapsed_seconds": 120.5
}
```

### Health Check
```http
GET /api/health
```

### Database Check
```http
GET /api/check-db
```

### Search Verification
```http
GET /api/check-search?resource_id=<ID>
```

> ⚠️ **Security Note:** Never hardcode the Function Key in code or documentation. Store it in Azure App Settings and reference it via environment variables or the Azure Portal.

---

## Environment Variables

Configure all variables in **Azure Function App → Settings → Environment Variables**.

| Variable | Description |
|----------|-------------|
| `AZURE_SEARCH_ENDPOINT` | Azure Cognitive Search endpoint URL |
| `AZURE_SEARCH_API_KEY` | Azure Search admin key |
| `AZURE_SEARCH_INDEX_NAME_1` | JSON metadata index (`json-final`) |
| `AZURE_SEARCH_INDEX_NAME_2` | Blob attachments index (`blob-attachments-final`) |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key |
| `AZURE_OPENAI_API_VERSION` | API version |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Embedding model deployment name |
| `AZURE_MODEL_NAME` | Model name |
| `AZURE_STORAGE_CONNECTION_STRING` | Storage account connection string |
| `CONTAINER_NAME` | Main blob container |
| `STAGING_CONTAINER_NAME` | Staging container for temp files |
| `AZURE_SQL_SERVER` | SQL server hostname |
| `AZURE_SQL_DATABASE` | Database name |
| `AZURE_SQL_USERNAME` | SQL username |
| `AZURE_SQL_PASSWORD` | SQL password |
| `COGNITIVE_SERVICES_KEY` | Cognitive Services API key |
| `COGNITIVE_SERVICES_URL` | Cognitive Services endpoint |

---

## Deployment

### Prerequisites
- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli)
- [Azure Functions Core Tools](https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local)
- Python 3.11+

### Deploy to Azure
```bash
func azure functionapp publish cpalmsazurefunctions --python
```

### Restart Function App
```bash
az functionapp restart \
  --name cpalmsazurefunctions \
  --resource-group AI_Pilot
```

---

## Database Query Logic

Resources are selected for indexing when they have never been indexed or have been updated since last indexed:

```sql
SELECT *
FROM ResourceCore
WHERE LastIndexed IS NULL
   OR LastIndexed < LastUpdated
ORDER BY ResourceID
OFFSET @offset ROWS
FETCH NEXT @batch_size ROWS ONLY
```

After successful indexing, `LastIndexed` is updated to the current UTC timestamp and the transaction is committed immediately.

> If resources keep re-indexing, ensure `LastIndexed` is `DATETIME2`:
> ```sql
> ALTER TABLE ResourceCore
> ALTER COLUMN LastIndexed DATETIME2 NULL;
> ```

---

## Performance

| Metric | Value |
|--------|-------|
| Batch size | 100 resources |
| Parallel workers | 10 resources at a time |
| Average speed | ~8 resources/minute |
| Max runtime | 8 hours (safety limit) |
| Est. time for 9,000 resources | ~19 hours |

---

## Monitoring & Logs

### Azure Portal
1. Go to [portal.azure.com](https://portal.azure.com)
2. Search for `cpalmsazurefunctions`
3. Navigate to **Functions → indexer_daily_timer → Monitor**

### Blob Logs
Logs are written to Azure Blob Storage:
- **Container:** `datastorage`
- **Path:** `Indexing logs version 2/indexing_logs_YYYY-MM-DD.txt`

---

## Timer Schedule

| Field | Value |
|-------|-------|
| CRON Expression | `0 8 16 * * *` |
| UTC | 4:08 PM |
| EST | 12:08 AM (midnight) |
| PST | 9:08 PM (previous day) |
| IST | 9:38 PM |

---

## Azure Resources

| Resource | Value |
|----------|-------|
| Function App | `cpalmsazurefunctions` |
| Resource Group | `AI_Pilot` |
| Runtime | Python 3.11 |

---

## Troubleshooting

**Indexing not starting?**
- Verify the timer trigger is enabled in Azure Portal
- Check function app status:
  ```bash
  az functionapp show --name cpalmsazurefunctions --resource-group AI_Pilot
  ```

**Want to trigger manually?**
- Call the HTTP endpoint: `GET /api/indexer?code=<FUNCTION_KEY>`

**Check remaining resources?**
- Call the status endpoint: `GET /api/indexer/status?code=<FUNCTION_KEY>`