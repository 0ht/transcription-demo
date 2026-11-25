import base64
from datetime import datetime, timezone

from azure.core import MatchConditions


STATUSES = {"queued", "processing", "completed", "failed", "unknown"}


def encode_metadata_text(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def decode_metadata_text(text: str) -> str:
    return base64.b64decode(text, validate=True).decode("utf-8") if text else ""


def status_metadata(status: str, *, error: str = "", result_blob_name: str = "") -> dict:
    if status not in STATUSES:
        raise ValueError(f"Invalid processing status: {status}")
    return {
        "processing_status": status,
        "processing_updated_at": datetime.now(timezone.utc).isoformat(),
        "processing_error": encode_metadata_text(error[:500]),
        "processing_result": encode_metadata_text(result_blob_name),
    }


def read_status(metadata: dict, default: str = "unknown") -> dict:
    status = metadata.get("processing_status", default)
    if status not in STATUSES:
        raise ValueError(f"Invalid stored processing status: {status}")
    return {
        "status": status,
        "status_updated_at": metadata.get("processing_updated_at"),
        "error": decode_metadata_text(metadata.get("processing_error", "")),
        "result_blob_name": decode_metadata_text(metadata.get("processing_result", "")),
    }


def set_status(blob, status: str, *, error: str = "", result_blob_name: str = "", properties=None) -> None:
    properties = properties if properties is not None else blob.get_blob_properties()
    metadata = {
        **(properties.metadata or {}),
        **status_metadata(status, error=error, result_blob_name=result_blob_name),
    }
    blob.set_blob_metadata(
        metadata, etag=properties.etag, match_condition=MatchConditions.IfNotModified
    )
