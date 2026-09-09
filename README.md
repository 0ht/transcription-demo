# Blob 文字起こしシステム

Azure Blob Storage にアップロードした音声・テキストファイルを自動で文字起こしし、結果を Web UI で閲覧・検索（RAG）できるデモシステムです。ネットワークは **UI の Ingress のみ外部公開**、それ以外は Private Endpoint / VNet 統合による閉域構成です。

## アーキテクチャ概要

```
ユーザー ──HTTPS──▶ FastAPI UI (Container Apps)
                        │  (VNet 統合 / Private Endpoint)
   input へアップロード ▼
Blob Storage ─BlobCreated▶ Event Grid ─▶ Storage Queue ─▶ Azure Functions
                                                              │ 音声: Speech Batch Transcription
                                                              │ テキスト: 直接抽出
                                                              ▼
                                            output(結果) / processed(原本退避)
output ─▶ Azure AI Search indexer ─▶ チャンク化 + 統合ベクトル化
RAG: UI ─▶ Azure AI Search（ベクトル+セマンティック） ─▶ Azure OpenAI
```

Azure AI Search は容量を確保しやすいリージョンへ分離できます。既定は `eastus` で、Private Endpoint は他リソースと同じ VNet リージョンに配置します。

詳細な構成図・要件は [docs/requirement.md](docs/requirement.md) を参照してください。

## 主要コンポーネント

| ディレクトリ | 役割 |
|---|---|
| `functions/` | Queue Trigger の文字起こし処理（Speech Batch Transcription / テキスト抽出） |
| `ui/` | FastAPI Web UI（リアルタイム文字起こし・履歴・文書閲覧・RAG チャット・OCR） |
| `infra/` | Bicep IaC（ネットワーク・ストレージ・AI・検索・監視など） |
| `tests/` | 単体テスト（pytest、Azure 接続不要） |
| `docs/` | 要件定義・デプロイ手順 |

## デプロイ

[Azure Developer CLI (azd)](https://learn.microsoft.com/azure/developer/azure-developer-cli/) と、azd hook の Python 実行環境を管理する [uv](https://docs.astral.sh/uv/) を使用します。事前に Azure CLI、azd、uv をインストールしてください。

```powershell
az login
azd auth login
azd init -e dev
azd up          # プロビジョニング + デプロイ
```

`azd provision` の `postprovision` フックは Search の shared private link を承認し、`documents` index、datasource、skillset、indexer を構成します。Search のリージョンを変更する場合は、実行前に `azd env set AZURE_SEARCH_LOCATION <region>` を設定してください。

前提条件・手順の詳細、E2E 動作確認、トラブルシューティングは [docs/deploy-guide.md](docs/deploy-guide.md) を参照してください。

## 開発・テスト

単体テストは Azure に接続せず、外部依存（Blob / OpenAI / Search）をモック化してローカルで実行できます。

```powershell
uv run --python 3.11 `
   --with-requirements requirements-test.txt `
   --with-requirements ui/requirements.txt `
   pytest
```

対象は出力パス生成・テキスト/JSON 抽出・検索フィルタ生成・冪等性判定などの純粋ロジックです。

## 対応ファイル形式

- 音声: `.wav` `.mp3` `.m4a` `.ogg` `.flac` `.wma`
- テキスト: `.txt` `.md` `.json` `.vtt`

動画ファイルは Functions の処理対象外です。音声を抽出して上記の音声形式へ変換してから `input` コンテナへ配置してください。
