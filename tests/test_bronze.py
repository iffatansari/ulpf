"""
Unit tests for Module 2 Bronze persistence helpers in the orchestrator.
"""

from orchestrator.main import (
    BRONZE_MAPPING,
    extract_upload_id,
    ensure_index,
)


class FakeIndices:
    def __init__(self, exists: bool):
        self._exists = exists
        self.created = []

    def exists(self, index):
        return self._exists

    def create(self, index, body):
        self.created.append((index, body))


class FakeES:
    def __init__(self, exists: bool):
        self.indices = FakeIndices(exists)


def test_ensure_index_creates_when_missing():
    es = FakeES(exists=False)
    ensure_index(es, "ulpf-bronze", BRONZE_MAPPING)
    assert es.indices.created == [("ulpf-bronze", BRONZE_MAPPING)]


def test_ensure_index_is_idempotent():
    es = FakeES(exists=True)
    ensure_index(es, "ulpf-bronze", BRONZE_MAPPING)
    assert es.indices.created == []


def test_bronze_mapping_marks_lineage_fields_as_keyword():
    props = BRONZE_MAPPING["mappings"]["properties"]
    assert props["source_id"]["type"] == "keyword"
    assert props["transport"]["type"] == "keyword"
    assert props["upload_id"]["type"] == "keyword"


def test_extract_upload_id_accepts_bytes_and_str():
    assert extract_upload_id([("upload_id", b"abc")]) == "abc"
    assert extract_upload_id([("upload_id", "abc")]) == "abc"


def test_extract_upload_id_is_optional():
    assert extract_upload_id(None) is None
    assert extract_upload_id([]) is None
    assert extract_upload_id([("other", b"x")]) is None
    assert extract_upload_id([("upload_id", None)]) is None
