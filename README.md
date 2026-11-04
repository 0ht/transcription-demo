# Blob 文字起こしシステム

Azure Blob Storage にアップロードした音声・テキストファイルを自動で文字起こしし、結果を Web UI で閲覧・検索（RAG）できるデモシステムです。ネットワークは **UI の Ingress のみ外部公開**、それ以外は Private Endpoint / VNet 統合による閉域構成です。

## アーキテクチャ概要

```
ユーザー ──HTTPS──▶ FastAPI UI (Container Apps)
                        │  (VNet 統合 / Private Endpoint)
   input へアップロード ▼
Blob Storage ─BlobCreated▶ Event Grid ─▶ Storage Queue ─▶ Azure Functions
                                                              │ 音声: Speech Fast Transcription（音声本体を送信）
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
| `functions/` | Queue Trigger の文字起こし処理（Speech Fast Transcription / テキスト抽出） |
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

データ Storage は `publicNetworkAccess=Disabled` のままです。Functions が Private Endpoint で音声を読み、Speech に multipart で直接送信するため、Speech から Storage への Trusted Access や SAS は不要です。音声は **250 MB 未満・2 時間未満**を運用上限とし、超過時は分割して投入してください。Queue による非同期実行と JSON/TXT 出力形式は維持します。

UI の組み込み Entra ID 認証には `AAD_CLIENT_ID` / `AAD_CLIENT_SECRET` と、管理者のオブジェクト ID を列挙した `UI_ADMIN_USER_IDS`（カンマ区切り）を azd 環境へ設定します。シークレットはコミットしないでください。初回アクセスはサインインへ転送され、未割当のユーザーは権限を取得しません。

マイク入力は `gpt-realtime` のセッションへ 24 kHz mono PCM で送信し、入力文字起こしは `gpt-4o-mini-transcribe`（`INPUT_TRANSCRIPTION_MODEL`）が担当します。GPT の自動音声応答は無効です。旧 `WHISPER_MODEL` はローカル設定の互換用にのみ読みます。連続発話の失敗や接続終了はマイク状態に表示します。

## 公開・デプロイ前の確認

- `.env` / `.env.*`（サンプルを除く）、`.azure/`、`local.settings.json`、秘密鍵、ログは Git に含めません。サンプルにも実際の秘密情報を記入しないでください。
- 会議履歴は `ui/data/`（空ディレクトリを維持する `.gitkeep` を除く）と `data/`、利用者が保存するプロンプトは `ui/prompts/` と `prompts/` を除外します。同梱の `ui/prompt/` は公開対象です。保存先を変更する場合は、そのパスも除外してください。
- 実環境の URL や運用記録を含む `docs/deployment-recovery-report.md` はローカル専用です。共有が必要な場合は、内容を確認・匿名化した別の資料を作成してください。
- UI の `.dockerignore` と Functions の `.funcignore` でも秘密情報やローカル生成物を除外します。Git の除外設定だけではデプロイ時の混入を防げません。
- **会議履歴は過去コミットにも存在します。** 追跡解除や除外設定の追加では過去の内容は消えません。公開前に過去コミットを含めて確認し、必要なら承認の上で履歴から除去してください。すでに公開済みの場合は、公開停止・削除の対応を検討してください。秘密情報が公開された場合は資格情報も失効・再発行してください。

## 開発・テスト

単体テストは Azure に接続せず、外部依存（Blob / OpenAI / Search）をモック化してローカルで実行できます。

```powershell
uv run --python 3.11 `
   --with-requirements requirements-test.txt `
   --with-requirements functions/requirements.txt `
   --with-requirements ui/requirements.txt `
   pytest
```

対象は出力パス生成・テキスト/JSON 抽出・検索フィルタ生成・冪等性判定などの純粋ロジックです。

## アップロード後のステータス

音声の文書ダッシュボードとOCRのアップロード一覧に、ファイルごとの **待機中 / 処理中 / 完了 / 失敗** を表示し、5秒ごとに自動更新します。失敗時には理由も表示します。状態は元ファイルのBlobメタデータに保存するため、画面を再読み込みしても維持されます。状態の記録がない旧ファイルは、結果が確認できない場合に「状態不明」と表示されます。

音声はアップロード後にAzure Functionsが自動処理します。OCRは従来どおり、アップロード済みファイルをクリックして実行します。OCR画面は **左に元文書、右に抽出テキスト** を表示します。PDF・PNG・JPEG・BMPは画面内でプレビューでき、TIFFなどブラウザー非対応形式は原本を開く / 保存するリンクから確認できます。旧OCR結果は対応する原本を一意に特定できる場合に表示し、原本が削除されている場合は取得エラーを表示します。

処理済み音声は文書一覧から選択して再生できます。再生先には結果の保存日と元ファイル名を使い、入力Blobのパスに含まれる日付を二重に付加しません。元ファイルが存在しない場合は取得APIが404を返し、取得や再生に失敗した場合はプレーヤーの下にエラーを表示します。ブラウザーの対応形式は異なるため、再生確認にはWAVまたはMP3を推奨します。

OCRの同名ファイルを再アップロードすると、名前に識別子を付けて別ファイルとして保存します。既存の原本を上書きしないため、過去のOCR結果と元文書の対応も維持されます。

狭い画面では、元文書とOCR結果を縦に並べて表示します。

## 対応ファイル形式

- 音声: `.wav` `.mp3` `.m4a` `.ogg` `.flac` `.wma`
- テキスト: `.txt` `.md` `.json` `.vtt`
- OCR: `.pdf` `.png` `.jpg` `.jpeg` `.tif` `.tiff` `.bmp`

動画ファイルは Functions の処理対象外です。音声を抽出して上記の音声形式へ変換してから `input` コンテナへ配置してください。
