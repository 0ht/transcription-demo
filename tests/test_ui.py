"""ui/ 配下の純粋ロジックに対する単体テスト。

blob_service / index_service / search_service は import 時に Azure クライアントを
生成しないため、環境変数のデフォルトのままモジュールを import できる。
"""
import base64

import pytest

import blob_service
import index_service
import search_service


@pytest.mark.unit
class TestSpeakerColor:
    def test_same_name_returns_same_color(self):
        assert blob_service.speaker_color("Speaker 0") == blob_service.speaker_color("Speaker 0")

    def test_color_is_from_palette(self):
        assert blob_service.speaker_color("誰か") in blob_service.SPEAKER_COLORS


@pytest.mark.unit
class TestExtractBlobName:
    @pytest.mark.parametrize(
        "message,expected",
        [
            ("Processing meeting.wav (attempt 1/3)", "meeting.wav"),
            # best-effort 抽出: パス付きの場合はパスごと返る
            ("Completed: 2026/07/03/call.mp3", "2026/07/03/call.mp3"),
            ("All 3 attempts failed for sample.m4a: boom", "sample.m4a"),
            ("Skipping unsupported file: video.mkv (.mkv)", "video.mkv"),
        ],
    )
    def test_extracts_filename_from_known_patterns(self, message, expected):
        assert blob_service._extract_blob_name(message) == expected

    def test_returns_empty_when_no_match(self):
        assert blob_service._extract_blob_name("no file name here") == ""


@pytest.mark.unit
class TestBuildFilter:
    def test_returns_none_without_conditions(self):
        assert search_service._build_filter() is None

    def test_single_condition(self):
        assert search_service._build_filter(source_file="a.wav") == "source_file eq 'a.wav'"

    def test_both_conditions_joined_with_and(self):
        result = search_service._build_filter(source_file="a.wav", transcript_path="p/x.json")
        assert result == "source_file eq 'a.wav' and transcript_path eq 'p/x.json'"

    def test_escapes_single_quote(self):
        # OData のシングルクォートは '' でエスケープされる（インジェクション対策）
        result = search_service._build_filter(source_file="O'Brien.wav")
        assert result == "source_file eq 'O''Brien.wav'"


@pytest.mark.unit
class TestDocId:
    def test_is_deterministic(self):
        assert index_service._doc_id("2026/07/03/x.json", 3) == index_service._doc_id(
            "2026/07/03/x.json", 3
        )

    def test_encodes_path_and_chunk(self):
        doc_id = index_service._doc_id("p/x.json", 0)
        decoded = base64.urlsafe_b64decode(doc_id + "===").decode("utf-8")
        assert decoded == "p/x.json#0"

    def test_has_no_base64_padding(self):
        assert "=" not in index_service._doc_id("p/x.json", 12345)


@pytest.mark.unit
class TestSplitText:
    def test_short_text_returns_single_chunk(self):
        assert index_service._split_text("短い文", 100) == ["短い文"]

    def test_empty_text_returns_empty_list(self):
        assert index_service._split_text("   ", 100) == []

    def test_long_text_is_split_within_limit(self):
        text = "。".join(f"文{i}" for i in range(200))  # 十分に長い
        parts = index_service._split_text(text, 50)
        assert len(parts) > 1
        assert all(len(p) <= 50 for p in parts)

    def test_split_preserves_all_content(self):
        text = "あ" * 500
        parts = index_service._split_text(text, 120)
        assert "".join(parts) == text


@pytest.mark.unit
class TestChunkSegments:
    def test_empty_segments_return_empty(self):
        assert index_service._chunk_segments([]) == []

    def test_blank_segments_are_skipped(self):
        segs = [{"speaker": "A", "text": "   "}, {"speaker": "B", "text": ""}]
        assert index_service._chunk_segments(segs) == []

    def test_short_segments_are_merged_into_one_chunk(self):
        segs = [
            {"speaker": "A", "text": "こんにちは", "startTime": "PT0S"},
            {"speaker": "B", "text": "はい", "startTime": "PT1S"},
        ]
        chunks = index_service._chunk_segments(segs)
        assert len(chunks) == 1
        # 先頭セグメントの話者/時刻がチャンクのメタになる
        assert chunks[0]["speaker"] == "A"
        assert chunks[0]["start_time"] == "PT0S"
        assert "[A] こんにちは" in chunks[0]["content"]
        assert "[B] はい" in chunks[0]["content"]

    def test_long_segment_produces_multiple_chunks(self):
        long_text = "。".join(f"文{i}" for i in range(2000))
        chunks = index_service._chunk_segments([{"speaker": "A", "text": long_text}])
        assert len(chunks) > 1


class _FakeWriteClient:
    """delete_transcript_docs 用のダミー SearchClient。"""

    def __init__(self, hit_count: int):
        self._hits = [{"id": f"doc-{i}"} for i in range(hit_count)]
        self.deleted_batches: list[int] = []

    def search(self, **kwargs):
        # top 未指定でも全件を返す（SDK の自動ページング相当）
        return iter(self._hits)

    def delete_documents(self, documents):
        self.deleted_batches.append(len(documents))


@pytest.mark.unit
class TestDeleteTranscriptDocs:
    def test_no_hits_returns_zero_and_no_delete(self, monkeypatch):
        fake = _FakeWriteClient(0)
        monkeypatch.setattr(index_service, "get_write_client", lambda: fake)
        assert index_service.delete_transcript_docs("p/x.json") == 0
        assert fake.deleted_batches == []

    def test_deletes_in_batches_of_1000(self, monkeypatch):
        # 1000 件上限を超えても取りこぼさず、1000 件ごとに分割削除する
        fake = _FakeWriteClient(2300)
        monkeypatch.setattr(index_service, "get_write_client", lambda: fake)
        deleted = index_service.delete_transcript_docs("p/x.json")
        assert deleted == 2300
        assert fake.deleted_batches == [1000, 1000, 300]
