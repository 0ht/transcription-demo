"""functions/function_app.py の純粋ロジックに対する単体テスト。"""
import json
from datetime import datetime, timezone

import pytest

import function_app


# ---------------------------------------------------------------------------
# テスト用のダミー Blob SDK（download → readall のチェーンだけ再現）
# ---------------------------------------------------------------------------
class _FakeDownload:
    def __init__(self, data: bytes):
        self._data = data

    def readall(self) -> bytes:
        return self._data


class _FakeBlobClient:
    def __init__(self, data: bytes):
        self._data = data

    def download_blob(self):
        return _FakeDownload(self._data)


class _FakeBlobService:
    def __init__(self, data: bytes):
        self._data = data

    def get_blob_client(self, container, name):
        return _FakeBlobClient(self._data)


@pytest.mark.unit
class TestOutputPrefix:
    def test_prefix_uses_utc_date_and_stem(self, monkeypatch):
        # Arrange: 日付を固定
        fixed = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed

        monkeypatch.setattr(function_app, "datetime", _FixedDatetime)

        # Act
        result = function_app._output_prefix("folder/meeting.wav")

        # Assert
        assert result == "2026/07/03/meeting"

    def test_prefix_strips_directory_and_extension(self, monkeypatch):
        fixed = datetime(2026, 1, 9, tzinfo=timezone.utc)

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed

        monkeypatch.setattr(function_app, "datetime", _FixedDatetime)

        assert function_app._output_prefix("a/b/c/audio.final.mp3").startswith("2026/01/09/")
        assert function_app._output_prefix("a/b/c/audio.final.mp3").endswith("/audio.final")


@pytest.mark.unit
class TestBlobUrl:
    def test_builds_full_https_url(self):
        # Act
        url = function_app._blob_url("input", "2026/07/03/x.wav")

        # Assert
        assert url == f"{function_app.STORAGE_URL}/input/2026/07/03/x.wav"

    def test_uses_configured_storage_account(self):
        assert function_app.STORAGE_URL.startswith("https://")
        assert function_app.STORAGE_URL.endswith(".blob.core.windows.net")


@pytest.mark.unit
class TestExtractText:
    def test_plain_text_is_decoded_utf8(self, monkeypatch):
        # Arrange
        monkeypatch.setattr(
            function_app, "_blob_svc", lambda: _FakeBlobService("こんにちは".encode("utf-8"))
        )

        # Act
        result = function_app.extract_text("note.txt", ".txt")

        # Assert
        assert result["sourceFile"] == "note.txt"
        assert result["segments"][0]["speaker"] == "Text"
        assert result["segments"][0]["text"] == "こんにちは"

    def test_json_is_pretty_reformatted(self, monkeypatch):
        raw = b'{"b":2,"a":1}'
        monkeypatch.setattr(
            function_app, "_blob_svc", lambda: _FakeBlobService(raw)
        )

        result = function_app.extract_text("data.json", ".json")
        text = result["segments"][0]["text"]

        # 整形済み JSON として再パースできる
        assert json.loads(text) == {"b": 2, "a": 1}
        assert "\n" in text  # indent=2 で整形されている

    def test_invalid_json_falls_back_to_raw(self, monkeypatch):
        raw = b"{not valid json"
        monkeypatch.setattr(
            function_app, "_blob_svc", lambda: _FakeBlobService(raw)
        )

        result = function_app.extract_text("broken.json", ".json")
        assert result["segments"][0]["text"] == "{not valid json"
