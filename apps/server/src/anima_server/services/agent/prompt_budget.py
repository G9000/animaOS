"""Prompt budget planner.

Assigns explicit priorities and per-block caps to runtime memory blocks so the
prompt stays within the model's context window without crowding out critical
state. The budgeter now exposes a trace describing which blocks were kept,
truncated, or dropped so prompt-quality regressions are debuggable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil

from anima_server.services.agent.memory_blocks import MemoryBlock


@dataclass(frozen=True, slots=True)
class BlockBudgetPolicy:
    tier: int
    order: int
    max_chars: int | None = None


@dataclass(frozen=True, slots=True)
class PromptBudgetBlockDecision:
    label: str
    tier: int
    status: str
    original_chars: int
    final_chars: int
    reason: str


@dataclass(frozen=True, slots=True)
class PromptBudgetTrace:
    total_budget: int
    retained_chars: int
    dropped_chars: int
    retained_token_estimate: int
    dropped_token_estimate: int
    tier_usage: dict[str, int]
    tier_budgets: dict[str, int]
    dynamic_identity_chars: int = 0
    dynamic_identity_token_estimate: int = 0
    system_prompt_chars: int = 0
    system_prompt_token_estimate: int = 0
    decisions: tuple[PromptBudgetBlockDecision, ...] = ()


@dataclass(frozen=True, slots=True)
class PromptBudgetPlan:
    blocks: tuple[MemoryBlock, ...]
    trace: PromptBudgetTrace


_DEFAULT_POLICY = BlockBudgetPolicy(tier=3, order=999, max_chars=1000)
_BLOCK_POLICIES: dict[str, BlockBudgetPolicy] = {
    "soul": BlockBudgetPolicy(tier=0, order=0, max_chars=None),
    "user_directive": BlockBudgetPolicy(tier=0, order=1, max_chars=None),
    "self_identity": BlockBudgetPolicy(tier=1, order=0, max_chars=1600),
    "current_focus": BlockBudgetPolicy(tier=1, order=1, max_chars=1400),
    "thread_summary": BlockBudgetPolicy(tier=1, order=2, max_chars=1800),
    "self_inner_state": BlockBudgetPolicy(tier=1, order=3, max_chars=900),
    "self_working_memory": BlockBudgetPolicy(tier=1, order=4, max_chars=700),
    "human": BlockBudgetPolicy(tier=1, order=5, max_chars=700),
    "relevant_memories": BlockBudgetPolicy(tier=2, order=0, max_chars=2200),
    "emotional_context": BlockBudgetPolicy(tier=2, order=1, max_chars=700),
    "user_tasks": BlockBudgetPolicy(tier=2, order=2, max_chars=1400),
    "facts": BlockBudgetPolicy(tier=2, order=3, max_chars=1500),
    "preferences": BlockBudgetPolicy(tier=2, order=4, max_chars=1200),
    "self_intentions": BlockBudgetPolicy(tier=2, order=5, max_chars=1000),
    "goals": BlockBudgetPolicy(tier=3, order=0, max_chars=1000),
    "relationships": BlockBudgetPolicy(tier=3, order=1, max_chars=1000),
    "recent_episodes": BlockBudgetPolicy(tier=3, order=2, max_chars=1000),
    "session_memory": BlockBudgetPolicy(tier=3, order=3, max_chars=1200),
    "self_growth_log": BlockBudgetPolicy(tier=3, order=4, max_chars=700),
}


@dataclass(frozen=True, slots=True)
class BudgetConfig:
    """Character budgets per tier. total_budget is the hard ceiling."""
    total_budget: int = 24000
    tier_0_budget: int = 4000
    tier_1_budget: int = 6000
    tier_2_budget: int = 6000
    tier_3_budget: int = 8000


DEFAULT_BUDGET = BudgetConfig()


def estimate_char_tokens(char_count: int) -> int:
    if char_count <= 0:
        return 0
    return max(1, ceil(char_count / 4))


def plan_prompt_budget(
    blocks: Sequence[MemoryBlock],
    budget: BudgetConfig = DEFAULT_BUDGET,
) -> PromptBudgetPlan:
    tier_budgets = {
        0: budget.tier_0_budget,
        1: budget.tier_1_budget,
        2: budget.tier_2_budget,
        3: budget.tier_3_budget,
    }
    tier_usage = {tier: 0 for tier in tier_budgets}

    ordered_blocks = sorted(
        enumerate(blocks),
        key=lambda item: (
            _policy_for_label(item[1].label).tier,
            _policy_for_label(item[1].label).order,
            item[0],
        ),
    )

    total_chars = 0
    decisions: list[PromptBudgetBlockDecision] = []
    result: list[MemoryBlock] = []

    for _original_index, block in ordered_blocks:
        policy = _policy_for_label(block.label)
        original_chars = len(block.value)

        if original_chars == 0:
            decisions.append(
                PromptBudgetBlockDecision(
                    label=block.label,
                    tier=policy.tier,
                    status="dropped",
                    original_chars=0,
                    final_chars=0,
                    reason="empty",
                )
            )
            continue

        capped_value = _apply_block_cap(block.value, policy.max_chars)
        capped_chars = len(capped_value)
        tier_remaining = tier_budgets[policy.tier] - tier_usage[policy.tier]
        total_remaining = budget.total_budget - total_chars
        available = min(tier_remaining, total_remaining)

        if available <= 0:
            decisions.append(
                PromptBudgetBlockDecision(
                    label=block.label,
                    tier=policy.tier,
                    status="dropped",
                    original_chars=original_chars,
                    final_chars=0,
                    reason=(
                        "total_budget_exhausted"
                        if total_remaining <= 0
                        else "tier_budget_exhausted"
                    ),
                )
            )
            continue

        final_chars = min(capped_chars, available)
        if final_chars <= 0:
            decisions.append(
                PromptBudgetBlockDecision(
                    label=block.label,
                    tier=policy.tier,
                    status="dropped",
                    original_chars=original_chars,
                    final_chars=0,
                    reason="budget_exhausted",
                )
            )
            continue

        final_value = capped_value[:final_chars]
        result.append(
            MemoryBlock(
                label=block.label,
                description=block.description,
                value=final_value,
                read_only=block.read_only,
            )
        )
        total_chars += final_chars
        tier_usage[policy.tier] += final_chars
        decisions.append(
            PromptBudgetBlockDecision(
                label=block.label,
                tier=policy.tier,
                status=("kept" if final_chars ==
                        original_chars else "truncated"),
                original_chars=original_chars,
                final_chars=final_chars,
                reason=_decision_reason(
                    original_chars=original_chars,
                    capped_chars=capped_chars,
                    final_chars=final_chars,
                ),
            )
        )

    dropped_chars = sum(
        max(0, decision.original_chars - decision.final_chars)
        for decision in decisions
    )
    trace = PromptBudgetTrace(
        total_budget=budget.total_budget,
        retained_chars=total_chars,
        dropped_chars=dropped_chars,
        retained_token_estimate=estimate_char_tokens(total_chars),
        dropped_token_estimate=estimate_char_tokens(dropped_chars),
        tier_usage={str(tier): used for tier, used in tier_usage.items()},
        tier_budgets={str(tier): limit for tier,
                      limit in tier_budgets.items()},
        decisions=tuple(decisions),
    )
    return PromptBudgetPlan(
        blocks=tuple(result),
        trace=trace,
    )


def apply_prompt_budget(
    blocks: Sequence[MemoryBlock],
    budget: BudgetConfig = DEFAULT_BUDGET,
) -> tuple[MemoryBlock, ...]:
    return plan_prompt_budget(blocks, budget).blocks


def _policy_for_label(label: str) -> BlockBudgetPolicy:
    return _BLOCK_POLICIES.get(label, _DEFAULT_POLICY)


def _apply_block_cap(value: str, max_chars: int | None) -> str:
    if max_chars is None or len(value) <= max_chars:
        return value
    return value[:max_chars]


def _decision_reason(
    *,
    original_chars: int,
    capped_chars: int,
    final_chars: int,
) -> str:
    if final_chars == original_chars:
        return "within_budget"
    if final_chars == capped_chars and capped_chars < original_chars:
        return "per_block_cap"
    if final_chars < capped_chars:
        return "budget_truncation"
    return "within_budget"
