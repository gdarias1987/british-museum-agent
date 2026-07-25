from contextlib import asynccontextmanager
from types import SimpleNamespace

from british_museum_agent.adapters_mcp import client as client_module
from british_museum_agent.adapters_mcp.client import (
    MCP_INTERNAL_TOKEN_HEADER,
    MCPMuseumTools,
)


def test_client_readiness_uses_corresponding_health_and_short_timeout(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured.update(url=url, **kwargs)
        return SimpleNamespace(status_code=200, json=lambda: {"status": "ok"})

    monkeypatch.setattr(client_module.httpx, "get", fake_get)
    tools = MCPMuseumTools("http://mcp.internal:8001/mcp", internal_token="test-token")

    assert tools.is_ready() is True
    assert captured["url"] == "http://mcp.internal:8001/health"
    assert captured["headers"] == {MCP_INTERNAL_TOKEN_HEADER: "test-token"}
    assert captured["timeout"] <= 1.0
    assert captured["follow_redirects"] is False


def test_client_sends_internal_token_only_in_transport_header(monkeypatch):
    captured = {}

    @asynccontextmanager
    async def fake_transport(url, *, headers):
        captured["url"] = url
        captured["headers"] = headers
        yield object(), object(), lambda: None

    class FakeClientSession:
        def __init__(self, read_stream, write_stream):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def initialize(self):
            return None

        async def call_tool(self, name, payload):
            captured["tool_name"] = name
            captured["payload"] = payload
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"id": 1, "status": "open"}')]
            )

    monkeypatch.setattr(client_module, "streamablehttp_client", fake_transport)
    monkeypatch.setattr(client_module, "ClientSession", FakeClientSession)
    tools = MCPMuseumTools("http://mcp.internal:8001/mcp", internal_token="test-token")

    output, call = tools.create_incident(
        {
            "gallery_id": "room-4",
            "category": "label",
            "description": "Falta una etiqueta descriptiva.",
            "priority": "medium",
        },
        reported_by="staff@example.com",
    )

    assert output["status"] == "open"
    assert captured["headers"] == {MCP_INTERNAL_TOKEN_HEADER: "test-token"}
    assert "internal_token" not in captured["payload"]
    assert "internal_token" not in call.input
