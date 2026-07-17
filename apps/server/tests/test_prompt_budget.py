from __future__ import annotations

import pytest
from anima_server.config import settings
from anima_server.services.agent.memory_blocks import MemoryBlock
from anima_server.services.agent.prompt_budget import (
    _DOCUMENT_CONTEXT_RESERVE_CHARS,
    DEFAULT_BUDGET,
    PROMPT_SCAFFOLDING_RESERVE_TOKENS,
    BudgetConfig,
    _truncate_at_boundary,
    estimate_char_tokens,
    plan_prompt_budget,
    resolve_budget_config,
    resolve_context_budget_tokens,
    resolve_document_context_budget_chars,
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


class TestDocumentContextBudget:
    def test_document_block_above_static_cap_survives_untruncated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A document_context block bigger than the old static 4000-char cap
        but within the resolved document budget must pass through the
        planner untouched (full-document context mode depends on it)."""
        monkeypatch.setattr(settings, "agent_context_window_tokens", None)
        monkeypatch.setattr(settings, "agent_max_tokens", 4096)
        value = "\n".join(
            f"Line {index} of the selected document." for index in range(400)
        )
        assert 4000 < len(value) <= resolve_document_context_budget_chars()
        block = MemoryBlock(
            label="document_context",
            description="Selected PDF full text",
            value=value,
        )

        plan = plan_prompt_budget([block], budget=resolve_budget_config())

        assert len(plan.blocks) == 1
        assert plan.blocks[0].value == value
        decision = next(
            d for d in plan.trace.decisions if d.label == "document_context"
        )
        assert decision.status == "kept"
        assert decision.reason == "within_budget"

    def test_document_budget_scales_with_context_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "agent_max_tokens", 4096)
        monkeypatch.setattr(settings, "document_full_context_budget_ratio", 0.5)
        monkeypatch.setattr(settings, "document_full_context_char_cap", 120_000)

        monkeypatch.setattr(settings, "agent_context_window_tokens", None)
        small = resolve_document_context_budget_chars()
        monkeypatch.setattr(settings, "agent_context_window_tokens", 200_000)
        large = resolve_document_context_budget_chars()

        assert small < large
        # The hard character ceiling bounds huge windows.
        assert large == 120_000

    def test_total_budget_still_trims_oversized_document_block(self) -> None:
        value = ("Sentence about the selected document. " * 200).strip()
        block = MemoryBlock(
            label="document_context",
            description="Selected PDF full text",
            value=value,
        )

        plan = plan_prompt_budget(
            [block],
            budget=BudgetConfig(
                total_budget=1000,
                tier_0_budget=1000,
                tier_1_budget=0,
                tier_2_budget=0,
                tier_3_budget=0,
            ),
        )

        assert len(plan.blocks) == 1
        assert len(plan.blocks[0].value) <= 1000
        decision = next(
            d for d in plan.trace.decisions if d.label == "document_context"
        )
        assert decision.status == "truncated"
        assert decision.reason == "budget_truncation"

    def test_document_char_cap_still_caps_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "agent_context_window_tokens", None)
        monkeypatch.setattr(settings, "document_full_context_char_cap", 100)
        value = ("Docs sentence one. " * 20).strip()
        block = MemoryBlock(
            label="document_context",
            description="Selected PDF full text",
            value=value,
        )

        plan = plan_prompt_budget([block], budget=DEFAULT_BUDGET)

        assert len(plan.blocks) == 1
        assert len(plan.blocks[0].value) <= 100
        decision = next(
            d for d in plan.trace.decisions if d.label == "document_context"
        )
        assert decision.status == "truncated"
        assert decision.reason == "per_block_cap"

    def test_budget_clamps_below_total_budget_by_the_reserve(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex P2 (PR #109, service.py:1842): with default budgets the
        unclamped document budget equals `resolve_budget_config()`'s
        total_budget whenever the hard char cap isn't binding, so an
        at-budget document_context block has no headroom left for the
        user_directive/today_user_context blocks that plan alongside it on
        a document turn and gets tail-truncated instead of the service
        falling back to retrieval. The budget must leave that headroom."""
        monkeypatch.setattr(settings, "agent_context_window_tokens", None)
        monkeypatch.setattr(settings, "agent_max_tokens", 4096)

        total_budget = resolve_budget_config().total_budget
        budget = resolve_document_context_budget_chars()

        assert budget == total_budget - _DOCUMENT_CONTEXT_RESERVE_CHARS

    def test_high_document_ratio_still_respects_the_reserve(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A generous document_full_context_budget_ratio must not be able to
        claim the whole total_budget: the clamp bounds ANY ratio config,
        not just the defaults."""
        monkeypatch.setattr(settings, "agent_context_window_tokens", None)
        monkeypatch.setattr(settings, "agent_max_tokens", 4096)
        monkeypatch.setattr(settings, "document_full_context_budget_ratio", 0.9)
        monkeypatch.setattr(settings, "document_full_context_char_cap", 120_000)

        total_budget = resolve_budget_config().total_budget
        budget = resolve_document_context_budget_chars()

        assert budget <= total_budget - _DOCUMENT_CONTEXT_RESERVE_CHARS

    def test_at_budget_document_block_survives_with_directive_and_today_siblings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The clamp restores the all-or-nothing guarantee end to end: since
        both the service's fit check and this planner call use the same
        `resolve_document_context_budget_chars()`, a document_context block
        sized exactly at that budget must survive `plan_prompt_budget`
        byte-identical even alongside the real user_directive and
        today_user_context blocks a document turn actually builds
        (`service._build_document_turn_directive` /
        `service._build_today_context_block`)."""
        from datetime import date

        from anima_server.schemas.chat import TodayContext
        from anima_server.services.agent import service as agent_service

        monkeypatch.setattr(settings, "agent_context_window_tokens", None)
        monkeypatch.setattr(settings, "agent_max_tokens", 4096)

        directive_block = agent_service._build_document_turn_directive(
            document_ids=[1]
        )
        assert directive_block is not None

        today_block = agent_service._build_today_context_block(
            TodayContext(
                date=date.today().isoformat(),
                mood="m" * 80,
                energy="e" * 40,
                note="n" * 280,
            )
        )
        assert today_block is not None

        budget_chars = resolve_document_context_budget_chars()
        doc_value = ("Document body line for the fit check. " * 2000)[:budget_chars]
        document_block = MemoryBlock(
            label="document_context",
            description="Selected PDF full text",
            value=doc_value,
        )

        plan = plan_prompt_budget(
            [directive_block, document_block, today_block],
            budget=resolve_budget_config(),
        )

        decision = next(
            d for d in plan.trace.decisions if d.label == "document_context"
        )
        assert decision.status == "kept"
        assert decision.reason == "within_budget"
        kept_block = next(b for b in plan.blocks if b.label == "document_context")
        assert kept_block.value == document_block.value


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
