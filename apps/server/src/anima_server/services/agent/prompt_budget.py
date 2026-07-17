"""Prompt budget planner.

Assigns explicit priorities and per-block caps to runtime memory blocks so the
prompt stays within the model's context window without crowding out critical
state. The budgeter now exposes a trace describing which blocks were kept,
truncated, or dropped so prompt-quality regressions are debuggable.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import ceil

from anima_server.services.agent.memory_blocks import MemoryBlock


@dataclass(frozen=True, slots=True)
class BlockBudgetPolicy:
    tier: int
    order: int
    max_chars: int | None = None
    # When set, the block's cap is computed at plan time (window-scaled)
    # and the block is bounded by that cap and the TOTAL budget only: the
    # static tier slices are sized for identity/memory blocks, not for
    # document payloads that legitimately dwarf them.
    resolve_max_chars: Callable[[], int] | None = None


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


def resolve_document_context_budget_chars() -> int:
    """Character budget for the ``document_context`` block, scaled to the
    resolved context budget and bounded by a hard character ceiling.

    Single source of truth for the document budget: the service layer uses
    it to decide whether full-document injection fits, and the planner uses
    it as the block's per-block cap, so what the service builds is what the
    prompt keeps. The ``* 3`` inverts ``estimate_char_tokens``'s
    conservative 3-chars-per-token ratio so the token and character budgets
    describe the same amount of text (same convention as
    ``resolve_budget_config``).
    """
    from anima_server.config import settings

    ratio = settings.document_full_context_budget_ratio
    budget_tokens = int(resolve_context_budget_tokens() * ratio)
    return min(budget_tokens * 3, settings.document_full_context_char_cap)


_DEFAULT_POLICY = BlockBudgetPolicy(tier=3, order=999, max_chars=1000)
_BLOCK_POLICIES: dict[str, BlockBudgetPolicy] = {
    "soul": BlockBudgetPolicy(tier=0, order=0, max_chars=None),
    "persona": BlockBudgetPolicy(tier=0, order=1, max_chars=None),
    "human": BlockBudgetPolicy(tier=0, order=2, max_chars=None),
    "user_profile": BlockBudgetPolicy(tier=0, order=3, max_chars=1800),
    "user_directive": BlockBudgetPolicy(tier=0, order=4, max_chars=None),
    # Cap resolved at plan time from the window-scaled document budget so
    # full-document injection survives prompt assembly (a static cap here
    # silently mangled any document selection larger than it).
    "document_context": BlockBudgetPolicy(
        tier=0, order=5, resolve_max_chars=resolve_document_context_budget_chars
    ),
    "self_identity": BlockBudgetPolicy(tier=1, order=0, max_chars=1600),
    "current_focus": BlockBudgetPolicy(tier=1, order=1, max_chars=1400),
    "thread_summary": BlockBudgetPolicy(tier=1, order=2, max_chars=1800),
    "self_inner_state": BlockBudgetPolicy(tier=1, order=3, max_chars=900),
    "self_working_memory": BlockBudgetPolicy(tier=1, order=4, max_chars=700),
    "learned_skills": BlockBudgetPolicy(tier=1, order=5, max_chars=1800),
    "past_approaches": BlockBudgetPolicy(tier=1, order=6, max_chars=1200),
    "relevant_memories": BlockBudgetPolicy(tier=2, order=0, max_chars=2200),
    "emotional_context": BlockBudgetPolicy(tier=2, order=2, max_chars=700),
    "cross_episode_patterns": BlockBudgetPolicy(tier=2, order=3, max_chars=1000),
    "foresight": BlockBudgetPolicy(tier=2, order=4, max_chars=1000),
    "user_tasks": BlockBudgetPolicy(tier=2, order=5, max_chars=1400),
    "facts": BlockBudgetPolicy(tier=2, order=6, max_chars=1500),
    "preferences": BlockBudgetPolicy(tier=2, order=7, max_chars=1200),
    "self_intentions": BlockBudgetPolicy(tier=2, order=8, max_chars=1000),
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

# Tier shares of the total budget, taken from the DEFAULT_BUDGET ratios
# (4/6/6/8 of 24).  Used when deriving a budget from the context window.
_TIER_SHARES = (4 / 24, 6 / 24, 6 / 24, 8 / 24)


# Fixed reservation for prompt content the char-based budgets never
# counted: the system template scaffolding around the blocks and the
# serialized tool JSON schemas sent with every request.
PROMPT_SCAFFOLDING_RESERVE_TOKENS = 3000


def resolve_context_budget_tokens() -> int:
    """Token budget available to the whole prompt (blocks + conversation).

    When ``agent_context_window_tokens`` is configured, the budget is the
    window minus the output reservation (``agent_max_tokens``) and the
    scaffolding/tool-schema reserve.  The legacy fallback (no window
    configured) derives from the block budget it has to accommodate:
    ``agent_max_tokens`` used to double as the whole context budget while
    DEFAULT_BUDGET simultaneously allowed more block chars than that
    budget could hold, so compaction triggers fired at the wrong point.
    """
    from anima_server.config import settings

    window = settings.agent_context_window_tokens
    if window is not None and window > 0:
        return max(
            1024,
            window - settings.agent_max_tokens - PROMPT_SCAFFOLDING_RESERVE_TOKENS,
        )
    # Blocks are roughly half the prompt; history keeps the other half.
    block_tokens = estimate_char_tokens(DEFAULT_BUDGET.total_budget)
    return max(settings.agent_max_tokens, block_tokens * 2)


def resolve_budget_config() -> BudgetConfig:
    """Budget for memory blocks, derived from the resolved context budget.

    Blocks may use at most ``agent_block_budget_ratio`` of the context
    budget so conversation history keeps the rest.  Without a configured
    context window, the static DEFAULT_BUDGET applies (legacy behaviour).
    """
    from anima_server.config import settings

    if settings.agent_context_window_tokens is None:
        return DEFAULT_BUDGET

    ratio = min(max(settings.agent_block_budget_ratio, 0.05), 0.95)
    block_tokens = int(resolve_context_budget_tokens() * ratio)
    # Inverse of estimate_char_tokens (chars/3), so the char budget and
    # the token budget describe the same amount of text.
    total_chars = max(4000, block_tokens * 3)
    tier_chars = [int(total_chars * share) for share in _TIER_SHARES]
    return BudgetConfig(
        total_budget=total_chars,
        tier_0_budget=tier_chars[0],
        tier_1_budget=tier_chars[1],
        tier_2_budget=tier_chars[2],
        tier_3_budget=tier_chars[3],
    )


def estimate_char_tokens(char_count: int) -> int:
    """Conservative token estimate (chars/3): never optimistic, so budget
    checks fail toward compaction rather than context overflow."""
    if char_count <= 0:
        return 0
    return max(1, ceil(char_count / 3))


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

        max_chars = (
            policy.resolve_max_chars()
            if policy.resolve_max_chars is not None
            else policy.max_chars
        )
        capped_value = _apply_block_cap(block.value, max_chars)
        capped_chars = len(capped_value)
        total_remaining = budget.total_budget - total_chars
        if policy.resolve_max_chars is not None:
            # Dynamically-budgeted blocks carry their own window-scaled cap
            # and share only the total budget with everything else; the
            # static tier slice would re-truncate them to a fraction of the
            # budget they were deliberately sized for.
            available = total_remaining
        else:
            tier_remaining = tier_budgets[policy.tier] - tier_usage[policy.tier]
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

        final_value = _truncate_at_boundary(capped_value, final_chars)
        final_chars = len(final_value)
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
                status=("kept" if final_chars == original_chars else "truncated"),
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
        max(0, decision.original_chars - decision.final_chars) for decision in decisions
    )
    trace = PromptBudgetTrace(
        total_budget=budget.total_budget,
        retained_chars=total_chars,
        dropped_chars=dropped_chars,
        retained_token_estimate=estimate_char_tokens(total_chars),
        dropped_token_estimate=estimate_char_tokens(dropped_chars),
        tier_usage={str(tier): used for tier, used in tier_usage.items()},
        tier_budgets={str(tier): limit for tier, limit in tier_budgets.items()},
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
    return _truncate_at_boundary(value, max_chars)


def _truncate_at_boundary(value: str, max_chars: int) -> str:
    """Truncate to at most *max_chars*, backing off to the last line or
    sentence boundary so a cut block never ends mid-fact
    ("user is allergic to")."""
    if len(value) <= max_chars:
        return value
    cut = value[:max_chars]
    for separator, keep in (("\n", 0), (". ", 1)):
        index = cut.rfind(separator)
        # Only back off when at least a third of the budget is preserved;
        # otherwise a single long sentence would be dropped entirely.
        if index >= max_chars // 3:
            return cut[: index + keep]
    return cut


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
