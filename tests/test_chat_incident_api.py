from fastapi.testclient import TestClient

from british_museum_agent.api.dependencies import get_chat_service
from british_museum_agent.api.main import app
from british_museum_agent.application.chat_service import ChatService
from tests.test_incident_chat import NoRagRetriever, RecordingIncidentTools, _token


def test_chat_does_not_trust_client_staff_role_without_jwt(api_client: TestClient):
    tools = RecordingIncidentTools()
    service = ChatService(NoRagRetriever(), tools)
    app.dependency_overrides[get_chat_service] = lambda: service
    try:
        response = api_client.post("/api/v1/chat", json={
            "message": "En la sala 4 hay un cartel ilegible sobre accesibilidad. RegistrÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ un incidente de prioridad media.",
            "user_role": "staff", "session_id": "forged-staff-session",
        })
    finally:
        app.dependency_overrides.pop(get_chat_service, None)

    assert response.status_code == 200
    assert "JWT" in response.json()["answer"]
    assert tools.calls == []


def test_authenticated_staff_chat_confirms_then_uses_mcp(api_client: TestClient, staff_token: str):
    tools = RecordingIncidentTools()
    service = ChatService(NoRagRetriever(), tools)
    app.dependency_overrides[get_chat_service] = lambda: service
    headers = {"Authorization": f"Bearer {staff_token}"}
    command = "En la sala 4 hay un cartel ilegible sobre accesibilidad. RegistrÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡ un incidente de prioridad media."
    try:
        prepared = api_client.post("/api/v1/chat", headers=headers, json={"message": command, "session_id": "staff-chat"})
        confirmation = "Confirmo " + _token(prepared.json()["answer"])
        confirmed = api_client.post("/api/v1/chat", headers=headers, json={"message": confirmation, "session_id": "staff-chat"})
    finally:
        app.dependency_overrides.pop(get_chat_service, None)

    assert prepared.status_code == 200
    assert "incidente, pero" in prepared.json()["answer"]
    assert confirmed.status_code == 200
    assert confirmed.json()["tool_calls"][0]["name"] == "create_incident"
    assert tools.calls[0]["reported_by"] == "staff@example.com"


def test_chat_incident_read_requires_jwt_and_authenticated_staff_uses_mcp(api_client: TestClient, staff_token: str):
    tools = RecordingIncidentTools()
    service = ChatService(NoRagRetriever(), tools)
    app.dependency_overrides[get_chat_service] = lambda: service
    try:
        visitor = api_client.post("/api/v1/chat", json={"message": "¿Podés contarme del incidente #25?", "session_id": "visitor-read"})
        staff = api_client.post("/api/v1/chat", headers={"Authorization": f"Bearer {staff_token}"}, json={"message": "¿Podés contarme del incidente #25?", "session_id": "staff-read"})
    finally:
        app.dependency_overrides.pop(get_chat_service, None)

    assert visitor.status_code == 200
    assert "JWT válido" in visitor.json()["answer"]
    assert staff.status_code == 200
    assert staff.json()["tool_calls"][0]["name"] == "get_incident"
