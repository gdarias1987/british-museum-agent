from __future__ import annotations

import logging
import re
from time import perf_counter
from typing import Any, TypedDict
from uuid import UUID

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from british_museum_agent.adapters_mcp.museum_tools import MuseumTools
from british_museum_agent.application.incident_chat import IncidentChatWorkflow
from british_museum_agent.domain.models import (
    ChatRequest,
    ChatResponse,
    ComponentRuntimeStatus,
    RetrievedSource,
    RuntimeStatus,
    ToolCall,
)
from british_museum_agent.generation.answer_generator import (
    AnswerGenerator,
    GenerationResult,
    GenerationStatus,
)
from british_museum_agent.orchestration.quality import (
    EVIDENCE_SCORE_THRESHOLD,
    MAX_RETRIEVAL_ATTEMPTS,
    evaluate_grounding,
    refine_retrieval_query,
)
from british_museum_agent.retrieval.knowledge_base import (
    KnowledgeDocument,
    KnowledgeRetriever,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_EXCERPT_LENGTH: int = 420

CONFIDENCE_NONE: float = 0.0
CONFIDENCE_LOW: float = 0.2
CONFIDENCE_MEDIUM: float = 0.5
CONFIDENCE_HIGH: float = 0.85


class MuseumAgentState(TypedDict, total=False):
    request: ChatRequest
    trace_id: str
    invalid_input: bool
    operational_query: bool
    incident_handled: bool
    matches: list[tuple[KnowledgeDocument, float]]
    sources: list[RetrievedSource]
    tool_request: dict[str, Any] | None
    tool_results: list[dict[str, Any]]
    tool_calls: list[ToolCall]
    answer: str
    confidence: float
    needs_clarification: bool
    safety_notes: list[str]
    generation_status: GenerationStatus
    runtime: RuntimeStatus
    retrieval_query: str
    refined_query: str
    iteration_count: int
    retry_reason: str | None
    quality_signal: str
    grounding_score: float
    retry_requested: bool
    tool_execution_attempted: bool


class MuseumAgentGraph:
    """LangGraph orchestration for grounded RAG, Gemini generation and controlled MCP tools."""

    def __init__(
        self,
        knowledge_base: KnowledgeRetriever,
        tools: MuseumTools,
        answer_generator: AnswerGenerator,
        *,
        tracing_enabled: bool = False,
        langsmith_project: str = "british-museum-agent",
    ):
        self.knowledge_base = knowledge_base
        self.tools = tools
        self.answer_generator = answer_generator
        self.incident_workflow = IncidentChatWorkflow(tools)
        self.checkpointer = MemorySaver()
        self.tracing_enabled = tracing_enabled
        self.langsmith_project = langsmith_project
        self.graph = self._build_graph()

    def invoke(self, request: ChatRequest, trace_id: str) -> ChatResponse:
        thread_id = f"{request.session_id}-{trace_id[:8]}"
        try:
            run_id = UUID(trace_id)
        except ValueError:
            run_id = None
            logger.warning("Invalid trace_id=%r, using run_id=None", trace_id)
        result = self.graph.invoke(
            {"request": request, "trace_id": trace_id},
            config={
                "configurable": {"thread_id": thread_id},
                "run_id": run_id,
                "run_name": "british-museum-agent-chat",
                "tags": [
                    "british-museum",
                    "rag",
                    request.user_role.value,
                    request.language,
                ],
                "metadata": {
                    "trace_id": trace_id,
                    "session_id": request.session_id,
                    "thread_id": thread_id,
                    "role": request.user_role.value,
                    "language": request.language,
                    "langsmith_project": self.langsmith_project,
                    "max_retrieval_attempts": MAX_RETRIEVAL_ATTEMPTS,
                },
            },
        )
        return ChatResponse(
            answer=result["answer"],
            trace_id=trace_id,
            sources=result.get("sources", []),
            tool_calls=result.get("tool_calls", []),
            confidence=result.get("confidence", 0.0),
            needs_clarification=result.get("needs_clarification", False),
            safety_notes=result.get("safety_notes", []),
            runtime=result["runtime"],
        )

    def _build_graph(self):
        graph = StateGraph(MuseumAgentState)
        graph.add_node("validate_input", self._validate_input)
        graph.add_node("handle_incident", self._handle_incident)
        graph.add_node("retrieve_context", self._retrieve_context)
        graph.add_node("evaluate_context", self._evaluate_context)
        graph.add_node("decide_tool_usage", self._decide_tool_usage)
        graph.add_node("execute_tool", self._execute_tool)
        graph.add_node("generate_answer", self._generate_answer)
        graph.add_node("generate_no_evidence", self._generate_no_evidence)
        graph.add_node("evaluate_or_refine", self._evaluate_or_refine)
        graph.add_node("finalize", self._finalize)

        graph.set_entry_point("validate_input")
        graph.add_conditional_edges(
            "validate_input",
            self._route_after_validation,
            {"valid": "handle_incident", "invalid": "finalize"},
        )
        graph.add_conditional_edges(
            "handle_incident",
            self._route_after_incident_handling,
            {"handled": "finalize", "continue": "retrieve_context"},
        )
        graph.add_edge("retrieve_context", "evaluate_context")
        graph.add_conditional_edges(
            "evaluate_context",
            self._route_after_context_evaluation,
            {
                "has_evidence": "decide_tool_usage",
                "no_evidence": "generate_no_evidence",
            },
        )
        graph.add_conditional_edges(
            "decide_tool_usage",
            self._route_after_tool_decision,
            {
                "use_tool": "execute_tool",
                "skip_tool": "generate_answer",
            },
        )
        graph.add_edge("execute_tool", "generate_answer")
        graph.add_edge("generate_answer", "evaluate_or_refine")
        graph.add_edge("generate_no_evidence", "evaluate_or_refine")
        graph.add_conditional_edges(
            "evaluate_or_refine",
            self._route_after_answer_evaluation,
            {
                "retry": "retrieve_context",
                "finalize": "finalize",
            },
        )
        graph.add_edge("finalize", END)
        return graph.compile(checkpointer=self.checkpointer)
    def _validate_input(self, state: MuseumAgentState) -> MuseumAgentState:
        request = state["request"]
        message = request.message.strip()
        if not message:
            generation_status = self.answer_generator.configured_status
            return {
                **state,
                "invalid_input": True,
                "answer": "Necesito una consulta para poder ayudarte.",
                "sources": [],
                "tool_calls": [],
                "confidence": CONFIDENCE_NONE,
                "needs_clarification": True,
                "safety_notes": ["La consulta vacía fue rechazada antes de recuperar información."],
                "runtime": self._runtime_status(generation_status),
            }
        return {
            **state,
            "invalid_input": False,
            "request": request.model_copy(update={"message": message, "language": "es"}),
        }

    def _route_after_validation(self, state: MuseumAgentState) -> str:
        return "invalid" if state.get("invalid_input", False) else "valid"

    def _handle_incident(self, state: MuseumAgentState) -> MuseumAgentState:
        result = self.incident_workflow.handle(state["request"])
        if result is None:
            return {**state, "incident_handled": False}
        generation_status = self.answer_generator.configured_status
        return {
            **state,
            "incident_handled": True,
            "answer": result.answer,
            "sources": [],
            "tool_calls": result.tool_calls,
            "confidence": result.confidence,
            "needs_clarification": result.needs_clarification,
            "safety_notes": result.safety_notes,
            "runtime": self._runtime_status(generation_status),
        }

    def _route_after_incident_handling(self, state: MuseumAgentState) -> str:
        return "handled" if state.get("incident_handled", False) else "continue"

    def _safe_search(self, query: str) -> list[tuple[KnowledgeDocument, float]]:
        """Wrap knowledge_base.search() with exception handling."""
        try:
            return self.knowledge_base.search(query)
        except Exception as exc:
            logger.error(
                "knowledge_base.search() falló: %s: %s", type(exc).__name__, exc
            )
            return []

    def _retrieve_context(self, state: MuseumAgentState) -> MuseumAgentState:
        request = state["request"]
        query = state.get("refined_query") or request.message
        if request.location_hint and request.location_hint.casefold() not in query.casefold():
            query = f"{query} {request.location_hint}"
        iteration_count = min(
            state.get("iteration_count", 0) + 1,
            MAX_RETRIEVAL_ATTEMPTS,
        )
        return {
            **state,
            "matches": self._safe_search(query),
            "retrieval_query": query,
            "iteration_count": iteration_count,
            "retry_requested": False,
        }
    def _evaluate_context(self, state: MuseumAgentState) -> MuseumAgentState:
        request = state["request"]
        matches = state.get("matches", [])
        has_evidence = bool(matches) and matches[0][1] >= EVIDENCE_SCORE_THRESHOLD
        operational_query = (
            _detect_operational_gallery_status(request.message, request.location_hint) is not None
        )
        return {
            **state,
            "operational_query": operational_query,
            "needs_clarification": not has_evidence and not operational_query,
            "confidence": (
                matches[0][1]
                if has_evidence
                else (CONFIDENCE_MEDIUM if operational_query else CONFIDENCE_LOW)
            ),
        }

    def _route_after_context_evaluation(self, state: MuseumAgentState) -> str:
        if state.get("operational_query", False):
            return "has_evidence"
        return "no_evidence" if state.get("needs_clarification", True) else "has_evidence"

    def _decide_tool_usage(self, state: MuseumAgentState) -> MuseumAgentState:
        if state.get("tool_execution_attempted", False):
            return {**state, "tool_request": None}

        request = state["request"]
        gallery_id = _detect_gallery_id(request.message, request.location_hint)
        if gallery_id is None:
            return {
                **state,
                "tool_request": None,
                "tool_results": state.get("tool_results", []),
                "tool_calls": state.get("tool_calls", []),
            }
        return {
            **state,
            "tool_request": {
                "name": "get_gallery_status",
                "input": {"gallery_id": gallery_id},
            },
            "tool_results": state.get("tool_results", []),
            "tool_calls": state.get("tool_calls", []),
        }
    def _route_after_tool_decision(self, state: MuseumAgentState) -> str:
        return "use_tool" if state.get("tool_request") else "skip_tool"

    def _execute_tool(self, state: MuseumAgentState) -> MuseumAgentState:
        request = state["request"]
        tool_request = state.get("tool_request")
        if not tool_request or tool_request["name"] != "get_gallery_status":
            return state
        started = perf_counter()
        try:
            output, call = self.tools.get_gallery_status(
                tool_request["input"],
                request.user_role,
            )
        except Exception as exc:
            output = {
                "error": "mcp_service_unavailable",
                "detail": type(exc).__name__,
            }
            call = ToolCall(
                name="get_gallery_status",
                input=tool_request["input"],
                output_summary=output,
                status="error",
                latency_ms=int((perf_counter() - started) * 1000),
            )
        tool_succeeded = call.status == "success"
        return {
            **state,
            "tool_results": state.get("tool_results", []) + [output],
            "tool_calls": state.get("tool_calls", []) + [call],
            "tool_execution_attempted": True,
            "confidence": (
                max(state.get("confidence", CONFIDENCE_NONE), CONFIDENCE_HIGH)
                if tool_succeeded
                else CONFIDENCE_LOW
            ),
            "needs_clarification": not tool_succeeded and not bool(state.get("matches")),
        }

    def _generate_answer(self, state: MuseumAgentState) -> MuseumAgentState:
        request = state["request"]
        matches = state.get("matches", [])
        sources = [
            RetrievedSource(
                title=doc.title,
                source=doc.source,
                url=doc.url,
                chunk_id=doc.chunk_id,
                score=score,
                excerpt=doc.text[:MAX_EXCERPT_LENGTH],
            )
            for doc, score in matches
        ]
        try:
            result = self.answer_generator.generate(
                request=request,
                matches=matches,
                tool_results=state.get("tool_results", []),
                trace_id=state["trace_id"],
            )
        except Exception as exc:
            logger.error("generate() failed: %s: %s", type(exc).__name__, exc)
            result = GenerationResult(
                answer="Lo siento, no pude generar una respuesta en este momento. El servicio de IA no está disponible.",
                status=GenerationStatus(
                    provider="local_error_fallback",
                    active=False,
                    detail=f"Generation failed ({type(exc).__name__}).",
                ),
                safety_note="La generación falló por un error interno; no se devolvió contenido generado.",
            )
        retrieval_status = self.knowledge_base.status
        safety_notes = [
            result.safety_note,
            _retrieval_safety_note(retrieval_status),
        ]
        if any(call.status == "error" for call in state.get("tool_calls", [])):
            safety_notes.append("MCP no estuvo disponible o no encontró la sala; la respuesta lo trata como fallback.")
        return {
            **state,
            "answer": result.answer,
            "sources": sources,
            "generation_status": result.status,
            "runtime": self._runtime_status(result.status),
            "safety_notes": safety_notes,
        }

    def _generate_no_evidence(self, state: MuseumAgentState) -> MuseumAgentState:
        generation_status = self.answer_generator.configured_status
        return {
            **state,
            "answer": (
                "No tengo evidencia suficiente en el corpus cargado para responder con confianza. "
                "Probá mencionar una sala, período, artista, objeto o necesidad de accesibilidad."
            ),
            "sources": [],
            "tool_calls": state.get("tool_calls", []),
            "confidence": CONFIDENCE_LOW,
            "needs_clarification": True,
            "generation_status": generation_status,
            "runtime": self._runtime_status(generation_status),
            "safety_notes": [
                "Respuesta sin evidencia: no se inventaron datos.",
                _retrieval_safety_note(self.knowledge_base.status),
            ],
        }

    def _evaluate_or_refine(self, state: MuseumAgentState) -> MuseumAgentState:
        generation_status = state.get("generation_status")
        if (
            generation_status is not None
            and generation_status.provider == "local_error_fallback"
        ):
            return {
                **state,
                "retry_reason": "generation_provider_failure",
                "quality_signal": "generation_error_fallback",
                "grounding_score": 0.0,
                "retry_requested": False,
                "needs_clarification": False,
            }

        evaluation = evaluate_grounding(
            matches=state.get("matches", []),
            tool_calls=state.get("tool_calls", []),
            answer=state.get("answer", ""),
        )
        iteration_count = state.get("iteration_count", 0)
        should_retry = (
            evaluation.retryable
            and iteration_count < MAX_RETRIEVAL_ATTEMPTS
            and not state.get("incident_handled", False)
        )
        if should_retry:
            retry_reason = evaluation.retry_reason or "quality_criteria_not_met"
            return {
                **state,
                "refined_query": refine_retrieval_query(
                    message=state["request"].message,
                    location_hint=state["request"].location_hint,
                    retry_reason=retry_reason,
                    matches=state.get("matches", []),
                ),
                "retry_reason": retry_reason,
                "quality_signal": f"retrying:{evaluation.signal}",
                "grounding_score": evaluation.grounding_score,
                "retry_requested": True,
            }

        quality_signal = evaluation.signal
        safety_notes = list(state.get("safety_notes", []))
        confidence = state.get("confidence", 0.0)
        needs_clarification = state.get("needs_clarification", False)
        if evaluation.retryable:
            quality_signal = f"retry_exhausted:{evaluation.signal}"
            confidence = min(confidence, max(0.2, evaluation.grounding_score))
            needs_clarification = True
            safety_notes.append(
                "La evaluación determinista agotó el único reintento permitido."
            )

        return {
            **state,
            "retry_reason": state.get("retry_reason") or evaluation.retry_reason,
            "quality_signal": quality_signal,
            "grounding_score": evaluation.grounding_score,
            "retry_requested": False,
            "confidence": confidence,
            "needs_clarification": needs_clarification,
            "safety_notes": safety_notes,
        }

    def _route_after_answer_evaluation(self, state: MuseumAgentState) -> str:
        return "retry" if state.get("retry_requested", False) else "finalize"

    def _runtime_status(
        self,
        generation_status: GenerationStatus,
        state: MuseumAgentState | None = None,
    ) -> RuntimeStatus:
        retrieval = self.knowledge_base.status
        tracing_detail = (
            f"LangSmith activo en el proyecto {self.langsmith_project}."
            if self.tracing_enabled
            else "LangSmith desactivado o sin credenciales."
        )
        if state is not None:
            retry_reason = state.get("retry_reason") or "none"
            quality_signal = state.get("quality_signal", "not_evaluated")
            tracing_detail = (
                f"{tracing_detail} Orchestration metadata: "
                f"iteration_count={state.get('iteration_count', 0)}; "
                f"retry_reason={retry_reason}; "
                f"quality_signal={quality_signal}; "
                f"grounding_score={state.get('grounding_score', 0.0):.3f}."
            )
        return RuntimeStatus(
            retrieval=ComponentRuntimeStatus(
                active=retrieval.retrieval_active,
                mode=retrieval.backend,
                detail=retrieval.retrieval_detail,
            ),
            reranking=ComponentRuntimeStatus(
                active=retrieval.reranker_active,
                mode=retrieval.reranker,
                detail=retrieval.reranker_detail,
            ),
            generation=ComponentRuntimeStatus(
                active=generation_status.active,
                mode=generation_status.provider,
                detail=generation_status.detail,
            ),
            tracing=ComponentRuntimeStatus(
                active=self.tracing_enabled,
                mode="langsmith" if self.tracing_enabled else "disabled",
                detail=tracing_detail,
            ),
        )
    def _finalize(self, state: MuseumAgentState) -> MuseumAgentState:
        if state.get("invalid_input", False):
            state = {
                **state,
                "iteration_count": 0,
                "retry_reason": None,
                "quality_signal": "invalid_input",
                "grounding_score": 0.0,
            }
        elif state.get("incident_handled", False):
            state = {
                **state,
                "iteration_count": 0,
                "retry_reason": None,
                "quality_signal": "incident_handled",
                "grounding_score": state.get("confidence", 0.0),
            }
        generation_status = state.get(
            "generation_status",
            self.answer_generator.configured_status,
        )
        return {
            **state,
            "runtime": self._runtime_status(generation_status, state),
        }

def _retrieval_safety_note(status) -> str:
    if status.backend == "chroma" and status.reranker_active:
        return "Contexto recuperado desde ChromaDB y reordenado con un cross-encoder multilingüe."
    return f"Modo de recuperación informado explícitamente: {status.retrieval_detail}"


def _detect_operational_gallery_status(
    message: str,
    location_hint: str | None,
) -> str | None:
    gallery_id = _detect_gallery_id(message, location_hint)
    if gallery_id is None:
        return None
    text = message.casefold()
    operational_patterns = (
        r"\babiert[ao]s?\b",
        r"\bcerrad[ao]s?\b",
        r"\bestado\b",
        r"\bdisponible\b",
        r"\boperativ[ao]s?\b",
        r"\bfunciona(?:ndo)?\b",
        r"\bpuedo\s+entrar\b",
        r"\bse\s+puede\s+entrar\b",
    )
    if any(re.search(pattern, text) for pattern in operational_patterns):
        return gallery_id
    return None


def _detect_gallery_id(message: str, location_hint: str | None) -> str | None:
    text = f"{message} {location_hint or ''}".casefold()
    if re.search(r"\b(room|sala)\s*4\b", text):
        return "room-4"
    if re.search(r"\b(room|rooms|sala|salas)\s*6\s*-\s*10\b", text):
        return "rooms-6-10"
    if re.search(r"\b(room|rooms|sala|salas)\s*61\s*-\s*66\b", text):
        return "rooms-61-66"
    if "middle east" in text or "medio oriente" in text:
        return "rooms-42-43-52-59"
    return None