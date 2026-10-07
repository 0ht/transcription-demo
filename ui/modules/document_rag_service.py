#modules/document_rag_service.py
from modules.search_service import search_transcripts  
from modules.llm import (  
    summarize_current_call,  
    summarize_question_intent,  
    generate_search_query,  
    answer_with_context,  
)  
from modules.config import aoai_model  
  
  
def rag_answer_for_document(  
    document_text: str,  
    question: str,  
    search_mode: str = "semantic_hybrid",  
    top_k: int = 5,  
    use_query_rewrite: bool = True,  
    source_file: str = None,  
    transcript_path: str = None,  
    model_name: str = aoai_model,  
) -> dict:  
    document_text = (document_text or "").strip()  
    question = (question or "").strip()  
  
    if not document_text:  
        raise ValueError("document_text is empty")  
    if not question:  
        raise ValueError("question is empty")  
  
    current_summary = summarize_current_call(document_text[:12000])  
  
    intent_summary = ""  
    if use_query_rewrite:  
        intent_summary = summarize_question_intent(question, model_name=model_name)  
        final_query = generate_search_query(  
            question=question,  
            current_call_summary=current_summary,  
            intent_summary=intent_summary,  
            model_name=model_name,  
        )  
    else:  
        final_query = question  
  
    contexts = search_transcripts(  
        query=final_query,  
        mode=search_mode,  
        top=top_k,  
        source_file=source_file,  
        transcript_path=transcript_path,  
    )  
  
    answer = ""  
    if contexts:  
        answer = answer_with_context(  
            question=question,  
            current_call_summary=current_summary,  
            contexts=contexts,  
            model_name=model_name,  
        )  
  
    return {  
        "summary": current_summary,  
        "intent_summary": intent_summary,  
        "final_query": final_query,  
        "contexts": contexts,  
        "answer": answer,  
    }  