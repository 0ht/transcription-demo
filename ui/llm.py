# ui/llm.py  
import json  
  
from openai import AzureOpenAI  
from azure.identity import DefaultAzureCredential  
  
from config import (  
    AZURE_OPENAI_ENDPOINT,  
    AZURE_OPENAI_CHAT_DEPLOYMENT,  
    AZURE_OPENAI_API_VERSION,  
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT,  
)  
  
_aoai_client_instance = None  
  
  
def get_aoai_client() -> AzureOpenAI:  
    global _aoai_client_instance  
  
    if _aoai_client_instance is None:  
        credential = DefaultAzureCredential()  
  
        def token_provider():  
            return credential.get_token(  
                "https://cognitiveservices.azure.com/.default"  
            ).token  
  
        _aoai_client_instance = AzureOpenAI(  
            azure_endpoint=AZURE_OPENAI_ENDPOINT,  
            api_version=AZURE_OPENAI_API_VERSION,  
            azure_ad_token_provider=token_provider,  
        )  
  
    return _aoai_client_instance  
  
  
def embed_texts(texts: list[str]) -> list[list[float]]:  
    """複数テキストをまとめて Azure OpenAI でベクトル化して返す。  
    リクエスト上限を避けるため一定件数ずつ分割して呼び出す。"""  
    if not texts:  
        return []  
    client = get_aoai_client()  
    vectors: list[list[float]] = []  
    batch_size = 16  
    for i in range(0, len(texts), batch_size):  
        batch = texts[i : i + batch_size]  
        resp = client.embeddings.create(  
            model=AZURE_OPENAI_EMBEDDING_DEPLOYMENT,  
            input=batch,  
        )  
        vectors.extend(d.embedding for d in resp.data)  
    return vectors  
  
  
CURRENT_CALL_SUMMARY_PROMPT = """あなたはコールセンター通話の内容整理アシスタントです。  
通話文字起こしから、外部ナレッジ検索に必要な情報を整理してください。  
  
抽出観点:  
- 顧客の目的  
- 顧客の要望  
- 条件（人数、地域、時期、予算、優先事項など）  
- 問題点や相談内容  
- オペレーターが確認した項目  
- まだ未確定な点  
  
出力は簡潔な箇条書きまたは短文のみ。説明不要。  
"""  
  
INTENT_SYSTEM_PROMPT = """あなたはコールセンター通話の検索支援アシスタントです。  
ユーザーの質問を、検索しやすいように「何を知りたいのか」という意図に要約してください。  
  
対象データは、顧客とサポートオペレーターの通話文字起こしです。  
以下の観点を意識してください:  
- 問い合わせ内容  
- 発生している問題  
- 顧客の要望  
- オペレーターの案内内容  
- 解決状況  
- 次のアクション  
- 約束事項  
  
出力は1〜3文の簡潔な要約のみ。説明不要。"""  
  
QUERY_SYSTEM_PROMPT = """あなたはAzure AI Search向けの検索クエリ生成アシスタントです。  
顧客とサポートオペレーターの通話文字起こしを検索するため、  
ユーザー質問と意図要約から、検索に有効な語句を生成してください。  
  
ルール:  
- 単語または短い句で出力する  
- 半角スペース区切りで並べる  
- 重要概念、類義語、業務用語、言い換えを含める  
- 口語表現は検索向けの表現に補正する  
- 不要な説明文は出さない  
- [] や <<>> は含めない  
"""  
  
ANSWER_SYSTEM_PROMPT = """あなたはコールセンター通話文字起こしを分析するアシスタントです。  
与えられた検索結果だけを根拠に、日本語で回答してください。  
  
検索結果のフィールド構造は固定ではありません。  
各検索結果に含まれるテキストやメタデータを読み取り、回答に使ってください。  
  
回答方針:  
- まず結論を簡潔に述べる  
- 次に根拠を整理して説明する  
- 顧客の要望とオペレーターの案内を区別してよい  
- 未確定な点は未確定と明示する  
- 根拠が不足している場合は推測しない  
- 可能なら参照番号 [1][2] を付ける  
"""  
  
  
def summarize_current_call(transcript_text: str) -> str:  
    client = get_aoai_client()  
  
    resp = client.chat.completions.create(  
        model=AZURE_OPENAI_CHAT_DEPLOYMENT,  
        messages=[  
            {"role": "system", "content": CURRENT_CALL_SUMMARY_PROMPT},  
            {"role": "user", "content": transcript_text},  
        ],  
        temperature=0.1,  
    )  
    return resp.choices[0].message.content.strip()  
  
  
def summarize_question_intent(question: str) -> str:  
    client = get_aoai_client()  
  
    resp = client.chat.completions.create(  
        model=AZURE_OPENAI_CHAT_DEPLOYMENT,  
        messages=[  
            {"role": "system", "content": INTENT_SYSTEM_PROMPT},  
            {"role": "user", "content": question},  
        ],  
        temperature=0.1,  
    )  
    return resp.choices[0].message.content.strip()  
  
  
def generate_search_query_from_intent(  
    question: str,  
    intent_summary: str,  
    current_call_summary: str = "",  
) -> str:  
    client = get_aoai_client()  
  
    user_prompt = f"""ユーザー質問:  
{question}  
  
現在の通話要約:  
{current_call_summary}  
  
意図要約:  
{intent_summary}  
  
上記をもとに検索クエリを生成してください。"""  
  
    resp = client.chat.completions.create(  
        model=AZURE_OPENAI_CHAT_DEPLOYMENT,  
        messages=[  
            {"role": "system", "content": QUERY_SYSTEM_PROMPT},  
            {"role": "user", "content": user_prompt},  
        ],  
        temperature=0.1,  
    )  
    return resp.choices[0].message.content.strip()  
  
  
def answer_with_context(  
    question: str,  
    contexts: list[dict],  
    current_call_summary: str = "",  
) -> str:  
    client = get_aoai_client()  
  
    context_text = "\n\n".join(  
        [  
            f"[{i+1}]\n{json.dumps(c, ensure_ascii=False, indent=2)}"  
            for i, c in enumerate(contexts)  
        ]  
    )  
  
    user_prompt = f"""質問:  
{question}  
  
現在の通話要約:  
{current_call_summary}  
  
検索結果:  
{context_text}  
  
上記の検索結果だけを根拠に回答してください。  
検索結果のフィールド名は固定ではないため、内容を読んで判断してください。"""  
  
    resp = client.chat.completions.create(  
        model=AZURE_OPENAI_CHAT_DEPLOYMENT,  
        messages=[  
            {"role": "system", "content": ANSWER_SYSTEM_PROMPT},  
            {"role": "user", "content": user_prompt},  
        ],  
        temperature=0.2,  
    )  
    return resp.choices[0].message.content.strip()  