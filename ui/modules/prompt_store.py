# modules/prompt_store.py  
import os  
from typing import List, Optional  
  
from azure.core.exceptions import ResourceNotFoundError  
  
from modules.config import (  
    PROMPT_CONTAINER_NAME,  
    get_blob_client,  
    get_blob_container_client,  
)  
  
PROMPT_STORAGE_MODE = os.getenv("PROMPT_STORAGE_MODE", "local")  # local / blob  
PROMPT_LOCAL_DIR = os.getenv("PROMPT_LOCAL_DIR", "prompts")  
PROMPT_BLOB_PREFIX = os.getenv("PROMPT_BLOB_PREFIX", "prompt-sets")  
  
  
DEFAULT_PROMPTS = {  
    "default": """あなたはカスタマーサポートエージェントです。  
ユーザーとの会話内容を整理し、関連文書を踏まえて回答方針、回答案、ヒアリングすべき項目を日本語で分かりやすく提示してください。  
断定しすぎず、根拠ベースで支援してください。""",  
}  
  
  
def _normalize_prompt_name(prompt_set_name: str) -> str:  
    name = (prompt_set_name or "default").strip()  
    return name or "default"  
  
  
def _load_prompt_local(prompt_set_name: str) -> Optional[str]:  
    path = os.path.join(PROMPT_LOCAL_DIR, f"{prompt_set_name}.txt")  
    if not os.path.exists(path):  
        return None  
  
    with open(path, "r", encoding="utf-8") as f:  
        text = f.read()  
  
    return text if text.strip() else None  
  
  
def _load_prompt_blob(prompt_set_name: str) -> Optional[str]:  
    blob_name = f"{PROMPT_BLOB_PREFIX}/{prompt_set_name}.txt"  
    try:  
        blob_client = get_blob_client(PROMPT_CONTAINER_NAME, blob_name)  
        raw = blob_client.download_blob().readall()  
        text = raw.decode("utf-8")  
        return text if text.strip() else None  
    except ResourceNotFoundError:  
        return None  
  
  
def get_prompt_set(prompt_set_name: str) -> str:  
    name = _normalize_prompt_name(prompt_set_name)  
    text = None  
  
    try:  
        if PROMPT_STORAGE_MODE == "blob":  
            text = _load_prompt_blob(name)  
        else:  
            text = _load_prompt_local(name)  
    except Exception as e:  
        print(f"[prompt_store] load prompt failed ({name}): {e}")  
  
    if text:  
        return text  
  
    return DEFAULT_PROMPTS.get(name, DEFAULT_PROMPTS["default"])  
  
  
def _list_prompt_sets_local() -> List[str]:  
    names = set(DEFAULT_PROMPTS.keys())  
  
    if not os.path.isdir(PROMPT_LOCAL_DIR):  
        return sorted(names)  
  
    for filename in os.listdir(PROMPT_LOCAL_DIR):  
        if filename.endswith(".txt"):  
            names.add(filename[:-4])  
  
    return sorted(names)  
  
  
def _list_prompt_sets_blob() -> List[str]:  
    names = set(DEFAULT_PROMPTS.keys())  
  
    try:  
        container_client = get_blob_container_client(PROMPT_CONTAINER_NAME)  
        prefix = f"{PROMPT_BLOB_PREFIX.rstrip('/')}/"  
  
        for blob in container_client.list_blobs(name_starts_with=prefix):  
            blob_name = blob.name  
            if not blob_name.endswith(".txt"):  
                continue  
  
            basename = blob_name[len(prefix):]  
            if "/" in basename:  
                continue  
  
            names.add(basename[:-4])  
  
    except Exception as e:  
        print(f"[prompt_store] list prompt sets from blob failed: {e}")  
  
    return sorted(names)  
  
  
def list_prompt_sets() -> List[str]:  
    if PROMPT_STORAGE_MODE == "blob":  
        return _list_prompt_sets_blob()  
    return _list_prompt_sets_local()  