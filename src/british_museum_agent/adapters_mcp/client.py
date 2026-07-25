from __future__ import annotations

import json
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import TextContent

from british_museum_agent.domain.models import ToolCall, UserRole

MCP_INTERNAL_TOKEN_HEADER = "X-MCP-Internal-Token"
_MCP_HEALTH_TIMEOUT_SECONDS = 1.0


class MCPConfigurationError(RuntimeError):
    pass


class MCPMuseumTools:
    """Cliente MCP del backend para las herramientas operativas del museo."""

    def __init__(self, server_url: str, internal_token: str | None = None):
        self.server_url = server_url
        self._internal_token = internal_token

    def is_ready(self) -> bool:
        try:
            response = httpx.get(
                _health_url(self.server_url),
                headers=self._transport_headers(),
                timeout=_MCP_HEALTH_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
            if response.status_code != 200:
                return False
            payload = response.json()
            return isinstance(payload, dict) and payload.get("status") == "ok"
        except (httpx.HTTPError, ValueError):
            return False

    def get_gallery_status(
        self,
        payload: dict[str, Any],
        role: UserRole,
    ) -> tuple[dict[str, Any], ToolCall]:
        return anyio.run(self._call_tool, "get_gallery_status", payload, payload)

    def create_incident(
        self,
        payload: dict[str, Any],
        *,
        reported_by: str,
    ) -> tuple[dict[str, Any], ToolCall]:
        if not self._internal_token:
            raise MCPConfigurationError("La autenticación interna de MCP no está configurada")
        public_payload = {**payload, "reported_by": reported_by}
        return anyio.run(self._call_tool, "create_incident", public_payload, public_payload)

    def get_incident(self, incident_id: int) -> tuple[dict[str, Any], ToolCall]:
        if not self._internal_token:
            raise MCPConfigurationError("La autenticación interna de MCP no está configurada")
        payload = {"incident_id": incident_id}
        return anyio.run(self._call_tool, "get_incident", payload, payload)

    async def _call_tool(
        self,
        name: str,
        payload: dict[str, Any],
        public_payload: dict[str, Any],
    ) -> tuple[dict[str, Any], ToolCall]:
        started = perf_counter()
        async with streamablehttp_client(
            self.server_url,
            headers=self._transport_headers(),
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(name, payload)
        output = _decode_tool_result(result.content)
        status = "error" if isinstance(output, dict) and "error" in output else "success"
        return output, ToolCall(
            name=name,
            input=public_payload,
            output_summary=output,
            status=status,
            latency_ms=int((perf_counter() - started) * 1000),
        )

    def _transport_headers(self) -> dict[str, str]:
        if not self._internal_token:
            return {}
        return {MCP_INTERNAL_TOKEN_HEADER: self._internal_token}


def _health_url(server_url: str) -> str:
    parsed = urlsplit(server_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("La URL del servidor MCP no es válida")
    return urlunsplit((parsed.scheme, parsed.netloc, "/health", "", ""))


def _decode_tool_result(content: list[Any]) -> dict[str, Any]:
    if not content:
        return {"error": "empty_mcp_tool_result"}
    first = content[0]
    if isinstance(first, TextContent):
        try:
            parsed = json.loads(first.text)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            return {"text": first.text}
    text = getattr(first, "text", None)
    if isinstance(text, str):
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            return {"text": text}
    return {"value": str(first)}
