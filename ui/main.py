import uuid  
from pathlib import Path
from typing import Dict, Optional  
  
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File   
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response   
from fastapi.staticfiles import StaticFiles  
from pydantic import BaseModel  
from starlette.concurrency import run_in_threadpool
from starlette.templating import Jinja2Templates  
  
from modules.realtime_session import RealtimeMeetingSession  
from modules.meeting_manager import MeetingManager  
from modules.storage import load_history, save_history  
from modules.query_planner import plan_meeting_topic  
from modules.ai_search import search_documents  
from modules.llm import generate_meeting_feedback, generate_chat_response  
from modules.prompt_store import list_prompt_sets  
from modules.chat_retrieval import decide_retrieval, merge_docs  

import datetime

from modules.ocr import (
upload_file_to_ocr_input,
list_uploaded_files,
process_ocr_from_blob,
list_ocr_results,
load_ocr_result_text,
delete_uploaded_file,
delete_ocr_result
)


from modules.blob_document_service import (  
    list_transcripts,  
    get_document_detail,  
    load_media,  
)  
from modules.document_rag_service import rag_answer_for_document  



app = FastAPI()  
  
app.mount("/static", StaticFiles(directory="static"), name="static")  
templates = Jinja2Templates(directory="templates")  


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(
        Path(__file__).resolve().parent / "static" / "favicon.ico",
        media_type="image/vnd.microsoft.icon",
    )
  
  
class AnalyzeRequest(BaseModel):  
    prompt_set_name: str = "default"  
    user_instruction: str = ""  
  
  
class ConfirmRequest(BaseModel):  
    prompt_set_name: str = "default"  
  
  
class DebugTextRequest(BaseModel):  
    text: str  
  
  
class ChatRequest(BaseModel):  
    message: str  
    prompt_set_name: str = "default"  
    use_rag: bool = True  
    context_mode: str = "current_transcript"  # "current_transcript" or "history"  
    topic_id: Optional[str] = None  

class DocumentChatRequest(BaseModel):  
    transcript_path: str  
    message: str  
    prompt_set_name: str = "default"  
    search_mode: str = "semantic_hybrid"  
    top_k: int = 5  
    use_query_rewrite: bool = True  

  
# session_id ごとに独立管理  
sessions: Dict[str, RealtimeMeetingSession] = {}  
meeting_managers: Dict[str, MeetingManager] = {}  
  
# 履歴は全体共有  
history_store = load_history()  
  

def serialize_blob_dt(dt):  
    return dt.isoformat() if dt else None  

def validate_prompt_set_or_400(prompt_set_name: str):  
    if prompt_set_name not in list_prompt_sets():  
        raise HTTPException(status_code=400, detail="invalid prompt_set_name")  
  
  
def get_session_or_404(session_id: str) -> RealtimeMeetingSession:  
    session = sessions.get(session_id)  
    if not session:  
        raise HTTPException(status_code=404, detail="session not found")  
    return session  
  
  
def get_manager_or_404(session_id: str) -> MeetingManager:  
    manager = meeting_managers.get(session_id)  
    if not manager:  
        raise HTTPException(status_code=404, detail="meeting manager not found")  
    return manager  
  
  
async def get_analysis_transcript(session: RealtimeMeetingSession) -> str:  
    """  
    分析用 transcript。  
    source=realtime/debug/manual のみ採用し、system系ログを除外する。  
    """  
    events = await session.get_transcript_events()  
    texts = [  
        (x.get("text") or "").strip()  
        for x in events  
        if x.get("source") in {"realtime", "debug", "manual"}  
    ]  
    return "\n".join([t for t in texts if t]).strip()  
  
  
def get_history_item_by_topic_id(topic_id: str):  
    for item in history_store:  
        if str(item.get("topic_id")) == str(topic_id):  
            return item  
    return None  
  
  
@app.get("/", response_class=HTMLResponse)  
async def index(request: Request):  
    return templates.TemplateResponse(  
        request=request,  
        name="index.html",  
        context={  
            "request": request,  
            "prompt_sets": list_prompt_sets(),  
        },  
    )  
  
  
@app.post("/api/session/start")  
async def start_session():  
    session_id = str(uuid.uuid4())  
    session = RealtimeMeetingSession(session_id=session_id)  
  
    try:  
        await session.start()  
        sessions[session_id] = session  
        meeting_managers[session_id] = MeetingManager()  
        return {"session_id": session_id, "status": "started"}  
    except Exception as e:  
        try:  
            await session.stop()  
        except Exception:  
            pass  
        raise HTTPException(  
            status_code=500,  
            detail=f"Failed to start realtime session: {str(e)}"  
        )  
  
  
@app.post("/api/session/{session_id}/stop")  
async def stop_session(session_id: str):  
    session = get_session_or_404(session_id)  
  
    try:  
        await session.stop()  
    finally:  
        sessions.pop(session_id, None)  
        meeting_managers.pop(session_id, None)  
  
    return {"session_id": session_id, "status": "stopped"}  
  
  
@app.websocket("/ws/audio/{session_id}")  
async def websocket_audio_ingest(websocket: WebSocket, session_id: str):  
    print(f"[ws] connect requested: {session_id}")  
  
    session = sessions.get(session_id)  
    if not session:  
        print(f"[ws] session not found: {session_id}")  
        await websocket.close(code=4404, reason="session not found")  
        return  
  
    await websocket.accept()  
    print(f"[ws] accepted: {session_id}")  
  
    try:  
        await websocket.send_json({  
            "type": "status",  
            "message": "connected",  
            "session_id": session_id  
        })  
        print(f"[ws] connected message sent: {session_id}")  
  
        while True:  
            message = await websocket.receive()  

            if message["type"] == "websocket.disconnect":
                break
  
            if "bytes" in message and message["bytes"] is not None:  
                chunk = message["bytes"]  
                print(f"[ws] bytes received: {len(chunk)}, head={list(chunk[:16])}")  
  
                await session.ingest_audio_chunk(chunk)  
  
                await websocket.send_json({  
                    "type": "ack",  
                    "audio_chunk_count": session.audio_chunk_count  
                })  
  
            elif "text" in message and message["text"] is not None:  
                text = message["text"]  
                print(f"[ws] text received: {text}")  
  
                if text == "__commit__":  
                    await session.finalize_audio_input()  
                    await websocket.send_json({"type": "committed"})  
                elif text == "__ping__":  
                    await websocket.send_json({"type": "pong"})  
                else:  
                    await websocket.send_json({  
                        "type": "ignored_text",  
                        "message": text  
                    })  
  
    except WebSocketDisconnect:  
        print(f"[ws] disconnected: {session_id}")  
    except Exception as e:  
        print(f"[ws] error: {e}")  
        try:  
            await websocket.send_json({  
                "type": "error",  
                "message": str(e)  
            })  
        except Exception:  
            pass  
    finally:  
        try:  
            await websocket.close()  
        except Exception:  
            pass  
        print(f"[ws] closed: {session_id}")  
  
  
@app.get("/api/session/{session_id}/transcript")  
async def get_transcript(session_id: str):  
    session = get_session_or_404(session_id)  
    manager = get_manager_or_404(session_id)  
  
    events = await session.get_transcript_events()  
    full_text = await get_analysis_transcript(session)  
    partial_text = await session.get_partial_transcript()  
  
    return {  
        "session_id": session_id,  
        "running": session.running,  
        "audio_chunk_count": session.audio_chunk_count,  
  
        "events": events,  
        "full_text": full_text,  
        "partial_text": partial_text,  
  
        "current_topic_title": manager.current_topic_title,  
        "current_summary": manager.current_summary,  
        "current_search_query": manager.current_search_query,  
        "current_analysis": manager.current_analysis,  
        "current_docs": manager.current_docs,  
        "current_issues": manager.current_issues,  
        "current_suggestions": manager.current_suggestions,  
        "current_next_actions": manager.current_next_actions,  
    }  
  
  
@app.post("/api/session/{session_id}/analyze")  
async def analyze_session(session_id: str, req: AnalyzeRequest):  
    validate_prompt_set_or_400(req.prompt_set_name)  
  
    session = get_session_or_404(session_id)  
    manager = get_manager_or_404(session_id)  
  
    conversation = await session.get_transcript_text_for_analysis()  
    manager.set_transcript(conversation)  
  
    if not manager.has_transcript():  
        raise HTTPException(status_code=400, detail="No transcript to analyze")  
  
    try:  
        plan = await run_in_threadpool(plan_meeting_topic, conversation)  
  
        docs = []  
        if plan.get("use_rag") and plan.get("search_query"):  
            docs = await run_in_threadpool(  
                search_documents,  
                "hybrid",  
                plan["search_query"]  
            )  
  
        analysis = await run_in_threadpool(  
            generate_meeting_feedback,  
            plan.get("summary", ""),  
            conversation,  
            docs,  
            req.prompt_set_name,  
            plan.get("issues", []),  
            plan.get("suggestions", []),  
            plan.get("next_actions", []),  
            req.user_instruction,  
        )  
  
        manager.set_analysis_result(  
            topic_title=plan.get("topic_title", ""),  
            summary=plan.get("summary", ""),  
            search_query=plan.get("search_query", ""),  
            analysis=analysis,  
            docs=docs,  
            issues=plan.get("issues", []),  
            suggestions=plan.get("suggestions", []),  
            next_actions=plan.get("next_actions", []),  
        )  
  
        return {  
            "status": "ok",  
            "topic_title": manager.current_topic_title,  
            "summary": manager.current_summary,  
            "search_query": manager.current_search_query,  
            "analysis": manager.current_analysis,  
            "docs": manager.current_docs,  
            "issues": manager.current_issues,  
            "suggestions": manager.current_suggestions,  
            "next_actions": manager.current_next_actions,  
        }  
  
    except Exception as e:  
        raise HTTPException(status_code=500, detail=f"Analyze failed: {str(e)}")  


  
@app.post("/api/session/{session_id}/confirm")  
async def confirm_session(session_id: str, req: ConfirmRequest):  
    global history_store  
  
    validate_prompt_set_or_400(req.prompt_set_name)  
  
    session = get_session_or_404(session_id)  
    manager = get_manager_or_404(session_id)  
  
    conversation = await session.get_transcript_text_for_analysis()  
    manager.set_transcript(conversation)  
  
    if not manager.has_transcript():  
        raise HTTPException(status_code=400, detail="No transcript to confirm")  
  
    try:  
        item = manager.confirm_topic(prompt_set=req.prompt_set_name)  
  
        history_store.insert(0, item)  
        await run_in_threadpool(save_history, history_store)  
  
        await session.clear_transcript()  
  
        manager.clear_chat()  
        manager.reset_chat_context()  
  
        return {"status": "confirmed", "item": item}  
    except Exception as e:  
        raise HTTPException(status_code=500, detail=f"Confirm failed: {str(e)}")  
  
  
@app.post("/api/session/{session_id}/clear")  
async def clear_session(session_id: str):  
    session = get_session_or_404(session_id)  
    manager = get_manager_or_404(session_id)  
  
    await session.clear_transcript()  
    manager.clear_current()  
    manager.clear_chat()  
    manager.reset_chat_context()  
  
    return {"status": "cleared", "session_id": session_id}  
  
  
@app.get("/api/history")  
async def get_history():  
    return {"items": history_store}  
  
  
@app.get("/api/history/{topic_id}")  
async def get_history_item(topic_id: str):  
    item = get_history_item_by_topic_id(topic_id)  
    if not item:  
        raise HTTPException(status_code=404, detail="not found")  
  
    return {  
        "topic_id": item.get("topic_id", ""),  
        "title": item.get("title", ""),  
        "topic_title": item.get("title", ""),  
        "summary": item.get("summary", ""),  
        "search_query": item.get("search_query", ""),  
        "analysis": item.get("analysis", ""),  
        "full_text": item.get("transcript", ""),  
        "partial_text": "",  
        "docs": item.get("retrieved_docs", []),  
        "chat_messages": item.get("chat_messages", []),  
    }  
  
  
@app.get("/api/prompt_sets")  
async def get_prompt_sets():  
    return {"prompt_sets": list_prompt_sets()}  
  
  
@app.post("/api/session/{session_id}/debug_text")  
async def add_debug_text(session_id: str, req: DebugTextRequest):  
    session = sessions.get(session_id)  
    if not session:  
        raise HTTPException(status_code=404, detail="session not found")  
  
    text = (req.text or "").strip()  
    if not text:  
        raise HTTPException(status_code=400, detail="text is empty")  
  
    await session.add_transcript_text(text, source="debug")  
    return {"session_id": session_id, "status": "added"}  
  
  
@app.post("/api/session/{session_id}/chat")  
async def chat_with_meeting(session_id: str, req: ChatRequest):  
    session = get_session_or_404(session_id)  
    manager = get_manager_or_404(session_id)  
  
    validate_prompt_set_or_400(req.prompt_set_name)  
  
    user_message = (req.message or "").strip()  
    if not user_message:  
        raise HTTPException(status_code=400, detail="message is empty")  
  
    try:  
        context_mode = req.context_mode or "current_transcript"  
        context_title = ""  
        context_summary = ""  
        context_text = ""  
        docs = []  
        search_query = ""  
        retrieval_reason = ""  
        need_search = False  
  
        if context_mode == "current_transcript":  
            conversation = await session.get_transcript_text_for_analysis()  
            manager.set_transcript(conversation)  
  
            if not manager.has_transcript():  
                raise HTTPException(status_code=400, detail="No transcript to chat with")  
  
            context_text = conversation  
            context_title = manager.current_topic_title or "現在の会話"  
            context_summary = manager.current_summary or ""  
            docs = manager.current_docs or []  
  
            manager.set_chat_context(mode="current_transcript", topic_id=None)  
  
        elif context_mode == "history":  
            if not req.topic_id:  
                raise HTTPException(status_code=400, detail="topic_id is required for history mode")  
  
            item = get_history_item_by_topic_id(req.topic_id)  
            if not item:  
                raise HTTPException(status_code=404, detail="history not found")  
  
            context_text = item.get("transcript", "") or ""  
            context_title = item.get("title", "") or "履歴"  
            context_summary = item.get("summary", "") or ""  
            docs = item.get("retrieved_docs", []) or []  
  
            manager.set_chat_context(mode="history", topic_id=req.topic_id)  
  
        else:  
            raise HTTPException(status_code=400, detail="invalid context_mode")  
  
        history_before = manager.get_chat_messages()  
        base_docs = docs[:]  
  
        if req.use_rag:  
            decision = await run_in_threadpool(  
                decide_retrieval,  
                context_title,  
                context_summary,  
                context_text,  
                history_before,  
                user_message,  
                base_docs,  
            )  
  
            need_search = bool(decision.get("need_search", False))  
            retrieval_reason = decision.get("reason", "") or ""  
  
            if need_search:  
                search_query = (decision.get("search_query") or "").strip() or user_message  
                extra_docs = await run_in_threadpool(  
                    search_documents,  
                    "hybrid",  
                    search_query  
                )  
                docs = merge_docs(base_docs, extra_docs)  
            else:  
                docs = base_docs  
  
        reply = await run_in_threadpool(  
            generate_chat_response,  
            context_text,  
            user_message,  
            history_before,  
            docs,  
            req.prompt_set_name,  
            context_title,  
            context_summary,  
        )  
  
        manager.add_chat_message("user", user_message)  
        manager.add_chat_message("assistant", reply)  
  
        return {  
            "status": "ok",  
            "reply": reply,  
            "messages": manager.get_chat_messages(),  
            "context": manager.get_chat_context(),  
            "context_title": context_title,  
            "summary": context_summary,  
            "search_query": search_query,  
            "docs": docs,  
            "need_search": need_search,  
            "retrieval_reason": retrieval_reason,  
        }  
  
    except HTTPException:  
        raise  
    except Exception as e:  
        raise HTTPException(status_code=500, detail=f"Chat failed: {str(e)}")  
    

  
  
@app.post("/api/session/{session_id}/chat/clear")  
async def clear_chat(session_id: str):  
    manager = get_manager_or_404(session_id)  
    manager.clear_chat()  
    manager.reset_chat_context()  
    return {"status": "cleared"}  
  
  
@app.post("/api/session/{session_id}/chat/load_history/{topic_id}")  
async def load_history_into_chat(session_id: str, topic_id: str):  
    manager = get_manager_or_404(session_id)  
  
    item = get_history_item_by_topic_id(topic_id)  
    if not item:  
        raise HTTPException(status_code=404, detail="history not found")  
  
    manager.clear_chat()  
    manager.set_chat_context(mode="history", topic_id=topic_id)  
  
    manager.add_chat_message(  
        "assistant",  
        f"履歴「{item.get('title', '無題')}」を読み込みました。内容について質問できます。"  
    )  
  
    return {  
        "status": "ok",  
        "messages": manager.get_chat_messages(),  
        "context": manager.get_chat_context(),  
        "history_item": {  
            "topic_id": item.get("topic_id"),  
            "title": item.get("title", ""),  
            "summary": item.get("summary", ""),  
            "analysis": item.get("analysis", ""),  
        }  
    }  
  
  

@app.post("/api/history/{topic_id}/chat")  
async def chat_with_history(topic_id: str, req: ChatRequest):  
    validate_prompt_set_or_400(req.prompt_set_name)  
  
    user_message = (req.message or "").strip()  
    if not user_message:  
        raise HTTPException(status_code=400, detail="message is empty")  
  
    item = get_history_item_by_topic_id(topic_id)  
    if not item:  
        raise HTTPException(status_code=404, detail="history not found")  
  
    try:  
        context_text = item.get("transcript", "") or ""  
        context_title = item.get("title", "") or "履歴"  
        context_summary = item.get("summary", "") or ""  
        docs = item.get("retrieved_docs", []) or []  
        search_query = ""  
        retrieval_reason = ""  
        need_search = False  
  
        if req.use_rag:  
            decision = await run_in_threadpool(  
                decide_retrieval,  
                context_title,  
                context_summary,  
                context_text,  
                [],   # sessionless なのでサーバ側会話履歴なし  
                user_message,  
                docs,  
            )  
  
            need_search = bool(decision.get("need_search", False))  
            retrieval_reason = decision.get("reason", "") or ""  
  
            if need_search:  
                search_query = (decision.get("search_query") or "").strip() or user_message  
                extra_docs = await run_in_threadpool(  
                    search_documents,  
                    "hybrid",  
                    search_query  
                )  
                docs = merge_docs(docs, extra_docs)  
  
        reply = await run_in_threadpool(  
            generate_chat_response,  
            context_text,  
            user_message,  
            [],  
            docs,  
            req.prompt_set_name,  
            context_title,  
            context_summary,  
        )  
  
        return {  
            "status": "ok",  
            "reply": reply,  
            "context_title": context_title,  
            "summary": context_summary,  
            "search_query": search_query,  
            "docs": docs,  
            "need_search": need_search,  
            "retrieval_reason": retrieval_reason,  
        }  
  
    except Exception as e:  
        raise HTTPException(status_code=500, detail=f"History chat failed: {str(e)}")  



@app.on_event("shutdown")  
async def shutdown_event():  
    for session in list(sessions.values()):  
        try:  
            await session.stop()  
        except Exception:  
            pass  
  
    sessions.clear()  
    meeting_managers.clear()  


@app.get("/ocr", response_class=HTMLResponse)  
async def ocr_page(request: Request):  
    return templates.TemplateResponse(  
        request=request,  
        name="ocr.html",  
        context={"request": request},  
    )  



@app.post("/api/ocr/upload")  
async def upload_ocr_file(file: UploadFile = File(...)):  
    data = await file.read()  
    saved_name = await run_in_threadpool(upload_file_to_ocr_input, file.filename, data)  
    return {"status": "ok", "file_name": saved_name}  



@app.get("/api/ocr/uploads")  
async def get_ocr_uploads():  
    items = await run_in_threadpool(list_uploaded_files)  
    return {"items": items}  


  
class OCRRunRequest(BaseModel):  
    blob_name: str  
  
@app.post("/api/ocr/run")  
async def run_ocr(req: OCRRunRequest):  
    try:  
        result_blob = await run_in_threadpool(process_ocr_from_blob, req.blob_name)  
        return {"status": "ok", "result_blob_name": result_blob}  
    except Exception as e:  
        raise HTTPException(status_code=500, detail=f"OCR failed: {str(e)}")  
    


@app.get("/api/ocr/results")  
async def get_ocr_results():  
    items = await run_in_threadpool(list_ocr_results)  
    return {"items": items}  



  
@app.get("/api/ocr/result/{blob_name}")  
async def get_ocr_result(blob_name: str):  
    try:  
        text = await run_in_threadpool(load_ocr_result_text, blob_name)  
        return {  
            "name": blob_name,  
            "text": text,  
        }  
    except Exception as e:  
        raise HTTPException(status_code=404, detail=f"result not found: {str(e)}")

 
  
@app.delete("/api/ocr/upload/{blob_name}")  
async def delete_ocr_upload(blob_name: str):  
    try:  
        await run_in_threadpool(delete_uploaded_file, blob_name)  
        return {"status": "deleted", "name": blob_name}  
    except Exception as e:  
        raise HTTPException(status_code=500, detail=f"delete failed: {str(e)}")  



  
@app.delete("/api/ocr/result/{blob_name}")  
async def delete_ocr_result_api(blob_name: str):  
    try:  
        await run_in_threadpool(delete_ocr_result, blob_name)  
        return {"status": "deleted", "name": blob_name}  
    except Exception as e:  
        raise HTTPException(status_code=500, detail=f"delete failed: {str(e)}") 


from fastapi.responses import PlainTextResponse  
  
@app.get("/api/ocr/result/{blob_name}/download")  
async def download_ocr_result(blob_name: str):  
    text = await run_in_threadpool(load_ocr_result_text, blob_name)  
    return PlainTextResponse(  
        content=text,  
        media_type="text/markdown; charset=utf-8",  
        headers={  
            "Content-Disposition": f'attachment; filename="{blob_name}"'  
        },  
    )    


@app.get("/documents", response_class=HTMLResponse)  
async def documents_page(request: Request):  
    return templates.TemplateResponse(  
        request=request,  
        name="documents.html",  
        context={  
            "request": request,  
        },  
    )  

@app.get("/api/documents")  
async def api_documents(  
    date_from: Optional[str] = None,  
    date_to: Optional[str] = None,  
    keyword: str = "",  
):  
    try:  
        parsed_date_from = datetime.date.fromisoformat(date_from) if date_from else None  
        parsed_date_to = datetime.date.fromisoformat(date_to) if date_to else None  


        items = await run_in_threadpool(  
            list_transcripts,  
            parsed_date_from,  
            parsed_date_to,  
            keyword,  
        )  
  
        return {  
            "items": [  
                {  
                    "name": x.get("name", ""),  
                    "path": x.get("path", ""),  
                    "date": x.get("date", ""),  
                    "size": x.get("size", 0),  
                    "last_modified": serialize_blob_dt(x.get("last_modified")),  
                    "status": "✅ 処理済み",  
                }  
                for x in items  
            ]  
        }  
    except Exception as e:  
        raise HTTPException(status_code=500, detail=f"documents list failed: {str(e)}")  
    

@app.get("/api/documents/detail")  
async def api_document_detail(transcript_path: str):  
    if not transcript_path:  
        raise HTTPException(status_code=400, detail="transcript_path is required")  
  
    try:  
        detail = await run_in_threadpool(get_document_detail, transcript_path)  
        return {  
            "transcript_path": detail["transcript_path"],  
            "source_file": detail["source_file"],  
            "duration": detail["duration"],  
            "language": detail["language"],  
            "processed_at": detail["processed_at"],  
            "segments": detail["segments"],  
            "transcript_text": detail["transcript_text"],  
            "txt_data": detail["txt_data"],  
            "media_path": detail["media_path"],  
            "is_audio": detail["is_audio"],  
            "is_video": detail["is_video"],  
        }  
    except Exception as e:  
        raise HTTPException(status_code=500, detail=f"document detail failed: {str(e)}")  


@app.get("/api/documents/download/json")  
async def api_document_download_json(transcript_path: str):  
    try:  
        detail = await run_in_threadpool(get_document_detail, transcript_path)  
        import json  
        content = json.dumps(detail["transcript_json"], ensure_ascii=False, indent=2)  
        filename = transcript_path.split("/")[-1]  
        return PlainTextResponse(  
            content=content,  
            media_type="application/json; charset=utf-8",  
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},  
        )  
    except Exception as e:  
        raise HTTPException(status_code=500, detail=f"json download failed: {str(e)}")  
    

@app.get("/api/documents/download/text")  
async def api_document_download_text(transcript_path: str):  
    try:  
        detail = await run_in_threadpool(get_document_detail, transcript_path)  
        filename = transcript_path.split("/")[-1].replace("_transcript.json", "_transcript.txt")  
        return PlainTextResponse(  
            content=detail["txt_data"] or detail["transcript_text"],  
            media_type="text/plain; charset=utf-8",  
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},  
        )  
    except Exception as e:  
        raise HTTPException(status_code=500, detail=f"text download failed: {str(e)}")  
    

@app.get("/api/documents/media")  
async def api_document_media(media_path: str):  
    if not media_path:  
        raise HTTPException(status_code=400, detail="media_path is required")  
  
    try:  
        media = await run_in_threadpool(load_media, media_path)  
        ext = media_path.lower().rsplit(".", 1)[-1] if "." in media_path else ""  
  
        media_type_map = {  
            "mp3": "audio/mpeg",  
            "wav": "audio/wav",  
            "m4a": "audio/mp4",  
            "ogg": "audio/ogg",  
            "flac": "audio/flac",  
            "mp4": "video/mp4",  
            "mov": "video/quicktime",  
            "webm": "video/webm",  
            "mkv": "video/x-matroska",  
            "avi": "video/x-msvideo",  
        }  
        media_type = media_type_map.get(ext, "application/octet-stream")  
        return Response(content=media, media_type=media_type)  
    except Exception as e:  
        raise HTTPException(status_code=500, detail=f"media load failed: {str(e)}")  
    

@app.post("/api/documents/chat")  
async def api_document_chat(req: DocumentChatRequest):  
    transcript_path = (req.transcript_path or "").strip()  
    message = (req.message or "").strip()  
  
    if not transcript_path:  
        raise HTTPException(status_code=400, detail="transcript_path is required")  
    if not message:  
        raise HTTPException(status_code=400, detail="message is empty")  
  
    try:  
        detail = await run_in_threadpool(get_document_detail, transcript_path)  
  
        result = await run_in_threadpool(  
            rag_answer_for_document,  
            detail["transcript_text"],  
            message,  
            req.search_mode,  
            req.top_k,  
            req.use_query_rewrite,  
            detail["source_file"],  
            transcript_path,  
        )  
  
        return {  
            "status": "ok",  
            "summary": result["summary"],  
            "intent_summary": result["intent_summary"],  
            "final_query": result["final_query"],  
            "contexts": result["contexts"],  
            "answer": result["answer"],  
        }  
    except Exception as e:  
        raise HTTPException(status_code=500, detail=f"document chat failed: {str(e)}")  