from __future__ import annotations

import math
from typing import Sequence


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


class MultilingualCrossEncoderReranker:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
        return self._model

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        raw_scores = self._get_model().predict(
            [(query, passage) for passage in passages],
            show_progress_bar=False,
        )
        return [_sigmoid(float(score)) for score in raw_scores]
