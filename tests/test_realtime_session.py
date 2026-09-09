import asyncio
import sys
from types import ModuleType

import pytest

config_stub = ModuleType("modules.config")
config_stub.create_realtime_client = lambda: None
config_stub.realtime_model = "test-realtime"
config_stub.whisper_model = "test-whisper"
sys.modules["modules.config"] = config_stub

from modules.realtime_session import RealtimeMeetingSession


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