from dataclasses import dataclass, field  
from typing import List, Dict, Any, Optional  
from datetime import datetime  
import uuid  

  
  
@dataclass  
class TopicRecord:  
    topic_id: str  
    created_at: str  
    prompt_set: str  
    title: str  
    transcript: str  
    summary: str  
    search_query: str  
    analysis: str  
    retrieved_docs: List[Dict[str, Any]] = field(default_factory=list)  
    issues: List[str] = field(default_factory=list)  
    suggestions: List[str] = field(default_factory=list)  
    next_actions: List[str] = field(default_factory=list)  
    chat_messages: List[Dict[str, str]] = field(default_factory=list)   # 追加  
  
  
class MeetingManager:  
    def __init__(self):  
        self.current_transcript_parts: List[str] = []  
        self.current_topic_title: str = ""  
        self.current_summary: str = ""  
        self.current_search_query: str = ""  
        self.current_analysis: str = ""  
        self.current_docs: List[Dict[str, Any]] = []  
        self.current_issues: List[str] = []  
        self.current_suggestions: List[str] = []  
        self.current_next_actions: List[str] = []  
        self.history: List[Dict[str, Any]] = []  
  
        # chat state  
        self.chat_messages: List[Dict[str, str]] = []  
        self.chat_context_mode: str = "current_transcript"   # or "history"  
        self.chat_context_topic_id: Optional[str] = None  
  
    def add_text(self, text: str):  
        text = (text or "").strip()  
        if text:  
            self.current_transcript_parts.append(text)  
  
    def set_transcript(self, full_text: str):  
        full_text = (full_text or "").strip()  
        if full_text:  
            self.current_transcript_parts = [full_text]  
        else:  
            self.current_transcript_parts = []  
  
    def append_transcript_line(self, text: str):  
        self.add_text(text)  
  
    def get_transcript(self) -> str:  
        return "\n".join(self.current_transcript_parts).strip()  
  
    def has_transcript(self) -> bool:  
        return bool(self.get_transcript())  
  
    def set_analysis_result(  
        self,  
        topic_title: str,  
        summary: str,  
        search_query: str,  
        analysis: str,  
        docs=None,  
        issues=None,  
        suggestions=None,  
        next_actions=None,  
    ):  
        self.current_topic_title = topic_title or ""  
        self.current_summary = summary or ""  
        self.current_search_query = search_query or ""  
        self.current_analysis = analysis or ""  
        self.current_docs = docs or []  
        self.current_issues = issues or []  
        self.current_suggestions = suggestions or []  
        self.current_next_actions = next_actions or []  
  
    def clear_current(self):  
        self.current_transcript_parts = []  
        self.current_topic_title = ""  
        self.current_summary = ""  
        self.current_search_query = ""  
        self.current_analysis = ""  
        self.current_docs = []  
        self.current_issues = []  
        self.current_suggestions = []  
        self.current_next_actions = []  
        self.clear_chat()  
        self.reset_chat_context()  
  
    def confirm_topic(self, prompt_set: str = "default") -> Dict[str, Any]:  
        item = TopicRecord(  
            topic_id=str(uuid.uuid4()),  
            created_at=datetime.utcnow().isoformat(),  
            prompt_set=prompt_set,  
            title=self.current_topic_title or f"議題{len(self.history) + 1}",  
            transcript=self.get_transcript(),  
            summary=self.current_summary,  
            search_query=self.current_search_query,  
            analysis=self.current_analysis,  
            retrieved_docs=self.current_docs,  
            issues=self.current_issues,  
            suggestions=self.current_suggestions,  
            next_actions=self.current_next_actions,  
            chat_messages=self.get_chat_messages(),   # 追加  
        )  
        item_dict = item.__dict__  
        self.history.insert(0, item_dict)  
        self.clear_current()  
        return item_dict  
  
    def load_history(self, items: List[Dict[str, Any]]):  
        self.history = items or []  
  
    # -------------------------  
    # chat methods  
    # -------------------------  
    def get_chat_messages(self) -> List[Dict[str, str]]:  
        return list(self.chat_messages)  
  
    def clear_chat(self):  
        self.chat_messages = []  
  
    def add_chat_message(self, role: str, content: str):  
        role = (role or "").strip()  
        content = (content or "").strip()  
        if role and content:  
            self.chat_messages.append({  
                "role": role,  
                "content": content,  
            })  
  
    def set_chat_context(self, mode: str = "current_transcript", topic_id: Optional[str] = None):  
        self.chat_context_mode = mode or "current_transcript"  
        self.chat_context_topic_id = topic_id  
  
    def get_chat_context(self) -> Dict[str, Any]:  
        return {  
            "mode": self.chat_context_mode,  
            "topic_id": self.chat_context_topic_id,  
        }  
  
    def reset_chat_context(self):  
        self.chat_context_mode = "current_transcript"  
        self.chat_context_topic_id = None  


    def load_topic_record(self, item: Dict[str, Any]):  
        self.set_transcript(item.get("transcript", ""))  
        self.current_topic_title = item.get("title", "") or ""  
        self.current_summary = item.get("summary", "") or ""  
        self.current_search_query = item.get("search_query", "") or ""  
        self.current_analysis = item.get("analysis", "") or ""  
        self.current_docs = item.get("retrieved_docs", []) or []  
        self.current_issues = item.get("issues", []) or []  
        self.current_suggestions = item.get("suggestions", []) or []  
        self.current_next_actions = item.get("next_actions", []) or []  
    
        self.clear_chat()  
        for msg in item.get("chat_messages", []) or []:  
            self.add_chat_message(msg.get("role", ""), msg.get("content", ""))  