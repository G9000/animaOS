from __future__ import annotations


def test_memory_similarity_handles_non_space_delimited_unicode() -> None:
    from anima_server.services.agent.memory_store import _similarity

    assert _similarity("今日は東京で寿司を食べた", "東京 寿司") > 0


def test_bm25_fallback_search_handles_mixed_language_text() -> None:
    from anima_server.services.agent.bm25_index import BM25Index

    index = BM25Index()
    index.build(
        [
            (1, "今日は東京で寿司を食べた"),
            (2, "Saya rindu nasi lemak dekat Kampung Baru"),
        ]
    )

    japanese_hits = index.search("東京 寿司")
    malay_hits = index.search("nasi lemak")

    assert japanese_hits
    assert japanese_hits[0][0] == 1
    assert malay_hits
    assert malay_hits[0][0] == 2


def test_bm25_fallback_handles_empty_tokenized_corpus() -> None:
    from anima_server.services.agent.bm25_index import BM25Index

    index = BM25Index()
    index.build([(1, "!!!"), (2, "...")])

    assert index.search("anything") == []


def test_transcript_fallback_scores_multilingual_text() -> None:
    from anima_server.services.agent.transcript_search import (
        _keyword_overlap_score,
        _text_overlap_score,
    )

    assert _text_overlap_score("東京 寿司", "今日は東京で寿司を食べた") > 0
    assert _keyword_overlap_score("東京 寿司", ["東京", "寿司"]) > 0
    assert _text_overlap_score(
        "nasi lemak",
        "Saya rindu nasi lemak dekat Kampung Baru",
    ) > 0


def test_in_memory_vector_text_similarity_handles_multilingual_text() -> None:
    from anima_server.services.agent.vector_store import _text_similarity

    assert _text_similarity("東京 寿司", "今日は東京で寿司を食べた") > 0
    assert _text_similarity("nasi lemak", "Saya rindu nasi lemak dekat Kampung Baru") > 0


def test_unicode_tokens_preserve_one_character_multilingual_and_digit_values() -> None:
    from anima_server.services.agent.text_processing import unicode_lexical_tokens

    assert "猫" in unicode_lexical_tokens("猫")
    assert "山" in unicode_lexical_tokens("山")
    assert "5" in unicode_lexical_tokens("age: 5")


def test_memory_relation_preserves_one_character_ascii_slot_values() -> None:
    from anima_server.services.agent.memory_store import _classify_memory_relation

    assert _classify_memory_relation("username: x", "username: y", "fact") == "update"


def test_degraded_retrieval_preserves_one_character_ascii_identifiers() -> None:
    from anima_server.services.agent.bm25_index import BM25Index
    from anima_server.services.agent.transcript_archive import _extract_keywords
    from anima_server.services.agent.transcript_search import _text_overlap_score
    from anima_server.services.agent.vector_store import _text_similarity

    index = BM25Index()
    index.build([(1, "username: x"), (2, "username: y")])

    hits = index.search("x")

    assert hits
    assert hits[0][0] == 1
    assert _text_similarity("x", "username: x") > 0
    assert _text_overlap_score("x", "username: x") > 0
    assert _text_overlap_score("x", "next extra text") == 0
    assert "x" in _extract_keywords([{"role": "user", "content": "username: x"}])


def test_single_character_cjk_queries_match_longer_non_space_text() -> None:
    from anima_server.services.agent.bm25_index import BM25Index
    from anima_server.services.agent.text_processing import unicode_lexical_tokens
    from anima_server.services.agent.vector_store import _text_similarity

    assert "猫" in unicode_lexical_tokens("我爱猫")
    assert _text_similarity("猫", "我爱猫") > 0

    index = BM25Index()
    index.build([(1, "我爱猫"), (2, "我爱狗")])

    hits = index.search("猫")
    assert hits
    assert hits[0][0] == 1


def test_bm25_fallback_ranks_cjk_unigram_by_overlap_when_bm25_scores_are_non_positive() -> None:
    from anima_server.services.agent.bm25_index import BM25Index

    index = BM25Index()
    index.build([(2, "猫狗"), (1, "猫猫")])

    hits = index.search("猫", limit=2)

    assert hits
    assert hits[0][0] == 1


def test_transcript_sidecar_keywords_include_multilingual_terms() -> None:
    from anima_server.services.agent.transcript_archive import _extract_keywords

    keywords = _extract_keywords(
        [
            {"role": "user", "content": "今日は東京で寿司を食べた"},
            {"role": "user", "content": "Saya rindu nasi lemak dekat Kampung Baru"},
        ]
    )

    assert "東京" in keywords
    assert "寿司" in keywords
    assert "nasi" in keywords
    assert "lemak" in keywords


def test_generic_claim_keys_do_not_collapse_for_non_english_content() -> None:
    from anima_server.services.agent.claims import _content_slug

    cat_name = _content_slug("猫の名前はモモです")
    tokyo_food = _content_slug("今日は東京で寿司を食べた")

    assert cat_name
    assert tokyo_food
    assert cat_name != tokyo_food
