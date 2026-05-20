"""
Streamlit UI - Blob 文字起こしダッシュボード
1画面構成: サイドバー（アップロード/状況） + メイン（統合ファイル一覧 + 詳細）
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
import urllib.parse

import streamlit as st

from components.ffmpeg_extractor import ffmpeg_extract

from blob_service import (
    AUDIO_EXTS,
    VIDEO_EXTS,
    ALL_SUPPORTED_EXTS,
    list_transcripts,
    list_input_files,
    list_processed_files,
    list_errors,
    load_json,
    load_error,
    load_media,
    load_text,
    speaker_color,
    upload_to_input,
    query_error_logs,
    list_queue_messages,
    list_poison_messages,
    clear_poison_queue,
    delete_poison_message_by_blob,
    delete_input_blob,
    delete_output_transcript,
    delete_output_error,
)


st.set_page_config(page_title="文字起こしダッシュボード", layout="wide", page_icon="🎙️")
st.title("🎙️ Blob 文字起こしダッシュボード")

# ===========================================================================
# CSS: テーブル罫線 + ファイル名リンクスタイル
# ===========================================================================
st.markdown(
    """
    <style>
    /* ファイル一覧の各行に下罫線 */
    div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stHorizontalBlock"] {
        border-bottom: 1px solid rgba(120, 120, 120, 0.55);
        padding-top: 4px;
        padding-bottom: 4px;
        align-items: center;
    }
    /* 列の境界（各カラム間に縦罫線） */
    div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
        border-right: 1px solid rgba(120, 120, 120, 0.45);
        padding-left: 8px;
        padding-right: 8px;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:last-child {
        border-right: none;
    }
    /* ファイル名リンク（完了ファイル） */
    a.file-link {
        color: #1f6feb;
        text-decoration: none;
        cursor: pointer;
    }
    a.file-link:hover {
        text-decoration: underline;
        color: #0a3d91;
    }
    a.file-link.selected {
        font-weight: bold;
        color: #d4380d;
    }
    /* ファイル名（クリック不可：処理待ち/エラー）*/
    span.file-text {
        color: #666;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ===========================================================================
# クエリパラメータからの選択処理（ファイル名リンククリック）
# ===========================================================================
_qp = st.query_params
if "selected" in _qp:
    st.session_state["selected"] = _qp["selected"]
    del st.query_params["selected"]
    st.rerun()
if "clear" in _qp:
    if "selected" in st.session_state:
        del st.session_state["selected"]
    del st.query_params["clear"]
    st.rerun()


# ===========================================================================
# 統合ファイルリスト構築
# ===========================================================================
def build_file_list(date_from, date_to, keyword):
    """input + output + poison を統合した1リストを返す。
    各ファイルは ✅処理済み / ❌エラー / ⏳処理待ち のいずれか1つだけを持つ。
    優先度: 処理済み(output) > エラー(poison) > 処理待ち(input)
    """
    files = {}

    # エラーログを先に取得
    try:
        error_logs = query_error_logs(hours=72, limit=200)
    except Exception:
        error_logs = []

    def _find_error_reason(target_name: str) -> str:
        if not target_name or not error_logs:
            return ""
        stem = os.path.splitext(target_name)[0]
        for log in error_logs:
            msg = log.get("message", "")
            log_blob = log.get("blob_name", "")
            if log_blob and (target_name.endswith(log_blob) or log_blob.endswith(target_name)):
                return msg[:300]
            if target_name in msg or stem in msg:
                return msg[:300]
        return ""

    # poison キューに含まれる blob 名集合（処理待ち判定で除外するため先に取得）
    try:
        poison_msgs = list_poison_messages(limit=50)
    except Exception:
        poison_msgs = []
    poison_blob_names = {m.get("blob_name", "") for m in poison_msgs if m.get("blob_name")}

    # 1) ✅ 処理済み（output）- 最優先
    try:
        for t in list_transcripts(date_from=date_from, date_to=date_to, keyword=keyword):
            path = t["path"]
            parts = path.split("/")
            basename = parts[-1].replace("_transcript.json", "") if parts else path
            date_str = "/".join(parts[:3]) if len(parts) >= 3 else ""
            files[basename] = {
                "name": basename,
                "date": date_str,
                "status": "✅ 処理済み",
                "status_detail": "",
                "transcript_path": path,
                "last_modified": t.get("last_modified"),
            }
    except Exception as e:
        st.error(f"output 取得エラー: {e}")

    # 2) ❌ エラー（output の _error.json）- 最終失敗マーカー
    #    Functions が最終失敗時に書き出すマーカー。input → processed への移動も
    #    Functions 側で完了済みなので、これがあれば確実にエラー終端済み。
    try:
        for e in list_errors(date_from=date_from, date_to=date_to, keyword=keyword):
            err_path = e["path"]
            try:
                meta = load_error(err_path)
            except Exception:
                meta = {}
            source = meta.get("sourceFile") or os.path.basename(err_path).replace("_error.json", "")
            stem = os.path.splitext(source)[0]
            if source in files or stem in files:
                continue
            reason = meta.get("error", "") or "(原因不明)"
            err_type = meta.get("errorType", "")
            parts = err_path.split("/")
            date_str = "/".join(parts[:3]) if len(parts) >= 3 else ""
            files[source] = {
                "name": source,
                "date": date_str,
                "status": "❌ エラー",
                "status_detail": f"{err_type}: {reason}" if err_type else reason,
                "transcript_path": None,
                "error_path": err_path,
                "last_modified": e.get("last_modified"),
            }
    except Exception as e:
        st.warning(f"エラーマーカー取得エラー: {e}")

    # 3) ❌ エラー（poison キュー）- 旧経路の互換用フォールバック
    for m in poison_msgs:
        blob_name = m.get("blob_name") or ""
        if not blob_name:
            continue
        # 既に処理済み or error.json で登録済みならスキップ
        stem = os.path.splitext(blob_name)[0]
        if blob_name in files or stem in files:
            continue
        if keyword and keyword.lower() not in blob_name.lower():
            continue
        ins = m.get("inserted_on")
        if ins:
            if date_from and ins.date() < date_from:
                continue
            if date_to and ins.date() > date_to:
                continue

        reason = _find_error_reason(blob_name) or "(原因不明 - poison キューに格納)"
        files[blob_name] = {
            "name": blob_name,
            "date": ins.strftime("%Y/%m/%d") if ins else "",
            "status": "❌ エラー",
            "status_detail": reason,
            "transcript_path": None,
            "last_modified": ins,
        }

    # 4) ⏳ 処理待ち（input）- 処理済み・エラーに無いもののみ
    try:
        for f in list_input_files():
            name = f["name"]
            basename = os.path.splitext(name)[0]
            # 処理済み or エラー(poison) に既に登録されているならスキップ
            if name in files or basename in files or name in poison_blob_names:
                continue
            if keyword and keyword.lower() not in name.lower():
                continue
            lm = f.get("last_modified")
            if lm:
                if date_from and lm.date() < date_from:
                    continue
                if date_to and lm.date() > date_to:
                    continue
            files[name] = {
                "name": name,
                "date": lm.strftime("%Y/%m/%d") if lm else "",
                "status": "⏳ 処理待ち",
                "status_detail": "",
                "transcript_path": None,
                "last_modified": lm,
            }
    except Exception as e:
        st.warning(f"input 取得エラー: {e}")

    # ソート: last_modified 降順
    return sorted(
        files.values(),
        key=lambda x: x.get("last_modified") or datetime.min.replace(tzinfo=None),
        reverse=True,
    )


# ===========================================================================
# サイドバー: アップロード + 処理状況
# ===========================================================================
with st.sidebar:
    st.header("📤 アップロード")
    st.caption("対応形式: " + ", ".join(sorted(ALL_SUPPORTED_EXTS)))

    # -----------------------------------------------------------------
    # 動画 → 音声抽出 (ブラウザ内 ffmpeg.wasm)
    # -----------------------------------------------------------------
    with st.expander("🎬 動画ファイルから音声を抽出（任意）", expanded=False):
        st.caption(
            "動画 (" + ", ".join(sorted(VIDEO_EXTS)) + ") をブラウザ内で .mp3 に変換し、"
            "下のアップロード欄に自動で追加します。サーバへは音声のみが送信されます。"
        )
        extracted = ffmpeg_extract(key="video_to_audio", height=380)
        if extracted is not None:
            ex_name, ex_bytes = extracted
            # 同じファイルの再受信を避けるため session_state で重複排除
            sig = (ex_name, len(ex_bytes))
            if st.session_state.get("_last_extracted_sig") != sig:
                st.session_state["_last_extracted_sig"] = sig
                pending = st.session_state.get("extracted_audios", [])
                pending.append({"name": ex_name, "data": ex_bytes})
                st.session_state["extracted_audios"] = pending
                st.rerun()

    extracted_audios = st.session_state.get("extracted_audios", [])
    if extracted_audios:
        st.success(f"🎵 抽出済み音声: {len(extracted_audios)} 件（下のアップロード対象に追加されます）")
        for i, a in enumerate(extracted_audios):
            cols = st.columns([5, 1])
            cols[0].text(f"✅ {a['name']} ({a['data'].__len__():,} B)")
            if cols[1].button("✖", key=f"rm_ex_{i}", help="この抽出済み音声を破棄"):
                extracted_audios.pop(i)
                st.session_state["extracted_audios"] = extracted_audios
                st.session_state.pop("_last_extracted_sig", None)
                st.rerun()

    # ブラウザのファイル選択ダイアログで拡張子フィルター（先頭ドット除去）
    accepted_types = [e.lstrip(".") for e in sorted(ALL_SUPPORTED_EXTS)]
    uploaded_files = st.file_uploader(
        "ファイルを選択",
        type=accepted_types,
        accept_multiple_files=True,
        key=f"uploader_{st.session_state.get('upload_counter', 0)}",
    )

    if uploaded_files or extracted_audios:
        # 通常アップロード分の拡張子チェック（二重防御）
        valid_files = []
        invalid_files = []
        for uf in uploaded_files or []:
            ext = Path(uf.name).suffix.lower()
            if ext in ALL_SUPPORTED_EXTS:
                valid_files.append(uf)
            else:
                invalid_files.append(uf)

        total_selected = len(uploaded_files or []) + len(extracted_audios)
        st.write(f"**{total_selected} 件アップロード対象:**")
        for uf in valid_files:
            st.text(f"✅ {uf.name} ({uf.size:,} B)")
        for a in extracted_audios:
            st.text(f"🎬→🎵 {a['name']} ({len(a['data']):,} B)")
        for uf in invalid_files:
            st.text(f"❌ {uf.name} (非対応)")

        if invalid_files:
            st.error(f"❌ 非対応ファイルが {len(invalid_files)} 件含まれています。アップロードできません。")

        # 非対応ファイルが1つでもあればボタンを無効化
        has_any = bool(valid_files) or bool(extracted_audios)
        if st.button(
            "🚀 アップロード実行",
            type="primary",
            use_container_width=True,
            disabled=bool(invalid_files) or not has_any,
        ):
            success_count = 0
            error_count = 0
            total = len(valid_files) + len(extracted_audios)
            progress = st.progress(0)
            done = 0
            for uf in valid_files:
                try:
                    upload_to_input(uf.name, uf.getvalue())
                    success_count += 1
                except Exception as e:
                    st.error(f"❌ {uf.name}: {e}")
                    error_count += 1
                done += 1
                progress.progress(done / total)
            for a in extracted_audios:
                try:
                    upload_to_input(a["name"], a["data"])
                    success_count += 1
                except Exception as e:
                    st.error(f"❌ {a['name']}: {e}")
                    error_count += 1
                done += 1
                progress.progress(done / total)

            if success_count:
                st.success(f"✅ {success_count} 件アップロード完了")
            if error_count:
                st.warning(f"⚠️ {error_count} 件失敗")

            # アップロード済みの抽出音声はクリア
            st.session_state["extracted_audios"] = []
            st.session_state.pop("_last_extracted_sig", None)
            st.session_state["upload_counter"] = st.session_state.get("upload_counter", 0) + 1
            st.rerun()

    st.divider()

    st.header("📊 処理状況")
    if st.button("🔄 更新", key="refresh_status", use_container_width=True):
        st.rerun()

    # poison キュー内の blob 名を取得（処理待ち集計から除外するため）
    try:
        _poison_msgs = list_poison_messages(limit=50)
        _poison_names = {m.get("blob_name", "") for m in _poison_msgs if m.get("blob_name")}
        poison_count = len(_poison_names)
    except Exception:
        _poison_names = set()
        poison_count = 0

    # output 側のエラーマーカー数（新しい失敗経路）
    try:
        _error_markers = list_errors()
        error_marker_count = len(_error_markers)
    except Exception:
        error_marker_count = 0

    try:
        _input_files = list_input_files()
        # poison に含まれるものは「エラー」として扱い、処理待ちからは除外
        waiting_count = sum(1 for f in _input_files if f["name"] not in _poison_names)
        st.metric("⏳ 処理待ち", f"{waiting_count} 件",
                  help="アップロード済みで処理中／処理開始待ちのファイル（エラーは含まない）")
    except Exception:
        st.metric("⏳ 処理待ち", "取得エラー")

    try:
        total_errors = poison_count + error_marker_count
        st.metric("❌ エラー", f"{total_errors} 件",
                  help="最終失敗したファイル数（output の _error.json + poison キュー）")
        if poison_count > 0:
            if st.button("🗑️ poison キュークリア", key="clear_poison", use_container_width=True,
                         help="poison キューの全メッセージを削除（旧経路の残骸処理）"):
                try:
                    clear_poison_queue()
                    st.success("✅ クリアしました")
                    st.rerun()
                except Exception as e:
                    st.error(f"クリア失敗: {e}")
    except Exception:
        st.metric("❌ エラー", "取得エラー")

    try:
        st.metric("✅ 処理済み", f"{len(list_transcripts())} 件",
                  help="文字起こしが完了したファイル数")
    except Exception:
        st.metric("✅ 処理済み", "取得エラー")


# ===========================================================================
# メイン領域
# ===========================================================================

# --- フィルタ ---
filter_col1, filter_col2, filter_col3 = st.columns([2, 2, 3])
date_from = filter_col1.date_input("開始日", value=datetime.now() - timedelta(days=30), key="date_from")
date_to = filter_col2.date_input("終了日", value=datetime.now(), key="date_to")
keyword = filter_col3.text_input("キーワード検索（ファイル名）", key="keyword")

with st.spinner("ファイル一覧を読み込み中..."):
    file_items = build_file_list(date_from, date_to, keyword)

# ステータス種別（サイドバーの3メトリクスと一致）
ALL_STATUSES = ["✅ 処理済み", "⏳ 処理待ち", "❌ エラー"]


# ---- 一括削除ヘルパー ----
def _delete_one(item: dict) -> tuple[bool, str]:
    """1件削除。(成功, メッセージ) を返す。"""
    status = item["status"]
    name = item["name"]
    try:
        if status.startswith("✅"):
            if item.get("transcript_path"):
                delete_output_transcript(item["transcript_path"])
            return True, name
        elif status.startswith("❌"):
            # 新方式: output の _error.json があればそれを削除（processed の元ファイルも）
            err_path = item.get("error_path")
            if err_path:
                try:
                    delete_output_error(err_path)
                except Exception as e:
                    if "BlobNotFound" not in str(e):
                        raise
                return True, name
            # 旧方式: poison キュー + input blob のフォールバック
            try:
                delete_poison_message_by_blob(name)
            except Exception:
                pass
            try:
                delete_input_blob(name)
            except Exception as e:
                if "BlobNotFound" not in str(e):
                    raise
            return True, name
        else:
            delete_input_blob(name)
            return True, name
    except Exception as e:
        return False, f"{name}: {e}"


list_container = st.container(height=520)
with list_container:
    st.markdown("#### 📋 ファイル一覧")

    # ステータスフィルタ初期化
    if "status_filter" not in st.session_state:
        st.session_state["status_filter"] = set(ALL_STATUSES)

    visible_items = [f for f in file_items if f["status"] in st.session_state["status_filter"]]

    # 選択集計
    selected_names = []
    select_all_state = st.session_state.get("select_all_files", False)
    for item in visible_items:
        cb_key = f"chk_{item['name']}"
        if select_all_state:
            st.session_state[cb_key] = True
        if st.session_state.get(cb_key):
            selected_names.append(item["name"])

    # 「表示」ボタン用: チェックされた完了ファイルが1つの場合に有効
    selected_completed = [
        item for item in visible_items
        if item["name"] in selected_names and item["transcript_path"] is not None
    ]
    can_view = len(selected_completed) == 1

    # ===== Azure Portal 風ツールバー: [👁 表示] [🗑️ 削除] [🔄 更新]   件数 =====
    tb1, tb2, tb3, tb4 = st.columns([1.3, 1.6, 1.3, 5.8])
    if tb1.button(
        "👁 表示",
        type="primary" if can_view else "secondary",
        disabled=not can_view,
        use_container_width=True,
        key="view_selected",
        help="チェックを1つだけ付けて『処理済み』ファイルを選択してください",
    ):
        st.session_state["selected"] = selected_completed[0]["transcript_path"]
        st.rerun()

    bulk_delete_clicked = tb2.button(
        f"🗑️ 削除 ({len(selected_names)})",
        type="primary" if selected_names else "secondary",
        disabled=not selected_names,
        use_container_width=True,
        key="bulk_delete",
    )
    if tb3.button("🔄 更新", use_container_width=True, key="refresh_list"):
        st.rerun()
    tb4.markdown(
        f"<div style='text-align:right;padding-top:6px;color:#888;'>{len(visible_items)} 件 / 全 {len(file_items)} 件</div>",
        unsafe_allow_html=True,
    )

    if bulk_delete_clicked:
        ok, ng = 0, []
        for item in file_items:
            if item["name"] in selected_names:
                success, msg = _delete_one(item)
                if success:
                    ok += 1
                else:
                    ng.append(msg)
        for k in list(st.session_state.keys()):
            if k.startswith("chk_") or k == "select_all_files":
                del st.session_state[k]
        if ok:
            st.success(f"✅ {ok} 件削除しました")
        if ng:
            st.error("削除失敗: " + "; ".join(ng))
        st.rerun()

    # ===== ヘッダ行（タイトル行に全選択チェックを配置） =====
    h0, h1, h2, h3 = st.columns([0.6, 5, 2, 3])
    h0.checkbox(" ", key="select_all_files", label_visibility="collapsed", help="全選択")
    h1.markdown("**ファイル名**")
    h2.markdown("**日付**")
    with h3:
        with st.popover(
            f"**ステータス** ▼ ({len(st.session_state['status_filter'])}/{len(ALL_STATUSES)})",
            use_container_width=True,
        ):
            st.caption("表示するステータスを選択")
            new_filter = set()
            for s in ALL_STATUSES:
                checked = st.checkbox(s, value=(s in st.session_state["status_filter"]), key=f"sf_{s}")
                if checked:
                    new_filter.add(s)
            if new_filter != st.session_state["status_filter"]:
                st.session_state["status_filter"] = new_filter
                st.rerun()

    if not visible_items:
        st.info("該当ファイルなし")
    else:
        for i, item in enumerate(visible_items):
            is_completed = item["transcript_path"] is not None
            is_selected = (
                is_completed
                and st.session_state.get("selected") == item["transcript_path"]
            )

            c0, c1, c2, c3 = st.columns([0.6, 5, 2, 3])
            c0.checkbox(" ", key=f"chk_{item['name']}", label_visibility="collapsed")

            # ファイル名: 完了 → クエリパラメータ付きリンク、それ以外 → グレーテキスト
            if is_completed:
                cls = "file-link selected" if is_selected else "file-link"
                qp = urllib.parse.quote(item["transcript_path"], safe="")
                arrow = "▶ " if is_selected else "📄 "
                c1.markdown(
                    f'<a class="{cls}" href="?selected={qp}" target="_self">{arrow}{item["name"]}</a>',
                    unsafe_allow_html=True,
                )
            else:
                c1.markdown(f'<span class="file-text">📄 {item["name"]}</span>', unsafe_allow_html=True)

            c2.text(item["date"])

            status = item["status"]
            detail = item["status_detail"]
            if status.startswith("✅"):
                c3.success(status)
            elif status.startswith("⏳"):
                c3.info(status)
            elif status.startswith("❌"):
                c3.error(f"{status}\n\n{detail}" if detail else status)
            else:
                c3.text(status)

st.divider()

# --- 下ペイン: 詳細表示 ---
st.markdown("#### 📝 詳細")
detail_container = st.container(height=600)
with detail_container:
    if "selected" not in st.session_state:
        st.info("↑ 上の一覧から「処理済み」のファイル名をクリックしてください")
    else:
        json_path = st.session_state["selected"]
        txt_path = json_path.replace("_transcript.json", "_transcript.txt")

        try:
            transcript = load_json(json_path)
        except Exception as e:
            st.error(f"トランスクリプトの読み込みに失敗しました: {e}")
            st.stop()

        source_file = transcript.get("sourceFile", "")

        head_col1, head_col2, head_col3 = st.columns([6, 2, 2])
        head_col1.markdown(f"##### {source_file}")

        json_data = json.dumps(transcript, ensure_ascii=False, indent=2)
        head_col2.download_button(
            "📥 JSON",
            json_data,
            file_name=os.path.basename(json_path),
            mime="application/json",
            use_container_width=True,
        )

        try:
            txt_data = load_text(txt_path)
            head_col3.download_button(
                "📥 テキスト",
                txt_data,
                file_name=os.path.basename(txt_path),
                mime="text/plain",
                use_container_width=True,
            )
        except Exception:
            head_col3.caption("テキストなし")

        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("処理日時", transcript.get("processedAt", "")[:19])
        col_m2.metric("長さ", transcript.get("duration", "-"))
        col_m3.metric("言語", transcript.get("language", "-"))

        if source_file:
            ext = os.path.splitext(source_file)[1].lower()
            parts = json_path.split("/")
            media_path = "/".join(parts[:3]) + "/" + source_file if len(parts) >= 3 else source_file
            try:
                media_data = load_media(media_path)
                if ext in AUDIO_EXTS:
                    st.audio(media_data)
                elif ext in VIDEO_EXTS:
                    st.video(media_data)
            except Exception:
                st.caption("⚠️ 元ファイルの再生ができません")

        st.markdown("**💬 トランスクリプト**")
        for seg in transcript.get("segments", []):
            speaker = seg.get("speaker", "Unknown")
            text = seg.get("text", "")
            color = speaker_color(speaker)
            time_info = seg.get("startTime", "")
            st.markdown(
                f'<div style="margin-bottom:4px;">'
                f'<span style="color:{color};font-weight:bold;">[{speaker}]</span>'
                f' <span style="color:#888;font-size:0.8em;">{time_info}</span><br/>'
                f"{text}</div>",
                unsafe_allow_html=True,
            )
