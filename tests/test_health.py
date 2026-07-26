import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from british_museum_agent.api.dependencies import (
    get_knowledge_retriever,
    get_sqlite_repository,
)
from british_museum_agent.api.main import app
from british_museum_agent.config import Settings, get_settings
from british_museum_agent.infrastructure.sqlite_repository import SQLiteRepository
from british_museum_agent.retrieval.knowledge_base import RetrievalStatus


class RetrievalStub:
    def __init__(
        self,
        *,
        backend: str,
        retrieval_active: bool,
        reranker_active: bool,
    ):
        self._status = RetrievalStatus(
            backend=backend,
            retrieval_active=retrieval_active,
            retrieval_detail="Test retrieval status.",
            reranker="test-reranker" if reranker_active else "disabled",
            reranker_active=reranker_active,
            reranker_detail="Test reranker status.",
        )

    @property
    def status(self) -> RetrievalStatus:
        return self._status


def test_health_returns_ok_when_required_components_are_ready(api_client: TestClient):
    response = api_client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["components"]["retrieval"]["ready"] is True
    assert payload["components"]["sqlite"]["ready"] is True
    assert payload["components"]["mcp"]["ready"] is True


def test_health_returns_503_when_mcp_is_not_ready(
    api_client: TestClient,
    fake_mcp_tools,
):
    fake_mcp_tools.ready = False

    response = api_client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["components"]["mcp"]["ready"] is False


def test_health_returns_503_when_required_chroma_is_unavailable(
    api_client: TestClient,
    test_settings: Settings,
):
    chroma_settings = test_settings.model_copy(update={"retrieval_backend": "chroma"})
    app.dependency_overrides[get_settings] = lambda: chroma_settings
    app.dependency_overrides[get_knowledge_retriever] = lambda: RetrievalStub(
        backend="lexical_fallback",
        retrieval_active=True,
        reranker_active=False,
    )

    response = api_client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["components"]["retrieval"]["ready"] is False


def test_health_returns_503_when_sqlite_schema_or_seed_is_missing(
    api_client: TestClient,
    tmp_path: Path,
):
    incomplete_db = tmp_path / "incomplete.db"
    with sqlite3.connect(incomplete_db) as conn:
        conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
    repository = SQLiteRepository(incomplete_db)
    app.dependency_overrides[get_sqlite_repository] = lambda: repository

    response = api_client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json()["components"]["sqlite"]["ready"] is False


def test_repository_readiness_requires_tables_and_minimum_seed(
    seeded_repository: SQLiteRepository,
    tmp_path: Path,
):
    assert seeded_repository.is_ready() is True
    assert SQLiteRepository(tmp_path / "missing.db").is_ready() is False


def test_health_keeps_chroma_ready_without_reranker(
    api_client: TestClient,
    test_settings: Settings,
):
    chroma_settings = test_settings.model_copy(update={"retrieval_backend": "chroma"})
    app.dependency_overrides[get_settings] = lambda: chroma_settings
    app.dependency_overrides[get_knowledge_retriever] = lambda: RetrievalStub(
        backend="chroma",
        retrieval_active=True,
        reranker_active=False,
    )

    response = api_client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["components"]["retrieval"]["ready"] is True
    assert payload["components"]["reranker"]["active"] is False
