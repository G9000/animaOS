from __future__ import annotations

import pytest
from anima_server.config import settings
from anima_server.services.agent.memory_blocks import MemoryBlock
from anima_server.services.agent.prompt_budget import (
    DEFAULT_BUDGET,
    PROMPT_SCAFFOLDING_RESERVE_TOKENS,
    BudgetConfig,
    _truncate_at_boundary,
    estimate_char_tokens,
    plan_prompt_budget,
    resolve_budget_config,
    resolve_context_budget_tokens,
)


class TestResolveBudget:
    def test_legacy_behaviour_without_context_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "agent_context_window_tokens", None)
        monkeypatch.setattr(settings, "agent_max_tokens", 4096)
        assert resolve_budget_config() is DEFAULT_BUDGET
        # The legacy fallback derives from the block budget it has to
        # hold (blocks ≈ half the prompt) instead of letting
        # agent_max_tokens double as a context budget smaller than the
        # blocks alone.
        assert (
            resolve_context_budget_tokens()
            == estimate_char_tokens(DEFAULT_BUDGET.total_budget) * 2
        )

    def test_window_reserves_output_tokens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "agent_context_window_tokens", 32768)
        monkeypatch.setattr(settings, "agent_max_tokens", 4096)
        assert (
            resolve_context_budget_tokens()
            == 32768 - 4096 - PROMPT_SCAFFOLDING_RESERVE_TOKENS
        )

    def test_block_budget_scales_with_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "agent_max_tokens", 4096)
        monkeypatch.setattr(settings, "agent_block_budget_ratio", 0.5)

        monkeypatch.setattr(settings, "agent_context_window_tokens", 32768)
        small = resolve_budget_config()
        monkeypatch.setattr(settings, "agent_context_window_tokens", 131072)
        large = resolve_budget_config()

        assert small.total_budget < large.total_budget
        # Blocks get block_budget_ratio of the context budget, converted
        # to chars with the same 3-chars/token ratio as the estimator.
        assert (
            small.total_budget
            == (32768 - 4096 - PROMPT_SCAFFOLDING_RESERVE_TOKENS) // 2 * 3
        )
        # Tier budgets keep the default 4/6/6/8 proportions.
        assert large.tier_0_budget < large.tier_1_budget <= large.tier_3_budget
        assert (
            small.tier_0_budget
            + small.tier_1_budget
            + small.tier_2_budget
            + small.tier_3_budget
            <= small.total_budget + 4  # rounding slack
        )

    def test_tiny_window_keeps_minimum_block_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "agent_context_window_tokens", 2048)
        monkeypatch.setattr(settings, "agent_max_tokens", 4096)
        budget = resolve_budget_config()
        assert budget.total_budget >= 4000


class TestBoundaryTruncation:
    def test_no_truncation_when_within_limit(self) -> None:
        assert _truncate_at_boundary("short", 100) == "short"

    def test_backs_off_to_line_boundary(self) -> None:
        value = "- user is allergic to peanuts\n- user is allergic to shellfish and tree nuts"
        result = _truncate_at_boundary(value, 50)
        assert result == "- user is allergic to peanuts"

    def test_backs_off_to_sentence_boundary(self) -> None:
        value = "The user is diabetic. The user is allergic to penicillin and aspirin"
        result = _truncate_at_boundary(value, 50)
        assert result == "The user is diabetic."

    def test_hard_cut_when_no_boundary_in_second_half(self) -> None:
        value = "x" * 200
        result = _truncate_at_boundary(value, 50)
        assert result == "x" * 50

    def test_budget_truncation_never_ends_mid_fact(self) -> None:
        block = MemoryBlock(
            label="facts",
            description="facts",
            value="- user is vegetarian\n- user is allergic to peanuts and lentils",
        )
        plan = plan_prompt_budget(
            [block],
            budget=BudgetConfig(
                total_budget=40,
                tier_0_budget=40,
                tier_1_budget=40,
                tier_2_budget=40,
                tier_3_budget=40,
            ),
        )
        assert len(plan.blocks) == 1
        assert plan.blocks[0].value == "- user is vegetarian"
        decision = next(d for d in plan.trace.decisions if d.label == "facts")
        assert decision.status == "truncated"
        assert decision.final_chars == len("- user is vegetarian")


class TestBlockPriority:
    def test_user_profile_keeps_identity_priority_under_tight_budget(self) -> None:
        blocks = [
            MemoryBlock(
                label="goals",
                description="Goals",
                value="g" * 50,
            ),
            MemoryBlock(
                label="user_profile",
                description="Structured user profile",
                value="u" * 60,
            ),
        ]

        plan = plan_prompt_budget(
            blocks,
            budget=BudgetConfig(
                total_budget=100,
                tier_0_budget=60,
                tier_1_budget=0,
                tier_2_budget=0,
                tier_3_budget=50,
            ),
        )

        labels = [block.label for block in plan.blocks]
        user_profile_decision = next(
            decision
            for decision in plan.trace.decisions
            if decision.label == "user_profile"
        )
        assert "user_profile" in labels
        assert user_profile_decision.tier == 0
        assert user_profile_decision.status == "kept"

    def test_document_context_precedes_personal_memory_blocks(self) -> None:
        blocks = [
            MemoryBlock(
                label="relevant_memories",
                description="Relevant memories",
                value="The user has platinum hair and star nails.",
            ),
            MemoryBlock(
                label="document_context",
                description="Selected PDF excerpts",
                value="2026 price list: rapid tests are RM50 each.",
            ),
            MemoryBlock(
                label="self_working_memory",
                description="Working memory",
                value="The user recently asked about a visual look.",
            ),
        ]

        plan = plan_prompt_budget(
            blocks,
            budget=BudgetConfig(
                total_budget=4000,
                tier_0_budget=4000,
                tier_1_budget=4000,
                tier_2_budget=4000,
                tier_3_budget=4000,
            ),
        )

        labels = [block.label for block in plan.blocks]
        assert labels.index("document_context") < labels.index("relevant_memories")
        assert labels.index("document_context") < labels.index("self_working_memory")
