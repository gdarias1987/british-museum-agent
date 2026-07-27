import os
import sqlite3
import tempfile
from pathlib import Path
from time import perf_counter

import pytest
from fastapi.testclient import TestClient

_TEST_DATA = Path(tempfile.mkdtemp(prefix="british-museum-agent-tests-"))
_TEST_STAFF_PASSWORD = "test-only-staff-password-2026!"
os.environ.update(
    {
        "APP_ENV": "test",
        "LANGSMITH_TRACING": "false",
        "LLM_PROVIDER": "local",
        "RETRIEVAL_BACKEND": "lexical",
        "INDEX_PATH": str(_TEST_DATA / "missing-index.json"),
        "CHROMA_PATH": str(_TEST_DATA / "chroma"),
        "SQLITE_PATH": str(_TEST_DATA / "never-use-real.db"),
        "STAFF_DEMO_PASSWORD": _TEST_STAFF_PASSWORD,
        "JWT_SECRET": "test-jwt-secret-that-is-long-and-not-production",
        "JWT_EXPIRATION_MINUTES": "15",
        "MCP_INTERNAL_TOKEN": "test-mcp-token-that-is-long-and-not-production",
    }
)

from scripts.seed_db import create_schema, seed_data  # noqa: E402
from british_museum_agent.api.dependencies import (  # noqa: E402
    get_knowledge_retriever,
    get_mcp_museum_tools,
    get_sqlite_repository,
)
from british_museum_agent.api.main import app, limiter  # noqa: E402
from british_museum_agent.config import Settings, get_settings  # noqa: E402
from british_museum_agent.domain.models import ToolCall  # noqa: E402
from british_museum_agent.infrastructure.sqlite_repository import (  # noqa: E402
    SQLiteRepository,
)
from british_museum_agent.retrieval.knowledge_base import RetrievalStatus  # noqa: E402


class ReadyLexicalRetriever:
    @property
    def status(self) -> RetrievalStatus:
        return RetrievalStatus(
            backend="lexical_fallback",
            retrieval_active=True,
            retrieval_detail="Test lexical index.",
            reranker="disabled",
            reranker_active=False,
            reranker_detail="Not required by the test configuration.",
        )

    def warmup(self) -> None:
        return None

    def search(self, query: str, top_k: int = 4):
        return []


class FakeMCPMuseumTools:
    def __init__(self, repository: SQLiteRepository, *, ready: bool = True):
        self.repository = repository
        self.ready = ready
        self.incident_calls: list[dict] = []

    def is_ready(self) -> bool:
        return self.ready

    def create_incident(self, payload: dict, *, reported_by: str):
        started = perf_counter()
        call_payload = {**payload, "reported_by": reported_by}
        self.incident_calls.append(call_payload)
        if self.repository.get_gallery_status(payload["gallery_id"]) is None:
            output = {"error": "gallery_not_found", "gallery_id": payload["gallery_id"]}
        else:
            output = self.repository.create_incident(**call_payload)
        return output, ToolCall(
            name="create_incident",
            input=call_payload,
            output_summary=output,
            status="error" if "error" in output else "success",
            latency_ms=int((perf_counter() - started) * 1000),
        )

    def get_incident(self, incident_id: int):
        started = perf_counter()
        output = self.repository.get_incident(incident_id)
        if output is None:
            output = {"error": "incident_not_found", "incident_id": incident_id}
        return output, ToolCall(
            name="get_incident",
            input={"incident_id": incident_id},
            output_summary=output,
            status="error" if "error" in output else "success",
            latency_ms=int((perf_counter() - started) * 1000),
        )


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        retrieval_backend="lexical",
        index_path=tmp_path / "index.json",
        chroma_path=tmp_path / "chroma",
        sqlite_path=tmp_path / "test.db",
        llm_provider="local",
        langsmith_tracing=False,
        staff_demo_password=_TEST_STAFF_PASSWORD,
        jwt_secret="test-jwt-secret-that-is-long-and-not-production",
        jwt_expiration_minutes=15,
        mcp_internal_token="test-mcp-token-that-is-long-and-not-production",
    )


@pytest.fixture
def seeded_repository(tmp_path: Path, test_settings: Settings) -> SQLiteRepository:
    db_path = tmp_path / "database" / "test.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        create_schema(conn)
        seed_data(conn, test_settings)
    return SQLiteRepository(db_path)


@pytest.fixture
def fake_mcp_tools(seeded_repository: SQLiteRepository) -> FakeMCPMuseumTools:
    return FakeMCPMuseumTools(seeded_repository)


@pytest.fixture
def api_client(
    seeded_repository: SQLiteRepository,
    fake_mcp_tools: FakeMCPMuseumTools,
    test_settings: Settings,
):
    app.dependency_overrides[get_sqlite_repository] = lambda: seeded_repository
    app.dependency_overrides[get_mcp_museum_tools] = lambda: fake_mcp_tools
    app.dependency_overrides[get_knowledge_retriever] = ReadyLexicalRetriever
    app.dependency_overrides[get_settings] = lambda: test_settings
    limiter.reset()
    with TestClient(app) as client:
        yield client
    limiter.reset()
    app.dependency_overrides.clear()


@pytest.fixture
def staff_password() -> str:
    return _TEST_STAFF_PASSWORD


@pytest.fixture
def staff_token(api_client: TestClient, staff_password: str) -> str:
    response = api_client.post(
        "/api/v1/auth/login",
        json={"username": "staff@example.com", "password": staff_password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]
