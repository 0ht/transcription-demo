"""
Streamlit カスタムコンポーネント: ffmpeg.wasm によるブラウザ内音声抽出。

- 動画ファイル (.mp4 / .mov / .mkv / .webm / .avi) をブラウザで選択
- ffmpeg.wasm で音声トラックを抽出（可能なら -c:a copy、不可なら AAC 再エンコード）
- 抽出済み音声バイト列を Streamlit (Python) に返す

Python 側は (filename: str, audio_bytes: bytes) を受け取る。
未抽出時は None を返す。
"""

from __future__ import annotations

import base64
import os
from typing import Optional, Tuple

import streamlit.components.v1 as components

_COMPONENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")

_component_func = components.declare_component(
    "ffmpeg_extractor_v5",
    path=_COMPONENT_DIR,
)


def ffmpeg_extract(
    key: str = "ffmpeg_extractor_v5",
    height: int = 420,
) -> Optional[Tuple[str, bytes]]:
    """音声抽出コンポーネントを描画し、抽出済みファイル (name, bytes) を返す。

    返り値:
        None: まだ抽出されていない
        (filename, audio_bytes): 抽出完了（filename は拡張子を含む音声ファイル名）
    """
    result = _component_func(key=key, default=None, height=height)
    if not result:
        return None
    name = result.get("name")
    b64 = result.get("data_b64")
    if not name or not b64:
        return None
    try:
        data = base64.b64decode(b64)
    except Exception:
        return None
    return name, data
