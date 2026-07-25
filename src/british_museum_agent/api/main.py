from __future__ import annotations

from contextlib import asynccontextmanager
from time import perf_counter
from typing import Any

import anyio
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status

from british_museum_agent.adapters_mcp.client import (
    MCPConfigurationError,
    MCPMuseumTools,
)
from british_museum_agent.api.dependencies import (
    get_chat_service,
    get_knowledge_retriever,
    get_mcp_museum_tools,
    get_sqlite_repository,
)
from british_museum_agent.api.security import (
    StaffIdentity,
    create_staff_access_token,
    optional_staff,
    require_staff,
)
from british_museum_agent.application.chat_service import ChatService
from british_museum_agent.config import Settings, get_settings
from british_museum_agent.domain.models import (
    ChatRequest,
    ChatResponse,
    LoginRequest,
    LoginResponse,
    UserRole,
)
from british_museum_agent.domain.operations import (
    GalleryStatus,
    IncidentCreateRequest,
    IncidentResponse,
)
from british_museum_agent.infrastructure.sqlite_repository import SQLiteRepository
from british_museum_agent.observability.metrics import get_service_metrics
from british_museum_agent.observability.tracing import (
    configure_phoenix,
    get_phoenix_status,
    shutdown_phoenix,
)
from british_museum_agent.retrieval.knowledge_base import KnowledgeRetriever


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_phoenix(get_settings())
    try:
        retriever = get_knowledge_retriever()
        warmup = getattr(retriever, "warmup", None)
        if callable(warmup):
            await anyio.to_thread.run_sync(warmup)
        app.state.retrieval_warmup_complete = True
        yield
    finally:
        shutdown_phoenix()


_boot_settings = get_settings()
app = FastAPI(title=_boot_settings.app_name, version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def observe_http_requests(request: Request, call_next):
    started = perf_counter()
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        route_path = getattr(route, "path", None)
        get_service_metrics().record_http_request(
            method=request.method,
            route=route_path,
            status_code=status_code,
            duration_seconds=perf_counter() - started,
        )


@app.get("/api/v1/health")
def health(
    response: Response,
    repo: SQLiteRepository = Depends(get_sqlite_repository),
    retriever: KnowledgeRetriever = Depends(get_knowledge_retriever),
    tools: MCPMuseumTools = Depends(get_mcp_museum_tools),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    phoenix_status = get_phoenix_status()
    retrieval_status = retriever.status
    sqlite_ready = repo.is_ready()
    chroma_required = settings.retrieval_backend == "chroma"
    retrieval_ready = retrieval_status.retrieval_active and (
        not chroma_required
        or (
            retrieval_status.backend == "chroma"
            and retrieval_status.reranker_active
        )
    )
    try:
        mcp_ready = tools.is_ready()
    except Exception:
        mcp_ready = False
    ready = sqlite_ready and retrieval_ready and mcp_ready
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if ready else "degraded",
        "app": settings.app_name,
        "environment": settings.app_env,
        "components": {
            "retrieval": {
                "ready": retrieval_ready,
                "required_backend": settings.retrieval_backend,
                "active_backend": retrieval_status.backend,
                "detail": retrieval_status.retrieval_detail,
            },
            "chroma": {
                "configured": chroma_required,
                "index_ready": retrieval_status.backend == "chroma",
                "path": str(settings.chroma_path),
                "detail": retrieval_status.retrieval_detail,
            },
            "reranker": {
                "active": retrieval_status.reranker_active,
                "mode": retrieval_status.reranker,
                "detail": retrieval_status.reranker_detail,
            },
            "lexical_fallback": {
                "index_ready": settings.index_path.is_file(),
            },
            "gemini": {
                "configured": bool(
                    settings.llm_provider.lower() == "gemini"
                    and settings.resolved_gemini_api_key
                ),
                "model": settings.gemini_model,
            },
            "langsmith": {
                "configured": settings.langsmith_enabled,
                "project": settings.langsmith_project,
            },
            "phoenix": {
                "configured": settings.phoenix_enabled,
                "active": phoenix_status.active,
                "project": phoenix_status.project,
                "detail": phoenix_status.detail,
            },
            "sqlite": {
                "ready": sqlite_ready,
            },
            "mcp": {
                "ready": mcp_ready,
                "detail": (
                    "El servicio MCP respondió correctamente."
                    if mcp_ready
                    else "El servicio MCP no está disponible o no superó su health check."
                ),
            },
        },
    }


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics() -> Response:
    return Response(
        content=get_service_metrics().render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/api/v1/metrics/summary")
def metrics_summary() -> dict[str, Any]:
    return get_service_metrics().summary()


@app.post("/api/v1/auth/login", response_model=LoginResponse)
def login(
    request: LoginRequest,
    repo: SQLiteRepository = Depends(get_sqlite_repository),
    settings: Settings = Depends(get_settings),
) -> LoginResponse:
    if not repo.validate_staff_credentials(request.username, request.password):
        raise HTTPException(status_code=401, detail="Credenciales de personal inválidas")
    return LoginResponse(
        access_token=create_staff_access_token(request.username, settings),
    )


@app.post("/api/v1/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    staff: StaffIdentity | None = Depends(optional_staff),
    service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    # user_role is client-controlled, so only a verified JWT enables staff actions.
    request = request.model_copy(update={
        "user_role": staff.role if staff else UserRole.visitor,
        "staff_username": staff.username if staff else None,
    })
    return service.answer(request)


@app.get("/api/v1/artworks/{inventory_id}")
def get_artwork(inventory_id: str) -> dict:
    return {
        "inventory_id": inventory_id,
        "status": "placeholder",
        "message": "El adaptador de catálogo está declarado, pero todavía no tiene una fuente operativa.",
    }


@app.get("/api/v1/galleries/{gallery_id}/status", response_model=GalleryStatus)
def get_gallery_status(
    gallery_id: str,
    repo: SQLiteRepository = Depends(get_sqlite_repository),
) -> GalleryStatus:
    gallery = repo.get_gallery_status(gallery_id)
    if gallery is None:
        raise HTTPException(status_code=404, detail="No se encontró la sala o SQLite no está listo")
    return GalleryStatus(**gallery)


@app.post("/api/v1/incidents", response_model=IncidentResponse)
def create_incident(
    request: IncidentCreateRequest,
    staff: StaffIdentity = Depends(require_staff),
    tools: MCPMuseumTools = Depends(get_mcp_museum_tools),
) -> IncidentResponse:
    try:
        incident, _ = tools.create_incident(
            request.model_dump(),
            reported_by=staff.username,
        )
    except MCPConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La autenticación interna de MCP no está configurada",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El servicio MCP no está disponible",
        ) from exc

    error = incident.get("error")
    if error == "gallery_not_found":
        raise HTTPException(status_code=404, detail="No se encontró la sala")
    if error == "invalid_priority":
        raise HTTPException(status_code=422, detail="La prioridad del incidente no es válida")
    if error:
        raise HTTPException(status_code=502, detail="MCP rechazó el incidente")
    return IncidentResponse(**incident)
