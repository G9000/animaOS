from anima_server.services.agent.retrieval_intent import RetrievalMode, classify_retrieval_intent


def test_classifies_count_questions_as_aggregate() -> None:
    intent = classify_retrieval_intent("How many model kits have I worked on or bought?")

    assert intent.mode == RetrievalMode.AGGREGATE
    assert intent.needs_wide_evidence is True
    assert intent.candidate_limit >= 40


def test_classifies_latest_update_questions() -> None:
    intent = classify_retrieval_intent("Where did Rachel move to after her recent relocation?")

    assert intent.mode == RetrievalMode.LATEST_UPDATE
    assert intent.needs_wide_evidence is True


def test_classifies_preference_recommendation_questions() -> None:
    intent = classify_retrieval_intent(
        "Can you recommend some resources where I can learn more about video editing?"
    )

    assert intent.mode == RetrievalMode.PREFERENCE
    assert intent.needs_wide_evidence is True


def test_keeps_plain_chat_on_direct_mode() -> None:
    intent = classify_retrieval_intent("What is my dog's name?")

    assert intent.mode == RetrievalMode.DIRECT
    assert intent.needs_wide_evidence is False


