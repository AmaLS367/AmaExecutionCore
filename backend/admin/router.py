from __future__ import annotations

import time
from typing import Annotated, cast

import jwt
from fastapi import APIRouter, Cookie, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.admin import auth
from backend.admin.models import AdminUser, AuditLog

router = APIRouter(prefix="/admin/auth", tags=["admin-auth"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


from pydantic import BaseModel, Field

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(..., min_length=8, max_length=128)


class LoginResponse(BaseModel):
    totp_required: bool
    session_token: str


class VerifyTotpRequest(BaseModel):
    session_token: str
    totp_code: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LogoutResponse(BaseModel):
    ok: bool


# ---------------------------------------------------------------------------
# Brute-force protection (in-memory, per IP)
# ---------------------------------------------------------------------------

_brute_force: dict[str, dict[str, float]] = {}
_totp_failures: dict[str, int] = {}
_MAX_FAILURES = 5
_BLOCK_SECONDS = 900.0  # 15 minutes


def _is_blocked(ip: str) -> bool:
    record = _brute_force.get(ip)
    if record is None:
        return False
    return record.get("blocked_until", 0.0) > time.monotonic()


def _record_failure(ip: str) -> None:
    record = _brute_force.setdefault(ip, {"failures": 0.0, "blocked_until": 0.0})
    record["failures"] += 1.0
    if record["failures"] >= _MAX_FAILURES:
        record["blocked_until"] = time.monotonic() + _BLOCK_SECONDS


def _reset_failures(ip: str) -> None:
    _brute_force.pop(ip, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return cast("async_sessionmaker[AsyncSession]", request.app.state.session_factory)


import ipaddress

def _is_trusted_proxy(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False

def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else None
    if peer and _is_trusted_proxy(peer):
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip
    return peer or "unknown"


async def _append_audit(
    factory: async_sessionmaker[AsyncSession],
    admin_id: int,
    action: str,
    ip: str,
    user_agent: str | None,
) -> None:
    async with factory() as session:
        session.add(
            AuditLog(admin_id=admin_id, action=action, ip_address=ip, user_agent=user_agent)
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
async def login(request: Request, payload: LoginRequest) -> LoginResponse:
    ip = _client_ip(request)
    if _is_blocked(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts — try again later",
        )

    factory = _session_factory(request)
    async with factory() as session:
        row = (
            await session.execute(
                select(AdminUser).where(AdminUser.username == payload.username)
            )
        ).scalar_one_or_none()

    user_agent = request.headers.get("User-Agent")
    valid = (
        row is not None
        and row.is_active
        and auth.verify_password(payload.password, row.password_hash)
    )

    if not valid:
        _record_failure(ip)
        if row is not None:
            await _append_audit(factory, row.id, "login_failed", ip, user_agent)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    _reset_failures(ip)
    return LoginResponse(
        totp_required=True,
        session_token=auth.create_totp_pending_token(row.username),  # type: ignore[union-attr]
    )


@router.post("/verify-totp", response_model=TokenResponse)
async def verify_totp(
    request: Request, response: Response, payload: VerifyTotpRequest
) -> TokenResponse:
    ip = _client_ip(request)
    user_agent = request.headers.get("User-Agent")

    if _totp_failures.get(payload.session_token, 0) >= 5:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed TOTP attempts. Please login again.",
        )

    try:
        username = auth.decode_token(payload.session_token, "totp_pending")
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token",
        ) from exc

    factory = _session_factory(request)
    async with factory() as session:
        row = (
            await session.execute(
                select(AdminUser).where(AdminUser.username == username)
            )
        ).scalar_one_or_none()

    if row is None or not row.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    if not auth.verify_totp(row.totp_secret, payload.totp_code):
        _totp_failures[payload.session_token] = _totp_failures.get(payload.session_token, 0) + 1
        await _append_audit(factory, row.id if row else 0, "totp_failed", ip, user_agent)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    await _append_audit(factory, row.id, "login_success", ip, user_agent)
    refresh_token = auth.create_refresh_token(username)
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=60 * 60 * 24 * 30,
    )
    return TokenResponse(access_token=auth.create_access_token(username))


@router.post("/refresh", response_model=TokenResponse)
async def refresh_access_token(
    request: Request,
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> TokenResponse:
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF protection missing header")
    if refresh_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="No refresh token provided"
        )
    try:
        username = auth.decode_token(refresh_token, "refresh")
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        ) from exc
    return TokenResponse(access_token=auth.create_access_token(username))


@router.post("/logout", response_model=LogoutResponse)
async def logout(response: Response) -> LogoutResponse:
    response.delete_cookie("refresh_token")
    return LogoutResponse(ok=True)
