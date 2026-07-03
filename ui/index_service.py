# ui/index_service.py
"""Azure AI Search のインデックス作成と、文字起こし JSON のチャンク登録。

- ensure_index(): "documents" インデックスが無ければ作成する。
- index_transcript(): 文字起こしの segments をチャンク化し、Azure OpenAI で
  ベクトルを計算して push 登録する。

注: クエリ時のベクトル化も UI 側（managed identity）で行う。OpenAI は
パブリックアクセス無効のため Search の統合ベクトライザー（Search→OpenAI 呼び出し）は
使えないので、インデックスにベクトライザーは定義しない。
"""
import base64

from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    SimpleField,
    SearchableField,
    VectorSearch,
    VectorSearchProfile,
    HnswAlgorithmConfiguration,
    SemanticConfiguration,
    SemanticSearch,
    SemanticPrioritizedFields,
    SemanticField,
)

from config import (
    SEARCH_ENDPOINT,
    SEARCH_INDEX_NAME,
    SEMANTIC_CONFIG_NAME,
    AZURE_OPENAI_EMBEDDING_DIMENSIONS,
)
from llm import embed_texts

_VECTOR_PROFILE = "vprofile-hnsw"
_ALGORITHM = "alg-hnsw"

# 1チャンクあたりの目安文字数（segments をこの長さまで連結する）
# Azure OpenAI embedding の入力上限(8192 tokens)を超えないよう、保守的な文字数に抑える。
_CHUNK_CHAR_LIMIT = 1200


def _get_credential():
    return DefaultAzureCredential()


def get_index_client() -> SearchIndexClient:
    return SearchIndexClient(endpoint=SEARCH_ENDPOINT, credential=_get_credential())


def get_write_client() -> SearchClient:
    return SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=SEARCH_INDEX_NAME,
        credential=_get_credential(),
    )


def _build_index() -> SearchIndex:
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SimpleField(name="source_file", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="transcript_path", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="chunk_id", type=SearchFieldDataType.Int32, filterable=True, sortable=True),
        SimpleField(name="speaker", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="start_time", type=SearchFieldDataType.String),
        SearchField(
            name="text_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=AZURE_OPENAI_EMBEDDING_DIMENSIONS,
            vector_search_profile_name=_VECTOR_PROFILE,
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name=_ALGORITHM)],
        profiles=[
            VectorSearchProfile(
                name=_VECTOR_PROFILE,
                algorithm_configuration_name=_ALGORITHM,
            )
        ],
    )

    semantic_search = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name=SEMANTIC_CONFIG_NAME,
                prioritized_fields=SemanticPrioritizedFields(
                    content_fields=[SemanticField(field_name="content")],
                ),
            )
        ]
    )

    return SearchIndex(
        name=SEARCH_INDEX_NAME,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )


def ensure_index() -> bool:
    """インデックスが存在しなければ作成する。作成したら True、既存なら False。"""
    client = get_index_client()
    try:
        client.get_index(SEARCH_INDEX_NAME)
        return False
    except Exception:
        client.create_index(_build_index())
        return True


def _doc_id(transcript_path: str, chunk_id: int) -> str:
    raw = f"{transcript_path}#{chunk_id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _split_text(text: str, limit: int) -> list[str]:
    """1つの長いテキストを limit 文字以内の塊に分割する。改行・句点を優先して区切る。"""
    parts: list[str] = []
    remaining = text.strip()
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut < int(limit * 0.5):
            cut = remaining.rfind("。", 0, limit)
            if cut >= 0:
                cut += 1
        if cut < int(limit * 0.5):
            cut = limit
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def _chunk_segments(segments: list[dict]) -> list[dict]:
    """segments を _CHUNK_CHAR_LIMIT 文字程度のチャンクに連結する。
    1セグメントが長大な場合は内部でさらに分割する。"""
    chunks: list[dict] = []
    buf_lines: list[str] = []
    buf_len = 0
    first_speaker = ""
    first_time = ""

    def flush():
        nonlocal buf_lines, buf_len, first_speaker, first_time
        if buf_lines:
            chunks.append(
                {
                    "content": "\n".join(buf_lines),
                    "speaker": first_speaker,
                    "start_time": first_time,
                }
            )
        buf_lines = []
        buf_len = 0
        first_speaker = ""
        first_time = ""

    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = seg.get("speaker", "Unknown")
        time_info = seg.get("startTime", "")
        for piece in _split_text(text, _CHUNK_CHAR_LIMIT):
            if buf_lines and buf_len + len(piece) > _CHUNK_CHAR_LIMIT:
                flush()
            if not buf_lines:
                first_speaker = speaker
                first_time = time_info
            buf_lines.append(f"[{speaker}] {piece}")
            buf_len += len(piece)
            if buf_len >= _CHUNK_CHAR_LIMIT:
                flush()

    flush()
    return chunks


def delete_transcript_docs(transcript_path: str) -> int:
    """同一 transcript_path の既存ドキュメントを削除する（再登録時の重複防止）。

    ヒット件数が多くても取りこぼさないよう、SearchItemPaged の自動ページングで全件の
    id を収集し、削除 API の 1 リクエスト上限（1000 件）ごとにバッチ削除する。
    """
    client = get_write_client()
    escaped = transcript_path.replace("'", "''")
    # top を指定しない → SDK が nextLink を辿って全ページを列挙する
    results = client.search(
        search_text="*",
        filter=f"transcript_path eq '{escaped}'",
        select=["id"],
    )
    ids = [{"id": r["id"]} for r in results]
    if not ids:
        return 0
    for i in range(0, len(ids), 1000):
        client.delete_documents(documents=ids[i : i + 1000])
    return len(ids)


def index_transcript(transcript: dict, transcript_path: str) -> int:
    """文字起こしをチャンク化・ベクトル化してインデックスに登録する。登録チャンク数を返す。"""
    source_file = transcript.get("sourceFile", "")
    segments = transcript.get("segments", [])
    chunks = _chunk_segments(segments)
    if not chunks:
        return 0

    vectors = embed_texts([c["content"] for c in chunks])

    docs = []
    for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
        docs.append(
            {
                "id": _doc_id(transcript_path, i),
                "content": chunk["content"],
                "source_file": source_file,
                "transcript_path": transcript_path,
                "chunk_id": i,
                "speaker": chunk["speaker"],
                "start_time": chunk["start_time"],
                "text_vector": vector,
            }
        )

    client = get_write_client()
    client.upload_documents(documents=docs)
    return len(docs)
