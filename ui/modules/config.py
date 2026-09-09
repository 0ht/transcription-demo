# modules/config.py  
import os  
from typing import Optional  
  
from dotenv import load_dotenv  
from azure.identity import DefaultAzureCredential,get_bearer_token_provider
from openai import AzureOpenAI, AsyncOpenAI
from azure.storage.blob import BlobServiceClient  
from azure.search.documents import SearchClient  
from azure.core.credentials import AzureKeyCredential  
  
# .env読み込み（プロジェクトルートに配置）  
load_dotenv()  
  
  
def require_env(name: str) -> str:  
    value = os.getenv(name)  
    if not value:  
        raise RuntimeError(f"Missing required environment variable: {name}")  
    return value  
  
  
# -----------------------------  
# Azure credentials  
# -----------------------------  
credential = DefaultAzureCredential()  
  
  
# -----------------------------  
# Azure OpenAI  
# -----------------------------  
AOAI_ENDPOINT = require_env("AOAI_ENDPOINT")  
AOAI_API_VERSION = os.getenv("AOAI_API_VERSION", "2024-10-21")  
AOAI_KEY: Optional[str] = os.getenv("AOAI_KEY")  
  
aoai_model = require_env("AOAI_MODEL_NAME")  
aoai_model_fast = os.getenv("AOAI_MODEL_NAME_FAST", aoai_model)  
realtime_model = require_env("REALTIME_MODEL")  
whisper_model = os.getenv("WHISPER_MODEL", "whisper")  
  
  
def _get_cognitive_token_provider():  
    return get_bearer_token_provider(  
        credential,  
        "https://cognitiveservices.azure.com/.default",  
    )  
  
  
def _get_ai_foundry_token_provider():  
    return get_bearer_token_provider(  
        credential,  
        "https://ai.azure.com/.default",  
    )  
  
  
# 通常の Chat / Responses 用  
if AOAI_KEY:  
    aoai_client = AzureOpenAI(  
        azure_endpoint=AOAI_ENDPOINT,  
        api_key=AOAI_KEY,  
        api_version=AOAI_API_VERSION,  
    )  
else:  
    aoai_client = AzureOpenAI(  
        azure_endpoint=AOAI_ENDPOINT,  
        api_version=AOAI_API_VERSION,  
        azure_ad_token_provider=_get_cognitive_token_provider(),  
    )  
  
AOAI_REALTIME_ENDPOINT = os.getenv("AOAI_REALTIME_ENDPOINT", AOAI_ENDPOINT)  
AOAI_REALTIME_KEY = os.getenv("AOAI_REALTIME_KEY") 
realtime_model = os.getenv("REALTIME_MODEL")



def create_realtime_client() -> AsyncOpenAI:  
    """  
    Azure OpenAI Realtime 用クライアントを生成する。  
    Realtime は sample に合わせて AsyncOpenAI + websocket_base_url を使う。  
    """  
    websocket_base_url = AOAI_REALTIME_ENDPOINT.replace("https://", "wss://").rstrip("/") + "/openai/v1"  
  
    if AOAI_REALTIME_KEY:  
        return AsyncOpenAI(  
            websocket_base_url=websocket_base_url,  
            api_key=AOAI_REALTIME_KEY,  
        )  
  
    token_provider = _get_ai_foundry_token_provider()  
    token = token_provider()  
  
    return AsyncOpenAI(  
        websocket_base_url=websocket_base_url,  
        api_key=token,  
    )  
  
  
# -----------------------------  
# Azure AI Search  
# -----------------------------  
SEARCH_ENDPOINT = os.getenv("SEARCH_ENDPOINT")  
SEARCH_KEY = os.getenv("SEARCH_KEY")  
SEARCH_INDEX = os.getenv("SEARCH_INDEX")  
SEMANTIC_CONFIG_NAME = os.getenv("SEMANTIC_CONFIG_NAME")  
SEARCH_TOP_COUNT = int(os.getenv("SEARCH_TOP_COUNT", "10"))
read_fields = ["title","chunk"]

#Azure AI Search
if SEARCH_KEY:
    search_client = SearchClient(  
        endpoint=SEARCH_ENDPOINT,  
        index_name=SEARCH_INDEX,  
        credential=AzureKeyCredential(SEARCH_KEY)  
    )  
else:
    search_client = SearchClient(  
        endpoint=SEARCH_ENDPOINT,  
        index_name=SEARCH_INDEX,  
        credential=credential  
    )  
  

# -----------------------------  
# Azure AI Document Intelligence
# -----------------------------  
ADI_ENDPOINT = os.environ.get("ADI_ENDPOINT")
ADI_KEY = os.environ.get("ADI_KEY","")
ADI_MODEL = os.environ.get("ADI_MODEL","")




# -----------------------------  
# Blob Storage  
# -----------------------------  
HISTORY_STORAGE_MODE = os.getenv("HISTORY_STORAGE_MODE", "local")  # local / blob  
BLOB_CONNECTION_STRING = os.getenv("BLOB_CONNECT_STR", "")  
STORAGE_ACCOUNT_NAME = os.getenv("STORAGE_ACCOUNT_NAME", "")   
PROMPT_CONTAINER_NAME = os.getenv("PROMPT_CONTAINER_NAME", "prompts")  
HISTORY_CONTAINER_NAME = os.getenv("HISTORY_CONTAINER_NAME", "meeting-history")  
HISTORY_BLOB_NAME = os.getenv("HISTORY_BLOB_NAME", "history/history.json")  
LOCAL_HISTORY_PATH = os.getenv("LOCAL_HISTORY_PATH", "data/meeting_history.json")  


CONTAINER_INPUT = os.getenv("CONTAINER_INPUT", "input")  
CONTAINER_OUTPUT = os.getenv("CONTAINER_OUTPUT", "output")  
CONTAINER_PROCESSED = os.getenv("CONTAINER_PROCESSED", "processed")  


UPLOAD_CONTAINER_NAME = os.getenv("UPLOAD_CONTAINER_NAME", "upload")  
CHUNK_CONTAINER_NAME = os.getenv("CHUNK_CONTAINER_NAME", "chunk")  

  
def get_blob_service_client() -> BlobServiceClient:  
    if BLOB_CONNECTION_STRING:  
        return BlobServiceClient.from_connection_string(BLOB_CONNECTION_STRING)  
  
    if not STORAGE_ACCOUNT_NAME:  
        raise RuntimeError("Missing STORAGE_ACCOUNT_NAME for managed identity blob access")  
  
    return BlobServiceClient(  
        account_url=f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net",  
        credential=credential,  
    )  
  
  
def get_blob_container_client(container_name: str):  
    service = get_blob_service_client()  
    return service.get_container_client(container_name)  
  
  
def get_blob_client(container_name: str, blob_name: str):  
    container = get_blob_container_client(container_name)  
    return container.get_blob_client(blob_name)  

