# modules/storage.py  
import json  
from typing import Any, List  
  
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError  
  
from modules.config import (  
    HISTORY_STORAGE_MODE,  
    HISTORY_CONTAINER_NAME,  
    HISTORY_BLOB_NAME,  
    LOCAL_HISTORY_PATH, 
    PROMPT_CONTAINER_NAME, 
    get_blob_container_client,  
    get_blob_client,  
)  
  
  
def _ensure_container(container_name: str):  
    container_client = get_blob_container_client(container_name)  
    try:  
        container_client.create_container()  
    except ResourceExistsError:  
        pass  
    return container_client  
  
  
def _load_history_from_blob() -> List[Any]:  
    try:  
        _ensure_container(HISTORY_CONTAINER_NAME)  
        blob_client = get_blob_client(HISTORY_CONTAINER_NAME, HISTORY_BLOB_NAME)  
        raw = blob_client.download_blob().readall()  
        text = raw.decode("utf-8")  
        data = json.loads(text)  
        return data if isinstance(data, list) else []  
    except ResourceNotFoundError:  
        return []  
    except Exception as e:  
        print(f"[storage] load_history(blob) failed: {e}")  
        return []  
  
  
def _save_history_to_blob(items: List[Any]) -> None:  
    try:  
        _ensure_container(HISTORY_CONTAINER_NAME)  
        blob_client = get_blob_client(HISTORY_CONTAINER_NAME, HISTORY_BLOB_NAME)  
        payload = json.dumps(items, ensure_ascii=False, indent=2)  
        blob_client.upload_blob(payload.encode("utf-8"), overwrite=True)  
    except Exception as e:  
        print(f"[storage] save_history(blob) failed: {e}")  
        raise  
  
  
def _load_history_from_local() -> List[Any]:  
    try:  
        import os  
  
        if not os.path.exists(LOCAL_HISTORY_PATH):  
            return []  
  
        with open(LOCAL_HISTORY_PATH, "r", encoding="utf-8") as f:  
            data = json.load(f)  
  
        return data if isinstance(data, list) else []  
    except Exception as e:  
        print(f"[storage] load_history(local) failed: {e}")  
        return []  
  
  
def _save_history_to_local(items: List[Any]) -> None:  
    try:  
        with open(LOCAL_HISTORY_PATH, "w", encoding="utf-8") as f:  
            json.dump(items, f, ensure_ascii=False, indent=2)  
    except Exception as e:  
        print(f"[storage] save_history(local) failed: {e}")  
        raise  
  
  
def load_history() -> List[Any]:  
    if HISTORY_STORAGE_MODE == "blob":  
        return _load_history_from_blob()  
    return _load_history_from_local()  
  
  
def save_history(items: List[Any]) -> None:  
    if HISTORY_STORAGE_MODE == "blob":  
        _save_history_to_blob(items)  
        return  
    _save_history_to_local(items) 



def load_prompt_text(prompt_filename) -> str:  
    blob_client = get_blob_client(PROMPT_CONTAINER_NAME, prompt_filename)  
    raw = blob_client.download_blob().readall()  
    return raw.decode("utf-8")  