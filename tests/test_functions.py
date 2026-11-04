"""functions/function_app.py の純粋ロジックに対する単体テスト。"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

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


class _ExistsBlobClient:
    def __init__(self, exists: bool):
        self._exists = exists

    def exists(self) -> bool:
        return self._exists


class _ExistsBlobService:
    def __init__(self, exists: bool):
        self._exists = exists

    def get_blob_client(self, container, name):
        return _ExistsBlobClient(self._exists)


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
class TestPrivateSpeech:
    @pytest.mark.parametrize("duration", [1234567, 7199999, 7200000])
    def test_streams_audio_and_preserves_output_contract(self, monkeypatch, duration):
        blob = Mock()
        blob.get_blob_properties.return_value.size = 4
        blob.download_blob.return_value.readinto.side_effect = lambda stream: stream.write(b"RIFF")
        service = Mock()
        service.get_blob_client.return_value = blob
        monkeypatch.setattr(function_app, "_blob_svc", lambda: service)
        monkeypatch.setattr(function_app, "_speech_headers", lambda: {"Authorization": "Bearer test"})
        response = Mock()
        response.json.return_value = {
            "durationMilliseconds": duration,
            "phrases": [{"speaker": 0, "text": "hello", "offsetMilliseconds": 1234001,
                         "durationMilliseconds": 566}],
        }

        def post(url, *, params, headers, data, timeout):
            assert url.endswith("/speechtotext/transcriptions:transcribe")
            assert params == {"api-version": "2025-10-15"}
            assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
            assert timeout == (30, 900)
            assert json.loads(data.fields["definition"])["diarization"]["enabled"] is True
            payload = data.to_string()
            assert b"RIFF" in payload
            assert b"contentUrls" not in payload and b"audioUrl" not in payload
            return response

        monkeypatch.setattr(function_app.requests, "post", post)
        if duration == 7200000:
            with pytest.raises(function_app.InvalidAudioError, match="2 hours"):
                function_app.transcribe_audio("folder/meeting.wav")
            return
        result = function_app.transcribe_audio("folder/meeting.wav")
        assert result["sourceFile"] == "folder/meeting.wav"
        assert result["duration"] == function_app._iso_duration_ms(duration)
        assert result["segments"] == [{
            "speaker": "Speaker 0", "text": "hello",
            "startTime": "PT1234.001S", "endTime": "PT1234.567S",
        }]

    @pytest.mark.parametrize("size", [0, 250_000_000, 250_000_001])
    def test_rejects_empty_or_oversized_audio_before_download(self, monkeypatch, size):
        blob = Mock()
        blob.get_blob_properties.return_value = SimpleNamespace(size=size)
        service = Mock()
        service.get_blob_client.return_value = blob
        monkeypatch.setattr(function_app, "_blob_svc", lambda: service)
        with pytest.raises(function_app.InvalidAudioError):
            function_app.transcribe_audio("test.wav")
        blob.download_blob.assert_not_called()

    @pytest.mark.parametrize("status", [400, 401, 403, 413, 415, 422, 429, 500])
    def test_surfaces_speech_errors_and_only_retries_transient_failures(self, monkeypatch, status):
        blob = Mock()
        blob.get_blob_properties.return_value.size = 1
        blob.download_blob.return_value.readinto.side_effect = lambda stream: stream.write(b"x")
        service = Mock()
        service.get_blob_client.return_value = blob
        monkeypatch.setattr(function_app, "_blob_svc", lambda: service)
        monkeypatch.setattr(function_app, "_speech_headers", lambda: {})
        response = Mock(status_code=status, text="service error")
        response.raise_for_status.side_effect = requests.HTTPError("failed")
        monkeypatch.setattr(function_app.requests, "post", lambda *args, **kwargs: response)
        expected = requests.HTTPError if status in (429, 500) else function_app.InvalidAudioError
        with pytest.raises(expected):
            function_app.transcribe_audio("test.wav")

    @pytest.mark.parametrize("value", [-1, 1.2, True, None])
    def test_rejects_invalid_timestamps(self, value):
        with pytest.raises(ValueError):
            function_app._iso_duration_ms(value)


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


@pytest.mark.unit
class TestInputExists:
    def test_true_when_blob_present(self, monkeypatch):
        monkeypatch.setattr(function_app, "_blob_svc", lambda: _ExistsBlobService(True))
        assert function_app._input_exists("a.wav") is True

    def test_false_when_blob_absent(self, monkeypatch):
        # 処理済みで input から消えている状態（Queue 再配信の冪等スキップ対象）
        monkeypatch.setattr(function_app, "_blob_svc", lambda: _ExistsBlobService(False))
        assert function_app._input_exists("a.wav") is False
