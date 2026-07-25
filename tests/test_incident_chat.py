from __future__ import annotations

import re

from british_museum_agent.application.chat_service import ChatService
from british_museum_agent.application.incident_chat import IncidentChatWorkflow
from british_museum_agent.domain.models import ChatRequest, ToolCall, UserRole
from british_museum_agent.retrieval.knowledge_base import RetrievalStatus

COMMAND = "En la sala 4 hay un cartel ilegible sobre accesibilidad. Registrá un incidente de prioridad media."


class NoRagRetriever:
    @property
    def status(self):
        return RetrievalStatus("test", False, "RAG must not run", "disabled", False, "disabled")

    def search(self, query: str, top_k: int = 4):
        raise AssertionError("incident commands must be handled before the RAG gate")


class RecordingIncidentTools:
    def __init__(self):
        self.calls: list[dict] = []

    def create_incident(self, payload: dict, *, reported_by: str):
        call_payload = {**payload, "reported_by": reported_by}
        self.calls.append(call_payload)
        output = {**call_payload, "id": 42, "status": "open", "created_at": "2026-07-22T00:00:00Z"}
        return output, ToolCall(name="create_incident", input=call_payload, output_summary=output, status="success", latency_ms=0)

    def get_incident(self, incident_id: int):
        output = {"id": incident_id, "gallery_id": "room-4", "category": "accessibility", "description": "Rampa bloqueada.", "priority": "high", "status": "open", "reported_by": "staff@example.com", "created_at": "2026-07-22T00:00:00Z"}
        return output, ToolCall(name="get_incident", input={"incident_id": incident_id}, output_summary=output, status="success", latency_ms=0)


def _staff_request(message: str, session_id: str = "incident-session") -> ChatRequest:
    return ChatRequest(message=message, user_role=UserRole.staff, staff_username="staff@example.com", session_id=session_id)


def _token(answer: str) -> str:
    return re.search(r"Confirmo ([A-Za-z0-9_-]+)", answer).group(1)


def test_staff_incident_command_is_confirmed_before_mcp_and_skips_rag():
    tools = RecordingIncidentTools()
    service = ChatService(NoRagRetriever(), tools)
    prepared = service.answer(_staff_request(COMMAND))

    assert "todavía no fue registrado" in prepared.answer
    assert prepared.tool_calls == []
    assert tools.calls == []
    assert prepared.sources == []

    created = service.answer(_staff_request(f"Confirmo {_token(prepared.answer)}"))

    assert created.answer == "Incidente #42 registrado correctamente."
    assert created.tool_calls[0].name == "create_incident"
    assert tools.calls[0]["gallery_id"] == "room-4"


def test_expired_pending_incident_cannot_be_confirmed():
    now = [100.0]
    tools = RecordingIncidentTools()
    workflow = IncidentChatWorkflow(tools, clock=lambda: now[0], pending_ttl_seconds=60)
    prepared = workflow.handle(_staff_request(COMMAND))
    now[0] += 61

    result = workflow.handle(_staff_request(f"Confirmo {_token(prepared.answer)}"))

    assert "expiró" in result.answer
    assert tools.calls == []


def test_replaced_pending_incident_rejects_old_token_and_accepts_current_token():
    tools = RecordingIncidentTools()
    workflow = IncidentChatWorkflow(tools)
    first = workflow.handle(_staff_request(COMMAND))
    replacement_command = COMMAND.replace("cartel ilegible", "rampa bloqueada")
    replacement = workflow.handle(_staff_request(replacement_command))

    stale = workflow.handle(_staff_request(f"Confirmo {_token(first.answer)}"))
    created = workflow.handle(_staff_request(f"Confirmo {_token(replacement.answer)}"))

    assert "no coincide" in stale.answer
    assert tools.calls == [{
        "gallery_id": "room-4", "category": "accessibility",
        "description": "En la sala 4 hay un rampa bloqueada sobre accesibilidad",
        "priority": "medium", "reported_by": "staff@example.com",
    }]
    assert created.tool_calls[0].name == "create_incident"


def test_incident_command_cannot_mutate_for_visitor_even_if_role_is_claimed():
    tools = RecordingIncidentTools()
    response = ChatService(NoRagRetriever(), tools).answer(ChatRequest(message=COMMAND, user_role=UserRole.visitor, session_id="visitor-session"))

    assert "JWT válido" in response.answer
    assert response.tool_calls == []
    assert tools.calls == []


def test_staff_incident_id_query_uses_mcp_before_rag_and_formats_only_returned_fields():
    response = ChatService(NoRagRetriever(), RecordingIncidentTools()).answer(
        _staff_request("¿Podés contarme del incidente #25?")
    )

    assert response.tool_calls[0].name == "get_incident"
    assert response.tool_calls[0].input == {"incident_id": 25}
    assert "Sala: room-4" in response.answer
    assert "Categoría: accessibility" in response.answer
    assert "Prioridad: high" in response.answer
    assert "Autor: staff@example.com" in response.answer


def test_visitor_incident_id_query_is_blocked_before_rag_and_mcp():
    response = ChatService(NoRagRetriever(), RecordingIncidentTools()).answer(
        ChatRequest(message="¿Podés contarme del incidente #25?", user_role=UserRole.visitor, session_id="visitor-session")
    )

    assert "JWT válido" in response.answer
    assert response.tool_calls == []
