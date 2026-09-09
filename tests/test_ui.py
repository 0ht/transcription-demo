"""ui/modules 配下の純粋ロジックに対する単体テスト。"""

import sys
from types import ModuleType

import pytest


config_stub = ModuleType("modules.config")
config_stub.BLOB_CONNECTION_STRING = ""
config_stub.STORAGE_ACCOUNT_NAME = "teststorage"
config_stub.SEARCH_ENDPOINT = "https://test.search.windows.net"
config_stub.SEARCH_KEY = None
config_stub.SEARCH_INDEX = "documents"
config_stub.SEMANTIC_CONFIG_NAME = "default-semantic"
config_stub.read_fields = ["content", "source_file"]

previous_config = sys.modules.get("modules.config")
sys.modules["modules.config"] = config_stub
from modules import blob_document_service, search_service

if previous_config is None:
    del sys.modules["modules.config"]
else:
    sys.modules["modules.config"] = previous_config


@pytest.mark.unit
class TestSpeakerColor:
    def test_same_name_returns_same_color(self):
        assert blob_document_service.speaker_color(
            "Speaker 0"
        ) == blob_document_service.speaker_color("Speaker 0")

    def test_color_is_from_palette(self):
        assert (
            blob_document_service.speaker_color("誰か")
            in blob_document_service.SPEAKER_COLORS
        )


@pytest.mark.unit
class TestBuildTranscriptText:
    def test_formats_non_empty_segments(self):
        transcript = {
            "segments": [
                {"speaker": "A", "text": "こんにちは"},
                {"speaker": "B", "text": ""},
                {"speaker": "B", "text": "はい"},
            ]
        }

        assert blob_document_service.build_transcript_text(transcript) == (
            "A: こんにちは\nB: はい"
        )

    def test_returns_empty_for_missing_segments(self):
        assert blob_document_service.build_transcript_text({}) == ""


@pytest.mark.unit
class TestGetDocumentDetail:
    def test_builds_media_metadata(self, monkeypatch):
        transcript = {
            "sourceFile": "meeting.mp4",
            "duration": "PT1M",
            "language": "ja-JP",
            "processedAt": "2026-09-09T12:34:56Z",
            "segments": [{"speaker": "A", "text": "議事録"}],
        }
        monkeypatch.setattr(blob_document_service, "load_json", lambda _path: transcript)
        monkeypatch.setattr(blob_document_service, "load_text", lambda _path: "議事録")

        detail = blob_document_service.get_document_detail(
            "2026/09/09/meeting_transcript.json"
        )

        assert detail["media_path"] == "2026/09/09/meeting.mp4"
        assert detail["is_video"] is True
        assert detail["is_audio"] is False
        assert detail["transcript_text"] == "A: 議事録"


@pytest.mark.unit
class TestBuildFilter:
    def test_returns_none_without_source_file(self):
        assert search_service._build_filter() is None

    def test_filters_by_source_file(self):
        assert search_service._build_filter(source_file="a.wav") == "source_file eq 'a.wav'"

    def test_ignores_transcript_path(self):
        assert search_service._build_filter(transcript_path="p/x.json") is None

    def test_escapes_single_quote(self):
        result = search_service._build_filter(source_file="O'Brien.wav")
        assert result == "source_file eq 'O''Brien.wav'"
