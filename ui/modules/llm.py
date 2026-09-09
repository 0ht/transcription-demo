#modules/llm.py

import json  
from typing import List, Dict, Any  
  
from modules.config import aoai_client, aoai_model  
from modules.prompt_store import get_prompt_set  
  
  
# ============================================================================  
# 共通  
# ============================================================================  
  
def format_docs(docs: List[Dict[str, Any]]) -> str:  
    if not docs:  
        return "関連文書は取得されていません。"  
  
    chunks = []  
    for i, d in enumerate(docs, start=1):  
        title = d.get("title", "No Title")  
        content = d.get("content", "") or d.get("chunk", "") or d.get("text", "")  
        chunks.append(f"[文書{i}] タイトル: {title}\n内容: {content}")  
  
    return "\n\n".join(chunks)  
  
  
# ============================================================================  
# 既存: 会議分析  
# ============================================================================  
  
def generate_meeting_feedback(  
    summary: str,  
    conversation: str,  
    docs: List[Dict[str, Any]],  
    prompt_set_name: str = "default",  
    issues=None,  
    suggestions=None,  
    next_actions=None,  
    user_instruction: str = "",  
) -> str:  
    system_prompt = get_prompt_set(prompt_set_name)  
    docs_text = format_docs(docs)  
  
    issues = issues or []  
    suggestions = suggestions or []  
    next_actions = next_actions or []  
  
    prompt = f"""以下は現在の会話に関する情報です。  
  
【会話全文】  
{conversation}  
  
【議題要約】  
{summary}  
  
【ユーザーの今回の要望】  
{user_instruction or "特になし"}  
  
【現時点の論点・懸念】  
{chr(10).join(f"- {x}" for x in issues) if issues else "- なし"}  
  
【提案候補】  
{chr(10).join(f"- {x}" for x in suggestions) if suggestions else "- なし"}  
  
【次アクション候補】  
{chr(10).join(f"- {x}" for x in next_actions) if next_actions else "- なし"}  
  
【関連文書】  
{docs_text}  
  
以下の条件を守って日本語で回答してください。  
- ユーザーの今回の要望があればそれを優先する  
- 回答は冗長にしすぎず、実務で使いやすい形にする  
- 必要に応じて箇条書きを使う  
- 後から出た修正指示を優先する  
"""  
  
    res = aoai_client.chat.completions.create(  
        model=aoai_model,  
        messages=[  
            {"role": "system", "content": system_prompt},  
            {"role": "user", "content": prompt},  
        ],  
    )  
  
    return (res.choices[0].message.content or "").strip()  
  
  
def generate_chat_response(  
    context_text: str,  
    user_message: str,  
    chat_history: List[Dict[str, str]],  
    docs: List[Dict[str, Any]],  
    prompt_set_name: str = "default",  
    context_title: str = "",  
    summary: str = "",  
) -> str:  
    system_prompt = get_prompt_set(prompt_set_name)  
    docs_text = format_docs(docs)  
  
    history_messages = []  
    for msg in chat_history[-10:]:  
        role = msg.get("role")  
        content = (msg.get("content") or "").strip()  
        if role in {"user", "assistant"} and content:  
            history_messages.append({  
                "role": role,  
                "content": content,  
            })  
  
    context_block = f"""あなたはカスタマーサポート支援AIです。  
通話の文字おこし、過去履歴、関連文書をもとに、ユーザーの質問に日本語で簡潔かつ実務的に答えてください。  
必要に応じて箇条書きを使ってください。  
情報が足りない場合は、足りない点を明示してください。  
後から出た指示を優先してください。  
  
【対象タイトル】  
{context_title or "未設定"}  
  
【対象要約】  
{summary or "なし"}  
  
【通話コンテキスト】  
{context_text or "なし"}  
  
【関連文書】  
{docs_text}  
"""  
  
    messages = [{"role": "system", "content": system_prompt}]  
    messages.append({"role": "system", "content": context_block})  
    messages.extend(history_messages)  
    messages.append({"role": "user", "content": user_message})  
  
    res = aoai_client.chat.completions.create(  
        model=aoai_model,  
        messages=messages,  
    )  
  
    return (res.choices[0].message.content or "").strip()  
  
  
# ============================================================================  
# 追加: Blob文書RAG / transcript RAG 用  
# ============================================================================  
  
CURRENT_CALL_SUMMARY_PROMPT = """あなたはコールセンター通話の内容整理アシスタントです。  
通話文字起こしや文書内容から、外部ナレッジ検索に必要な情報を整理してください。  
  
抽出観点:  
- 顧客の目的  
- 顧客の要望  
- 条件（人数、地域、時期、予算、優先事項など）  
- 問題点や相談内容  
- オペレーターが確認した項目  
- まだ未確定な点  
  
出力は簡潔な箇条書きまたは短文のみ。説明不要。  
"""  
  
INTENT_SYSTEM_PROMPT = """あなたは検索支援アシスタントです。  
ユーザーの質問を、検索しやすいように「何を知りたいのか」という意図に要約してください。  
  
出力は1〜3文の簡潔な要約のみ。説明不要。  
"""  
  
QUERY_SYSTEM_PROMPT = """あなたは Azure AI Search 向けの検索クエリ生成アシスタントです。  
現在の会話内容または文書内容とユーザー質問をもとに、  
マニュアル・FAQ・過去通話記録・関連文書を検索するための有効な語句を生成してください。  
  
ルール:  
- 単語または短い句で出力する  
- 半角スペース区切りで並べる  
- 文脈中の具体条件をできるだけ反映する  
- 類義語、業務用語、関連表現も必要に応じて含める  
- 不要な説明文は出さない  
- [] や <<>> は含めない  
"""  
  
ANSWER_SYSTEM_PROMPT = """あなたは業務支援アシスタントです。  
現在の会話内容または文書要約と検索結果だけを根拠に、日本語で回答してください。  
  
回答方針:  
- まず結論を簡潔に述べる  
- 次に根拠を整理して説明する  
- 現在の要望や文脈にどう合うかを明示する  
- 不足情報がある場合は追加確認事項も示す  
- 根拠が不足している場合は推測しない  
- 可能なら参照番号 [1][2] を付ける  
"""  
  
  
def summarize_current_call(transcript_text: str) -> str:  
    transcript_text = (transcript_text or "").strip()  
    if not transcript_text:  
        return ""  
  
    resp = aoai_client.chat.completions.create(  
        model=aoai_model,  
        messages=[  
            {"role": "system", "content": CURRENT_CALL_SUMMARY_PROMPT},  
            {"role": "user", "content": transcript_text[:12000]},  
        ],  
    )  
    return (resp.choices[0].message.content or "").strip()  
  
  
def summarize_question_intent(question: str) -> str:  
    question = (question or "").strip()  
    if not question:  
        return ""  
  
    resp = aoai_client.chat.completions.create(  
        model=aoai_model,  
        messages=[  
            {"role": "system", "content": INTENT_SYSTEM_PROMPT},  
            {"role": "user", "content": question},  
        ],  
    )  
    return (resp.choices[0].message.content or "").strip()  
  
  
def generate_search_query(  
    question: str,  
    current_call_summary: str,  
    intent_summary: str = "",  
) -> str:  
    question = (question or "").strip()  
    current_call_summary = (current_call_summary or "").strip()  
    intent_summary = (intent_summary or "").strip()  
  
    user_prompt = f"""ユーザー質問:  
{question or "なし"}  
  
現在の通話/文書要約:  
{current_call_summary or "なし"}  
  
質問意図:  
{intent_summary or "なし"}  
  
上記をもとに検索クエリを生成してください。"""  
  
    resp = aoai_client.chat.completions.create(  
        model=aoai_model,  
        messages=[  
            {"role": "system", "content": QUERY_SYSTEM_PROMPT},  
            {"role": "user", "content": user_prompt},  
        ],  
    )  
    return (resp.choices[0].message.content or "").strip()  
  
  
def answer_with_context(  
    question: str,  
    current_call_summary: str,  
    contexts: List[Dict[str, Any]],  
) -> str:  
    question = (question or "").strip()  
    current_call_summary = (current_call_summary or "").strip()  
    contexts = contexts or []  
  
    context_text = "\n\n".join(  
        [  
            f"[{i + 1}]\n{json.dumps(c, ensure_ascii=False, indent=2)}"  
            for i, c in enumerate(contexts)  
        ]  
    )  
  
    user_prompt = f"""ユーザー質問:  
{question or "なし"}  
  
現在の通話/文書要約:  
{current_call_summary or "なし"}  
  
検索結果:  
{context_text or "なし"}  
  
上記だけを根拠に回答してください。"""  
  
    resp = aoai_client.chat.completions.create(  
        model=aoai_model,  
        messages=[  
            {"role": "system", "content": ANSWER_SYSTEM_PROMPT},  
            {"role": "user", "content": user_prompt},  
        ],  
    )  
    return (resp.choices[0].message.content or "").strip()  