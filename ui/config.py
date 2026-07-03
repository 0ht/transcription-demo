# ui/config.py
import os

# ローカル確認用: .env があれば読み込む（コンテナ環境では未インストールでも無視）
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


#Azure OpenAI
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "")
AZURE_OPENAI_EMBEDDING_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
AZURE_OPENAI_EMBEDDING_MODEL = os.getenv("AZURE_OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
AZURE_OPENAI_EMBEDDING_DIMENSIONS = int(os.getenv("AZURE_OPENAI_EMBEDDING_DIMENSIONS", "3072"))
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")

#Azure AI Search
# 環境変数名は container-apps.bicep が設定するキーに合わせる
SEARCH_ENDPOINT = os.environ.get("AZURE_SEARCH_ENDPOINT", "")
SEARCH_INDEX_NAME = os.environ.get("AZURE_SEARCH_INDEX_NAME", "")

# select 既定は実運用（container-apps.bicep / main.bicep）と揃える。
# これらのフィールドが取れないと UI の根拠表示（話者・時刻・出典）が欠落するため。
READ_FIELDS = os.getenv(
    "READ_FIELDS", "content,source_file,transcript_path,chunk_id,speaker,start_time"
).split(",")
SEMANTIC_CONFIG_NAME = os.environ.get("AZURE_SEARCH_SEMANTIC_CONFIG", "default")

"""
インデックスの構成案
READ_FIELDS = [  
    "id",  
    "source_file",  
    "transcript_path",  
    "chunk_id",  
    "content",  
    "speaker",  
    "start_time",  
]  
"""

#Azure Blob Storage
STORAGE_ACCOUNT = os.environ.get("DATA_STORAGE_ACCOUNT_NAME", "")
CONTAINER_INPUT = os.environ.get("CONTAINER_INPUT", "input")
CONTAINER_OUTPUT = os.environ.get("CONTAINER_OUTPUT", "output")
CONTAINER_PROCESSED = os.environ.get("CONTAINER_PROCESSED", "processed")
ACCOUNT_URL = f"https://{STORAGE_ACCOUNT}.blob.core.windows.net"
BLOB_CONNECTION_STRING = os.getenv("BLOB_CONNECTION_STRING","")


# RAG（AIに質問）機能の有効化フラグ
# Azure OpenAI と AI Search のエンドポイント/デプロイが揃っている場合のみ有効化する。
# 未設定でもダッシュボード本体は動作する（質問UIが非表示になるだけ）。
RAG_ENABLED = bool(AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_CHAT_DEPLOYMENT and SEARCH_ENDPOINT)
