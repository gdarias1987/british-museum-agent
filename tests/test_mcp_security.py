from types import SimpleNamespace

from mcp.server.fastmcp import Context
from starlette.datastructures import Headers

from british_museum_agent.adapters_mcp import server
from british_museum_agent.adapters_mcp.client import MCP_INTERNAL_TOKEN_HEADER
from british_museum_agent.config import Settings
from british_museum_agent.infrastructure.sqlite_repository import SQLiteRepository


def _context_with_token(token: str) -> Context:
    request = SimpleNamespace(headers=Headers({MCP_INTERNAL_TOKEN_HEADER: token}))
    return Context(request_context=SimpleNamespace(request=request))


def test_mutating_mcp_tool_rejects_invalid_internal_header(
    monkeypatch,
    seeded_repository: SQLiteRepository,
    test_settings: Settings,
):
    monkeypatch.setattr(server, "_repository", lambda: seeded_repository)
    monkeypatch.setattr(server, "get_settings", lambda: test_settings)

    result = server.create_incident(
        gallery_id="room-4",
        category="label",
        description="Falta la etiqueta descriptiva junto a la vitrina.",
        priority="medium",
        reported_by="staff@example.com",
        context=_context_with_token("wrong-token"),
    )

    assert result == {"error": "unauthorized_internal_call"}


def test_mutating_mcp_tool_accepts_configured_internal_header(
    monkeypatch,
    seeded_repository: SQLiteRepository,
    test_settings: Settings,
):
    monkeypatch.setattr(server, "_repository", lambda: seeded_repository)
    monkeypatch.setattr(server, "get_settings", lambda: test_settings)

    result = server.create_incident(
        gallery_id="room-4",
        category="label",
        description="Falta la etiqueta descriptiva junto a la vitrina.",
        priority="medium",
        reported_by="staff@example.com",
        context=_context_with_token(test_settings.mcp_internal_token_value),
    )

    assert result["status"] == "open"
    assert result["reported_by"] == "staff@example.com"


def test_mutating_mcp_tool_schema_excludes_internal_token():
    tool = server.mcp._tool_manager.get_tool("create_incident")

    assert tool is not None
    assert "internal_token" not in tool.parameters["properties"]
    assert tool.context_kwarg == "context"


def test_read_incident_mcp_tool_requires_internal_header(monkeypatch, seeded_repository: SQLiteRepository, test_settings: Settings):
    monkeypatch.setattr(server, "_repository", lambda: seeded_repository)
    monkeypatch.setattr(server, "get_settings", lambda: test_settings)
    created = seeded_repository.create_incident(gallery_id="room-4", category="label", description="Falta una etiqueta descriptiva junto a la vitrina.", priority="medium", reported_by="staff@example.com")

    denied = server.get_incident(created["id"], _context_with_token("wrong-token"))
    allowed = server.get_incident(created["id"], _context_with_token(test_settings.mcp_internal_token_value))

    assert denied == {"error": "unauthorized_internal_call"}
    assert allowed["id"] == created["id"]
    assert allowed["description"] == created["description"]
