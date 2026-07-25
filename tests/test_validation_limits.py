import pytest
from pydantic import ValidationError

from british_museum_agent.domain.models import ChatRequest, LoginRequest


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("message", "x" * 4_001),
        ("session_id", "x" * 129),
        ("location_hint", "x" * 257),
    ],
)
def test_chat_request_rejects_oversized_text_fields(field: str, value: str):
    payload = {"message": "consulta válida", field: value}
    with pytest.raises(ValidationError):
        ChatRequest(**payload)


def test_chat_request_rejects_oversized_serialized_metadata():
    with pytest.raises(ValidationError, match="8192 bytes"):
        ChatRequest(message="consulta válida", metadata={"payload": "x" * 8_192})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("username", "x" * 255),
        ("password", "x" * 257),
    ],
)
def test_login_request_rejects_oversized_credentials(field: str, value: str):
    payload = {"username": "staff@example.com", "password": "válida", field: value}
    with pytest.raises(ValidationError):
        LoginRequest(**payload)
