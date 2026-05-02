from anima_server.services.agent.memory_blocks import build_evidence_memory_block


def test_build_evidence_memory_block_uses_task_specific_description() -> None:
    block = build_evidence_memory_block(
        [
            (1, "Session date: 2023/05/29\nUser: I got a 1/72 scale B-29 bomber.", 0.98),
            (2, "Session date: 2023/05/21\nUser: I finished a Revell F-15 Eagle kit.", 0.91),
        ]
    )

    assert block is not None
    assert block.label == "evidence_memories"
    assert "Use this evidence to answer count" in block.description
    assert "B-29 bomber" in block.value


def test_build_evidence_memory_block_returns_none_for_empty() -> None:
    assert build_evidence_memory_block([]) is None
