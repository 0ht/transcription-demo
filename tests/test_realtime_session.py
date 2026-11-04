import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

config_stub = ModuleType("modules.config")
config_stub.create_realtime_client = lambda: None
config_stub.realtime_model = "test-realtime"
config_stub.input_transcription_model = "test-transcription"

with patch.dict(sys.modules, {"modules.config": config_stub}):
    from modules.realtime_session import RealtimeMeetingSession
    from modules import realtime_session


@pytest.mark.unit
def test_partial_transcript_accumulates_deltas():
    async def exercise():
        session = RealtimeMeetingSession(session_id="test")

        await session._append_partial_transcript("これは")
        await session._append_partial_transcript("文字起こし")
        await session._append_partial_transcript("です。")

        assert await session.get_partial_transcript() == "これは文字起こしです。"

        await session._clear_partial_transcript()
        assert await session.get_partial_transcript() == ""

    asyncio.run(exercise())


class EventConnection:
    def __init__(self, events):
        self.events = events

    async def __aiter__(self):
        for event in self.events:
            yield SimpleNamespace(**event)


@pytest.mark.unit
def test_partials_preserve_whitespace_and_other_inflight_items():
    async def exercise():
        session = RealtimeMeetingSession("test")
        await session._append_partial_transcript("First", "one")
        await session._append_partial_transcript(" phrase", "one")
        await session._append_partial_transcript("Second", "two")
        assert await session.get_partial_transcript() == "First phraseSecond"
        await session._clear_partial_transcript("one")
        assert await session.get_partial_transcript() == "Second"
        await session.clear_transcript()
        assert await session.get_partial_transcript() == ""

    asyncio.run(exercise())


@pytest.mark.unit
def test_listener_records_two_utterances_without_assistant_speech():
    async def exercise():
        session = RealtimeMeetingSession("test")
        session.running = True
        session.connection = EventConnection([
            {"type": "conversation.item.input_audio_transcription.delta", "delta": "First", "item_id": "one"},
            {"type": "conversation.item.input_audio_transcription.delta", "delta": " phrase", "item_id": "one"},
            {"type": "conversation.item.input_audio_transcription.completed", "transcript": "First phrase", "item_id": "one"},
            {"type": "response.output_audio_transcript.done", "transcript": "Assistant speech"},
            {"type": "conversation.item.input_audio_transcription.completed", "transcript": "Second phrase", "item_id": "two"},
        ])
        await session._listen()
        assert await session.get_transcript_text_for_analysis() == "First phrase\nSecond phrase"
        assert await session.get_partial_transcript() == ""
        assert session.running is False
        assert "connection closed" in session.last_error

    asyncio.run(exercise())


@pytest.mark.unit
def test_transcription_rate_limit_is_not_silently_ignored():
    async def exercise():
        session = RealtimeMeetingSession("test")
        session.running = True
        session.connection = EventConnection([{
            "type": "conversation.item.input_audio_transcription.failed",
            "item_id": "one",
            "error": SimpleNamespace(code="RateLimitReached", message="Quota exceeded"),
        }])
        await session._append_partial_transcript("unfinished", "one")
        await session._listen()
        assert "RateLimitReached" in session.last_error
        assert await session.get_partial_transcript() == ""
        events = await session.get_transcript_events()
        assert events[-1]["event_type"] == "transcription_failed"

    asyncio.run(exercise())


@pytest.mark.unit
def test_failed_audio_append_is_not_acknowledged_as_success():
    async def exercise():
        session = RealtimeMeetingSession("test")
        session.running = True
        session.connection = SimpleNamespace(
            input_audio_buffer=SimpleNamespace(
                append=AsyncMock(side_effect=RuntimeError("socket closed"))
            )
        )
        with pytest.raises(RuntimeError, match="socket closed"):
            await session.ingest_audio_chunk(b"\x00\x00")
        assert session.audio_chunk_count == 0
        assert "socket closed" in session.last_error

    asyncio.run(exercise())


@pytest.mark.unit
def test_audio_after_listener_shutdown_is_rejected():
    async def exercise():
        session = RealtimeMeetingSession("test")
        with pytest.raises(RuntimeError, match="not running"):
            await session.ingest_audio_chunk(b"\x00\x00")

    asyncio.run(exercise())


@pytest.mark.unit
def test_start_configures_separate_realtime_and_transcription_models(monkeypatch):
    async def exercise():
        connection = SimpleNamespace(session=SimpleNamespace(update=AsyncMock()))
        context = SimpleNamespace(
            __aenter__=AsyncMock(return_value=connection), __aexit__=AsyncMock()
        )
        client = SimpleNamespace(
            realtime=SimpleNamespace(connect=lambda **kwargs: context)
        )
        monkeypatch.setattr(realtime_session, "create_realtime_client", lambda: client)
        session = RealtimeMeetingSession("test")
        await session.start()
        settings = connection.session.update.call_args.kwargs["session"]
        assert settings["audio"]["input"]["transcription"]["model"] == "test-transcription"
        assert settings["audio"]["input"]["turn_detection"]["create_response"] is False
        await session.stop()

    asyncio.run(exercise())