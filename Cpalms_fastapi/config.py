"""
Configuration and client initialization
"""
import os
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from azure.core.pipeline.policies import RetryPolicy
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI
import tiktoken

load_dotenv()
_credential = DefaultAzureCredential()
_COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"

_token_provider_leela = get_bearer_token_provider(_credential, _COGNITIVE_SERVICES_SCOPE)
_token_provider_cpalms = get_bearer_token_provider(_credential, _COGNITIVE_SERVICES_SCOPE)

retry_policy = RetryPolicy(retry_total=2, timeout=120)
AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX")
AZURE_SEARCH_INDEX_NAME_1 = os.getenv("AZURE_SEARCH_INDEX_1")

OPENAI_API_VERSION = os.getenv("OPENAI_API_VERSION")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE")
OPENAI_DEPLOYMENT_NAME = os.getenv("OPENAI_DEPLOYMENT_NAME")

OPENAI_API_VERSION_2 = os.getenv("OPENAI_API_VERSION_2")
OPENAI_API_BASE_2 = os.getenv("OPENAI_API_BASE_2")
OPENAI_DEPLOYMENT_NAME_2 = os.getenv("OPENAI_DEPLOYMENT_NAME_2")

OPENAI_API_VERSION_3 = os.getenv("OPENAI_API_VERSION_3")
OPENAI_API_BASE_3 = os.getenv("OPENAI_API_BASE_3")
OPENAI_DEPLOYMENT_NAME_3 = os.getenv("OPENAI_DEPLOYMENT_NAME_3")

OPENAI_API_VERSION_4 = os.getenv("OPENAI_API_VERSION_4")
OPENAI_API_BASE_4 = os.getenv("OPENAI_API_BASE_4")
OPENAI_DEPLOYMENT_NAME_4 = os.getenv("OPENAI_DEPLOYMENT_NAME_4")

AZURE_SQL_CONNECTION = os.getenv("AZURE_SQL_CONNECTION_STRING")
SQL_SERVER = os.getenv("SQL_SERVER")
SQL_DATABASE = os.getenv("SQL_DATABASE")
SQL_USERNAME = os.getenv("SQL_USERNAME")
SQL_PASSWORD = os.getenv("SQL_PASSWORD")

AZURE_BLOB_SAS_TOKEN = os.getenv("AZURE_BLOB_SAS_TOKEN")
AZURE_BLOB_BASE_URL = "https://cpalmsmediaprod.blob.core.windows.net"

VALID_API_KEYS = [
    os.getenv("API_KEY_1", "your-first-api-key"),
    os.getenv("API_KEY_2", "your-second-api-key"),
]

search_client = SearchClient(
    endpoint=AZURE_SEARCH_ENDPOINT,
    index_name=AZURE_SEARCH_INDEX_NAME,
    credential=_credential,          
    retry_policy=retry_policy
)

search_client_1 = SearchClient(
    endpoint=AZURE_SEARCH_ENDPOINT,
    index_name=AZURE_SEARCH_INDEX_NAME_1,
    credential=_credential,         
    retry_policy=retry_policy
)

client = AzureOpenAI(
    azure_ad_token_provider=_token_provider_leela,
    api_version=OPENAI_API_VERSION,
    azure_endpoint=OPENAI_API_BASE
)

client_2 = AzureOpenAI(
    azure_ad_token_provider=_token_provider_cpalms,
    api_version=OPENAI_API_VERSION_2,
    azure_endpoint=OPENAI_API_BASE_2
)

client_3 = AzureOpenAI(
    azure_ad_token_provider=_token_provider_leela,
    api_version=OPENAI_API_VERSION_3,
    azure_endpoint=OPENAI_API_BASE_3
)

client_4 = AzureOpenAI(
    azure_ad_token_provider=_token_provider_leela,
    api_version=OPENAI_API_VERSION_4,
    azure_endpoint=OPENAI_API_BASE_4
)

encoding = tiktoken.get_encoding("cl100k_base")

FIRST_CHUNK_SIZE = 100_000
MAX_REMAINING_TOKENS = 265_000

openai_executor = ThreadPoolExecutor(max_workers=40, thread_name_prefix="openai")