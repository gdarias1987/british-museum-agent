import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

_MAX_METADATA_SERIALIZED_BYTES = 8_192


class UserRole(str, Enum):
    visitor = "visitor"
    staff = "staff"


class RetrievedSource(BaseModel):
    title: str
    source: str
    chunk_id: str
    score: float = Field(ge=0.0, le=1.0)
    excerpt: str
    url: str | None = None


class ToolCall(BaseModel):
    name: str
    input: dict
    output_summary: dict
    status: str
    latency_ms: int


class ComponentRuntimeStatus(BaseModel):
    active: bool
    mode: str
    detail: str


class RuntimeStatus(BaseModel):
    retrieval: ComponentRuntimeStatus
    reranking: ComponentRuntimeStatus
    generation: ComponentRuntimeStatus
    tracing: ComponentRuntimeStatus


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)
    user_role: UserRole = UserRole.visitor
    session_id: str = Field(default="demo-session", min_length=1, max_length=128)
    language: str = Field(default="es", min_length=2, max_length=16)
    location_hint: str | None = Field(default=None, max_length=256)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Set exclusively by the API after validating the Bearer token. It is not
    # trusted when supplied by a chat client.
    staff_username: str | None = Field(default=None, exclude=True)

    @field_validator("metadata")
    @classmethod
    def validate_metadata_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            serialized = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata debe poder serializarse como JSON") from exc
        if len(serialized) > _MAX_METADATA_SERIALIZED_BYTES:
            raise ValueError(
                f"metadata no puede superar {_MAX_METADATA_SERIALIZED_BYTES} bytes serializados"
            )
        return value


class ChatResponse(BaseModel):
    answer: str
    trace_id: str
    sources: list[RetrievedSource]
    tool_calls: list[ToolCall] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    needs_clarification: bool = False
    safety_notes: list[str] = Field(default_factory=list)
    runtime: RuntimeStatus


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: UserRole = UserRole.staff