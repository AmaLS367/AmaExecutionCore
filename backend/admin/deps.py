from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.admin import auth

_bearer = HTTPBearer()


async def get_current_admin(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> str:
    """Extract and validate an access JWT from the Authorization: Bearer header.

    Returns the username on success. Raises 401 on any token failure.
    """
    try:
        payload = auth.decode_token(credentials.credentials, "access")
        jti = str(payload.get("jti", ""))
        if jti and await request.app.state.redis.exists(f"bl:{jti}"):
            raise jwt.PyJWTError("Token revoked")  # noqa: TRY301
        return str(payload.get("sub"))
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
