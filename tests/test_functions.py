"""functions/function_app.py の純粋ロジックに対する単体テスト。"""
import json
import io
import struct
import subprocess
import wave
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
import imageio_ffmpeg

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

    @pytest.mark.parametrize("filename", ["test.wav", "test.mp4"])
    @pytest.mark.parametrize("size", [0, 500_000_001, 500_000_002])
    def test_rejects_empty_or_oversized_audio_before_download(self, monkeypatch, size, filename):
        blob = Mock()
        blob.get_blob_properties.return_value = SimpleNamespace(size=size)
        service = Mock()
        service.get_blob_client.return_value = blob
        monkeypatch.setattr(function_app, "_blob_svc", lambda: service)
        with pytest.raises(function_app.InvalidAudioError):
            function_app.transcribe_audio(filename)
        blob.download_blob.assert_not_called()

    @pytest.mark.parametrize("filename", ["test.wav", "test.mp4"])
    @pytest.mark.parametrize("size", [250_000_000, 499_999_999, 500_000_000])
    def test_accepts_source_sizes_up_to_500mb_before_download(self, monkeypatch, filename, size):
        blob = Mock()
        blob.get_blob_properties.return_value.size = size
        blob.download_blob.side_effect = RuntimeError("download started")
        service = Mock()
        service.get_blob_client.return_value = blob
        monkeypatch.setattr(function_app, "_blob_svc", lambda: service)
        with pytest.raises(RuntimeError, match="download started"):
            function_app.transcribe_audio(filename)
        blob.download_blob.assert_called_once()

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
class TestVideoSpeech:
    @pytest.mark.parametrize("filename", ["meeting.mp4", "meeting.MP4", "会議 #1.mp4"])
    def test_extracts_real_mp3_and_preserves_video_source(
        self, monkeypatch, sample_mp4, filename
    ):
        original = sample_mp4.read_bytes()
        blob = Mock()
        blob.get_blob_properties.return_value.size = len(original)
        blob.download_blob.return_value.readinto.side_effect = lambda stream: stream.write(original)
        service = Mock()
        service.get_blob_client.return_value = blob
        monkeypatch.setattr(function_app, "_blob_svc", lambda: service)
        monkeypatch.setattr(function_app, "_speech_headers", lambda: {})
        response = Mock()
        response.json.return_value = {
            "durationMilliseconds": 200,
            "phrases": [{"speaker": 0, "text": "hello", "offsetMilliseconds": 0,
                         "durationMilliseconds": 200}],
        }
        temporary_files = []

        def post(url, *, params, headers, data, timeout):
            name, audio, _ = data.fields["audio"]
            assert name == Path(filename).with_suffix(".mp3").name
            temporary_files.append(Path(audio.name))
            encoded = audio.read()
            decoded = subprocess.run(
                [
                    imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-loglevel", "info",
                    "-i", "pipe:0", "-f", "s16le", "-codec:a", "pcm_s16le", "pipe:1",
                ],
                input=encoded, capture_output=True, check=True, timeout=30,
            )
            assert decoded.stdout
            assert b"16000 Hz, mono" in decoded.stderr
            audio.seek(0)
            assert encoded in data.to_string()
            return response

        monkeypatch.setattr(function_app.requests, "post", post)
        result = function_app.transcribe_audio(f"folder/{filename}")
        assert result["sourceFile"] == f"folder/{filename}"
        assert result["duration"] == "PT0.200S"
        assert result["segments"] == [{
            "speaker": "Speaker 0", "text": "hello",
            "startTime": "PT0.000S", "endTime": "PT0.200S",
        }]
        assert temporary_files
        assert all(not path.parent.exists() for path in temporary_files)

    @pytest.mark.parametrize(
        ("extension", "codecs"),
        [
            (".mov", ["-c", "copy"]),
            (".mkv", ["-c", "copy"]),
            (".avi", ["-c:v", "copy", "-c:a", "libmp3lame"]),
            (".webm", ["-c:v", "libvpx", "-c:a", "libopus"]),
        ],
    )
    def test_extracts_other_accepted_video_formats(self, tmp_path, sample_mp4, extension, codecs):
        source = tmp_path / f"video{extension}"
        executable = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run(
            [executable, "-nostdin", "-loglevel", "error", "-i", str(sample_mp4),
             *codecs, str(source)],
            check=True, capture_output=True, timeout=30,
        )
        destination = tmp_path / "audio.mp3"
        function_app._extract_media_audio(source, destination)
        decoded = subprocess.run(
            [executable, "-nostdin", "-loglevel", "error", "-i", str(destination),
             "-f", "s16le", "-codec:a", "pcm_s16le", "pipe:1"],
            check=True, capture_output=True, timeout=30,
        )
        assert decoded.stdout

    def test_video_without_audio_is_rejected(self, tmp_path, sample_mp4):
        silent = tmp_path / "silent.mp4"
        subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-loglevel", "error",
                "-i", str(sample_mp4), "-an", "-c:v", "copy", str(silent),
            ],
            check=True, capture_output=True, timeout=30,
        )
        with pytest.raises(function_app.InvalidAudioError, match="audio track"):
            function_app._extract_media_audio(silent, tmp_path / "audio.mp3")

    def test_large_audio_is_compressed_before_speech(self, monkeypatch):
        data = io.BytesIO()
        with wave.open(data, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(struct.pack("<h", 1000) * 16000)
        original = data.getvalue()
        monkeypatch.setattr(function_app, "MAX_AUDIO_BYTES", 16000)
        assert len(original) >= function_app.MAX_AUDIO_BYTES
        blob = Mock()
        blob.get_blob_properties.return_value.size = len(original)
        blob.download_blob.return_value.readinto.side_effect = lambda stream: stream.write(original)
        service = Mock()
        service.get_blob_client.return_value = blob
        monkeypatch.setattr(function_app, "_blob_svc", lambda: service)
        monkeypatch.setattr(function_app, "_speech_headers", lambda: {})
        response = Mock()
        response.json.return_value = {"durationMilliseconds": 1000, "phrases": []}

        def post(url, *, params, headers, data, timeout):
            name, audio, _ = data.fields["audio"]
            assert name == "meeting.mp3"
            encoded = audio.read()
            assert 0 < len(encoded) < function_app.MAX_AUDIO_BYTES
            assert encoded != original
            return response

        post_mock = Mock(side_effect=post)
        monkeypatch.setattr(function_app.requests, "post", post_mock)
        result = function_app.transcribe_audio("folder/meeting.wav")
        assert result["sourceFile"] == "folder/meeting.wav"
        assert result["duration"] == "PT1.000S"
        post_mock.assert_called_once()

    @pytest.mark.parametrize("ext", sorted(function_app.VIDEO_EXTS))
    def test_extraction_failure_cleans_temporary_files(self, monkeypatch, ext):
        blob = Mock()
        blob.get_blob_properties.return_value.size = 4
        blob.download_blob.return_value.readinto.side_effect = lambda stream: stream.write(b"bad!")
        service = Mock()
        service.get_blob_client.return_value = blob
        monkeypatch.setattr(function_app, "_blob_svc", lambda: service)
        temporary_files = []

        def extract(source, destination):
            temporary_files.append(source)
            assert source.suffix == ext
            raise function_app.InvalidAudioError("bad video")

        monkeypatch.setattr(function_app, "_extract_media_audio", extract)
        post = Mock()
        monkeypatch.setattr(function_app.requests, "post", post)
        with pytest.raises(function_app.InvalidAudioError, match="bad video"):
            function_app.transcribe_audio(f"meeting{ext}")
        post.assert_not_called()
        assert temporary_files
        assert all(not path.parent.exists() for path in temporary_files)

    def test_extraction_timeout_is_nonretryable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(function_app.subprocess, "run",
                            Mock(side_effect=subprocess.TimeoutExpired("ffmpeg", 900)))
        with pytest.raises(function_app.InvalidAudioError, match="timed out"):
            function_app._extract_media_audio(tmp_path / "video.mp4", tmp_path / "audio.mp3")

    @pytest.mark.parametrize("extracted_size", [0, 16, 17])
    def test_rejects_empty_or_oversized_extracted_audio(self, monkeypatch, extracted_size):
        monkeypatch.setattr(function_app, "MAX_AUDIO_BYTES", 16)
        blob = Mock()
        blob.get_blob_properties.return_value.size = 4
        blob.download_blob.return_value.readinto.side_effect = lambda stream: stream.write(b"data")
        service = Mock()
        service.get_blob_client.return_value = blob
        monkeypatch.setattr(function_app, "_blob_svc", lambda: service)
        monkeypatch.setattr(function_app, "_extract_media_audio",
                            lambda source, target: target.write_bytes(b"x" * extracted_size))
        post = Mock()
        monkeypatch.setattr(function_app.requests, "post", post)
        with pytest.raises(function_app.InvalidAudioError, match="Extracted audio"):
            function_app.transcribe_audio("meeting.mp4")
        post.assert_not_called()


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
