from __future__ import annotations

from uuid import uuid4

from british_museum_agent.adapters_mcp.museum_tools import MuseumTools
from british_museum_agent.agent.graph import MuseumAgentGraph
from british_museum_agent.domain.models import ChatRequest, ChatResponse
from british_museum_agent.generation.answer_generator import (
    AnswerGenerator,
    GroundedFallbackGenerator,
)
from british_museum_agent.observability.metrics import get_service_metrics
from british_museum_agent.observability.tracing import trace_chat
from british_museum_agent.retrieval.knowledge_base import KnowledgeRetriever


class ChatService:
    def __init__(
        self,
        knowledge_base: KnowledgeRetriever,
        tools: MuseumTools,
        answer_generator: AnswerGenerator | None = None,
        *,
        tracing_enabled: bool = False,
        langsmith_project: str = "british-museum-agent",
    ):
        generator = answer_generator or GroundedFallbackGenerator("generador no configurado")
        self.agent = MuseumAgentGraph(
            knowledge_base,
            tools,
            generator,
            tracing_enabled=tracing_enabled,
            langsmith_project=langsmith_project,
        )

    def answer(self, request: ChatRequest) -> ChatResponse:
        trace_id = str(uuid4())
        metrics = get_service_metrics()
        metrics.record_chat_started()
        with trace_chat(trace_id) as span:
            try:
                response = self.agent.invoke(request=request, trace_id=trace_id)
            except Exception as exc:
                metrics.record_chat_failure()
                span.record_error(exc)
                raise
            metrics.record_chat_response(response)
            span.record_response(response)
            return response