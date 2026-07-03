"""
Azure Functions - Blob 文字起こし処理
Queue Trigger → Batch Transcription API / テキスト抽出
対応: 音声ファイル (.wav/.mp3/.m4a/.ogg/.flac/.wma) + テキスト (.txt/.md/.json/.vtt)
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import azure.functions as func
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
import requests

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
POLL_INTERVAL_SEC = 30
MAX_POLL_COUNT = 120  # 120 × 30s = 60 min

# リトライしても回復しない例外（認証・権限不足など）は即座に終端化する。
_NON_RETRYABLE = (ClientAuthenticationError,)

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


def _speech_headers():
    token = _credential().get_token(
        "https://cognitiveservices.azure.com/.default"
    ).token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _blob_url(container: str, name: str) -> str:
    return f"{STORAGE_URL}/{container}/{name}"


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
# Batch Transcription API
# ---------------------------------------------------------------------------
def create_transcription(audio_url: str, blob_name: str) -> str:
    """Batch Transcription ジョブを作成し、ID を返す"""
    url = f"{AI_ENDPOINT}/speechtotext/v3.2/transcriptions"
    body = {
        "contentUrls": [audio_url],
        "locale": SPEECH_LANG,
        "displayName": f"Transcription: {blob_name}",
        "properties": {
            "diarizationEnabled": True,
            "wordLevelTimestampsEnabled": True,
            "punctuationMode": "DictatedAndAutomatic",
            "timeToLiveHours": 48,
            # ステレオ音声でも diarization を有効化するため、単一チャンネル(0)のみを処理対象とする
            # （Speech Batch Transcription はマルチチャンネル + diarization の併用を許可しないため）
            "channels": [0],
        },
    }
    resp = requests.post(url, headers=_speech_headers(), json=body, timeout=60)
    resp.raise_for_status()
    return resp.json()["self"].rsplit("/", 1)[-1]


def wait_for_transcription(tid: str) -> None:
    """ポーリングで Batch Transcription の完了を待機"""
    url = f"{AI_ENDPOINT}/speechtotext/v3.2/transcriptions/{tid}"
    for i in range(MAX_POLL_COUNT):
        resp = requests.get(url, headers=_speech_headers(), timeout=30)
        resp.raise_for_status()
        body = resp.json()
        status = body["status"]
        logging.info("Transcription %s poll %d/%d: %s", tid, i + 1, MAX_POLL_COUNT, status)
        if status == "Succeeded":
            return
        if status == "Failed":
            # 失敗時はジョブ全体と /files の TranscriptionReport を出力
            logging.error("Transcription %s FULL job body: %s", tid, json.dumps(body))
            try:
                files_resp = requests.get(f"{url}/files", headers=_speech_headers(), timeout=30)
                files_json = files_resp.json()
                logging.error("Transcription %s /files: %s", tid, json.dumps(files_json)[:4000])
                # report.json の中身を取得
                for f in files_json.get("values", []):
                    if f.get("kind") == "TranscriptionReport":
                        rep_url = f["links"]["contentUrl"]
                        rep_resp = requests.get(rep_url, timeout=30)
                        logging.error("Transcription %s REPORT body: %s", tid, rep_resp.text[:4000])
            except Exception as exc:
                logging.error("Failed to fetch error details for %s: %s", tid, exc)
            error = body.get("properties", {}).get("error", {})
            raise Exception(f"Transcription failed: {json.dumps(error)}")
        time.sleep(POLL_INTERVAL_SEC)
    raise Exception(f"Transcription {tid} timed out after {MAX_POLL_COUNT * POLL_INTERVAL_SEC}s")


def fetch_transcription_results(tid: str, blob_name: str) -> dict:
    """Batch Transcription の結果を取得して返す（ジョブ削除は呼び出し側の責務）"""
    files_url = f"{AI_ENDPOINT}/speechtotext/v3.2/transcriptions/{tid}/files"
    resp = requests.get(files_url, headers=_speech_headers(), timeout=30)
    resp.raise_for_status()

    segments = []
    duration = "PT0S"

    for f in resp.json().get("values", []):
        if f.get("kind") != "Transcription":
            continue
        content_url = f["links"]["contentUrl"]
        # contentUrl は SAS 付き URL なので Authorization ヘッダを付けない
        content_resp = requests.get(content_url, timeout=120)
        content_resp.raise_for_status()
        content = content_resp.json()
        duration = content.get("duration", duration)
        for seg in content.get("recognizedPhrases", []):
            speaker = f"Speaker {seg.get('speaker', '?')}"
            best = seg["nBest"][0] if seg.get("nBest") else {}
            segments.append({
                "speaker": speaker,
                "text": best.get("display", ""),
                "startTime": seg.get("offset", "PT0S"),
                "endTime": seg.get("offsetEnd", "PT0S"),
            })

    return {
        "sourceFile": blob_name,
        "processedAt": datetime.now(timezone.utc).isoformat(),
        "duration": duration,
        "language": SPEECH_LANG,
        "segments": segments,
    }


def delete_transcription_job(tid: str) -> None:
    """Batch Transcription ジョブを削除（クリーンアップ）。失敗してもログのみで握りつぶす"""
    try:
        del_url = f"{AI_ENDPOINT}/speechtotext/v3.2/transcriptions/{tid}"
        requests.delete(del_url, headers=_speech_headers(), timeout=30)
    except Exception as exc:
        logging.warning("Failed to delete transcription job %s: %s", tid, exc)


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
    dst.upload_blob(data, overwrite=True)
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

            if ext in TEXT_EXTS:
                result = extract_text(blob_name, ext)
            else:
                # Speech Batch Transcription は Trusted Service + MI でプレーン URL アクセス
                blob_url = _blob_url(CONTAINER_INPUT, blob_name)
                tid = create_transcription(blob_url, blob_name)
                try:
                    wait_for_transcription(tid)
                    result = fetch_transcription_results(tid, blob_name)
                finally:
                    # 成功/失敗/タイムアウトを問わずジョブを必ず削除
                    delete_transcription_job(tid)

            save_results(blob_name, result)
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
    # エラーマーカーを output に書き、input → processed へ移動して終端化する。
    # これにより:
    #   - キューは正常完了扱いで dequeue され、poison キューに溜まらない
    #   - input から消えるため UI で「処理待ち」に残らない
    #   - output の `_error.json` を UI が ❌ エラー として表示できる
    try:
        save_error(blob_name, last_error)
    except Exception as exc:
        logging.error("Failed to save error marker for %s: %s", blob_name, exc)
    try:
        move_to_processed(blob_name)
    except Exception as exc:
        logging.error("Failed to move failed blob %s to processed: %s", blob_name, exc)
