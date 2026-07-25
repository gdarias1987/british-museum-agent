from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient

from british_museum_agent.api.security import JWT_ALGORITHM
from british_museum_agent.config import Settings


def test_staff_login_emits_signed_hs256_jwt(
    api_client: TestClient,
    test_settings: Settings,
    staff_password: str,
):
    response = api_client.post(
        "/api/v1/auth/login",
        json={"username": "staff@example.com", "password": staff_password},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "staff"
    assert body["token_type"] == "bearer"
    header = jwt.get_unverified_header(body["access_token"])
    assert header["alg"] == JWT_ALGORITHM
    payload = jwt.decode(
        body["access_token"],
        test_settings.jwt_secret_value,
        algorithms=[JWT_ALGORITHM],
    )
    assert payload["sub"] == "staff@example.com"
    assert payload["role"] == "staff"
    assert payload["exp"] - payload["iat"] == test_settings.jwt_expiration_minutes * 60


def test_staff_login_rejects_invalid_credentials(api_client: TestClient):
    response = api_client.post(
        "/api/v1/auth/login",
        json={"username": "staff@example.com", "password": "wrong"},
    )
    assert response.status_code == 401


def test_gallery_status_returns_seed_gallery(api_client: TestClient):
    response = api_client.get("/api/v1/galleries/room-4/status")
    assert response.status_code == 200
    assert response.json()["id"] == "room-4"
    assert response.json()["department"] == "Egipto y Sudán"


def test_create_incident_requires_bearer_token(api_client: TestClient):
    response = api_client.post(
        "/api/v1/incidents",
        json={
            "gallery_id": "room-4",
            "category": "label",
            "description": "Missing descriptive label near display case.",
            "priority": "medium",
        },
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_create_incident_rejects_expired_token(
    api_client: TestClient,
    test_settings: Settings,
):
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "staff@example.com",
            "role": "staff",
            "iat": now - timedelta(minutes=2),
            "exp": now - timedelta(minutes=1),
        },
        test_settings.jwt_secret_value,
        algorithm=JWT_ALGORITHM,
    )
    response = api_client.post(
        "/api/v1/incidents",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "gallery_id": "room-4",
            "category": "label",
            "description": "Missing descriptive label near display case.",
            "priority": "medium",
        },
    )
    assert response.status_code == 401


def test_create_incident_uses_mcp_and_token_identity(
    api_client: TestClient,
    staff_token: str,
    fake_mcp_tools,
):
    response = api_client.post(
        "/api/v1/incidents",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={
            "gallery_id": "room-4",
            "category": "label",
            "description": "Missing descriptive label near display case.",
            "priority": "medium",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "open"
    assert response.json()["reported_by"] == "staff@example.com"
    assert fake_mcp_tools.incident_calls == [
        {
            "gallery_id": "room-4",
            "category": "label",
            "description": "Missing descriptive label near display case.",
            "priority": "medium",
            "reported_by": "staff@example.com",
        }
    ]


def test_create_incident_rejects_client_controlled_reporter(
    api_client: TestClient,
    staff_token: str,
):
    response = api_client.post(
        "/api/v1/incidents",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={
            "gallery_id": "room-4",
            "category": "label",
            "description": "Missing descriptive label near display case.",
            "priority": "medium",
            "reported_by": "attacker@example.com",
        },
    )
    assert response.status_code == 422
