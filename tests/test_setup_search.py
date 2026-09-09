import pytest

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