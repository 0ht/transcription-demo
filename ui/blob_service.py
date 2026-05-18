"""
Blob Storage アクセスのビジネスロジック
Streamlit に依存しない純粋な関数群（テスト容易化）
"""

import json
import os
from datetime import datetime

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
STORAGE_ACCOUNT = os.environ.get("DATA_STORAGE_ACCOUNT_NAME", "")
CONTAINER_INPUT = os.environ.get("CONTAINER_INPUT", "input")
CONTAINER_OUTPUT = os.environ.get("CONTAINER_OUTPUT", "output")
CONTAINER_PROCESSED = os.environ.get("CONTAINER_PROCESSED", "processed")
ACCOUNT_URL = f"https://{STORAGE_ACCOUNT}.blob.core.windows.net"

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".wma"}
VIDEO_EXTS = {".mp4", ".avi", ".mov", ".webm", ".mkv"}

ALL_SUPPORTED_EXTS = AUDIO_EXTS | {".txt", ".md", ".json", ".vtt"}

SPEAKER_COLORS = [
    "#4a9eff", "#ff6b6b", "#51cf66", "#ffd43b",
    "#cc5de8", "#ff922b", "#20c997", "#e599f7",
]

# ---------------------------------------------------------------------------
# Blob Service
# ---------------------------------------------------------------------------
_blob_svc_instance = None


def get_blob_service() -> BlobServiceClient:
    global _blob_svc_instance
    if _blob_svc_instance is None:
        credential = DefaultAzureCredential()
        _blob_svc_instance = BlobServiceClient(ACCOUNT_URL, credential=credential)
    return _blob_svc_instance


def reset_blob_service():
    global _blob_svc_instance
    _blob_svc_instance = None


def upload_to_input(file_name: str, data: bytes) -> str:
    """ファイルを input コンテナにアップロード。アップロード先の Blob 名を返す"""
    svc = get_blob_service()
    blob_client = svc.get_blob_client(CONTAINER_INPUT, file_name)
    blob_client.upload_blob(data, overwrite=True)
    return file_name


def list_input_files() -> list[dict]:
    """input コンテナのファイル一覧（処理待ち）"""
    svc = get_blob_service()
    container = svc.get_container_client(CONTAINER_INPUT)
    results = []
    for blob in container.list_blobs():
        results.append({
            "name": blob.name,
            "size": blob.size,
            "last_modified": blob.last_modified,
        })
    return sorted(results, key=lambda x: x.get("last_modified") or datetime.min, reverse=True)


def list_processed_files() -> list[dict]:
    """processed コンテナのファイル一覧（処理済み原本）"""
    svc = get_blob_service()
    container = svc.get_container_client(CONTAINER_PROCESSED)
    results = []
    for blob in container.list_blobs():
        results.append({
            "name": blob.name,
            "size": blob.size,
            "last_modified": blob.last_modified,
        })
    return sorted(results, key=lambda x: x.get("last_modified") or datetime.min, reverse=True)


# ---------------------------------------------------------------------------
# Business Logic
# ---------------------------------------------------------------------------
def list_transcripts(date_from=None, date_to=None, keyword=""):
    """output コンテナから _transcript.json を列挙"""
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
            except (ValueError, IndexError):
                pass

        if blob_date:
            if date_from and blob_date.date() < date_from:
                continue
            if date_to and blob_date.date() > date_to:
                continue

        if keyword:
            name_match = keyword.lower() in blob.name.lower()
            if not name_match:
                try:
                    data = container.get_blob_client(blob.name).download_blob().readall()
                    transcript = json.loads(data)
                    full_text = " ".join(
                        s.get("text", "") for s in transcript.get("segments", [])
                    )
                    if keyword.lower() not in full_text.lower():
                        continue
                except Exception:
                    continue

        results.append(
            {
                "path": blob.name,
                "date": blob_date,
                "size": blob.size,
                "last_modified": blob.last_modified,
            }
        )

    return sorted(results, key=lambda x: x["path"], reverse=True)


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


def speaker_color(name: str) -> str:
    return SPEAKER_COLORS[hash(name) % len(SPEAKER_COLORS)]


# ---------------------------------------------------------------------------
# Application Insights ログ取得
# ---------------------------------------------------------------------------
LOG_ANALYTICS_WORKSPACE_ID = os.environ.get("LOG_ANALYTICS_WORKSPACE_ID", "")


def query_function_logs(hours: int = 24, limit: int = 100) -> list[dict]:
    """Application Insights / Log Analytics から Functions のログを取得"""
    if not LOG_ANALYTICS_WORKSPACE_ID:
        return []

    try:
        from azure.monitor.query import LogsQueryClient, LogsQueryStatus

        credential = DefaultAzureCredential()
        client = LogsQueryClient(credential)

        query = f"""
        AppTraces
        | where AppRoleName has "func-transcription"
        | where TimeGenerated > ago({hours}h)
        | project TimeGenerated, Message
        | order by TimeGenerated desc
        | take {limit}
        """

        response = client.query_workspace(
            workspace_id=LOG_ANALYTICS_WORKSPACE_ID,
            query=query,
            timespan=None,
        )

        if response.status == LogsQueryStatus.SUCCESS:
            rows = []
            for table in response.tables:
                for row in table.rows:
                    rows.append({
                        "timestamp": str(row[0]),
                        "message": str(row[1] if len(row) > 1 else ""),
                    })
            return rows
    except Exception as e:
        return [{"timestamp": "", "message": f"ログ取得エラー: {e}"}]

    return []


def query_error_logs(hours: int = 24, limit: int = 50) -> list[dict]:
    """エラーレベルのログ + 例外を取得（処理失敗の可視化用）

    Returns: list of {timestamp, severity, blob_name, message}
    """
    if not LOG_ANALYTICS_WORKSPACE_ID:
        return []

    try:
        from azure.monitor.query import LogsQueryClient, LogsQueryStatus

        credential = DefaultAzureCredential()
        client = LogsQueryClient(credential)

        # AppTraces の Error/Warning + AppExceptions を統合
        query = f"""
        let traces = AppTraces
            | where AppRoleName has "func-transcription"
            | where TimeGenerated > ago({hours}h)
            | where SeverityLevel >= 2
            | project TimeGenerated, Severity = case(SeverityLevel == 3, "Error", SeverityLevel == 2, "Warning", "Info"), Message;
        let exc = AppExceptions
            | where AppRoleName has "func-transcription"
            | where TimeGenerated > ago({hours}h)
            | project TimeGenerated, Severity = "Exception", Message = strcat(ExceptionType, ": ", OuterMessage);
        union traces, exc
        | order by TimeGenerated desc
        | take {limit}
        """

        response = client.query_workspace(
            workspace_id=LOG_ANALYTICS_WORKSPACE_ID,
            query=query,
            timespan=None,
        )

        if response.status != LogsQueryStatus.SUCCESS:
            return []

        rows = []
        for table in response.tables:
            for row in table.rows:
                msg = str(row[2] if len(row) > 2 else "")
                rows.append({
                    "timestamp": str(row[0]),
                    "severity": str(row[1]),
                    "blob_name": _extract_blob_name(msg),
                    "message": msg,
                })
        return rows
    except Exception as e:
        return [{"timestamp": "", "severity": "Error", "blob_name": "",
                 "message": f"ログ取得エラー: {e}"}]


def _extract_blob_name(message: str) -> str:
    """ログ message から blob 名らしき文字列を抽出（best-effort）"""
    import re
    # "for <name>" / "Processing <name>" / "Completed: <name>" などのパターン
    patterns = [
        r"for ([^\s:]+\.[a-zA-Z0-9]{2,5})",
        r"Processing ([^\s:]+\.[a-zA-Z0-9]{2,5})",
        r"Completed: ([^\s:]+\.[a-zA-Z0-9]{2,5})",
        r"Skipping[^:]*: ([^\s:]+\.[a-zA-Z0-9]{2,5})",
    ]
    for p in patterns:
        m = re.search(p, message)
        if m:
            return m.group(1)
    return ""


def list_queue_messages() -> list[dict]:
    """Storage Queue のメッセージ数（メイン + poison）"""
    results = []
    try:
        from azure.storage.queue import QueueServiceClient

        credential = DefaultAzureCredential()
        queue_svc = QueueServiceClient(
            account_url=f"https://{STORAGE_ACCOUNT}.queue.core.windows.net",
            credential=credential,
        )
        for q_name in ("blob-events", "blob-events-poison"):
            try:
                queue = queue_svc.get_queue_client(q_name)
                props = queue.get_queue_properties()
                results.append({"queue": q_name, "count": props.approximate_message_count})
            except Exception:
                results.append({"queue": q_name, "count": 0})
    except Exception as e:
        results.append({"queue": "blob-events", "count": f"エラー: {e}"})
    return results


def list_poison_messages(limit: int = 10) -> list[dict]:
    """poison queue のメッセージを peek（再試行尽きで失敗したファイル）"""
    try:
        from azure.storage.queue import QueueServiceClient

        credential = DefaultAzureCredential()
        queue_svc = QueueServiceClient(
            account_url=f"https://{STORAGE_ACCOUNT}.queue.core.windows.net",
            credential=credential,
        )
        queue = queue_svc.get_queue_client("blob-events-poison")
        results = []
        for m in queue.peek_messages(max_messages=limit):
            blob_name = ""
            try:
                body = json.loads(m.content)
                subject = body.get("subject", "")
                if "/blobs/" in subject:
                    blob_name = subject.split("/blobs/", 1)[-1]
            except Exception:
                pass
            results.append({
                "id": m.id,
                "inserted_on": m.inserted_on,
                "blob_name": blob_name,
                "raw": m.content[:300],
            })
        return results
    except Exception:
        return []


def delete_poison_message_by_blob(blob_name: str) -> bool:
    """指定 blob 名に該当する poison メッセージを削除する。

    キュー先頭から最大 32 件を receive（visibility_timeout=30s）して走査し、
    同じ blob 名の重複 poison も含めてすべて削除する。該当しないメッセージは
    その場で可視性をリセットし、他の処理（Functions の另処理等）をブロックしないようにする。

    Returns:
        該当メッセージを 1 件以上削除した場合 True。
    """
    from azure.storage.queue import QueueServiceClient

    credential = DefaultAzureCredential()
    queue_svc = QueueServiceClient(
        account_url=f"https://{STORAGE_ACCOUNT}.queue.core.windows.net",
        credential=credential,
    )
    queue = queue_svc.get_queue_client("blob-events-poison")

    deleted_count = 0
    for m in queue.receive_messages(max_messages=32, visibility_timeout=30):
        target = ""
        try:
            body = json.loads(m.content)
            subject = body.get("subject", "")
            if "/blobs/" in subject:
                target = subject.split("/blobs/", 1)[-1]
        except Exception:
            pass
        if target == blob_name:
            try:
                queue.delete_message(m)
                deleted_count += 1
            except Exception:
                # 他クライアントが先に取得・削除した場合の pop_receipt 失効は無視
                pass
        else:
            # 該当しないメッセージは即座に再可視化（Functions の処理をブロックしない）
            try:
                queue.update_message(
                    m.id, m.pop_receipt, visibility_timeout=0, content=m.content,
                )
            except Exception:
                pass
    return deleted_count > 0


def delete_input_blob(blob_name: str) -> None:
    """input コンテナから blob を削除"""
    svc = get_blob_service()
    svc.get_blob_client(CONTAINER_INPUT, blob_name).delete_blob(delete_snapshots="include")


def delete_output_transcript(transcript_path: str) -> list[str]:
    """output コンテナから transcript JSON とその関連ファイルを削除。
    削除した blob 名のリストを返す。BlobNotFound は無視。
    """
    svc = get_blob_service()
    container = svc.get_container_client(CONTAINER_OUTPUT)
    deleted = []
    # 同じディレクトリの transcript.json / transcript.txt / 元メディアを削除
    base = transcript_path.rsplit("/", 1)[0] if "/" in transcript_path else ""
    candidates = [transcript_path, transcript_path.replace("_transcript.json", "_transcript.txt")]
    # 同ディレクトリ配下のすべての blob を対象にする
    if base:
        for blob in container.list_blobs(name_starts_with=base + "/"):
            if blob.name not in candidates:
                candidates.append(blob.name)
    for name in candidates:
        try:
            container.get_blob_client(name).delete_blob(delete_snapshots="include")
            deleted.append(name)
        except Exception as e:
            if "BlobNotFound" not in str(e):
                raise
    return deleted


def clear_poison_queue() -> int:
    """poison queue のメッセージをすべて削除し、削除した件数を返す。

    `clear_messages()` は件数を返さないため、事前にキュープロパティの
    approximate_message_count を取得してから削除する。同時にメッセージが
    追加されるケースを考慮し、返却値は「削除前の推定件数」として取り扱う。
    """
    from azure.storage.queue import QueueServiceClient

    credential = DefaultAzureCredential()
    queue_svc = QueueServiceClient(
        account_url=f"https://{STORAGE_ACCOUNT}.queue.core.windows.net",
        credential=credential,
    )
    queue = queue_svc.get_queue_client("blob-events-poison")
    try:
        approx_count = queue.get_queue_properties().approximate_message_count or 0
    except Exception:
        approx_count = 0
    queue.clear_messages()
    return int(approx_count)
