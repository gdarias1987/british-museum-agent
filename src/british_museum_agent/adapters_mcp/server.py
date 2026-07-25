from __future__ import annotations

import hmac
import os
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from british_museum_agent.adapters_mcp.client import MCP_INTERNAL_TOKEN_HEADER
from british_museum_agent.config import get_settings
from british_museum_agent.infrastructure.sqlite_repository import SQLiteRepository

mcp = FastMCP(
    "british-museum-agent-tools",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8001")),
    streamable_http_path=os.getenv("MCP_PATH", "/mcp"),
)


def _repository() -> SQLiteRepository:
    settings = get_settings()
    return SQLiteRepository(settings.sqlite_path)


def _valid_internal_token(candidate: str | None) -> bool:
    expected = get_settings().mcp_internal_token_value
    if expected is None or not candidate:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def _request_internal_token(context: Context) -> str | None:
    try:
        return context.request_context.request.headers.get(MCP_INTERNAL_TOKEN_HEADER)
    except (AttributeError, ValueError):
        return None


@mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
async def mcp_health(request: Request) -> JSONResponse:
    settings = get_settings()
    sqlite_ready = _repository().is_ready()
    token_ready = bool(settings.mcp_internal_token_value)
    ready = sqlite_ready and token_ready
    return JSONResponse(
        {
            "status": "ok" if ready else "degraded",
            "components": {
                "sqlite": {"ready": sqlite_ready},
                "internal_auth": {"configured": token_ready},
            },
        },
        status_code=200 if ready else 503,
    )


@mcp.tool()
def get_gallery_status(gallery_id: str) -> dict[str, Any]:
    """Devuelve el estado operativo de una sala del British Museum."""
    gallery = _repository().get_gallery_status(gallery_id)
    if gallery is None:
        return {"error": "gallery_not_found", "gallery_id": gallery_id}
    return gallery


@mcp.tool()
def create_incident(
    gallery_id: str,
    category: str,
    description: str,
    priority: str,
    reported_by: str,
    context: Context,
) -> dict[str, Any]:
    """Crea un incidente operativo con autenticación interna y datos validados."""
    if not _valid_internal_token(_request_internal_token(context)):
        return {"error": "unauthorized_internal_call"}
    repo = _repository()
    if priority not in {"low", "medium", "high"}:
        return {"error": "invalid_priority", "allowed": ["low", "medium", "high"]}
    if repo.get_gallery_status(gallery_id) is None:
        return {"error": "gallery_not_found", "gallery_id": gallery_id}
    return repo.create_incident(
        gallery_id=gallery_id,
        category=category,
        description=description,
        priority=priority,
        reported_by=reported_by,
    )


@mcp.tool()
def get_incident(incident_id: int, context: Context) -> dict[str, Any]:
    """Devuelve un incidente operativo por ID tras autenticación interna."""
    if not _valid_internal_token(_request_internal_token(context)):
        return {"error": "unauthorized_internal_call"}
    incident = _repository().get_incident(incident_id)
    if incident is None:
        return {"error": "incident_not_found", "incident_id": incident_id}
    return incident


@mcp.tool()
def get_accessibility_info(gallery_id: str) -> dict[str, Any]:
    """Devuelve las notas de accesibilidad de una sala."""
    gallery = _repository().get_gallery_status(gallery_id)
    if gallery is None:
        return {"error": "gallery_not_found", "gallery_id": gallery_id}
    return {
        "gallery_id": gallery["id"],
        "name": gallery["name"],
        "accessibility_notes": gallery["accessibility_notes"],
    }


if __name__ == "__main__":
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))
