from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
import pyotp

from backend.config import settings

_ALGORITHM = "HS256"
_TYPE_ACCESS = "access"
_TYPE_REFRESH = "refresh"
_TYPE_TOTP_PENDING = "totp_pending"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def _make_token(username: str, token_type: str, expires_delta: timedelta) -> str:
    expires = datetime.now(UTC) + expires_delta
    payload: dict[str, object] = {"sub": username, "type": token_type, "exp": expires}
    return jwt.encode(payload, settings.admin_jwt_secret, algorithm=_ALGORITHM)


def create_access_token(username: str) -> str:
    return _make_token(
        username, _TYPE_ACCESS, timedelta(minutes=settings.admin_jwt_access_ttl_minutes),
    )


def create_refresh_token(username: str) -> str:
    return _make_token(
        username, _TYPE_REFRESH, timedelta(days=settings.admin_jwt_refresh_ttl_days),
    )


def create_totp_pending_token(username: str) -> str:
    return _make_token(username, _TYPE_TOTP_PENDING, timedelta(minutes=5))


def decode_token(token: str, expected_type: str) -> str:
    """Decode a JWT and return the subject username.

    Raises jwt.PyJWTError on expiry, bad signature, or type mismatch.
    """
    payload = jwt.decode(token, settings.admin_jwt_secret, algorithms=[_ALGORITHM])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("Token type mismatch")
    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise jwt.InvalidTokenError("Missing or invalid subject claim")
    return sub


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def verify_totp(secret: str, code: str) -> bool:
    return bool(pyotp.TOTP(secret).verify(code, valid_window=1))


def get_totp_provisioning_uri(secret: str, username: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(
        name=username,
        issuer_name=settings.admin_totp_issuer,
    )
