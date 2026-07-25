from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from pydantic import BaseModel

from british_museum_agent.config import Settings, get_settings
from british_museum_agent.domain.models import UserRole

JWT_ALGORITHM = "HS256"
bearer_scheme = HTTPBearer(auto_error=False)


class StaffIdentity(BaseModel):
    username: str
    role: UserRole = UserRole.staff


def create_staff_access_token(username: str, settings: Settings) -> str:
    secret = settings.jwt_secret_value
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La firma JWT no está configurada",
        )
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": UserRole.staff.value,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expiration_minutes),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def require_staff(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> StaffIdentity:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("Se requiere un token Bearer de personal")
    secret = settings.jwt_secret_value
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La verificación JWT no está configurada",
        )
    try:
        payload = jwt.decode(
            credentials.credentials,
            secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "role", "iat", "exp"]},
        )
    except InvalidTokenError as exc:
        raise _unauthorized("El token de personal es inválido o venció") from exc

    username = payload.get("sub")
    if not isinstance(username, str) or not username.strip():
        raise _unauthorized("La identidad del personal no es válida")
    if payload.get("role") != UserRole.staff.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Se requiere el rol de personal")
    return StaffIdentity(username=username.strip())


def optional_staff(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> StaffIdentity | None:
    """Allow public chat, while validating any supplied Bearer token strictly."""
    if credentials is None:
        return None
    return require_staff(credentials=credentials, settings=settings)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )
