from pathlib import Path

from british_museum_agent.adapters_mcp.museum_tools import MuseumTools
from british_museum_agent.application.chat_service import ChatService
from british_museum_agent.domain.models import ChatRequest
from british_museum_agent.retrieval.knowledge_base import KnowledgeBase


class EmptyRepository:
    def get_gallery_status(self, gallery_id: str):
        return None


def test_chat_returns_no_evidence_when_index_missing(tmp_path: Path):
    service = ChatService(KnowledgeBase(tmp_path / "missing.json"), MuseumTools(EmptyRepository()))
    response = service.answer(ChatRequest(message="Tell me about something unknown"))
    assert response.needs_clarification is True
    assert response.sources == []
    assert response.confidence < 0.5
