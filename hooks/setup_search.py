# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""postprovision hook: Azure AI Search の index / datasource / skillset / indexer を
データプレーン(az rest)で作成し、統合ベクトル化で output コンテナの文字起こし JSON を取り込む。

閉域構成のため:
  1. Search が Storage / OpenAI を呼ぶための shared private link 接続を対象側で承認
  2. dev マシンからデータプレーン操作するため Search を一時的に public 開放（完了後に閉鎖）
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

logging.basicConfig(level=logging.INFO, format=">>> %(message)s")
LOGGER = logging.getLogger(__name__)

SEARCH_API_VERSION = "2024-07-01"
SEARCH_AUDIENCE = "https://search.azure.com"
EMBEDDING_DIMENSIONS = 3072
PUBLIC_READY_TIMEOUT = 300
POLL_INTERVAL = 10

INDEX_NAME_DEFAULT = "documents"
DATASOURCE_NAME = "ds-transcripts"
SKILLSET_NAME = "ss-transcripts"
INDEXER_NAME = "ix-transcripts"


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} が設定されていません。")
    return value


def _az_exe() -> str:
    exe = shutil.which("az") or shutil.which("az.cmd")
    if exe is None:
        raise RuntimeError("Azure CLI (az) が PATH にありません。")
    return exe


def az(*args: str, parse: bool = True):
    result = subprocess.run(
        [_az_exe(), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"az {' '.join(args)} failed: {message}")
    out = result.stdout.strip()
    if not parse or not out:
        return out or None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out


def az_rest_put(url: str, body: dict) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(body, f, ensure_ascii=False)
        body_path = f.name
    try:
        az(
            "rest",
            "--method", "put",
            "--url", url,
            "--resource", SEARCH_AUDIENCE,
            "--headers", "Content-Type=application/json",
            "--body", f"@{body_path}",
            parse=False,
        )
    finally:
        os.unlink(body_path)


def approve_shared_private_link(target_resource_id: str, label: str) -> None:
    """対象リソース側の pending な private endpoint connection（Search の SPL）を承認する。"""
    connections = az(
        "network", "private-endpoint-connection", "list",
        "--id", target_resource_id,
    ) or []
    pending = [
        c for c in connections
        if (c.get("properties", {})
             .get("privateLinkServiceConnectionState", {})
             .get("status", "")).lower() == "pending"
    ]
    if not pending:
        LOGGER.info("%s: 承認待ちの shared private link はありません。", label)
        return
    for conn in pending:
        az(
            "network", "private-endpoint-connection", "approve",
            "--id", conn["id"],
            "--description", "Approved by azd setup_search hook",
            parse=False,
        )
        LOGGER.info("%s: shared private link を承認しました (%s)", label, conn.get("name"))


def set_search_public_access(search_id: str, enabled: bool) -> None:
    state = "enabled" if enabled else "disabled"
    az(
        "resource", "update",
        "--ids", search_id,
        "--set", f"properties.publicNetworkAccess={state}",
        "--api-version", "2023-11-01",
    )
    LOGGER.info("Azure AI Search public access: %s", state)


def wait_for_search_ready(endpoint: str) -> None:
    """public 開放後、データプレーンに到達できるまで待機（401/403 でも到達とみなす）。"""
    url = f"{endpoint}/indexes?api-version={SEARCH_API_VERSION}&$select=name"
    deadline = time.time() + PUBLIC_READY_TIMEOUT
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=10)
            return
        except urllib.error.HTTPError:
            # 401/403 = 認証エラーだが到達はしている
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(POLL_INTERVAL)
    LOGGER.warning("Search 到達性の確認がタイムアウトしました。処理を継続します。")


def build_index(index_name: str, aoai_endpoint: str, embed_deployment: str, semantic: str) -> dict:
    return {
        "name": index_name,
        "fields": [
            {
                "name": "id",
                "type": "Edm.String",
                "key": True,
                "searchable": True,
                "filterable": True,
                "analyzer": "keyword",
            },
            {"name": "parent_id", "type": "Edm.String", "filterable": True},
            {"name": "source_file", "type": "Edm.String", "filterable": True, "facetable": True, "searchable": True},
            {"name": "transcript_path", "type": "Edm.String", "filterable": True},
            {"name": "title", "type": "Edm.String", "searchable": True},
            {"name": "chunk", "type": "Edm.String", "searchable": True},
            {
                "name": "text_vector",
                "type": "Collection(Edm.Single)",
                "searchable": True,
                "dimensions": EMBEDDING_DIMENSIONS,
                "vectorSearchProfile": "vprofile",
            },
        ],
        "vectorSearch": {
            "algorithms": [{"name": "hnsw", "kind": "hnsw"}],
            "vectorizers": [
                {
                    "name": "aoai-vec",
                    "kind": "azureOpenAI",
                    "azureOpenAIParameters": {
                        "resourceUri": aoai_endpoint,
                        "deploymentId": embed_deployment,
                        "modelName": "text-embedding-3-large",
                    },
                }
            ],
            "profiles": [{"name": "vprofile", "algorithm": "hnsw", "vectorizer": "aoai-vec"}],
        },
        "semantic": {
            "configurations": [
                {
                    "name": semantic,
                    "prioritizedFields": {
                        "titleField": {"fieldName": "title"},
                        "prioritizedContentFields": [{"fieldName": "chunk"}],
                    },
                }
            ]
        },
    }


def build_datasource(storage_id: str) -> dict:
    return {
        "name": DATASOURCE_NAME,
        "type": "azureblob",
        # ResourceId 形式 + 資格情報なし → Search の system MI で接続（shared private link 経由）
        "credentials": {"connectionString": f"ResourceId={storage_id};"},
        "container": {"name": "output"},
    }


def build_skillset(index_name: str, aoai_endpoint: str, embed_deployment: str) -> dict:
    return {
        "name": SKILLSET_NAME,
        "skills": [
            {
                "@odata.type": "#Microsoft.Skills.Text.MergeSkill",
                "context": "/document",
                "insertPreTag": "\n",
                "insertPostTag": "",
                "inputs": [{"name": "itemsToInsert", "source": "/document/segments/*/text"}],
                "outputs": [{"name": "mergedText", "targetName": "merged_text"}],
            },
            {
                "@odata.type": "#Microsoft.Skills.Text.SplitSkill",
                "context": "/document",
                "textSplitMode": "pages",
                "maximumPageLength": 2000,
                "pageOverlapLength": 500,
                "inputs": [{"name": "text", "source": "/document/merged_text"}],
                "outputs": [{"name": "textItems", "targetName": "pages"}],
            },
            {
                "@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
                "context": "/document/pages/*",
                "resourceUri": aoai_endpoint,
                "deploymentId": embed_deployment,
                "modelName": "text-embedding-3-large",
                "dimensions": EMBEDDING_DIMENSIONS,
                "inputs": [{"name": "text", "source": "/document/pages/*"}],
                "outputs": [{"name": "embedding", "targetName": "text_vector"}],
            },
        ],
        "indexProjections": {
            "selectors": [
                {
                    "targetIndexName": index_name,
                    "parentKeyFieldName": "parent_id",
                    "sourceContext": "/document/pages/*",
                    "mappings": [
                        {"name": "chunk", "source": "/document/pages/*"},
                        {"name": "text_vector", "source": "/document/pages/*/text_vector"},
                        {"name": "source_file", "source": "/document/sourceFile"},
                        {"name": "transcript_path", "source": "/document/metadata_storage_path"},
                        {"name": "title", "source": "/document/sourceFile"},
                    ],
                }
            ],
            "parameters": {"projectionMode": "skipIndexingParentDocuments"},
        },
    }


def build_indexer(index_name: str) -> dict:
    return {
        "name": INDEXER_NAME,
        "dataSourceName": DATASOURCE_NAME,
        "skillsetName": SKILLSET_NAME,
        "targetIndexName": index_name,
        "parameters": {
            "configuration": {
                "parsingMode": "json",
                "indexedFileNameExtensions": ".json",
                "excludedFileNameExtensions": ".txt",
            }
        },
    }


def main() -> int:
    subscription = required_env("AZURE_SUBSCRIPTION_ID")
    resource_group = required_env("AZURE_RESOURCE_GROUP")
    search_endpoint = required_env("AZURE_SEARCH_ENDPOINT").rstrip("/")
    search_name = required_env("AZURE_SEARCH_SERVICE_NAME")
    index_name = os.environ.get("AZURE_SEARCH_INDEX_NAME", INDEX_NAME_DEFAULT)
    semantic = os.environ.get("AZURE_SEARCH_SEMANTIC_CONFIG", "default-semantic")
    aoai_endpoint = required_env("AZURE_OPENAI_ENDPOINT").rstrip("/") + "/"
    embed_deployment = required_env("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
    storage_name = required_env("AZURE_DATA_STORAGE_ACCOUNT_NAME")
    aiservices_name = required_env("AI_SERVICES_ACCOUNT_NAME")

    search_id = (
        f"/subscriptions/{subscription}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.Search/searchServices/{search_name}"
    )
    storage_id = (
        f"/subscriptions/{subscription}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.Storage/storageAccounts/{storage_name}"
    )
    aiservices_id = (
        f"/subscriptions/{subscription}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{aiservices_name}"
    )

    public_enabled = False
    try:
        LOGGER.info("shared private link 接続を承認中...")
        approve_shared_private_link(storage_id, "データ Storage")
        approve_shared_private_link(aiservices_id, "AI Services")

        LOGGER.info("Search を一時的に public 開放...")
        set_search_public_access(search_id, enabled=True)
        public_enabled = True
        wait_for_search_ready(search_endpoint)

        LOGGER.info("index / datasource / skillset / indexer を作成中...")
        az_rest_put(
            f"{search_endpoint}/indexes/{index_name}?api-version={SEARCH_API_VERSION}",
            build_index(index_name, aoai_endpoint, embed_deployment, semantic),
        )
        az_rest_put(
            f"{search_endpoint}/datasources/{DATASOURCE_NAME}?api-version={SEARCH_API_VERSION}",
            build_datasource(storage_id),
        )
        az_rest_put(
            f"{search_endpoint}/skillsets/{SKILLSET_NAME}?api-version={SEARCH_API_VERSION}",
            build_skillset(index_name, aoai_endpoint, embed_deployment),
        )
        az_rest_put(
            f"{search_endpoint}/indexers/{INDEXER_NAME}?api-version={SEARCH_API_VERSION}",
            build_indexer(index_name),
        )

        LOGGER.info("indexer を実行...")
        az(
            "rest",
            "--method", "post",
            "--url", f"{search_endpoint}/indexers/{INDEXER_NAME}/run?api-version={SEARCH_API_VERSION}",
            "--resource", SEARCH_AUDIENCE,
            parse=False,
        )
        LOGGER.info("Search セットアップ完了。")
        return 0
    except (KeyError, RuntimeError, json.JSONDecodeError) as error:
        LOGGER.error("Search セットアップに失敗しました: %s", error)
        return 1
    finally:
        if public_enabled:
            try:
                set_search_public_access(search_id, enabled=False)
            except RuntimeError as rollback_error:
                LOGGER.error("Search public access の閉鎖に失敗: %s", rollback_error)


if __name__ == "__main__":
    raise SystemExit(main())
