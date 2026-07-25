from __future__ import annotations

from time import perf_counter
from typing import Any

from pydantic import BaseModel, Field

from british_museum_agent.domain.models import ToolCall, UserRole
from british_museum_agent.infrastructure.sqlite_repository import SQLiteRepository


class GalleryStatusInput(BaseModel):
    gallery_id: str = Field(min_length=2)


class MuseumTools:
    """Controlled tool layer that will later be exposed through a real MCP server."""

    def __init__(self, repository: SQLiteRepository):
        self.repository = repository

    def get_gallery_status(self, payload: dict[str, Any], role: UserRole) -> tuple[dict[str, Any], ToolCall]:
        started = perf_counter()
        tool_input = GalleryStatusInput(**payload)
        gallery = self.repository.get_gallery_status(tool_input.gallery_id)
        status = "success" if gallery else "error"
        output = gallery or {"error": "gallery_not_found", "gallery_id": tool_input.gallery_id}
        return output, ToolCall(
            name="get_gallery_status",
            input=tool_input.model_dump(),
            output_summary=output,
            status=status,
            latency_ms=int((perf_counter() - started) * 1000),
        )
