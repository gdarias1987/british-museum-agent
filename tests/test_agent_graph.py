import json
from pathlib import Path

from british_museum_agent.adapters_mcp.museum_tools import MuseumTools
from british_museum_agent.application.chat_service import ChatService
from british_museum_agent.domain.models import ChatRequest
from british_museum_agent.retrieval.knowledge_base import KnowledgeBase


class FakeRepository:
    def get_gallery_status(self, gallery_id: str):
        if gallery_id != "room-4":
            return None
        return {
            "id": "room-4",
            "name": "Sala 4 - Escultura egipcia",
            "floor": "Planta baja",
            "department": "Egipto y Sudán",
            "status": "open",
            "accessibility_notes": "Usá la ruta sin escalones.",
        }


def _write_index(path: Path) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "chunk_id": "room-4-1",
                    "title": "Sala 4 - Escultura egipcia",
                    "source": "fixture.md",
                    "url": "https://example.com/room-4",
                    "text": "La Sala 4 contiene escultura egipcia y la Piedra de Rosetta.",
                    "tags": ["egipto", "sala-4"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_langgraph_chat_returns_source_grounded_answer(tmp_path: Path):
    index = tmp_path / "knowledge_index.json"
    _write_index(index)
    service = ChatService(KnowledgeBase(index), MuseumTools(FakeRepository()))

    response = service.answer(ChatRequest(message="¿Qué puedo ver de Egipto?"))

    assert response.needs_clarification is False
    assert response.sources[0].chunk_id == "room-4-1"
    assert response.tool_calls == []
    assert response.runtime.retrieval.mode == "lexical_fallback"
    assert response.runtime.generation.active is False
    assert "Gemini no está activo" in response.answer
    assert response.confidence >= 0.25


def test_langgraph_chat_uses_gallery_status_tool(tmp_path: Path):
    index = tmp_path / "knowledge_index.json"
    _write_index(index)
    service = ChatService(KnowledgeBase(index), MuseumTools(FakeRepository()))

    response = service.answer(ChatRequest(message="¿Está abierta la Sala 4?"))

    assert response.needs_clarification is False
    assert response.tool_calls[0].name == "get_gallery_status"
    assert response.tool_calls[0].input == {"gallery_id": "room-4"}
    assert "figura como open" in response.answer
    assert "ruta sin escalones" in response.answer


def test_operational_gallery_query_uses_mcp_without_rag_evidence(tmp_path: Path):
    index = tmp_path / "knowledge_index.json"
    index.write_text("[]", encoding="utf-8")
    service = ChatService(KnowledgeBase(index), MuseumTools(FakeRepository()))

    response = service.answer(ChatRequest(message="¿Está abierta la Sala 4?"))

    assert response.needs_clarification is False
    assert response.sources == []
    assert response.tool_calls[0].name == "get_gallery_status"
    assert response.tool_calls[0].input == {"gallery_id": "room-4"}
    assert "figura como open" in response.answer


def test_langgraph_chat_routes_to_no_evidence(tmp_path: Path):
    index = tmp_path / "knowledge_index.json"
    index.write_text("[]", encoding="utf-8")
    service = ChatService(KnowledgeBase(index), MuseumTools(FakeRepository()))

    response = service.answer(ChatRequest(message="Contame sobre un objeto desconocido"))

    assert response.needs_clarification is True
    assert response.sources == []
    assert response.tool_calls == []
    assert "no se inventaron datos" in response.safety_notes[0]
    assert response.runtime.retrieval.active is False