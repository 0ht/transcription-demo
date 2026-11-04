import json
import importlib.util
import io
import struct
import sys
import wave
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError
from fastapi import HTTPException
from fastapi.testclient import TestClient

import function_app


config_stub = ModuleType("modules.config")
for name, value in {
    "BLOB_CONNECTION_STRING": "",
    "STORAGE_ACCOUNT_NAME": "teststorage",
    "UPLOAD_CONTAINER_NAME": "upload",
    "CHUNK_CONTAINER_NAME": "chunk",
    "ADI_ENDPOINT": "https://example.invalid",
    "ADI_KEY": "",
    "ADI_MODEL": "prebuilt-layout",
}.items():
    setattr(config_stub, name, value)
with patch.dict(sys.modules, {"modules.config": config_stub}):
    from modules import blob_document_service, ocr, processing_status


class FakeBlob:
    def __init__(self, name):
        self.name = name
        self.data = None
        self.metadata = {}
        self.version = 0
        self.transitions = []
        self.last_modified = datetime.now(timezone.utc)

    def exists(self):
        return self.data is not None

    def get_blob_properties(self):
        if not self.exists():
            raise ResourceNotFoundError("missing blob")
        return SimpleNamespace(
            metadata=dict(self.metadata), etag=str(self.version), size=len(self.data)
        )

    def upload_blob(self, data, *, overwrite=False, metadata=None):
        self.data = data
        self.metadata = dict(metadata or {})
        self.version += 1
        self.last_modified = datetime.now(timezone.utc)

    def set_blob_metadata(self, metadata, **kwargs):
        if kwargs.get("etag", str(self.version)) != str(self.version):
            raise ResourceModifiedError("etag mismatch")
        self.metadata = dict(metadata)
        self.transitions.append(metadata["processing_status"])
        self.version += 1
        self.last_modified = datetime.now(timezone.utc)

    def download_blob(self):
        self.get_blob_properties()
        return SimpleNamespace(readall=lambda: self.data)

    def delete_blob(self):
        self.data = None


class FakeContainer:
    def __init__(self):
        self.blobs = {}

    def get_blob_client(self, name):
        return self.blobs.setdefault(name, FakeBlob(name))

    def upload_blob(self, name, data, **kwargs):
        self.get_blob_client(name).upload_blob(data, **kwargs)

    def download_blob(self, name):
        return self.get_blob_client(name).download_blob()

    def list_blobs(self, **kwargs):
        return [
            SimpleNamespace(
                name=blob.name, size=len(blob.data),
                last_modified=blob.last_modified, metadata=dict(blob.metadata),
            )
            for blob in self.blobs.values() if blob.exists()
        ]


class FakeService:
    def __init__(self):
        self.containers = {}

    def get_container_client(self, name):
        return self.containers.setdefault(name, FakeContainer())

    def get_blob_client(self, container, name):
        return self.get_container_client(container).get_blob_client(name)


@pytest.fixture
def storage(monkeypatch):
    service = FakeService()
    monkeypatch.setattr(blob_document_service, "get_blob_service", lambda: service)
    monkeypatch.setattr(ocr, "get_blob_service_client", lambda: service)
    monkeypatch.setattr(function_app, "_blob_svc", lambda: service)
    return service


@pytest.mark.unit
@pytest.mark.parametrize("status", ["queued", "processing", "completed", "failed", "unknown"])
def test_status_contract_supports_japanese_and_ascii_metadata(status):
    metadata = processing_status.status_metadata(
        status, error="読み取りに失敗しました", result_blob_name="資料_ocr_20261103_120000.md"
    )
    assert all(value.isascii() for value in metadata.values())
    decoded = processing_status.read_status(metadata)
    assert decoded["status"] == status
    assert decoded["error"] == "読み取りに失敗しました"
    assert decoded["result_blob_name"] == "資料_ocr_20261103_120000.md"
    assert decoded["status_updated_at"]


@pytest.mark.unit
def test_invalid_metadata_is_not_silently_accepted():
    with pytest.raises(ValueError):
        processing_status.read_status({"processing_status": "invalid"})
    with pytest.raises(ValueError):
        processing_status.read_status({"processing_error": "not base64!"})


@pytest.mark.unit
def test_audio_upload_starts_queued_and_reupload_resets_failure(storage):
    name = blob_document_service.upload_source_file("meeting.wav", b"audio")
    blob = storage.get_blob_client("input", name)
    processing_status.set_status(blob, "failed", error="old error")
    assert blob_document_service.upload_source_file("meeting.wav", b"new audio") == name
    items = blob_document_service.list_source_uploads()
    assert items[0]["status"] == "queued"
    assert items[0]["error"] == ""


@pytest.mark.unit
def test_unsupported_video_does_not_remain_queued(storage):
    blob_document_service.upload_source_file("video.mp4", b"video")
    item = blob_document_service.list_source_uploads()[0]
    assert item["status"] == "failed"
    assert "動画" in item["error"]


def queue_message(name):
    payload = {"subject": f"/blobServices/default/containers/input/blobs/{name}"}
    return SimpleNamespace(get_body=lambda: json.dumps(payload).encode())


@pytest.mark.unit
def test_audio_processing_status_survives_move_and_can_open_result(storage, monkeypatch):
    name = blob_document_service.upload_source_file("meeting.wav", b"audio")
    source = storage.get_blob_client("input", name)
    monkeypatch.setattr(function_app, "transcribe_audio", lambda _: {
        "sourceFile": name, "segments": [{"speaker": "A", "text": "テスト"}],
    })
    function_app.blob_transcribe(queue_message(name))
    assert source.transitions == ["processing", "completed"]
    assert not source.exists()
    item = blob_document_service.list_source_uploads()[0]
    assert item["status"] == "completed"
    assert item["result_blob_name"].endswith("_transcript.json")
    assert storage.get_blob_client("output", item["result_blob_name"]).exists()


@pytest.mark.unit
def test_audio_nonretryable_failure_is_persistent(storage, monkeypatch):
    name = blob_document_service.upload_source_file("bad.wav", b"audio")
    source = storage.get_blob_client("input", name)
    transcribe = Mock(side_effect=function_app.InvalidAudioError("音声形式が不正です"))
    monkeypatch.setattr(function_app, "transcribe_audio", transcribe)
    function_app.blob_transcribe(queue_message(name))
    assert transcribe.call_count == 1
    assert source.transitions == ["processing", "failed"]
    item = blob_document_service.list_source_uploads()[0]
    assert item["status"] == "failed"
    assert item["error"] == "音声形式が不正です"
    assert not item["result_blob_name"]


@pytest.mark.unit
def test_audio_transient_failure_retries_then_completes(storage, monkeypatch):
    name = blob_document_service.upload_source_file("retry.wav", b"audio")
    transcribe = Mock(side_effect=[
        RuntimeError("temporary"), {"sourceFile": name, "segments": []},
    ])
    monkeypatch.setattr(function_app, "transcribe_audio", transcribe)
    monkeypatch.setattr(function_app.time, "sleep", lambda _: None)
    function_app.blob_transcribe(queue_message(name))
    assert transcribe.call_count == 2
    assert blob_document_service.list_source_uploads()[0]["status"] == "completed"


@pytest.mark.unit
def test_failure_marker_storage_error_is_not_swallowed(storage, monkeypatch):
    name = blob_document_service.upload_source_file("bad.wav", b"audio")
    monkeypatch.setattr(function_app, "transcribe_audio",
                        Mock(side_effect=function_app.InvalidAudioError("bad audio")))
    monkeypatch.setattr(function_app, "save_error", Mock(side_effect=RuntimeError("storage unavailable")))
    with pytest.raises(RuntimeError, match="storage unavailable"):
        function_app.blob_transcribe(queue_message(name))
    assert blob_document_service.list_source_uploads()[0]["status"] == "failed"


@pytest.mark.unit
def test_legacy_audio_result_and_error_markers(storage):
    for name in ("ok.wav", "bad.wav", "unknown.wav"):
        storage.get_blob_client("processed", f"2026/11/03/{name}").upload_blob(b"audio")
    storage.get_blob_client("output", "2026/11/03/ok_transcript.json").upload_blob(b"{}")
    storage.get_blob_client("output", "2026/11/03/bad_error.json").upload_blob(
        json.dumps({"error": "old failure"}).encode()
    )
    items = {item["name"]: item for item in blob_document_service.list_source_uploads()}
    assert items["ok.wav"]["status"] == "completed"
    assert items["bad.wav"]["status"] == "failed"
    assert items["bad.wav"]["error"] == "old failure"
    assert items["unknown.wav"]["status"] == "unknown"


@pytest.mark.unit
def test_ocr_processing_and_original_source_association(storage, monkeypatch):
    name = ocr.upload_file_to_ocr_input("資料.pdf", b"%PDF-sample")
    source = storage.get_blob_client("upload", name)
    assert ocr.list_uploaded_files()[0]["status"] == "queued"

    def analyze(**kwargs):
        assert ocr.list_uploaded_files()[0]["status"] == "processing"
        assert kwargs["body"].read() == b"%PDF-sample"
        return SimpleNamespace(result=lambda: SimpleNamespace(content="# OCR結果\nテスト"))

    monkeypatch.setattr(ocr, "get_adi_client", lambda: SimpleNamespace(begin_analyze_document=analyze))
    result = ocr.process_ocr_from_blob(name)
    assert source.transitions == ["processing", "completed"]
    item = ocr.list_uploaded_files()[0]
    assert item["status"] == "completed"
    assert item["result_blob_name"] == result
    assert ocr.get_ocr_result_source(result) == name
    assert ocr.list_ocr_results()[0]["source_blob_name"] == name
    assert ocr.load_ocr_source(name) == b"%PDF-sample"
    assert ocr.load_ocr_result_text(result) == "# OCR結果\nテスト"


@pytest.mark.unit
def test_ocr_failure_can_be_retried(storage, monkeypatch):
    name = ocr.upload_file_to_ocr_input("image.png", b"png")
    client = Mock()
    client.begin_analyze_document.side_effect = RuntimeError("読み取り失敗")
    monkeypatch.setattr(ocr, "get_adi_client", lambda: client)
    with pytest.raises(RuntimeError, match="読み取り失敗"):
        ocr.process_ocr_from_blob(name)
    assert ocr.list_uploaded_files()[0]["status"] == "failed"
    assert ocr.list_uploaded_files()[0]["error"] == "読み取り失敗"
    client.begin_analyze_document.side_effect = None
    client.begin_analyze_document.return_value.result.return_value.content = "成功"
    ocr.process_ocr_from_blob(name)
    assert ocr.list_uploaded_files()[0]["status"] == "completed"
    assert ocr.list_uploaded_files()[0]["error"] == ""


@pytest.mark.unit
def test_ocr_client_initialization_failure_is_recorded(storage, monkeypatch):
    name = ocr.upload_file_to_ocr_input("sample.pdf", b"pdf")
    monkeypatch.setattr(ocr, "get_adi_client", Mock(side_effect=RuntimeError("invalid endpoint")))
    with pytest.raises(RuntimeError, match="invalid endpoint"):
        ocr.process_ocr_from_blob(name)
    assert ocr.list_uploaded_files()[0]["status"] == "failed"


@pytest.mark.unit
def test_ocr_reupload_preserves_original_and_previous_result(storage):
    original = ocr.upload_file_to_ocr_input("sample.pdf", b"original")
    result = "sample_ocr_20261103_120000.md"
    storage.get_blob_client("chunk", result).upload_blob(
        b"original text", metadata={
            "source_blob_name": processing_status.encode_metadata_text(original)
        }
    )
    reuploaded = ocr.upload_file_to_ocr_input("sample.pdf", b"replacement")
    assert reuploaded != original
    assert reuploaded.endswith(".pdf")
    assert ocr.load_ocr_source(ocr.get_ocr_result_source(result)) == b"original"
    assert ocr.load_ocr_source(reuploaded) == b"replacement"


@pytest.mark.unit
def test_duplicate_ocr_is_rejected_before_calling_service(storage, monkeypatch):
    name = ocr.upload_file_to_ocr_input("image.png", b"png")
    processing_status.set_status(storage.get_blob_client("upload", name), "processing")
    client = Mock()
    monkeypatch.setattr(ocr, "get_adi_client", lambda: client)
    with pytest.raises(ResourceModifiedError):
        ocr.process_ocr_from_blob(name)
    client.begin_analyze_document.assert_not_called()


@pytest.mark.unit
def test_legacy_ocr_mapping_does_not_guess_between_same_stems(storage):
    storage.get_blob_client("upload", "sample.pdf").upload_blob(b"pdf")
    result = "sample_ocr_20261103_120000.md"
    storage.get_blob_client("chunk", result).upload_blob(b"text")
    assert ocr.get_ocr_result_source(result) == "sample.pdf"
    assert ocr.list_uploaded_files()[0]["status"] == "completed"
    storage.get_blob_client("upload", "sample.png").upload_blob(b"png")
    assert ocr.get_ocr_result_source(result) == ""
    assert all(item["status"] == "unknown" for item in ocr.list_uploaded_files())


@pytest.mark.unit
def test_missing_original_is_reported(storage):
    with pytest.raises(ResourceNotFoundError):
        ocr.load_ocr_source("missing.pdf")


@pytest.fixture
def api_client(monkeypatch, storage):
    stubs = {
        "modules.ocr": ocr,
        "modules.blob_document_service": blob_document_service,
        "modules.processing_status": processing_status,
    }
    imports = {
        "realtime_session": ["RealtimeMeetingSession"],
        "meeting_manager": ["MeetingManager"],
        "storage": ["load_history", "save_history"],
        "query_planner": ["plan_meeting_topic"],
        "ai_search": ["search_documents"],
        "llm": ["generate_meeting_feedback", "generate_chat_response"],
        "prompt_store": ["list_prompt_sets", "save_prompt_set", "get_prompt_set", "delete_prompt_set"],
        "chat_retrieval": ["decide_retrieval", "merge_docs"],
        "document_rag_service": ["rag_answer_for_document"],
        "auth": [
            "get_user_context", "get_user_context_from_websocket", "require_admin",
            "require_user_or_admin", "resolve_prompt_and_model_for_request",
        ],
    }
    for module_name, names in imports.items():
        stub = ModuleType(f"modules.{module_name}")
        for name in names:
            setattr(stub, name, Mock())
        stubs[stub.__name__] = stub
    stubs["modules.storage"].load_history = lambda: []
    config = ModuleType("modules.config")
    config.get_default_llm_model = lambda: "test-model"
    config.list_available_llm_models = lambda: ["test-model"]
    stubs[config.__name__] = config
    ui_dir = Path(__file__).resolve().parents[1] / "ui"
    monkeypatch.chdir(ui_dir)
    spec = importlib.util.spec_from_file_location("upload_status_api", ui_dir / "main.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    with TestClient(module.app) as client:
        yield client, module


@pytest.mark.unit
def test_audio_status_api_returns_persistent_fields(api_client):
    client, _ = api_client
    response = client.post("/api/documents/upload", files={"files": ("meeting.wav", b"audio")})
    assert response.status_code == 200
    assert response.json()["items"][0]["processing_status"] == "queued"
    response = client.get("/api/documents/uploads")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["status"] == "queued"
    assert item["last_modified"]
    assert item["status_updated_at"]


@pytest.mark.unit
@pytest.mark.parametrize("filename", ["audio-04-minutes.wav", "会議 #1.wav"])
def test_uploaded_audio_can_be_loaded_from_document_detail(api_client, storage, monkeypatch, filename):
    client, _ = api_client
    data = io.BytesIO()
    with wave.open(data, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(struct.pack("<h", 1000) * 1600)
    original = data.getvalue()
    upload = client.post("/api/documents/upload", files={"files": (filename, original)})
    name = upload.json()["items"][0]["uploaded"]
    monkeypatch.setattr(function_app, "transcribe_audio", lambda _: {
        "sourceFile": name, "segments": [{"speaker": "A", "text": "テスト"}],
    })
    function_app.blob_transcribe(queue_message(name))
    result_path = blob_document_service.list_source_uploads()[0]["result_blob_name"]
    detail_response = client.get("/api/documents/detail", params={"transcript_path": result_path})
    assert detail_response.status_code == 200
    detail = detail_response.json()
    expected_path = next(iter(storage.get_container_client("processed").list_blobs())).name
    assert detail["media_path"] == expected_path
    media = client.get("/api/documents/media", params={"media_path": detail["media_path"]})
    assert media.status_code == 200
    assert media.headers["content-type"] == "audio/wav"
    assert media.content == original
    assert int(media.headers["content-length"]) == len(original)
    with wave.open(io.BytesIO(media.content), "rb") as audio:
        assert audio.getnframes() == 1600
        assert audio.getframerate() == 16000


@pytest.mark.unit
def test_missing_media_returns_not_found(api_client):
    client, _ = api_client
    response = client.get("/api/documents/media", params={"media_path": "2026/11/03/missing.wav"})
    assert response.status_code == 404
    assert "見つかりません" in response.json()["detail"]


@pytest.mark.unit
def test_media_storage_failures_remain_explicit_errors(api_client, monkeypatch):
    client, module = api_client
    monkeypatch.setattr(module, "load_media", Mock(side_effect=RuntimeError("storage unavailable")))
    response = client.get("/api/documents/media", params={"media_path": "2026/11/03/meeting.wav"})
    assert response.status_code == 500
    assert "storage unavailable" in response.json()["detail"]


@pytest.mark.unit
def test_ocr_api_returns_source_and_preview_bytes(api_client, storage):
    client, _ = api_client
    response = client.post("/api/ocr/upload", files={"file": ("sample.pdf", b"%PDF-sample")})
    assert response.status_code == 200
    assert response.json()["processing_status"] == "queued"
    storage.get_blob_client("chunk", "sample_ocr_20261103_120000.md").upload_blob(
        b"extracted text", metadata={
            "source_blob_name": processing_status.encode_metadata_text("sample.pdf")
        }
    )
    detail = client.get("/api/ocr/result/sample_ocr_20261103_120000.md").json()
    assert detail["text"] == "extracted text"
    assert detail["source_blob_name"] == "sample.pdf"
    original = client.get("/api/ocr/source/sample.pdf")
    assert original.status_code == 200
    assert original.content == b"%PDF-sample"
    assert original.headers["content-type"] == "application/pdf"
    assert original.headers["x-content-type-options"] == "nosniff"


@pytest.mark.unit
def test_ocr_api_rejects_duplicate_run_and_reports_missing_original(api_client, storage):
    client, _ = api_client
    ocr.upload_file_to_ocr_input("sample.png", b"png")
    processing_status.set_status(storage.get_blob_client("upload", "sample.png"), "processing")
    assert client.post("/api/ocr/run", json={"blob_name": "sample.png"}).status_code == 409
    assert client.get("/api/ocr/source/missing.pdf").status_code == 404
    assert client.get("/api/ocr/source/page.html").status_code == 400


@pytest.mark.unit
@pytest.mark.parametrize("path", [
    "/api/documents/uploads", "/api/ocr/source/sample.pdf",
    "/api/documents/media?media_path=2026%2F11%2F03%2Fmeeting.wav",
])
def test_new_apis_require_admin(api_client, path):
    client, module = api_client
    module.require_admin.side_effect = HTTPException(status_code=403, detail="Forbidden")
    assert client.get(path).status_code == 403
