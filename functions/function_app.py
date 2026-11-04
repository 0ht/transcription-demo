"""
Azure Functions - Blob 文字起こし処理
Queue Trigger → Fast Transcription API / テキスト抽出
対応: 音声ファイル (.wav/.mp3/.m4a/.ogg/.flac/.wma) + テキスト (.txt/.md/.json/.vtt)
"""

import base64
import json
import logging
import os
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import azure.functions as func
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
STORAGE_ACCOUNT = os.environ["DATA_STORAGE_ACCOUNT_NAME"]
CONTAINER_INPUT = os.environ.get("DATA_STORAGE_CONTAINER_INPUT", "input")
CONTAINER_OUTPUT = os.environ.get("DATA_STORAGE_CONTAINER_OUTPUT", "output")
CONTAINER_PROCESSED = os.environ.get("DATA_STORAGE_CONTAINER_PROCESSED", "processed")
AI_ENDPOINT = os.environ["AI_SERVICES_ENDPOINT"]
SPEECH_LANG = os.environ.get("SPEECH_LANGUAGE", "ja-JP")

STORAGE_URL = f"https://{STORAGE_ACCOUNT}.blob.core.windows.net"

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".wma"}
TEXT_EXTS = {".txt", ".md", ".json", ".vtt"}
ALL_SUPPORTED = AUDIO_EXTS | TEXT_EXTS

MAX_RETRIES = 3
MAX_AUDIO_BYTES = 250_000_000
MAX_AUDIO_DURATION_MS = 2 * 60 * 60 * 1000
SPEECH_API_VERSION = '2025-10-15'


class InvalidAudioError(ValueError):
    pass

# リトライしても回復しない例外（認証・権限不足など）は即座に終端化する。
_NON_RETRYABLE = (ClientAuthenticationError, InvalidAudioError)

app = func.FunctionApp()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _credential():
    return DefaultAzureCredential()


def _blob_svc():
    return BlobServiceClient(STORAGE_URL, credential=_credential())


def _input_exists(blob_name: str) -> bool:
    """input コンテナに対象 blob がまだ存在するか。

    処理成功時は move_to_processed で input から削除されるため、存在しなければ
    別インスタンスが既に処理済み（Queue の at-least-once 再配信）と判断できる。
    """
    return _blob_svc().get_blob_client(CONTAINER_INPUT, blob_name).exists()


def _set_processing_status(blob_name: str, status: str, *, error: str = "", result_path: str = "") -> None:
    blob = _blob_svc().get_blob_client(CONTAINER_INPUT, blob_name)
    metadata = dict(blob.get_blob_properties().metadata or {})
    metadata.update({
        "processing_status": status,
        "processing_updated_at": datetime.now(timezone.utc).isoformat(),
        "processing_error": base64.b64encode(error[:500].encode("utf-8")).decode("ascii"),
        "processing_result": base64.b64encode(result_path.encode("utf-8")).decode("ascii"),
    })
    blob.set_blob_metadata(metadata)


def _speech_headers():
    token = _credential().get_token(
        "https://cognitiveservices.azure.com/.default"
    ).token
    return {"Authorization": f"Bearer {token}"}


def _output_prefix(blob_name: str) -> str:
    now = datetime.now(timezone.utc)
    stem = Path(blob_name).stem
    return f"{now:%Y}/{now:%m}/{now:%d}/{stem}"


# ---------------------------------------------------------------------------
# テキスト抽出
# ---------------------------------------------------------------------------
def extract_text(blob_name: str, ext: str) -> dict:
    """テキスト系ファイルの内容を抽出"""
    svc = _blob_svc()
    data = svc.get_blob_client(CONTAINER_INPUT, blob_name).download_blob().readall()

    if ext in (".txt", ".md", ".vtt"):
        text = data.decode("utf-8", errors="replace")
    elif ext == ".json":
        # JSON はそのまま整形して保持
        try:
            obj = json.loads(data.decode("utf-8", errors="replace"))
            text = json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception:
            text = data.decode("utf-8", errors="replace")
    else:
        text = ""

    return {
        "sourceFile": blob_name,
        "processedAt": datetime.now(timezone.utc).isoformat(),
        "language": SPEECH_LANG,
        "segments": [
            {"speaker": "Text", "text": text, "startTime": "PT0S", "endTime": "PT0S"}
        ],
    }


# ---------------------------------------------------------------------------
# Fast Transcription API
# ---------------------------------------------------------------------------
def _iso_duration_ms(milliseconds: int) -> str:
    if type(milliseconds) is not int or milliseconds < 0:
        raise ValueError("Speech returned an invalid millisecond timestamp")
    seconds, fraction = divmod(milliseconds, 1000)
    return f"PT{seconds}.{fraction:03d}S"


def transcribe_audio(blob_name: str) -> dict:
    """Private Blob を読み、URL や SAS を渡さず音声本体を Speech に送る。"""
    blob = _blob_svc().get_blob_client(CONTAINER_INPUT, blob_name)
    size = blob.get_blob_properties().size
    if not 0 < size < MAX_AUDIO_BYTES:
        raise InvalidAudioError("Audio must be nonempty and smaller than 250 MB")

    with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as audio:
        downloaded = blob.download_blob().readinto(audio)
        if downloaded != size:
            raise InvalidAudioError("Audio size changed during download; upload it again")
        audio.seek(0)
        form = MultipartEncoder(fields={
            "audio": (Path(blob_name).name, audio, "application/octet-stream"),
            "definition": json.dumps({
                "locales": [SPEECH_LANG],
                "diarization": {"enabled": True, "maxSpeakers": 35},
            }),
        })
        headers = _speech_headers()
        headers["Content-Type"] = form.content_type
        response = requests.post(
            f"{AI_ENDPOINT.rstrip('/')}/speechtotext/transcriptions:transcribe",
            params={"api-version": SPEECH_API_VERSION},
            headers=headers, data=form, timeout=(30, 900),
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as error:
            logging.error("Speech HTTP %s: %s", response.status_code, response.text[:2000])
            if response.status_code in (400, 401, 403, 413, 415, 422):
                raise InvalidAudioError(
                    f"Speech rejected audio (HTTP {response.status_code}): "
                    f"{response.text[:1000]}"
                ) from error
            raise
        content = response.json()

    duration = _iso_duration_ms(content["durationMilliseconds"])
    if content["durationMilliseconds"] >= MAX_AUDIO_DURATION_MS:
        raise InvalidAudioError("Audio must be shorter than 2 hours; split it before uploading")
    segments = []
    for phrase in content["phrases"]:
        start = phrase["offsetMilliseconds"]
        length = phrase["durationMilliseconds"]
        start_time = _iso_duration_ms(start)
        _iso_duration_ms(length)
        segments.append({
            "speaker": f"Speaker {phrase.get('speaker', '?')}",
            "text": phrase["text"],
            "startTime": start_time,
            "endTime": _iso_duration_ms(start + length),
        })
    return {
        "sourceFile": blob_name,
        "processedAt": datetime.now(timezone.utc).isoformat(),
        "duration": duration,
        "language": SPEECH_LANG,
        "segments": segments,
    }


# ---------------------------------------------------------------------------
# 結果保存 / ファイル移動
# ---------------------------------------------------------------------------
def save_results(blob_name: str, result: dict) -> dict:
    """JSON と txt を output コンテナに保存"""
    prefix = _output_prefix(blob_name)
    svc = _blob_svc()

    json_path = f"{prefix}_transcript.json"
    svc.get_blob_client(CONTAINER_OUTPUT, json_path).upload_blob(
        json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"),
        overwrite=True,
    )

    lines = [f"[{s['speaker']}] {s['text']}" for s in result.get("segments", [])]
    txt_path = f"{prefix}_transcript.txt"
    svc.get_blob_client(CONTAINER_OUTPUT, txt_path).upload_blob(
        "\n".join(lines).encode("utf-8"), overwrite=True,
    )

    logging.info("Saved results: %s, %s", json_path, txt_path)
    return {"json": json_path, "txt": txt_path}


def save_error(blob_name: str, error: BaseException) -> str:
    """最終失敗時にエラーマーカー JSON を output コンテナへ書き出す。
    UI 側はこの `_error.json` を検知して ❌ エラーとして一覧表示する。
    """
    prefix = _output_prefix(blob_name)
    svc = _blob_svc()
    err_path = f"{prefix}_error.json"
    payload = {
        "sourceFile": blob_name,
        "processedAt": datetime.now(timezone.utc).isoformat(),
        "errorType": type(error).__name__,
        "error": str(error)[:2000],
    }
    svc.get_blob_client(CONTAINER_OUTPUT, err_path).upload_blob(
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        overwrite=True,
    )
    logging.info("Saved error marker: %s", err_path)
    return err_path


def move_to_processed(blob_name: str) -> str:
    """元ファイルを input → processed へ移動"""
    now = datetime.now(timezone.utc)
    dest = f"{now:%Y}/{now:%m}/{now:%d}/{Path(blob_name).name}"
    svc = _blob_svc()

    src = svc.get_blob_client(CONTAINER_INPUT, blob_name)
    dst = svc.get_blob_client(CONTAINER_PROCESSED, dest)

    data = src.download_blob().readall()
    metadata = src.get_blob_properties().metadata or {}
    dst.upload_blob(data, overwrite=True, metadata=metadata)
    src.delete_blob()

    logging.info("Moved %s → processed/%s", blob_name, dest)
    return dest


# ===========================================================================
# Queue Trigger — Event Grid → Storage Queue → Functions
# 閉域構成: Event Grid が Storage Queue に配信、Functions がポーリング
# ===========================================================================
@app.queue_trigger(
    arg_name="msg",
    queue_name="blob-events",
    connection="DataStorage",
)
def blob_transcribe(msg: func.QueueMessage):
    body = json.loads(msg.get_body().decode("utf-8"))

    # Event Grid イベントの subject から blob 名を取得
    subject = body.get("subject", "")
    blob_name = subject.split("/blobs/", 1)[-1] if "/blobs/" in subject else ""
    if not blob_name:
        logging.warning("Could not parse blob name from event subject: %s", subject)
        return

    ext = Path(blob_name).suffix.lower()
    if ext not in ALL_SUPPORTED:
        logging.info("Skipping unsupported file: %s (%s)", blob_name, ext)
        return

    # 冪等性: Queue は at-least-once 配信のため、同一 blob のイベントが再配信され得る。
    # 処理成功時は input から processed へ移動して消えるので、既に input に無ければスキップ。
    if not _input_exists(blob_name):
        logging.info("Input no longer exists (already processed), skipping: %s", blob_name)
        return

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logging.info(
                "Processing %s (attempt %d/%d)", blob_name, attempt, MAX_RETRIES
            )
            _set_processing_status(blob_name, "processing")

            if ext in TEXT_EXTS:
                result = extract_text(blob_name, ext)
            else:
                result = transcribe_audio(blob_name)

            paths = save_results(blob_name, result)
            _set_processing_status(blob_name, "completed", result_path=paths["json"])
            move_to_processed(blob_name)
            logging.info("Completed: %s", blob_name)
            return

        except _NON_RETRYABLE as exc:
            # 認証/権限エラーはリトライしても無駄なので即座に打ち切る。
            last_error = exc
            logging.error("Non-retryable error for %s: %s", blob_name, exc)
            break

        except Exception as exc:
            last_error = exc
            logging.warning(
                "Attempt %d/%d failed for %s: %s",
                attempt, MAX_RETRIES, blob_name, exc,
            )
            if attempt < MAX_RETRIES:
                time.sleep(5)

    logging.error(
        "All %d attempts failed for %s: %s", MAX_RETRIES, blob_name, last_error
    )
    _set_processing_status(blob_name, "failed", error=str(last_error))
    save_error(blob_name, last_error)
    move_to_processed(blob_name)
