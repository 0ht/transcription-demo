#modules/blob_document_service.py
import json  
import os  
from datetime import datetime  
from typing import Optional  
  
from azure.identity import DefaultAzureCredential  
from azure.storage.blob import BlobServiceClient  
  
from modules.config import (  
    BLOB_CONNECTION_STRING,  
    STORAGE_ACCOUNT_NAME,  
)  
  
# 必要に応じて config.py に追加してください  
CONTAINER_INPUT = os.getenv("CONTAINER_INPUT", "input")  
CONTAINER_OUTPUT = os.getenv("CONTAINER_OUTPUT", "output")  
CONTAINER_PROCESSED = os.getenv("CONTAINER_PROCESSED", "processed")  
  
ACCOUNT_URL = f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net"  
  
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".wma"}  
VIDEO_EXTS = {".mp4", ".avi", ".mov", ".webm", ".mkv"}  
  
SPEAKER_COLORS = [  
    "#4a9eff", "#ff6b6b", "#51cf66", "#ffd43b",  
    "#cc5de8", "#ff922b", "#20c997", "#e599f7",  
]  
  
_blob_svc_instance = None  
  
  
def get_blob_service() -> BlobServiceClient:  
    global _blob_svc_instance  
    if _blob_svc_instance is None:  
        if BLOB_CONNECTION_STRING:  
            _blob_svc_instance = BlobServiceClient.from_connection_string(BLOB_CONNECTION_STRING)  
        else:  
            credential = DefaultAzureCredential()  
            _blob_svc_instance = BlobServiceClient(account_url=ACCOUNT_URL, credential=credential)  
    return _blob_svc_instance  
  
  
def speaker_color(name: str) -> str:  
    return SPEAKER_COLORS[hash(name) % len(SPEAKER_COLORS)]  
  
  
def list_transcripts(date_from=None, date_to=None, keyword: str = "") -> list[dict]:  
    svc = get_blob_service()  
    container = svc.get_container_client(CONTAINER_OUTPUT)  
    results = []  
  
    for blob in container.list_blobs():  
        if not blob.name.endswith("_transcript.json"):  
            continue  
  
        parts = blob.name.split("/")  
        blob_date = None  
        if len(parts) >= 3:  
            try:  
                blob_date = datetime(int(parts[0]), int(parts[1]), int(parts[2]))  
            except Exception:  
                blob_date = None  
  
        if blob_date:  
            if date_from and blob_date.date() < date_from:  
                continue  
            if date_to and blob_date.date() > date_to:  
                continue  
  
        if keyword and keyword.lower() not in blob.name.lower():  
            continue  
  
        results.append(  
            {  
                "path": blob.name,  
                "date": blob_date.strftime("%Y/%m/%d") if blob_date else "",  
                "size": blob.size,  
                "last_modified": blob.last_modified,  
                "name": os.path.basename(blob.name).replace("_transcript.json", ""),  
            }  
        )  
  
    return sorted(  
        results,  
        key=lambda x: x.get("last_modified") or datetime.min,  
        reverse=True,  
    )  
  
  
def load_json(path: str) -> dict:  
    svc = get_blob_service()  
    data = svc.get_blob_client(CONTAINER_OUTPUT, path).download_blob().readall()  
    return json.loads(data)  
  
  
def load_text(path: str) -> str:  
    svc = get_blob_service()  
    return (  
        svc.get_blob_client(CONTAINER_OUTPUT, path)  
        .download_blob()  
        .readall()  
        .decode("utf-8")  
    )  
  
  
def load_media(path: str) -> bytes:  
    svc = get_blob_service()  
    return svc.get_blob_client(CONTAINER_PROCESSED, path).download_blob().readall()  
  
  
def build_transcript_text(transcript: dict) -> str:  
    segments = transcript.get("segments", []) or []  
    lines = []  
    for seg in segments:  
        speaker = seg.get("speaker", "Unknown")  
        text = seg.get("text", "")  
        if text:  
            lines.append(f"{speaker}: {text}")  
    return "\n".join(lines).strip()  
  
  
def get_document_detail(transcript_path: str) -> dict:  
    transcript = load_json(transcript_path)  
    txt_path = transcript_path.replace("_transcript.json", "_transcript.txt")  
  
    txt_data = ""  
    try:  
        txt_data = load_text(txt_path)  
    except Exception:  
        txt_data = ""  
  
    source_file = transcript.get("sourceFile", "")  
    ext = os.path.splitext(source_file)[1].lower() if source_file else ""  
  
    media_path = None  
    if source_file:  
        parts = transcript_path.split("/")  
        media_path = "/".join(parts[:3]) + "/" + source_file if len(parts) >= 3 else source_file  
  
    return {  
        "transcript_path": transcript_path,  
        "txt_path": txt_path,  
        "source_file": source_file,  
        "duration": transcript.get("duration", "-"),  
        "language": transcript.get("language", "-"),  
        "processed_at": transcript.get("processedAt", "")[:19],  
        "segments": transcript.get("segments", []),  
        "transcript_json": transcript,  
        "transcript_text": build_transcript_text(transcript),  
        "txt_data": txt_data,  
        "media_path": media_path,  
        "media_ext": ext,  
        "is_audio": ext in AUDIO_EXTS,  
        "is_video": ext in VIDEO_EXTS,  
    }  