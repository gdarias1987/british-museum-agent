from fastapi.testclient import TestClient


def _failed_login(
    client: TestClient,
    username: str = "staff@example.com",
    forwarded_for: str | None = None,
):
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else None
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "incorrect-password"},
        headers=headers,
    )


def test_login_is_limited_per_normalized_account(api_client: TestClient):
    for attempt in range(10):
        username = " STAFF@example.com " if attempt % 2 else "staff@example.com"
        assert _failed_login(api_client, username).status_code == 401

    response = _failed_login(api_client, "Staff@Example.Com")

    assert response.status_code == 429
    assert "Rate limit exceeded" in response.json()["error"]


def test_forwarded_for_does_not_bypass_account_limit(api_client: TestClient):
    for attempt in range(10):
        response = _failed_login(
            api_client,
            forwarded_for=f"203.0.113.{attempt + 1}",
        )
        assert response.status_code == 401

    response = _failed_login(api_client, forwarded_for="198.51.100.20")

    assert response.status_code == 429


def test_different_account_has_an_independent_limit(api_client: TestClient):
    for _ in range(10):
        assert _failed_login(api_client).status_code == 401

    assert _failed_login(api_client, "other@example.com").status_code == 401


def test_login_is_limited_per_direct_source(api_client: TestClient):
    for attempt in range(120):
        response = _failed_login(api_client, f"user-{attempt}@example.com")
        assert response.status_code == 401

    response = _failed_login(api_client, "last-user@example.com")

    assert response.status_code == 429
