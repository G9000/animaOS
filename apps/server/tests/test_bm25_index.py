"""Tests for BM25 index -- F1 hybrid search."""
import pytest
from anima_server.services.agent.bm25_index import (
    BM25Index,
    _tokenize,
    bm25_search,
    get_or_build_index,
    invalidate_index,
    _user_indices,
)


class TestTokenize:
    def test_lowercase_split(self):
        assert _tokenize("Hello World") == ["hello", "world"]

    def test_empty_string(self):
        assert _tokenize("") == []

    def test_punctuation_kept(self):
        # Simple whitespace split keeps punctuation attached
        result = _tokenize("PostgreSQL is great!")
        assert "postgresql" in result

    def test_multiple_spaces(self):
        result = _tokenize("  hello   world  ")
        assert result == ["hello", "world"]


class TestBM25Index:
    def test_build_and_search(self):
        idx = BM25Index()
        idx.build([
            (1, "User works at Google as a software engineer"),
            (2, "User prefers PostgreSQL over MySQL"),
            (3, "User lives in Berlin Germany"),
        ])
        results = idx.search("PostgreSQL", limit=3)
        # PostgreSQL doc should rank first (rare term = high IDF)
        assert results[0][0] == 2

    def test_search_empty_index(self):
        idx = BM25Index()
        results = idx.search("anything")
        assert results == []

    def test_search_empty_query(self):
        idx = BM25Index()
        idx.build([(1, "hello world")])
        results = idx.search("")
        assert results == []

    def test_document_count(self):
        idx = BM25Index()
        idx.build([(1, "doc one"), (2, "doc two")])
        assert idx.document_count == 2

    def test_document_count_empty(self):
        idx = BM25Index()
        assert idx.document_count == 0

    def test_add_document(self):
        idx = BM25Index()
        idx.build([
            (1, "hello world"),
            (3, "something else entirely"),
        ])
        idx.add_document(2, "goodbye world")
        assert idx.document_count == 3
        results = idx.search("goodbye")
        assert any(item_id == 2 for item_id, _ in results)

    def test_remove_document(self):
        idx = BM25Index()
        idx.build([(1, "hello"), (2, "goodbye")])
        idx.remove_document(1)
        assert idx.document_count == 1
        results = idx.search("hello")
        assert not any(item_id == 1 for item_id, _ in results)

    def test_remove_nonexistent_document(self):
        idx = BM25Index()
        idx.build([(1, "hello")])
        idx.remove_document(999)  # should not raise
        assert idx.document_count == 1

    def test_common_word_low_score(self):
        """Common words should not dominate scoring (IDF weighting)."""
        idx = BM25Index()
        idx.build([
            (1, "the cat is on the mat"),
            (2, "the dog is in the house"),
            (3, "PostgreSQL database configuration"),
        ])
        results_common = idx.search("the", limit=3)
        results_rare = idx.search("PostgreSQL", limit=3)
        # Rare term should produce a higher top score
        if results_common and results_rare:
            assert results_rare[0][1] >= results_common[0][1]

    def test_scores_are_nonzero(self):
        """Matching documents should have non-zero scores."""
        idx = BM25Index()
        idx.build([
            (1, "python programming language"),
            (2, "java programming language"),
            (3, "rust systems programming"),
        ])
        results = idx.search("python")
        assert len(results) > 0
        for _, score in results:
            assert score != 0.0

    def test_limit_respected(self):
        idx = BM25Index()
        docs = [(i, f"document number {i} with shared words") for i in range(20)]
        idx.build(docs)
        results = idx.search("document", limit=5)
        assert len(results) <= 5

    def test_results_sorted_descending(self):
        idx = BM25Index()
        idx.build([
            (1, "python python python"),
            (2, "python java"),
            (3, "java java java"),
        ])
        results = idx.search("python")
        for i in range(1, len(results)):
            assert results[i - 1][1] >= results[i][1]


class TestModuleLevelCache:
    def setup_method(self):
        _user_indices.clear()

    def teardown_method(self):
        _user_indices.clear()

    def test_invalidate_clears_cache(self):
        _user_indices[99] = BM25Index()
        invalidate_index(99)
        assert 99 not in _user_indices

    def test_invalidate_nonexistent_user(self):
        # Should not raise
        invalidate_index(999)
        assert 999 not in _user_indices
