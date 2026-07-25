from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Sequence

from british_museum_agent.domain.models import ToolCall
from british_museum_agent.retrieval.knowledge_base import KnowledgeDocument

MAX_RETRIEVAL_ATTEMPTS = 2
EVIDENCE_SCORE_THRESHOLD = 0.25
MINIMUM_ANSWER_LENGTH = 32


@dataclass(frozen=True)
class GroundingEvaluation:
    """Deterministic result used to route the bounded LangGraph cycle."""

    signal: str
    grounding_score: float
    retry_reason: str | None
    retryable: bool


def evaluate_grounding(
    *,
    matches: Sequence[tuple[KnowledgeDocument, float]],
    tool_calls: Sequence[ToolCall],
    answer: str,
) -> GroundingEvaluation:
    """Evaluate evidence, answer quality and attribution without another model call."""

    has_retrieval_evidence = bool(matches) and (
        matches[0][1] >= EVIDENCE_SCORE_THRESHOLD
    )
    has_operational_evidence = any(call.status == "success" for call in tool_calls)
    evidence_score = max(
        matches[0][1] if has_retrieval_evidence else 0.0,
        1.0 if has_operational_evidence else 0.0,
    )

    if not has_retrieval_evidence and not has_operational_evidence:
        return GroundingEvaluation(
            signal="no_evidence",
            grounding_score=0.0,
            retry_reason="no_evidence",
            retryable=True,
        )

    normalized_answer = " ".join(answer.split())
    if len(normalized_answer) < MINIMUM_ANSWER_LENGTH:
        return GroundingEvaluation(
            signal="answer_too_short",
            grounding_score=min(evidence_score, 0.4),
            retry_reason="answer_too_short",
            retryable=True,
        )

    if has_retrieval_evidence and not _mentions_source_title(answer, matches):
        return GroundingEvaluation(
            signal="missing_source_attribution",
            grounding_score=min(evidence_score, 0.6),
            retry_reason="missing_source_attribution",
            retryable=True,
        )

    return GroundingEvaluation(
        signal="grounded",
        grounding_score=evidence_score,
        retry_reason=None,
        retryable=False,
    )


def refine_retrieval_query(
    *,
    message: str,
    location_hint: str | None,
    retry_reason: str,
    matches: Sequence[tuple[KnowledgeDocument, float]],
) -> str:
    """Create one deterministic, inspectable retrieval refinement."""

    parts = [message.strip()]
    if location_hint and location_hint.casefold() not in message.casefold():
        parts.append(location_hint.strip())

    if retry_reason == "no_evidence":
        parts.append("British Museum colección objeto sala ubicación accesibilidad")
    else:
        source_titles = " ".join(document.title for document, _ in matches[:3])
        if source_titles:
            parts.append(source_titles)
        parts.append("evidencia verificable fuente")

    return " ".join(part for part in parts if part)


def _mentions_source_title(
    answer: str,
    matches: Sequence[tuple[KnowledgeDocument, float]],
) -> bool:
    normalized_answer = _normalize(answer)
    return any(
        normalized_title and normalized_title in normalized_answer
        for document, _ in matches
        if (normalized_title := _normalize(document.title))
    )


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[a-z0-9]+", without_accents))
