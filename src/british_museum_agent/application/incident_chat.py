from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from secrets import token_urlsafe
from time import monotonic, perf_counter
from typing import Any, Callable

from british_museum_agent.domain.models import ChatRequest, ToolCall, UserRole
from british_museum_agent.domain.operations import IncidentCreateRequest

_PENDING_TTL_SECONDS = 300


@dataclass(frozen=True)
class IncidentChatResult:
    answer: str
    tool_calls: list[ToolCall]
    confidence: float
    needs_clarification: bool
    safety_notes: list[str]


@dataclass(frozen=True)
class PendingIncident:
    payload: dict[str, str]
    username: str
    confirmation_token: str
    expires_at: float


class IncidentChatWorkflow:
    """Deterministic, confirmation-gated incident flow that runs before RAG."""

    def __init__(
        self,
        tools: Any,
        *,
        clock: Callable[[], float] = monotonic,
        pending_ttl_seconds: int = _PENDING_TTL_SECONDS,
    ):
        self._tools = tools
        self._clock = clock
        self._pending_ttl_seconds = pending_ttl_seconds
        self._pending: dict[tuple[str, str], PendingIncident] = {}

    def handle(self, request: ChatRequest) -> IncidentChatResult | None:
        username = request.staff_username
        key = (request.session_id, username or "")
        confirmation_token = _confirmation_token(request.message)
        pending = self._pending.get(key)
        if username and pending and confirmation_token is not None:
            if pending.expires_at <= self._clock():
                self._pending.pop(key, None)
                return _confirmation_failure("La confirmaci\u00f3n expir\u00f3. Prepar\u00e1 el incidente nuevamente.")
            if confirmation_token != pending.confirmation_token:
                return _confirmation_failure("La confirmaci\u00f3n no coincide con el incidente pendiente. Revis\u00e1 el c\u00f3digo mostrado.")
            return self._create_pending_incident(key)

        incident_id = _incident_id_query(request.message)
        if incident_id is not None:
            return self._read_incident(incident_id, request)

        if not _looks_like_incident_command(request.message):
            return None
        if request.user_role is not UserRole.staff or not username:
            return IncidentChatResult(
                answer="Registrar incidentes desde el chat requiere una sesi\u00f3n de Personal con JWT v\u00e1lido. Pod\u00e9s usar el formulario luego de iniciar sesi\u00f3n.",
                tool_calls=[], confidence=1.0, needs_clarification=False,
                safety_notes=["La mutaci\u00f3n fue bloqueada: no hay identidad de personal verificada."],
            )

        payload, missing = _extract_incident(request.message, request.location_hint)
        if missing:
            return IncidentChatResult(
                answer="Para preparar el incidente necesito: " + ", ".join(missing) + ". No se registr\u00f3 nada.",
                tool_calls=[], confidence=1.0, needs_clarification=True,
                safety_notes=["La mutaci\u00f3n requiere datos completos y confirmaci\u00f3n expl\u00edcita."],
            )
        token = token_urlsafe(16)
        self._pending[key] = PendingIncident(
            payload=payload,
            username=username,
            confirmation_token=token,
            expires_at=self._clock() + self._pending_ttl_seconds,
        )
        return IncidentChatResult(
            answer=(
                "Prepar\u00e9 el incidente, pero todav\u00eda no fue registrado. "
                f"Sala: {payload['gallery_id']}; categor\u00eda: {payload['category']}; "
                f"prioridad: {payload['priority']}; descripci\u00f3n: {payload['description']}. "
                f"Respond\u00e9 exactamente `Confirmo {token}` dentro de 5 minutos para registrarlo."
            ),
            tool_calls=[], confidence=1.0, needs_clarification=False,
            safety_notes=["Incidente pendiente, con vencimiento y confirmaci\u00f3n vinculada al payload; MCP no fue invocado."],
        )

    def _create_pending_incident(self, key: tuple[str, str]) -> IncidentChatResult:
        pending = self._pending.pop(key)
        started = perf_counter()
        try:
            output, call = self._tools.create_incident(pending.payload, reported_by=pending.username)
        except Exception:
            output = {"error": "mcp_service_unavailable"}
            call = ToolCall(
                name="create_incident", input={**pending.payload, "reported_by": pending.username},
                output_summary=output, status="error", latency_ms=int((perf_counter() - started) * 1000),
            )
        if call.status == "success" and "error" not in output:
            return IncidentChatResult(
                answer=f"Incidente #{output.get('id')} registrado correctamente.",
                tool_calls=[call], confidence=1.0, needs_clarification=False,
                safety_notes=["Incidente creado mediante MCP tras confirmaci\u00f3n expl\u00edcita vinculada."],
            )
        return IncidentChatResult(
            answer="No se pudo registrar el incidente. No se reintenta autom\u00e1ticamente; revis\u00e1 los datos o us\u00e1 el formulario.",
            tool_calls=[call], confidence=0.2, needs_clarification=True,
            safety_notes=["MCP rechaz\u00f3 o no pudo completar la creaci\u00f3n del incidente."],
        )


    def _read_incident(self, incident_id: int, request: ChatRequest) -> IncidentChatResult:
        if request.user_role is not UserRole.staff or not request.staff_username:
            return IncidentChatResult(
                answer="Consultar incidentes operativos requiere una sesión de Personal con JWT válido.",
                tool_calls=[], confidence=1.0, needs_clarification=False,
                safety_notes=["La lectura operativa fue bloqueada: no hay identidad de personal verificada."],
            )
        started = perf_counter()
        try:
            output, call = self._tools.get_incident(incident_id)
        except Exception:
            output = {"error": "mcp_service_unavailable"}
            call = ToolCall(
                name="get_incident", input={"incident_id": incident_id}, output_summary=output,
                status="error", latency_ms=int((perf_counter() - started) * 1000),
            )
        if call.status == "success" and "error" not in output:
            return IncidentChatResult(
                answer=_format_incident(output), tool_calls=[call], confidence=1.0,
                needs_clarification=False,
                safety_notes=["Incidente obtenido mediante MCP para personal autenticado; no se consulta RAG."],
            )
        answer = (f"No encontré el incidente #{incident_id}." if output.get("error") == "incident_not_found"
                  else "No pude consultar el incidente en este momento. No se inventaron datos.")
        return IncidentChatResult(
            answer=answer, tool_calls=[call], confidence=0.2, needs_clarification=False,
            safety_notes=["La lectura operativa no devolvió datos verificables."],
        )


def _confirmation_failure(answer: str) -> IncidentChatResult:
    return IncidentChatResult(answer=answer, tool_calls=[], confidence=1.0, needs_clarification=True, safety_notes=["No se invoc\u00f3 MCP: confirmaci\u00f3n inv\u00e1lida o vencida."])


def _normalize(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFD", value.casefold()) if unicodedata.category(char) != "Mn")


def _looks_like_incident_command(message: str) -> bool:
    text = _normalize(message)
    return "incidente" in text and bool(re.search(r"\b(registr\w*|carga|crear|crea)\w*\b", text))


def _incident_id_query(message: str) -> int | None:
    """Recognize Spanish questions about an explicitly numbered incident."""
    text = _normalize(message)
    match = re.search(r"\bincidente\s*(?:n(?:umero)?\.?\s*)?#\s*(\d+)\b", text)
    return int(match.group(1)) if match else None


def _format_incident(incident: dict[str, Any]) -> str:
    labels = (("Sala", "gallery_id"), ("Categoría", "category"), ("Descripción", "description"), ("Prioridad", "priority"), ("Estado", "status"), ("Autor", "reported_by"), ("Fecha", "created_at"))
    details = [f"{label}: {incident[key]}" for label, key in labels if incident.get(key) is not None]
    return f"Incidente #{incident.get('id', 'solicitado')}: " + "; ".join(details) + "."


def _confirmation_token(message: str) -> str | None:
    match = re.fullmatch(r"\s*confirmo\s+([A-Za-z0-9_-]{16,128})\s*[.!]?\s*", message, flags=re.IGNORECASE)
    return match.group(1) if match else None


_GALLERY_ALIASES: dict[str, str] = {
    # Sala 4 — Egipto Antiguo
    r"\b(sala|room)\s*4\b": "room-4",
    # Rooms 6-10 — Grecia (Parthenon)
    r"\b(sala|room|salas|rooms)\s*(?:del\s+)?(?:6|7|8|9|10)\b": "rooms-6-10",
    # Salas 42-43 — Antiguo Oriente Medio
    r"\b(sala|room|salas|rooms)\s*(?:42|43)\b": "rooms-42-43-52-59",
    # Salas 52-59 — Antiguo Oriente Medio y Mesopotamia
    r"\b(sala|room|salas|rooms)\s*5[2-9]\b": "rooms-42-43-52-59",
    # Salas 61-66 — Egipto, Sudán y momias
    r"\b(sala|room|salas|rooms)\s*6[1-6]\b": "rooms-61-66",
    # Medio Oriente textual
    r"\b(?:medio\s+oriente|middle\s+east)\b": "rooms-42-43-52-59",
}

_CATEGORY_MAP: dict[str, str] = {
    "accesibilidad": "accessibility",
    "senalizacion": "signage",
    "cartel": "signage",
    "seguridad": "safety",
    "limpieza": "cleanliness",
    "mantenimiento": "maintenance",
}

_PRIORITY_MAP: dict[str, str] = {
    "prioridad baja": "low",
    "prioridad media": "medium",
    "prioridad alta": "high",
}


def _extract_incident(message: str, location_hint: str | None) -> tuple[dict[str, str], list[str]]:
    text = _normalize(f"{message} {location_hint or ''}")

    # Gallery ID
    gallery_id: str | None = None
    for pattern, gid in _GALLERY_ALIASES.items():
        if re.search(pattern, text):
            gallery_id = gid
            break

    # Category
    category = next(
        (name for token, name in _CATEGORY_MAP.items() if token in text),
        None,
    )

    # Priority
    priority = next(
        (value for token, value in _PRIORITY_MAP.items() if token in text),
        None,
    )

    # Description: take everything before the command verb, clean it up
    description = re.split(
        r"\b(registr\w*|carga|crear|crea|reportar|abrir|nuev[oa])\w*\b",
        message,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    # Remove leading noise like "Quiero", "Necesito", "Hay que", etc.
    description = re.sub(
        r"^\s*(?:quiero|necesito|hay\s+que|deb[eo]|pod[eí]as|me\s+gustar[íi]a|por\s+favor)\s+",
        "",
        description,
        flags=re.IGNORECASE,
    )
    description = re.sub(r"\s+", " ", description).strip(" .,;:")

    payload: dict[str, str] = {
        "gallery_id": gallery_id or "",
        "category": category or "",
        "description": description,
        "priority": priority or "",
    }

    missing = [
        label
        for label, value in (
            ("sala reconocida", gallery_id),
            ("categoría reconocida", category),
            ("prioridad baja/media/alta", priority),
            ("descripción", description if len(description) >= 5 else None),
        )
        if not value
    ]
    if missing:
        return payload, missing

    IncidentCreateRequest(**payload)
    return payload, []
