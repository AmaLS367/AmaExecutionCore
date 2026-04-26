from __future__ import annotations

import asyncio

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.admin import auth as admin_auth
from backend.admin.models import AdminUser, AuditLog
from backend.config import settings
from backend.main import create_app


@pytest.fixture(autouse=True)
def _configure_admin_settings() -> None:
    settings.admin_jwt_secret = "test-secret-at-least-32-characters-ok"


@pytest.fixture(autouse=True)
def _clear_brute_force() -> None:
    from backend.admin import router as admin_router

    admin_router._brute_force.clear()
    yield  # type: ignore[misc]
    admin_router._brute_force.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    username: str = "admin",
    password: str = "correct-pass",
    is_active: bool = True,
) -> tuple[str, str]:
    """Insert an AdminUser row; return (username, totp_secret)."""
    secret = admin_auth.generate_totp_secret()
    user = AdminUser(
        username=username,
        password_hash=admin_auth.hash_password(password),
        totp_secret=secret,
        is_active=is_active,
    )

    async def _insert() -> None:
        async with session_factory() as session:
            session.add(user)
            await session.commit()

    asyncio.run(_insert())
    return username, secret


def _totp_now(secret: str) -> str:
    return pyotp.TOTP(secret).now()


# ---------------------------------------------------------------------------
# POST /admin/auth/login
# ---------------------------------------------------------------------------


def test_login_unknown_user_returns_401(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=sqlite_session_factory)
    with TestClient(app) as client:
        r = client.post("/admin/auth/login", json={"username": "nobody", "password": "x-invalid"})
    assert r.status_code == 401


def test_login_wrong_password_returns_401(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _make_user(sqlite_session_factory)
    app = create_app(session_factory=sqlite_session_factory)
    with TestClient(app) as client:
        r = client.post("/admin/auth/login", json={"username": "admin", "password": "wrong-pass"})
    assert r.status_code == 401


def test_login_inactive_user_returns_401(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _make_user(sqlite_session_factory, is_active=False)
    app = create_app(session_factory=sqlite_session_factory)
    with TestClient(app) as client:
        r = client.post("/admin/auth/login", json={"username": "admin", "password": "correct-pass"})
    assert r.status_code == 401


def test_login_valid_credentials_returns_totp_required(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _make_user(sqlite_session_factory)
    app = create_app(session_factory=sqlite_session_factory)
    with TestClient(app) as client:
        r = client.post(
            "/admin/auth/login", json={"username": "admin", "password": "correct-pass"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["totp_required"] is True
    assert isinstance(body["session_token"], str)
    assert body["session_token"]


# ---------------------------------------------------------------------------
# POST /admin/auth/verify-totp
# ---------------------------------------------------------------------------


def test_verify_totp_wrong_code_returns_401(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _make_user(sqlite_session_factory)
    session_token = admin_auth.create_totp_pending_token("admin")
    app = create_app(session_factory=sqlite_session_factory)
    with TestClient(app) as client:
        r = client.post(
            "/admin/auth/verify-totp",
            json={"session_token": session_token, "totp_code": "000000"},
        )
    assert r.status_code == 401


def test_verify_totp_invalid_session_token_returns_401(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=sqlite_session_factory)
    with TestClient(app) as client:
        r = client.post(
            "/admin/auth/verify-totp",
            json={"session_token": "not-a-jwt", "totp_code": "123456"},
        )
    assert r.status_code == 401


def test_verify_totp_with_access_token_as_session_token_returns_401(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Access token has type='access', not 'totp_pending' — must be rejected."""
    _make_user(sqlite_session_factory)
    wrong_token = admin_auth.create_access_token("admin")
    app = create_app(session_factory=sqlite_session_factory)
    with TestClient(app) as client:
        r = client.post(
            "/admin/auth/verify-totp",
            json={"session_token": wrong_token, "totp_code": "123456"},
        )
    assert r.status_code == 401


def test_full_login_flow_returns_access_token_and_sets_cookie(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, secret = _make_user(sqlite_session_factory)
    app = create_app(session_factory=sqlite_session_factory)
    with TestClient(app, follow_redirects=False) as client:
        login_r = client.post(
            "/admin/auth/login", json={"username": "admin", "password": "correct-pass"},
        )
        session_token = login_r.json()["session_token"]
        verify_r = client.post(
            "/admin/auth/verify-totp",
            json={"session_token": session_token, "totp_code": _totp_now(secret)},
        )
    assert verify_r.status_code == 200
    body = verify_r.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert "refresh_token" in verify_r.cookies


# ---------------------------------------------------------------------------
# POST /admin/auth/refresh
# ---------------------------------------------------------------------------


_CSRF_HEADER = {"X-Requested-With": "XMLHttpRequest"}


def test_refresh_with_no_cookie_returns_401(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=sqlite_session_factory)
    with TestClient(app) as client:
        r = client.post("/admin/auth/refresh", headers=_CSRF_HEADER)
    assert r.status_code == 401


def test_refresh_with_valid_cookie_returns_new_access_token(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    refresh_cookie = admin_auth.create_refresh_token("admin")
    app = create_app(session_factory=sqlite_session_factory)
    with TestClient(app) as client:
        client.cookies.set("refresh_token", refresh_cookie)
        r = client.post("/admin/auth/refresh", headers=_CSRF_HEADER)
    assert r.status_code == 200
    assert "access_token" in r.json()


def test_refresh_with_garbage_cookie_returns_401(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=sqlite_session_factory)
    with TestClient(app) as client:
        client.cookies.set("refresh_token", "garbage")
        r = client.post("/admin/auth/refresh", headers=_CSRF_HEADER)
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /admin/auth/logout
# ---------------------------------------------------------------------------


def test_logout_returns_ok(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=sqlite_session_factory)
    with TestClient(app) as client:
        r = client.post("/admin/auth/logout")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ---------------------------------------------------------------------------
# Brute-force protection
# ---------------------------------------------------------------------------


def test_brute_force_blocks_ip_after_five_failed_logins(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=sqlite_session_factory)
    with TestClient(app) as client:
        for _ in range(5):
            client.post("/admin/auth/login", json={"username": "baduser", "password": "badpassword"})
        r = client.post("/admin/auth/login", json={"username": "baduser", "password": "badpassword"})
    assert r.status_code == 429


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def test_failed_login_writes_audit_log_for_known_user(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from sqlalchemy import select

    _make_user(sqlite_session_factory)
    app = create_app(session_factory=sqlite_session_factory)
    with TestClient(app) as client:
        client.post("/admin/auth/login", json={"username": "admin", "password": "wrong-pass"})

    async def _count() -> int:
        async with sqlite_session_factory() as session:
            rows = (await session.execute(select(AuditLog))).scalars().all()
            return len(rows)

    assert asyncio.run(_count()) == 1
