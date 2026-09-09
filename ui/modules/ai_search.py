#modules/ai_search.py
from modules.config import search_client, read_fields, SEMANTIC_CONFIG_NAME, SEARCH_TOP_COUNT
from azure.search.documents.models import VectorizableTextQuery, QueryType, QueryCaptionType, QueryAnswerType  


def search_documents(method,query):
    search_results = []

    #ハイブリッド検索＋セマンティック
    if method == "hybrid":
        print("ハイブリッド検索＋セマンティック検索")

        vector_query = VectorizableTextQuery(text=query, k_nearest_neighbors=50, fields="text_vector", exhaustive=True)

        results = search_client.search(  
                    search_text=query,
                    vector_queries=[vector_query],
                    semantic_configuration_name=SEMANTIC_CONFIG_NAME,
                    select=read_fields,
                    query_type=QueryType.SEMANTIC, 
                    #query_caption=QueryCaptionType.EXTRACTIVE, 
                    #query_answer=QueryAnswerType.EXTRACTIVE,
                    query_caption="extractive",  
                    query_answer="extractive",  
                    top=SEARCH_TOP_COUNT
                )

        print("検索結果取得")

    #ハイブリッド検索
    elif method == "keyword_vector":
        print("ハイブリッド検索")
        vector_query = VectorizableTextQuery(text=query, k_nearest_neighbors=50, fields="text_vector", exhaustive=True)

        results = search_client.search(  
                    search_text=query,
                    vector_queries=[vector_query],
                    select=read_fields,
                    top=SEARCH_TOP_COUNT
                )

        print("検索結果取得")

    #ベクトル＋セマンティック検索
    elif method == "semantic_vector":
        print("ベクトル検索＋セマンティック検索")

        vector_query = VectorizableTextQuery(text=query, k_nearest_neighbors=50, fields="text_vector", exhaustive=True)

        results = search_client.search(  
                    vector_queries=[vector_query],
                    semantic_configuration_name=SEMANTIC_CONFIG_NAME,
                    select=read_fields,
                    query_type=QueryType.SEMANTIC, 
                    #query_caption=QueryCaptionType.EXTRACTIVE, 
                    #query_answer=QueryAnswerType.EXTRACTIVE,
                    query_caption="extractive",  
                    query_answer="extractive",  
                    top=3
                )

        print("検索結果取得")

    #ベクトル検索
    elif method == "vector":
        print("ベクトル検索")
        vector_query = VectorizableTextQuery(text=query, k_nearest_neighbors=50, fields="text_vector", exhaustive=True)

        results = search_client.search(  
                    vector_queries=[vector_query],
                    select=read_fields,
                    top=SEARCH_TOP_COUNT
                )

        print("検索結果取得")

    #キーワード＋セマンティック検索
    elif method == "keyword_semantic":
        print("キーワード＋セマンティック検索")

        results = search_client.search(  
                    search_text=query,
                    semantic_configuration_name=SEMANTIC_CONFIG_NAME,
                    select=read_fields,
                    query_type=QueryType.SEMANTIC, 
                    #query_caption=QueryCaptionType.EXTRACTIVE, 
                    #query_answer=QueryAnswerType.EXTRACTIVE,
                    query_caption="extractive",  
                    query_answer="extractive",  
                    top=SEARCH_TOP_COUNT
                )

        print("検索結果取得")

    #キーワード検索
    elif method == "keyword":
        print("キーワード検索")
        vector_query = VectorizableTextQuery(text=query, k_nearest_neighbors=50, fields="text_vector", exhaustive=True)

        results = search_client.search(  
                    search_text=query,
                    select=read_fields,
                    top=SEARCH_TOP_COUNT
                )

        print("検索結果取得")

    else:
        print("指定の検索方法は選択できません")
        pass

    for result in results:  
        search_results.append({"title": result["title"], "content": result["chunk"]})  


    print(search_results)
    return search_results  