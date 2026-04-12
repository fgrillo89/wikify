"""ExtractCache key must partition on binding name."""

from wikify.cache import ExtractCacheKey


def test_binding_name_partitions_relpath():
    a = ExtractCacheKey(
        binding_name="fake",
        model_id="haiku",
        prompt_hash="abc123",
        chunk_id="doc-a/0",
    )
    b = ExtractCacheKey(
        binding_name="file_dispatch",
        model_id="haiku",
        prompt_hash="abc123",
        chunk_id="doc-a/0",
    )
    assert a.relpath() != b.relpath()
    assert a.relpath().parts[0] == "fake"
    assert b.relpath().parts[0] == "file_dispatch"


def test_same_binding_same_key():
    a = ExtractCacheKey(
        binding_name="fake",
        model_id="haiku",
        prompt_hash="abc123",
        chunk_id="doc-a/0",
    )
    b = ExtractCacheKey(
        binding_name="fake",
        model_id="haiku",
        prompt_hash="abc123",
        chunk_id="doc-a/0",
    )
    assert a.relpath() == b.relpath()
