#modules/query_planner.py
import json  
from modules.config import aoai_client, aoai_model_fast  
  
  
def plan_meeting_topic(conversation: str) -> dict:  
    conversation = (conversation or "").strip()  
  
    if not conversation:  
        return {  
            "topic_title": "",  
            "summary": "",  
            "use_rag": False,  
            "search_query": "",  
            "issues": [],  
            "suggestions": [],  
            "next_actions": []  
        }  
  
    prompt = f"""  
あなたはカスタマーサポート支援エージェントです。  
以下の会話内容を分析して、ユーザー意図の整理、検索要否判定、検索クエリ生成を行ってください。  
  
【会話内容】  
{conversation}  
  
要件:  
- 会話の主題を短いタイトルで表現する  
- 会話を簡潔に要約する  
- RAG検索が必要かを判定する  
- 必要な場合は検索に適した短めの検索クエリを作る  
- ユーザーの懸念点・不足情報を issues に入れる  
- ユーザー支援として有用な提案を suggestions に入れる
- 次に取るべき行動を next_actions に入れる  
  
返却は必ず以下のJSON形式:  
{{  
  "topic_title": "議題タイトル",  
  "summary": "会話要約",  
  "use_rag": true,  
  "search_query": "検索クエリ",  
  "issues": ["論点1", "論点2"],  
  "suggestions": ["提案1", "提案2"],  
  "next_actions": ["次アクション1", "次アクション2"]  
}}  
"""  
  
    res = aoai_client.chat.completions.create(  
        model=aoai_model_fast,  
        response_format={"type": "json_object"},  
        messages=[  
            {  
                "role": "system",  
                "content": "You analyze meeting discussions and output structured JSON for RAG planning."  
            },  
            {  
                "role": "user",  
                "content": prompt  
            }  
        ]  
    )  
  
    content = res.choices[0].message.content  
    data = json.loads(content)  
  
    return {  
        "topic_title": data.get("topic_title", ""),  
        "summary": data.get("summary", ""),  
        "use_rag": data.get("use_rag", False),  
        "search_query": data.get("search_query", ""),  
        "issues": data.get("issues", []),  
        "suggestions": data.get("suggestions", []),  
        "next_actions": data.get("next_actions", []),  
    }  