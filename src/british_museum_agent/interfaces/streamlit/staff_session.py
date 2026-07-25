"""Pure session-state helpers for the Streamlit staff workflow."""

from collections.abc import MutableMapping
from typing import Any

STAFF_SESSION_KEYS = ("access_token", "staff_username", "staff_role")
MANUAL_INCIDENT_FORM_KEY = "show_manual_incident_form"


def is_staff_authenticated(state: MutableMapping[str, Any]) -> bool:
    """Return true only for a session that has a staff JWT and staff role."""
    return bool(state.get("access_token")) and state.get("staff_role") == "staff"


def chat_role_for_session(state: MutableMapping[str, Any], profile: str = "Personal") -> str:
    """Prevent unauthenticated UI state from claiming the staff role."""
    return "staff" if profile == "Personal" and is_staff_authenticated(state) else "visitor"


def store_staff_session(state: MutableMapping[str, Any], *, access_token: str, username: str, role: str) -> None:
    """Store only the authenticated identity fields needed by the UI."""
    if not access_token or role != "staff":
        raise ValueError("La respuesta de autenticación no habilita acceso de personal")
    state["access_token"] = access_token
    state["staff_username"] = username
    state["staff_role"] = role


def clear_staff_session(state: MutableMapping[str, Any]) -> None:
    """Remove staff authentication material and related operational UI state."""
    for key in (*STAFF_SESSION_KEYS, MANUAL_INCIDENT_FORM_KEY):
        state.pop(key, None)


def toggle_manual_incident_form(state: MutableMapping[str, Any]) -> bool:
    """Toggle the staff-only manual incident form and return its new visibility."""
    visible = not bool(state.get(MANUAL_INCIDENT_FORM_KEY, False))
    state[MANUAL_INCIDENT_FORM_KEY] = visible
    return visible


def is_manual_incident_form_visible(state: MutableMapping[str, Any]) -> bool:
    """Return whether the manual incident form was explicitly opened this session."""
    return bool(state.get(MANUAL_INCIDENT_FORM_KEY, False))
