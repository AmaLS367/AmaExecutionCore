from __future__ import annotations

import ipaddress
import secrets
import time
from typing import Annotated, cast

import jwt
from fastapi import APIRouter, Cookie, HTTPException, Request, Response, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.admin import auth
from backend.admin.models import AdminUser, AuditLog

router = APIRouter(prefix="/admin/auth", tags=["admin-auth"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


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

_MAX_FAILURES = 5
_BLOCK_SECONDS = 900  # 15 minutes


async def _is_blocked(redis_client: object, ip: str) -> bool:
    return bool(await redis_client.exists(f"blocked:{ip}"))  # type: ignore[attr-defined]


async def _record_failure(redis_client: object, ip: str) -> None:
    key = f"failures:{ip}"
    count = await redis_client.incr(key)  # type: ignore[attr-defined]
    if count == 1:
        await redis_client.expire(key, 900)  # type: ignore[attr-defined]
    if count >= _MAX_FAILURES:
        await redis_client.setex(f"blocked:{ip}", _BLOCK_SECONDS, "1")  # type: ignore[attr-defined]


async def _reset_failures(redis_client: object, ip: str) -> None:
    await redis_client.delete(f"failures:{ip}")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return cast("async_sessionmaker[AsyncSession]", request.app.state.session_factory)


def _is_trusted_proxy(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    else:
        return ip.is_private or ip.is_loopback

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
            AuditLog(admin_id=admin_id, action=action, ip_address=ip, user_agent=user_agent),
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
async def login(request: Request, payload: LoginRequest) -> LoginResponse:
    ip = _client_ip(request)
    redis_client = request.app.state.redis
    if await _is_blocked(redis_client, ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts — try again later",
        )

    factory = _session_factory(request)
    async with factory() as session:
        row = (
            await session.execute(
                select(AdminUser).where(AdminUser.username == payload.username),
            )
        ).scalar_one_or_none()

    user_agent = request.headers.get("User-Agent")
    valid = (
        row is not None
        and row.is_active
        and auth.verify_password(payload.password, row.password_hash)
    )

    if not valid:
        await _record_failure(redis_client, ip)
        if row is not None:
            await _append_audit(factory, row.id, "login_failed", ip, user_agent)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    await _reset_failures(redis_client, ip)
    return LoginResponse(
        totp_required=True,
        session_token=auth.create_totp_pending_token(row.username),  # type: ignore[union-attr]
    )


@router.post("/verify-totp", response_model=TokenResponse)
async def verify_totp(
    request: Request, response: Response, payload: VerifyTotpRequest,
) -> TokenResponse:
    ip = _client_ip(request)
    user_agent = request.headers.get("User-Agent")
    redis_client = request.app.state.redis
    totp_fail_key = f"totp_fail:{payload.session_token}"
    fail_count = await redis_client.get(totp_fail_key)

    if fail_count and int(fail_count) >= 5:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed TOTP attempts. Please login again.",
        )

    try:
        token_payload = auth.decode_token(payload.session_token, "totp_pending")
        username = str(token_payload.get("sub"))
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token",
        ) from exc

    factory = _session_factory(request)
    async with factory() as session:
        row = (
            await session.execute(
                select(AdminUser).where(AdminUser.username == username),
            )
        ).scalar_one_or_none()

    if row is None or not row.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials",
        )

    if not auth.verify_totp(row.totp_secret, payload.totp_code):
        count = await redis_client.incr(totp_fail_key)
        if count == 1:
            await redis_client.expire(totp_fail_key, 300)
        await _append_audit(factory, row.id, "totp_failed", ip, user_agent)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials",
        )

    await _append_audit(factory, row.id, "login_success", ip, user_agent)
    refresh_token = auth.create_refresh_token(username)
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=60 * 60 * 24 * 30,
    )
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,
        secure=True,
        samesite="strict",
        max_age=60 * 60 * 24 * 30,
    )
    return TokenResponse(access_token=auth.create_access_token(username))


@router.post("/refresh", response_model=TokenResponse)
async def refresh_access_token(
    request: Request,
    refresh_token: Annotated[str | None, Cookie()] = None,
    csrf_token: Annotated[str | None, Cookie()] = None,
) -> TokenResponse:
    csrf_header = request.headers.get("X-CSRF-Token")
    if not csrf_token or not csrf_header or csrf_token != csrf_header:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token mismatch")
    if refresh_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="No refresh token provided",
        )
    try:
        token_payload = auth.decode_token(refresh_token, "refresh")
        username = str(token_payload.get("sub"))
        jti = str(token_payload.get("jti", ""))
        redis_client = request.app.state.redis
        if jti and await redis_client.exists(f"bl:{jti}"):
            raise jwt.PyJWTError("Token revoked")  # noqa: TRY301
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        ) from exc
    return TokenResponse(access_token=auth.create_access_token(username))


_security = HTTPBearer(auto_error=False)

@router.post("/logout", response_model=LogoutResponse)
async def logout(  # noqa: C901

    request: Request,
    response: Response,
    refresh_token: Annotated[str | None, Cookie()] = None,
    csrf_token: Annotated[str | None, Cookie()] = None,
    credentials: HTTPAuthorizationCredentials | None = Security(_security),
) -> LogoutResponse:
    if refresh_token:
        csrf_header = request.headers.get("X-CSRF-Token")
        if not csrf_token or not csrf_header or csrf_token != csrf_header:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token mismatch")

    redis_client = request.app.state.redis
    if credentials:
        try:
            acc_payload = auth.decode_token(credentials.credentials, "access")
            acc_jti = str(acc_payload.get("jti", ""))
            acc_exp = acc_payload.get("exp")
            if acc_jti and isinstance(acc_exp, (int, float)):
                ttl = int(acc_exp - time.time())
                if ttl > 0:
                    await redis_client.setex(f"bl:{acc_jti}", ttl, "1")
        except jwt.PyJWTError:
            pass

    if refresh_token:
        try:
            ref_payload = auth.decode_token(refresh_token, "refresh")
            ref_jti = str(ref_payload.get("jti", ""))
            ref_exp = ref_payload.get("exp")
            if ref_jti and isinstance(ref_exp, (int, float)):
                ttl = int(ref_exp - time.time())
                if ttl > 0:
                    await redis_client.setex(f"bl:{ref_jti}", ttl, "1")
        except jwt.PyJWTError:
            pass

    response.delete_cookie("refresh_token")
    return LogoutResponse(ok=True)
