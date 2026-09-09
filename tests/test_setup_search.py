import pytest
import subprocess

from hooks import setup_search


@pytest.mark.unit
def test_build_index_uses_keyword_analyzer_for_projection_key():
    index = setup_search.build_index(
        "documents",
        "https://example.openai.azure.com/",
        "text-embedding-3-large",
        "default-semantic",
    )

    key_field = next(field for field in index["fields"] if field.get("key"))

    assert key_field["searchable"] is True
    assert key_field["analyzer"] == "keyword"


@pytest.mark.unit
def test_az_logs_progress_while_command_is_running(monkeypatch, caplog):
    class FakeProcess:
        returncode = 0

        def __init__(self):
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(cmd="az", timeout=timeout)
            return '{"state": "done"}', ""

    process = FakeProcess()
    monkeypatch.setattr(setup_search, "_az_exe", lambda: "az")
    monkeypatch.setattr(setup_search.subprocess, "Popen", lambda *_args, **_kwargs: process)

    with caplog.at_level("INFO"):
        result = setup_search.az(
            "resource",
            "update",
            progress_message="Search の更新を待機中",
            progress_interval=30,
        )

    assert result == {"state": "done"}
    assert "Search の更新を待機中（30 秒経過）" in caplog.text