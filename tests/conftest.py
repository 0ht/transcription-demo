"""pytest 共通フィクスチャ / インポート前セットアップ。

- `functions/`、`ui/`、`hooks/` はトップレベルモジュールとして import されるため、
  各ディレクトリを sys.path に追加する。
- `functions/function_app.py` は import 時に必須環境変数を参照するため、
  テスト収集より前（本ファイルのモジュールスコープ）で設定しておく。
"""
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_FUNCTIONS_DIR = _ROOT / "functions"
_UI_DIR = _ROOT / "ui"
_HOOKS_DIR = _ROOT / "hooks"

for _p in (_ROOT, _FUNCTIONS_DIR, _UI_DIR, _HOOKS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# function_app.py が import 時に os.environ[...] で参照する必須値。
os.environ.setdefault("DATA_STORAGE_ACCOUNT_NAME", "teststorage")
os.environ.setdefault("AI_SERVICES_ENDPOINT", "https://test-ai.cognitiveservices.azure.com")
