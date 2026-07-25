from types import SimpleNamespace

from british_museum_agent.domain.models import ChatRequest
from british_museum_agent.generation.answer_generator import (
    GeminiAnswerGenerator,
    GroundedFallbackGenerator,
)
from british_museum_agent.retrieval.knowledge_base import KnowledgeDocument


class FakeGemini:
    def invoke(self, messages, config):
        assert config["run_name"] == "gemini-grounded-answer"
        return SimpleNamespace(content="La Piedra de Rosetta está en la Sala 4 [Piedra de Rosetta].")


class FailingGemini:
    def invoke(self, messages, config):
        raise ConnectionError("offline")


def _matches():
    return [
        (
            KnowledgeDocument(
                chunk_id="rosetta-1",
                title="Piedra de Rosetta",
                source="rosetta.md",
                url="https://example.com/rosetta",
                text="La Piedra de Rosetta se exhibe en la Sala 4.",
            ),
            0.91,
        )
    ]


def test_gemini_generator_uses_injected_llm_without_network():
    generator = GeminiAnswerGenerator(
        model_name="gemini-test",
        api_key="dummy-not-real",
        fallback=GroundedFallbackGenerator("test"),
        llm=FakeGemini(),
    )

    result = generator.generate(
        request=ChatRequest(message="¿Dónde está la Piedra de Rosetta?"),
        matches=_matches(),
        tool_results=[],
        trace_id="trace-test",
    )

    assert result.status.active is True
    assert result.status.provider == "gemini"
    assert "Sala 4" in result.answer


def test_gemini_failure_is_reported_as_explicit_local_fallback():
    generator = GeminiAnswerGenerator(
        model_name="gemini-test",
        api_key="dummy-not-real",
        fallback=GroundedFallbackGenerator("test"),
        llm=FailingGemini(),
    )

    result = generator.generate(
        request=ChatRequest(message="¿Dónde está la Piedra de Rosetta?"),
        matches=_matches(),
        tool_results=[],
        trace_id="trace-test",
    )

    assert result.status.active is False
    assert result.status.provider == "local_grounded_fallback"
    assert "Gemini no está activo" in result.answer
    assert "ConnectionError" in result.status.detail