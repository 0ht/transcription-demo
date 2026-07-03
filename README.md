# Blob 文字起こしシステム

Azure Blob Storage にアップロードした音声・テキストファイルを自動で文字起こしし、結果を Web UI で閲覧・検索（RAG）できるデモシステムです。ネットワークは **UI の Ingress のみ外部公開**、それ以外は Private Endpoint / VNet 統合による閉域構成です。

## アーキテクチャ概要

```
ユーザー ──HTTPS──▶ Streamlit UI (Container Apps)
                        │  (VNet 統合 / Private Endpoint)
   input へアップロード ▼
Blob Storage ─BlobCreated▶ Event Grid ─▶ Storage Queue ─▶ Azure Functions
                                                              │ 音声: Speech Batch Transcription
                                                              │ テキスト: 直接抽出
                                                              ▼
                                            output(結果) / processed(原本退避)
RAG: UI ─▶ Azure AI Search（ベクトル+セマンティック） / Azure OpenAI（埋め込み・回答生成）
```

詳細な構成図・要件は [docs/requirement.md](docs/requirement.md) を参照してください。

## 主要コンポーネント

| ディレクトリ | 役割 |
|---|---|
| `functions/` | Queue Trigger の文字起こし処理（Speech Batch Transcription / テキスト抽出） |
| `ui/` | Streamlit ダッシュボード（一覧・詳細・アップロード・RAG チャット） |
| `infra/` | Bicep IaC（ネットワーク・ストレージ・AI・検索・監視など） |
| `tests/` | 単体テスト（pytest、Azure 接続不要） |
| `docs/` | 要件定義・デプロイ手順 |

## デプロイ

[Azure Developer CLI (azd)](https://learn.microsoft.com/azure/developer/azure-developer-cli/) を使用します。

```powershell
azd auth login
azd up          # プロビジョニング + デプロイ
```

前提条件・手順の詳細、E2E 動作確認、トラブルシューティングは [docs/deploy-guide.md](docs/deploy-guide.md) を参照してください。

## 開発・テスト

単体テストは Azure に接続せず、外部依存（Blob / OpenAI / Search）をモック化してローカルで実行できます。

```powershell
pip install -r requirements-test.txt -r ui/requirements.txt
pytest
```

対象は出力パス生成・テキスト/JSON 抽出・検索フィルタ生成・チャンク分割・冪等性判定などの純粋ロジックです。

## 対応ファイル形式

- 音声: `.wav` `.mp3` `.m4a` `.ogg` `.flac` `.wma`
- テキスト: `.txt` `.md` `.json` `.vtt`
- 動画: `.mp4` `.avi` `.mov` `.webm` `.mkv`（UI 側でブラウザ内 MP3 変換後にアップロード）
