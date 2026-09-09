# ui/search_service.py  
import os  
from typing import Optional  
  
from azure.core.credentials import AzureKeyCredential  
from azure.identity import DefaultAzureCredential 
from azure.identity import DefaultAzureCredential  
from azure.search.documents import SearchClient  
from azure.search.documents.models import VectorizableTextQuery, QueryType, QueryCaptionType  
from modules.config import SEARCH_ENDPOINT,SEARCH_KEY,SEARCH_INDEX,SEMANTIC_CONFIG_NAME,read_fields
  
  
  
_search_client_instance = None  
  
  
def get_search_client() -> SearchClient:  
    global _search_client_instance  
    if _search_client_instance is None:  
        if SEARCH_KEY:
            _search_client_instance = SearchClient(  
                endpoint=SEARCH_ENDPOINT,  
                index_name=SEARCH_INDEX,  
                credential=AzureKeyCredential(SEARCH_KEY)  
            )         
        else:
            credential = DefaultAzureCredential()  
            _search_client_instance = SearchClient(  
                endpoint=SEARCH_ENDPOINT,  
                index_name=SEARCH_INDEX,  
                credential=credential,  
            )  
    return _search_client_instance  
  
  
def reset_search_client():  
    global _search_client_instance  
    _search_client_instance = None  
  
  
def _build_filter(source_file: Optional[str] = None, transcript_path: Optional[str] = None) -> Optional[str]:  
    filters = []  
    if source_file:  
        source_file_escaped = source_file.replace("'", "''")  
        filters.append(f"source_file eq '{source_file_escaped}'")  
    # transcript_path はインデクサー投入では相対 blob パスを再現できないため絞り込みに使わない（source_file で一意）
  
    if not filters:  
        return None  
    return " and ".join(filters)  
  
  
def search_transcripts(  
    query: str,  
    mode: str = "hybrid",  
    top: int = 5,  
    source_file: Optional[str] = None,  
    transcript_path: Optional[str] = None,  
) -> list[dict]:  
    client = get_search_client()  
    filter_expr = _build_filter(source_file=source_file, transcript_path=transcript_path)  
  
    kwargs = {  
        "top": top,  
        "select": read_fields,  
        "filter": filter_expr,  
    }  
  
    if mode in ("hybrid","semantic_hybrid","vector"):  
        vector_query = VectorizableTextQuery(  
            text=query,  
            k_nearest_neighbors=50,  
            fields="text_vector",  
            exhaustive=True,  
        )  
  
    if mode == "hybrid":  
        results = client.search(  
            search_text=query,  
            vector_queries=[vector_query],  
            **kwargs,  
        )  
    elif mode == "semantic_hybrid":  
        results = client.search(  
            search_text=query,  
            vector_queries=[vector_query],  
            query_type=QueryType.SEMANTIC,  
            semantic_configuration_name=SEMANTIC_CONFIG_NAME,  
            query_caption="extractive",  
            **kwargs,  
        ) 

    elif mode == "vector":  
        results = client.search(  
            vector_queries=[vector_query],  
            **kwargs,  
        )  
    elif mode == "keyword":  
        results = client.search(  
            search_text=query,  
            **kwargs,  
        )  
    else:  
        raise ValueError(f"Unsupported search mode: {mode}")  
  
    items = []  
    for r in results:  
        item = {}  
        for f in read_fields:  
            item[f] = r.get(f, "")  
    
        item["@search.score"] = r.get("@search.score")  
        item["@search.reranker_score"] = r.get("@search.reranker_score")  
    
        captions = r.get("@search.captions")  
        if captions:  
            item["@search.captions"] = [  
                {  
                    "text": getattr(c, "text", str(c)),  
                    "highlights": getattr(c, "highlights", None),  
                }  
                for c in captions  
            ]  
        else:  
            item["@search.captions"] = []  
    
        items.append(item)  
    
    return items  