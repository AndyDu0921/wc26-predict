from __future__ import annotations

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings, is_secure_admin_token
from app.exceptions import AuthorizationError

settings = get_settings()
bearer_scheme = HTTPBearer(auto_error=False)


def require_admin_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    if (
        not is_secure_admin_token(settings.admin_token)
        or credentials is None
        or credentials.credentials != settings.admin_token
    ):
        raise AuthorizationError()
    return credentials.credentials
