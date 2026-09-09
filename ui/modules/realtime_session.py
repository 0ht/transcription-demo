#modules/realtime_session.py
import asyncio  
import base64  
from datetime import datetime  
from typing import Any, Dict, List, Optional  
  
from modules.config import create_realtime_client, realtime_model, whisper_model  
  
  
class RealtimeMeetingSession:  
    def __init__(self, session_id: str):  
        self.session_id = session_id  
        self.client = None  
        self.connection = None  
        self.running = False  
  
        self.listen_task: Optional[asyncio.Task] = None  
        self.lock = asyncio.Lock()  
  
        # final のみ保持  
        self.transcript_events: List[Dict[str, Any]] = []  
  
        # partial は別管理  
        self.partial_transcript: str = ""  
  
        self.audio_chunk_count = 0  
  
        # __aenter__ / __aexit__ を明示管理するため保持  
        self._connection_ctx = None  
  
    def _now(self) -> str:  
        return datetime.utcnow().isoformat()  
  
    async def _append_event(  
        self,  
        text: str,  
        source: str = "system",  
        extra: Optional[Dict[str, Any]] = None  
    ):  
        payload = {  
            "timestamp": self._now(),  
            "source": source,  
            "text": text,  
        }  
        if extra:  
            payload.update(extra)  
  
        async with self.lock:  
            self.transcript_events.append(payload)  
  
    async def _append_partial_transcript(self, text: str):  
        async with self.lock:  
            self.partial_transcript += text or ""  
  
    async def _clear_partial_transcript(self):  
        async with self.lock:  
            self.partial_transcript = ""  

    async def get_partial_transcript(self) -> str:  
        async with self.lock:  
            return self.partial_transcript  
  
    async def start(self):  
        if self.running:  
            print(f"[rt] start skipped, already running: {self.session_id}")  
            return  
  
        try:  
            print(f"[rt] start() begin: {self.session_id}")  
            self.client = create_realtime_client()  
            print(f"[rt] client created: {self.session_id}")  
  
            self._connection_ctx = self.client.realtime.connect(model=realtime_model)  
            self.connection = await self._connection_ctx.__aenter__()  
            print(f"[rt] realtime connection opened: {self.session_id}")  
  
            await self.connection.session.update(  
                session={  
                    "type": "realtime",  
                    "instructions": (  
                        "You are a meeting transcription assistant. "  
                        "Focus on accurate speech transcription. "  
                        "Do not provide long spoken responses unless explicitly asked."  
                    ),  
                    "audio": {  
                        "input": {  
                            "transcription": {  
                                "model": whisper_model,  
                            },  
                            "format": {  
                                "type": "audio/pcm",  
                                "rate": 24000,  
                            },  
                            "turn_detection": {  
                                "type": "server_vad",  
                                "threshold": 0.5,  
                                "prefix_padding_ms": 300,  
                                "silence_duration_ms": 500,  
                                "create_response": False,  
                            },  
                        }  
                    },  
                }  
            )  
            print(f"[rt] session.update done: {self.session_id}")  
  
            self.running = True  
            self.listen_task = asyncio.create_task(self._listen())  
  
            await self._append_event(  
                f"[info] realtime session started: {self.session_id}",  
                source="system",  
                extra={"event_type": "session_started"},  
            )  
  
        except Exception as e:  
            await self._safe_close_connection()  
            raise RuntimeError(str(e))  
  
    async def stop(self):  
        self.running = False  
  
        if self.listen_task:  
            self.listen_task.cancel()  
            try:  
                await self.listen_task  
            except asyncio.CancelledError:  
                pass  
            except Exception:  
                pass  
            finally:  
                self.listen_task = None  
  
        await self._safe_close_connection()  
  
        await self._append_event(  
            f"[info] realtime session stopped: {self.session_id}",  
            source="system",  
            extra={"event_type": "session_stopped"},  
        )  
  
    async def _safe_close_connection(self):  
        try:  
            if self._connection_ctx is not None:  
                await self._connection_ctx.__aexit__(None, None, None)  
        except Exception:  
            pass  
        finally:  
            self._connection_ctx = None  
            self.connection = None  
            self.client = None  
  
    async def ingest_audio_chunk(self, chunk: bytes):  
        if not self.running or self.connection is None:  
            await self._append_event(  
                "[warning] realtime connection is not started",  
                source="system",  
                extra={"event_type": "warning"},  
            )  
            return  
  
        try:  
            encoded = base64.b64encode(chunk).decode("utf-8")  
            await self.connection.input_audio_buffer.append(audio=encoded)  
            self.audio_chunk_count += 1  
        except Exception as e:  
            await self._append_event(  
                f"[audio ingest error] {str(e)}",  
                source="system",  
                extra={"event_type": "audio_ingest_error"},  
            )  
  
    async def finalize_audio_input(self):  
        if not self.running or self.connection is None:  
            return  
  
        try:  
            await self.connection.input_audio_buffer.commit()  
            await self._append_event(  
                "[info] audio input committed",  
                source="system",  
                extra={"event_type": "audio_committed"},  
            )  
        except Exception as e:  
            await self._append_event(  
                f"[audio commit error] {str(e)}",  
                source="system",  
                extra={"event_type": "audio_commit_error"},  
            )  
  
    async def add_transcript_text(self, text: str, source: str = "manual"):  
        text = (text or "").strip()  
        if not text:  
            return  
  
        # manual/debug は final 扱い  
        await self._append_event(  
            text,  
            source=source,  
            extra={"event_type": "manual_text"},  
        )  
  
    async def _listen(self):  
        print(f"[rt] _listen begin: {self.session_id}")  
        try:  
            async for event in self.connection:  
                event_type = getattr(event, "type", "")  
                print(f"[rt] event received: {event_type}")  
  
                if event_type == "session.created":  
                    session_obj = getattr(event, "session", None)  
                    created_session_id = getattr(session_obj, "id", None) if session_obj else None  
                    await self._append_event(  
                        f"[info] realtime session created: {created_session_id}",  
                        source="system",  
                        extra={"event_type": event_type},  
                    )  
  
                # partial  
                elif event_type in {  
                    "conversation.item.input_audio_transcription.delta",  
                    "response.output_audio_transcript.delta",  
                }:  
                    delta = getattr(event, "delta", "") or ""  
                    if delta:  
                        await self._append_partial_transcript(delta)  
  
                # final  
                elif event_type in {  
                    "conversation.item.input_audio_transcription.completed",  
                    "response.output_audio_transcript.done",  
                }:  
                    transcript = ""  
  
                    if hasattr(event, "transcript"):  
                        transcript = event.transcript or ""  
                    elif hasattr(event, "text"):  
                        transcript = event.text or ""  
                    elif hasattr(event, "item") and getattr(event.item, "content", None):  
                        try:  
                            for c in event.item.content:  
                                if getattr(c, "type", "") in {"input_text", "text"}:  
                                    transcript += getattr(c, "text", "") or ""  
                        except Exception:  
                            pass  
  
                    transcript = transcript.strip()  
                    if transcript:  
                        await self._append_event(  
                            transcript,  
                            source="realtime",  
                            extra={"event_type": event_type},  
                        )  
  
                    await self._clear_partial_transcript()  
  
                elif event_type == "response.output_text.delta":  
                    delta = getattr(event, "delta", "") or ""  
                    if delta:  
                        await self._append_event(  
                            delta,  
                            source="assistant_delta",  
                            extra={"event_type": event_type},  
                        )  
  
                elif event_type == "response.output_text.done":  
                    await self._append_event(  
                        "[info] response text completed",  
                        source="system",  
                        extra={"event_type": event_type},  
                    )  
  
                elif event_type == "error":  
                    error = getattr(event, "error", None)  
                    code = getattr(error, "code", "") if error else ""  
                    message = getattr(error, "message", "") if error else ""  
                    event_id = getattr(error, "event_id", "") if error else ""  
  
                    await self._append_event(  
                        f"[Realtime error] code={code} event_id={event_id} message={message}",  
                        source="system",  
                        extra={"event_type": event_type},  
                    )  
  
                elif event_type == "response.done":  
                    await self._append_event(  
                        "[info] response done",  
                        source="system",  
                        extra={"event_type": event_type},  
                    )  
  
                else:  
                    # 必要に応じて debug を残す  
                    pass  
  
        except asyncio.CancelledError:  
            raise  
        except Exception as e:  
            await self._append_event(  
                f"[listener error] {str(e)}",  
                source="system",  
                extra={"event_type": "listener_error"},  
            )  
  
    async def get_transcript_events(self) -> List[Dict[str, Any]]:  
        async with self.lock:  
            return list(self.transcript_events)  
  
    async def get_transcript_text(self) -> str:  
        async with self.lock:  
            return "\n".join(  
                x["text"] for x in self.transcript_events if x.get("text")  
            ).strip()  
  
    async def get_transcript_text_for_analysis(self) -> str:  
        async with self.lock:  
            return "\n".join(  
                x["text"]  
                for x in self.transcript_events  
                if x.get("text") and x.get("source") in {"realtime", "debug", "manual"}  
            ).strip()  
  
    async def clear_transcript(self):  
        async with self.lock:  
            self.transcript_events = []  
            self.partial_transcript = ""  
            self.audio_chunk_count = 0  