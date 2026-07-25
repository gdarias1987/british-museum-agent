from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable


@dataclass(frozen=True)
class QualityProxies:
    groundedness: float
    hallucination_risk: float


def estimate_quality_proxies(
    confidence: float,
    source_scores: Iterable[float],
) -> QualityProxies | None:
    """Estimate retrieval support without inspecting or retaining answer content."""
    scores = [_clamp(score) for score in source_scores if isfinite(score)]
    if not scores:
        return None

    groundedness = _clamp((_clamp(confidence) + max(scores)) / 2.0)
    return QualityProxies(
        groundedness=groundedness,
        hallucination_risk=1.0 - groundedness,
    )


def _clamp(value: float) -> float:
    if not isfinite(value):
        return 0.0
    return max(0.0, min(1.0, float(value)))
