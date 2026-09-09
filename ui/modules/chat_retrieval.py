#modules/chat_retrieval.py
import json  
import logging  
from typing import List, Dict, Any  
  
from modules.config import aoai_client, aoai_model  
  
  
RETRIEVAL_DECISION_PROMPT = """あなたはユーザーサポート支援AIの検索判定器です。  
ユーザーとの会話の文字おこし、過去のチャット履歴、既存の関連文書、最新のユーザー質問を見て、  
外部文書検索が必要かどうかを判定してください。  
  
このシステムの検索先は Azure AI Search 上の社内文書・マニュアル・参考資料です。  
  
判定ルール:  
- ユーザーとの会話コンテキスト、要約、既存の関連文書、会話履歴だけで十分に答えられるなら need_search=false  
- 新しい根拠、出典、原文、正式名称、数値、日時、規程、仕様、別資料の確認が必要なら need_search=true  
- ユーザーが要約、整理、言い換え、簡略化、箇条書き化を求めているだけなら通常は need_search=false  
- need_search=true の場合は、会話文脈を踏まえて Azure AI Search 向けの検索クエリを作成してください  
- 検索クエリは自然文ではなく、検索に適した単語列にしてください  
- 類義語や言い換えを含めてもかまいません  
- 必ずJSON形式のみで返してください  
  
出力形式:  
{  
  "need_search": true,  
  "search_query": "検索語1 検索語2 検索語3",  
  "reason": "判定理由"  
}  
"""  
  
  
def _format_chat_history(chat_history: List[Dict[str, str]], max_turns: int = 6) -> str:  
    recent = chat_history[-max_turns:] if chat_history else []  
    lines = []  
    for msg in recent:  
        role = (msg.get("role") or "").strip()  
        content = (msg.get("content") or "").strip()  
        if role and content:  
            lines.append(f"{role}: {content}")  
    return "\n".join(lines)  
  
  
def _format_existing_docs(existing_docs: List[Dict[str, Any]], max_docs: int = 3, max_chars: int = 400) -> str:  
    if not existing_docs:  
        return "なし"  
  
    chunks = []  
    for i, d in enumerate(existing_docs[:max_docs], start=1):  
        title = d.get("title", "No Title")  
        content = (d.get("content") or d.get("chunk") or d.get("text") or "").strip()  
        if len(content) > max_chars:  
            content = content[:max_chars] + "..."  
        chunks.append(f"[既存文書{i}] タイトル: {title}\n内容: {content}")  
    return "\n\n".join(chunks)  
  
  
def decide_retrieval(  
    context_title: str,  
    summary: str,  
    context_text: str,  
    chat_history: List[Dict[str, str]],  
    user_message: str,  
    existing_docs: List[Dict[str, Any]],  
) -> Dict[str, Any]:  
    logging.info("chat retrieval decision start")  
  
    history_text = _format_chat_history(chat_history, max_turns=6)  
    docs_text = _format_existing_docs(existing_docs, max_docs=3, max_chars=400)  
  
    # context_text 全量は長くなりやすいので切る  
    # TODO:実際には全量を入れる
    short_context = (context_text or "").strip()  
    if len(short_context) > 2000:  
        short_context = short_context[-2000:]  
  
    user_content = f"""以下の情報をもとに検索要否を判定してください。  
  
【対象タイトル】  
{context_title or "未設定"}  
  
【対象要約】  
{summary or "なし"}  
  
【ユーザーとの会話コンテキスト抜粋】  
{short_context or "なし"}  
  
【既存の関連文書】  
{docs_text}  
  
【チャット履歴】  
{history_text or "なし"}  
  
【最新のユーザー質問】  
{user_message}  
"""  
  
    try:  
        completion = aoai_client.chat.completions.create(  
            model=aoai_model,  #TODO:レスポンスが早いモデルを選択
            messages=[  
                {"role": "system", "content": RETRIEVAL_DECISION_PROMPT},  
                {"role": "user", "content": user_content},  
            ],  
            response_format={"type": "json_object"},  
        )  
  
        content = (completion.choices[0].message.content or "").strip()  
        result = json.loads(content)  
  
        return {  
            "need_search": bool(result.get("need_search", False)),  
            "search_query": (result.get("search_query") or "").strip(),  
            "reason": (result.get("reason") or "").strip(),  
        }  
  
    except Exception:  
        logging.exception("chat retrieval decision failed")  
        # 失敗時は安全側で検索する  
        return {  
            "need_search": True,  
            "search_query": user_message.strip(),  
            "reason": "判定失敗のためフォールバック",  
        }  
  
  
def merge_docs(base_docs: List[Dict[str, Any]], extra_docs: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:  
    merged = []  
    seen = set()  
  
    for d in (base_docs or []) + (extra_docs or []):  
        title = (d.get("title") or "").strip()  
        content = (d.get("content") or d.get("chunk") or d.get("text") or "").strip()  
        key = (title, content[:200])  
  
        if key in seen:  
            continue  
        seen.add(key)  
        merged.append(d)  
  
        if len(merged) >= limit:  
            break  
  
    return merged  