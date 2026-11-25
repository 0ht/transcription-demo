import json
import asyncio
import hashlib
import importlib.util
import io
import struct
import sys
import wave
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import quote

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError
from fastapi import HTTPException, UploadFile
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
        self.download_calls = []
        self.last_modified = datetime.now(timezone.utc)

    def exists(self):
        return self.data is not None

    def get_blob_properties(self):
        if not self.exists():
            raise ResourceNotFoundError("missing blob")
        return SimpleNamespace(
            metadata=dict(self.metadata), etag=str(self.version), size=len(self.data),
            last_modified=self.last_modified,
        )

    def upload_blob(self, data, *, overwrite=False, metadata=None):
        self.data = data.read() if hasattr(data, "read") else data
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

    def download_blob(self, *, offset=None, length=None, etag=None, match_condition=None,
                      max_concurrency=None):
        self.get_blob_properties()
        if etag is not None and etag != str(self.version):
            raise ResourceModifiedError("etag mismatch")
        self.download_calls.append({"offset": offset, "length": length})
        start = offset or 0
        data = self.data[start:start + length] if length is not None else self.data[start:]
        return SimpleNamespace(
            readall=lambda: data,
            readinto=lambda stream: stream.write(data),
            chunks=lambda: (
                data[start:start + blob_document_service.MEDIA_CHUNK_BYTES]
                for start in range(0, len(data), blob_document_service.MEDIA_CHUNK_BYTES)
            ),
        )

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

    def get_blob_client(self, container, name, **kwargs):
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
@pytest.mark.parametrize("filename", ["meeting.wav", "meeting.mp4", "meeting.MP4"])
def test_audio_upload_starts_queued_and_reupload_resets_failure(storage, filename):
    name = blob_document_service.upload_source_file(filename, b"audio")
    blob = storage.get_blob_client("input", name)
    processing_status.set_status(blob, "failed", error="old error")
    assert blob_document_service.upload_source_file(filename, b"new audio") == name
    items = blob_document_service.list_source_uploads()
    assert items[0]["status"] == "queued"
    assert items[0]["error"] == ""


@pytest.mark.unit
def test_unsupported_format_does_not_remain_queued(storage):
    blob_document_service.upload_source_file("archive.zip", b"archive")
    item = blob_document_service.list_source_uploads()[0]
    assert item["status"] == "failed"
    assert "非対応形式" in item["error"]


@pytest.mark.unit
def test_ui_and_functions_support_the_same_source_formats():
    assert blob_document_service.SOURCE_EXTS == function_app.ALL_SUPPORTED
    assert blob_document_service.MAX_UPLOAD_BYTES == function_app.MAX_SOURCE_BYTES == 500_000_000


@pytest.mark.unit
def test_event_grid_forwards_every_supported_source_format():
    template = json.loads(
        (Path(__file__).resolve().parents[1] / "infra" / "main.json").read_text(encoding="utf-8")
    )
    module = next(resource for resource in template["resources"] if resource["name"] == "eventgrid")
    subscription = next(
        resource for resource in module["properties"]["template"]["resources"]
        if resource["type"] == "Microsoft.EventGrid/systemTopics/eventSubscriptions"
    )
    event_filter = subscription["properties"]["filter"]
    assert event_filter["subjectBeginsWith"] == "/blobServices/default/containers/input/"
    assert event_filter["includedEventTypes"] == ["Microsoft.Storage.BlobCreated"]
    extension_filter, = event_filter["advancedFilters"]
    assert extension_filter["key"] == "subject"
    assert extension_filter["operatorType"] == "StringEndsWith"
    assert set(extension_filter["values"]) == function_app.ALL_SUPPORTED


def queue_message(name):
    payload = {"subject": f"/blobServices/default/containers/input/blobs/{name}"}
    return SimpleNamespace(get_body=lambda: json.dumps(payload).encode())


@pytest.mark.unit
@pytest.mark.parametrize("filename", ["meeting.wav", "meeting.mp4", "meeting.MP4",
                                      "meeting.mov", "meeting.mkv", "meeting.webm", "meeting.avi"])
def test_audio_processing_status_survives_move_and_can_open_result(storage, monkeypatch, filename):
    name = blob_document_service.upload_source_file(filename, b"audio")
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
@pytest.mark.parametrize("filename", ["bad.wav", "bad.mp4"])
def test_audio_nonretryable_failure_is_persistent(storage, monkeypatch, filename):
    name = blob_document_service.upload_source_file(filename, b"audio")
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
@pytest.mark.parametrize("size", [499_999_999, 500_000_000, 500_000_001])
def test_upload_size_boundary_streams_accepted_files(api_client, monkeypatch, size):
    _, module = api_client
    stream = io.BytesIO(b"audio")
    file = UploadFile(stream, filename="meeting.mp4", size=size)
    upload = Mock(return_value="2026/11/06/meeting.mp4")
    monkeypatch.setattr(module, "upload_source_file", upload)
    if size > 500_000_000:
        with pytest.raises(HTTPException) as error:
            asyncio.run(module.api_document_upload(Mock(), files=[file]))
        assert error.value.status_code == 413
        assert "500 MB" in error.value.detail
        upload.assert_not_called()
    else:
        result = asyncio.run(module.api_document_upload(Mock(), files=[file]))
        assert result["count"] == 1
        upload.assert_called_once_with("meeting.mp4", stream)
        assert stream.tell() == 0


@pytest.mark.unit
def test_oversized_batch_is_rejected_before_uploading_any_file(api_client, monkeypatch):
    _, module = api_client
    files = [
        UploadFile(io.BytesIO(b"audio"), filename="small.mp4", size=5),
        UploadFile(io.BytesIO(b"audio"), filename="large.mp4", size=500_000_001),
    ]
    upload = Mock()
    monkeypatch.setattr(module, "upload_source_file", upload)
    with pytest.raises(HTTPException) as error:
        asyncio.run(module.api_document_upload(Mock(), files=files))
    assert error.value.status_code == 413
    upload.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("size", [None, 5])
def test_upload_stream_is_rewound_before_storage(api_client, monkeypatch, size):
    _, module = api_client
    stream = io.BytesIO(b"audio")
    stream.seek(3)
    file = UploadFile(stream, filename="meeting.wav", size=size)
    upload = Mock(return_value="2026/11/06/meeting.wav")
    monkeypatch.setattr(module, "upload_source_file", upload)
    result = asyncio.run(module.api_document_upload(Mock(), files=[file]))
    assert result["count"] == 1
    assert stream.tell() == 0
    upload.assert_called_once_with("meeting.wav", stream)


@pytest.mark.unit
@pytest.mark.parametrize("size", [0, None])
def test_empty_uploads_still_return_bad_request(api_client, monkeypatch, size):
    _, module = api_client
    upload = Mock()
    monkeypatch.setattr(module, "upload_source_file", upload)
    file = UploadFile(io.BytesIO(), filename="empty.mp4", size=size)
    with pytest.raises(HTTPException) as error:
        asyncio.run(module.api_document_upload(Mock(), files=[file]))
    assert error.value.status_code == 400
    upload.assert_not_called()


@pytest.mark.unit
def test_document_page_exposes_size_limit_and_accessible_progress_bar(api_client):
    client, _ = api_client
    response = client.get("/documents")
    assert response.status_code == 200
    assert 'data-max-file-bytes="500000000"' in response.text
    assert 'id="uploadProgress"' in response.text
    assert 'aria-label="ファイルの送信進捗"' in response.text
    assert "500 MB以下" in response.text
    static_dir = Path(__file__).resolve().parents[1] / "ui" / "static"
    for filename in ("documents.js", "style.css"):
        version = hashlib.sha256((static_dir / filename).read_bytes()).hexdigest()
        assert f"/static/{filename}?v={version}" in response.text


@pytest.mark.unit
def test_mp4_upload_transcribes_audio_and_preserves_original_video(
    api_client, storage, monkeypatch, sample_mp4
):
    client, _ = api_client
    original = sample_mp4.read_bytes()
    upload = client.post("/api/documents/upload",
                         files={"files": ("meeting.mp4", original, "video/mp4")})
    assert upload.status_code == 200
    assert upload.json()["items"][0]["processing_status"] == "queued"
    name = upload.json()["items"][0]["uploaded"]
    source = storage.get_blob_client("input", name)
    monkeypatch.setattr(function_app, "_speech_headers", lambda: {})
    response = Mock()
    response.json.return_value = {
        "durationMilliseconds": 200,
        "phrases": [{"speaker": 0, "text": "test video", "offsetMilliseconds": 0,
                     "durationMilliseconds": 200}],
    }

    def post(url, *, params, headers, data, timeout):
        assert data.fields["audio"][0] == "meeting.mp3"
        encoded = data.fields["audio"][1].read()
        assert encoded and encoded != original
        return response

    monkeypatch.setattr(function_app.requests, "post", post)
    function_app.blob_transcribe(queue_message(name))
    assert source.transitions == ["processing", "completed"]
    assert not source.exists()
    item = client.get("/api/documents/uploads").json()["items"][0]
    assert item["status"] == "completed"
    assert not item["error"]
    detail = client.get("/api/documents/detail",
                        params={"transcript_path": item["result_blob_name"]}).json()
    assert detail["source_file"] == name
    assert detail["is_video"] is True
    assert detail["is_audio"] is False
    assert detail["segments"][0]["text"] == "test video"
    media = client.get("/api/documents/media", params={"media_path": detail["media_path"]})
    assert media.status_code == 200
    assert media.headers["content-type"] == "video/mp4"
    assert media.content == original


@pytest.mark.unit
def test_corrupt_mp4_failure_is_persisted_without_calling_speech(storage, monkeypatch):
    name = blob_document_service.upload_source_file("broken.mp4", b"not a video")
    post = Mock()
    monkeypatch.setattr(function_app.requests, "post", post)
    function_app.blob_transcribe(queue_message(name))
    post.assert_not_called()
    item = blob_document_service.list_source_uploads()[0]
    assert item["status"] == "failed"
    assert "Video audio extraction failed" in item["error"]
    assert not item["result_blob_name"]
    assert storage.get_blob_client("processed", item["path"]).data == b"not a video"


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
    monkeypatch.setattr(module, "get_media_blob", Mock(side_effect=RuntimeError("storage unavailable")))
    response = client.get("/api/documents/media", params={"media_path": "2026/11/03/meeting.wav"})
    assert response.status_code == 500
    assert "storage unavailable" in response.json()["detail"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("header", "expected", "content_range", "offset"),
    [
        ("bytes=0-3", b"0123", "bytes 0-3/10", 0),
        ("bytes=6-", b"6789", "bytes 6-9/10", 6),
        ("bytes=-4", b"6789", "bytes 6-9/10", 6),
        ("bytes=7-99", b"789", "bytes 7-9/10", 7),
        ("bytes=-99", b"0123456789", "bytes 0-9/10", 0),
        ("bytes=0-9", b"0123456789", "bytes 0-9/10", 0),
    ],
)
def test_media_byte_ranges_return_only_requested_bytes(
    api_client, storage, header, expected, content_range, offset
):
    client, _ = api_client
    name = "2026/11/06/video.mp4"
    blob = storage.get_blob_client("processed", name)
    blob.upload_blob(b"0123456789")
    response = client.get("/api/documents/media", params={"media_path": name},
                          headers={"Range": header})
    assert response.status_code == 206
    assert response.content == expected
    assert response.headers["content-range"] == content_range
    assert response.headers["content-length"] == str(len(expected))
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-type"] == "video/mp4"
    assert blob.download_calls == [{"offset": offset, "length": len(expected)}]


@pytest.mark.unit
@pytest.mark.parametrize("header", [
    "bytes=10-", "bytes=100-200", "bytes=5-4", "bytes=-0", "bytes=-",
    "bytes=a-b", "bytes=0--1",
])
def test_invalid_media_ranges_return_416_without_download(api_client, storage, header):
    client, _ = api_client
    name = "video.mp4"
    blob = storage.get_blob_client("processed", name)
    blob.upload_blob(b"0123456789")
    response = client.get("/api/documents/media", params={"media_path": name},
                          headers={"Range": header})
    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */10"
    assert response.headers["accept-ranges"] == "bytes"
    assert not blob.download_calls


@pytest.mark.unit
@pytest.mark.parametrize("header", ["items=0-1", "bytes=0-1,4-5"])
def test_unsupported_range_requests_fall_back_to_full_stream(api_client, storage, header):
    client, _ = api_client
    storage.get_blob_client("processed", "video.mp4").upload_blob(b"0123456789")
    response = client.get("/api/documents/media", params={"media_path": "video.mp4"},
                          headers={"Range": header})
    assert response.status_code == 200
    assert response.content == b"0123456789"
    assert "content-range" not in response.headers


@pytest.mark.unit
def test_media_head_returns_metadata_without_downloading_body(api_client, storage):
    client, _ = api_client
    name = "video.mp4"
    blob = storage.get_blob_client("processed", name)
    blob.upload_blob(b"0123456789")
    blob.last_modified = datetime.fromisoformat("2026-11-06T13:00:00+09:00")
    response = client.head("/api/documents/media", params={"media_path": name},
                           headers={"Range": "bytes=0-3"})
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == "10"
    assert response.headers["accept-ranges"] == "bytes"
    assert "etag" in response.headers
    assert response.headers["last-modified"] == "Fri, 06 Nov 2026 04:00:00 GMT"
    assert not blob.download_calls


@pytest.mark.unit
@pytest.mark.parametrize("validator", ["etag", "last-modified", "stale"])
def test_if_range_only_honors_matching_media_version(api_client, storage, validator):
    client, _ = api_client
    name = "video.mp4"
    storage.get_blob_client("processed", name).upload_blob(b"0123456789")
    metadata = client.head("/api/documents/media", params={"media_path": name})
    value = metadata.headers[validator] if validator != "stale" else '"previous-version"'
    response = client.get("/api/documents/media", params={"media_path": name},
                          headers={"Range": "bytes=0-3", "If-Range": value})
    assert response.status_code == (200 if validator == "stale" else 206)
    assert response.content == (b"0123456789" if validator == "stale" else b"0123")


@pytest.mark.unit
def test_large_media_range_is_streamed_without_readall(api_client, monkeypatch):
    client, module = api_client
    blob = Mock()
    blob.get_blob_properties.return_value = SimpleNamespace(
        size=500_000_000, etag='"version"', last_modified=datetime.now(timezone.utc),
    )
    downloader = blob.download_blob.return_value
    downloader.chunks.return_value = iter([b"x" * 1024])
    downloader.readall.side_effect = AssertionError("Full media must not be loaded")
    monkeypatch.setattr(module, "get_media_blob", lambda _: blob)
    response = client.get("/api/documents/media", params={"media_path": "large.mp4"},
                          headers={"Range": "bytes=4096-5119"})
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 4096-5119/500000000"
    assert response.headers["content-length"] == "1024"
    assert response.content == b"x" * 1024
    blob.download_blob.assert_called_once_with(
        offset=4096, length=1024, max_concurrency=1,
        etag='"version"', match_condition=MatchConditions.IfNotModified,
    )
    downloader.chunks.assert_called_once()
    downloader.readall.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("connection_string", ["", "test-connection"])
def test_media_blob_download_buffers_are_bounded(monkeypatch, connection_string):
    constructor = Mock()
    credential = Mock()
    monkeypatch.setattr(blob_document_service, "_blob_svc_instance", None)
    monkeypatch.setattr(blob_document_service, "BLOB_CONNECTION_STRING", connection_string)
    monkeypatch.setattr(blob_document_service, "BlobServiceClient", constructor)
    monkeypatch.setattr(blob_document_service, "DefaultAzureCredential", lambda: credential)
    client = blob_document_service.get_media_blob("video.mp4")
    options = {"max_single_get_size": 1024 * 1024, "max_chunk_get_size": 1024 * 1024}
    if connection_string:
        constructor.from_connection_string.assert_called_once_with(connection_string, **options)
        service = constructor.from_connection_string.return_value
    else:
        constructor.assert_called_once_with(
            account_url=blob_document_service.ACCOUNT_URL, credential=credential, **options,
        )
        service = constructor.return_value
    service.get_blob_client.assert_called_once_with("processed", "video.mp4")
    assert client is service.get_blob_client.return_value


@pytest.mark.unit
def test_empty_media_is_explicit_and_never_downloaded(api_client, storage):
    client, _ = api_client
    blob = storage.get_blob_client("processed", "empty.mp4")
    blob.upload_blob(b"")
    response = client.get("/api/documents/media", params={"media_path": "empty.mp4"})
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == "0"
    response = client.get("/api/documents/media", params={"media_path": "empty.mp4"},
                          headers={"Range": "bytes=0-"})
    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */0"
    assert not blob.download_calls


@pytest.mark.unit
def test_media_version_change_is_not_silently_served(api_client, storage, monkeypatch):
    client, _ = api_client
    blob = storage.get_blob_client("processed", "video.mp4")
    blob.upload_blob(b"0123456789")
    monkeypatch.setattr(blob, "download_blob", Mock(side_effect=ResourceModifiedError("updated")))
    response = client.get("/api/documents/media", params={"media_path": "video.mp4"})
    assert response.status_code == 412
    assert "更新" in response.json()["detail"]


@pytest.mark.unit
def test_media_download_preserves_unicode_filename(api_client, storage):
    client, _ = api_client
    filename = "会議 #1.mp4"
    name = f"2026/11/06/{filename}"
    storage.get_blob_client("processed", name).upload_blob(b"video")
    response = client.get("/api/documents/media",
                          params={"media_path": name, "download": "true"})
    assert response.status_code == 200
    assert response.content == b"video"
    assert response.headers["content-disposition"] == (
        f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
    )


@pytest.mark.unit
def test_media_head_requires_admin(api_client):
    client, module = api_client
    module.require_admin.side_effect = HTTPException(status_code=403, detail="Forbidden")
    response = client.head("/api/documents/media", params={"media_path": "video.mp4"})
    assert response.status_code == 403


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
