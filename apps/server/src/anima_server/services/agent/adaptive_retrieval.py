from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from anima_server.services import anima_core_bindings

logger = logging.getLogger(__name__)

_rust_find_adaptive_cutoff = anima_core_bindings.rust_find_adaptive_cutoff
_rust_normalize_scores = anima_core_bindings.rust_normalize_scores


AdaptiveStrategy = Literal[
    "legacy",
    "absolute_threshold",
    "relative_threshold",
    "score_cliff",
    "elbow",
    "combined",
    "disabled",
]

@dataclass(frozen=True, slots=True)
class AdaptiveRetrievalConfig:
    enabled: bool = True
    strategy: AdaptiveStrategy = "combined"
    max_results: int = 12
    min_results: int = 3
    normalize_scores: bool = True
    absolute_min: float = 0.3
    relative_threshold: float = 0.5
    max_drop_ratio: float = 0.4
    sensitivity: float = 1.0
    high_confidence_threshold: float = 0.7
    gap_threshold: float = 0.15

    @classmethod
    def legacy(
        cls,
        *,
        max_results: int = 12,
        min_results: int = 3,
        high_confidence_threshold: float = 0.7,
        gap_threshold: float = 0.15,
    ) -> AdaptiveRetrievalConfig:
        return cls(
            strategy="legacy",
            max_results=max_results,
            min_results=min_results,
            high_confidence_threshold=high_confidence_threshold,
            gap_threshold=gap_threshold,
        )

    @classmethod
    def combined(
        cls,
        *,
        max_results: int = 12,
        min_results: int = 3,
        relative_threshold: float = 0.5,
        max_drop_ratio: float = 0.4,
        absolute_min: float = 0.3,
    ) -> AdaptiveRetrievalConfig:
        return cls(
            strategy="combined",
            max_results=max_results,
            min_results=min_results,
            relative_threshold=relative_threshold,
            max_drop_ratio=max_drop_ratio,
            absolute_min=absolute_min,
        )


@dataclass(frozen=True, slots=True)
class AdaptiveFilterStats:
    total_considered: int
    returned: int
    cutoff_index: int
    cutoff_score: float | None
    top_score: float | None
    cutoff_ratio: float | None
    triggered_by: str


@dataclass(frozen=True, slots=True)
class AdaptiveFilterResult[ItemT]:
    results: list[tuple[ItemT, float]]
    stats: AdaptiveFilterStats


def normalize_scores(scores: Sequence[float]) -> list[float]:
    if _rust_normalize_scores is not None:
        return [float(score) for score in _rust_normalize_scores(list(scores))]

    if not scores:
        return []

    max_score = max(scores)
    min_score = min(scores)
    score_range = max_score - min_score
    if abs(score_range) < 1e-12:
        return [1.0] * len(scores)
    return [float((score - min_score) / score_range) for score in scores]


def find_adaptive_cutoff(
    scores: Sequence[float],
    *,
    config: AdaptiveRetrievalConfig | None = None,
) -> tuple[int, str, list[float]]:
    active_config = config or AdaptiveRetrievalConfig()
    capped_scores = [float(score) for score in scores[: _effective_max(active_config)]]

    if not capped_scores:
        return 0, "no_results", []

    if not active_config.enabled or active_config.strategy == "disabled":
        return len(capped_scores), "disabled", normalize_scores(capped_scores)

    if active_config.strategy == "legacy":
        return _legacy_cutoff(capped_scores, active_config)

    if _rust_find_adaptive_cutoff is not None:
        cutoff, trigger, normalized = _rust_find_adaptive_cutoff(
            capped_scores,
            strategy=active_config.strategy,
            min_results=active_config.min_results,
            max_results=active_config.max_results,
            normalize=active_config.normalize_scores,
            absolute_min=active_config.absolute_min,
            relative_threshold=active_config.relative_threshold,
            max_drop_ratio=active_config.max_drop_ratio,
            sensitivity=active_config.sensitivity,
        )
        return int(cutoff), str(trigger), [float(score) for score in normalized]

    return _python_cutoff(capped_scores, active_config)


def apply_adaptive_filter[ItemT](
    results: list[tuple[ItemT, float]],
    *,
    config: AdaptiveRetrievalConfig | None = None,
) -> AdaptiveFilterResult[ItemT]:
    active_config = config or AdaptiveRetrievalConfig()
    capped = results[: _effective_max(active_config)]
    scores = [float(score) for _, score in capped]
    cutoff, triggered_by, _normalized = find_adaptive_cutoff(scores, config=active_config)
    cutoff = max(0, min(len(capped), cutoff))
    filtered = capped[:cutoff]

    top_score = scores[0] if scores else None
    cutoff_score = scores[cutoff - 1] if cutoff > 0 else None
    cutoff_ratio = None
    if top_score is not None and cutoff_score is not None and abs(top_score) > 1e-12:
        cutoff_ratio = cutoff_score / top_score

    return AdaptiveFilterResult(
        results=filtered,
        stats=AdaptiveFilterStats(
            total_considered=len(capped),
            returned=len(filtered),
            cutoff_index=cutoff,
            cutoff_score=cutoff_score,
            top_score=top_score,
            cutoff_ratio=cutoff_ratio,
            triggered_by=triggered_by,
        ),
    )


def _effective_max(config: AdaptiveRetrievalConfig) -> int:
    return max(1, config.max_results, config.min_results)


def _legacy_cutoff(
    scores: list[float],
    config: AdaptiveRetrievalConfig,
) -> tuple[int, str, list[float]]:
    normalized = normalize_scores(scores)

    if len(scores) <= config.min_results:
        return len(scores), "min_results", normalized

    top_scores = scores[: config.min_results]
    if all(score >= config.high_confidence_threshold for score in top_scores):
        cutoff = sum(1 for score in scores if score >= config.high_confidence_threshold)
        return cutoff, "legacy_precision", normalized

    for index in range(config.min_results, len(scores)):
        prev_score = scores[index - 1]
        curr_score = scores[index]
        if prev_score - curr_score > config.gap_threshold:
            return index, "legacy_gap", normalized

    return len(scores), "legacy_max_results", normalized


def _python_cutoff(
    scores: list[float],
    config: AdaptiveRetrievalConfig,
) -> tuple[int, str, list[float]]:
    normalized = normalize_scores(scores) if config.normalize_scores else list(scores)

    if not config.enabled or config.strategy == "disabled":
        return len(scores), "disabled", normalized

    if len(scores) <= config.min_results:
        return len(scores), "min_results", normalized

    if config.strategy == "absolute_threshold":
        cutoff, trigger = _find_absolute_cutoff(
            normalized,
            config.absolute_min,
            config.min_results,
        )
        return cutoff, trigger, normalized

    if config.strategy == "relative_threshold":
        threshold = normalized[0] * config.relative_threshold
        cutoff, _trigger = _find_absolute_cutoff(normalized, threshold, config.min_results)
        return cutoff, "relative_threshold", normalized

    if config.strategy == "score_cliff":
        cutoff, trigger = _find_cliff_cutoff(
            normalized,
            config.max_drop_ratio,
            config.min_results,
        )
        return cutoff, trigger, normalized

    if config.strategy == "elbow":
        cutoff, trigger = _find_elbow_cutoff(
            normalized,
            config.sensitivity,
            config.min_results,
        )
        return cutoff, trigger, normalized

    cutoff, trigger = _find_combined_cutoff(
        normalized,
        top_score=normalized[0],
        relative_threshold=config.relative_threshold,
        max_drop_ratio=config.max_drop_ratio,
        absolute_min=config.absolute_min,
        min_results=config.min_results,
    )
    return cutoff, trigger, normalized


def _find_absolute_cutoff(
    scores: Sequence[float],
    min_score: float,
    min_results: int,
) -> tuple[int, str]:
    for index, score in enumerate(scores):
        if index >= min_results and score < min_score:
            return index, "absolute_threshold"
    return len(scores), "no_cutoff"


def _find_cliff_cutoff(
    scores: Sequence[float],
    max_drop_ratio: float,
    min_results: int,
) -> tuple[int, str]:
    for index in range(1, len(scores)):
        if index < min_results:
            continue
        prev = scores[index - 1]
        curr = scores[index]
        if prev > 1e-12:
            drop_ratio = (prev - curr) / prev
            if drop_ratio > max_drop_ratio:
                return index, f"score_cliff({drop_ratio * 100.0:.1f}%)"
    return len(scores), "no_cutoff"


def _find_elbow_cutoff(
    scores: Sequence[float],
    sensitivity: float,
    min_results: int,
) -> tuple[int, str]:
    if len(scores) < 3:
        return len(scores), "too_few_points"

    x_norm = [index / (len(scores) - 1) for index in range(len(scores))]
    x1 = x_norm[0]
    y1 = scores[0]
    x2 = x_norm[-1]
    y2 = scores[-1]
    line_len = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    if line_len <= 1e-12:
        return len(scores), "flat_curve"

    max_distance = 0.0
    elbow_index = min_results
    for index in range(min_results, len(scores) - 1):
        x0 = x_norm[index]
        y0 = scores[index]
        distance = abs((y2 - y1) * x0 - (x2 - x1) * y0 + x2 * y1 - y2 * x1) / line_len
        adjusted_distance = distance * (1.0 + sensitivity * (1.0 - x_norm[index]))
        if adjusted_distance > max_distance:
            max_distance = adjusted_distance
            elbow_index = index

    if max_distance > 0.05 * sensitivity:
        return elbow_index + 1, "elbow_detection"
    return len(scores), "no_significant_elbow"


def _find_combined_cutoff(
    scores: Sequence[float],
    *,
    top_score: float,
    relative_threshold: float,
    max_drop_ratio: float,
    absolute_min: float,
    min_results: int,
) -> tuple[int, str]:
    relative_min = top_score * relative_threshold

    for index, score in enumerate(scores):
        if index < min_results:
            continue

        if score < absolute_min:
            return index, "absolute_min"

        if score < relative_min:
            return index, "relative_threshold"

        if index > 0:
            prev = scores[index - 1]
            if prev > 1e-12:
                drop_ratio = (prev - score) / prev
                if drop_ratio > max_drop_ratio:
                    return index, f"score_cliff({drop_ratio * 100.0:.1f}%)"

    return len(scores), "no_cutoff"
