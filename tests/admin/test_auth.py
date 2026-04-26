from __future__ import annotations

import time

import jwt
import pyotp
import pytest

from backend.config import settings


@pytest.fixture(autouse=True)
def _set_jwt_secret() -> None:
    settings.admin_jwt_secret = "test-secret-at-least-32-characters-ok"


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def test_hash_and_verify_password_roundtrip() -> None:
    from backend.admin import auth

    hashed = auth.hash_password("my-password")
    assert auth.verify_password("my-password", hashed)


def test_verify_password_fails_on_wrong_password() -> None:
    from backend.admin import auth

    hashed = auth.hash_password("correct-password")
    assert not auth.verify_password("wrong-password", hashed)


# ---------------------------------------------------------------------------
# JWT tokens
# ---------------------------------------------------------------------------


def test_create_access_token_decodes_with_access_type() -> None:
    from backend.admin import auth

    token = auth.create_access_token("alice")
    username = auth.decode_token(token, "access")
    assert username == "alice"


def test_create_totp_pending_token_decodes_with_totp_pending_type() -> None:
    from backend.admin import auth

    token = auth.create_totp_pending_token("alice")
    username = auth.decode_token(token, "totp_pending")
    assert username == "alice"


def test_create_refresh_token_decodes_with_refresh_type() -> None:
    from backend.admin import auth

    token = auth.create_refresh_token("alice")
    username = auth.decode_token(token, "refresh")
    assert username == "alice"


def test_decode_token_with_wrong_type_raises() -> None:
    from backend.admin import auth

    totp_token = auth.create_totp_pending_token("alice")
    with pytest.raises(jwt.PyJWTError):
        auth.decode_token(totp_token, "access")


def test_decode_expired_token_raises() -> None:
    expired = jwt.encode(
        {"sub": "alice", "type": "access", "exp": int(time.time()) - 1},
        settings.admin_jwt_secret,
        algorithm="HS256",
    )
    from backend.admin import auth

    with pytest.raises(jwt.PyJWTError):
        auth.decode_token(expired, "access")


def test_decode_garbage_token_raises() -> None:
    from backend.admin import auth

    with pytest.raises(jwt.PyJWTError):
        auth.decode_token("not.a.jwt.token", "access")


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------


def test_generate_totp_secret_returns_valid_base32_string() -> None:
    from backend.admin import auth

    secret = auth.generate_totp_secret()
    assert isinstance(secret, str)
    assert len(secret) >= 16


def test_verify_totp_accepts_current_code() -> None:
    from backend.admin import auth

    secret = auth.generate_totp_secret()
    current_code = pyotp.TOTP(secret).now()
    assert auth.verify_totp(secret, current_code)


def test_verify_totp_rejects_wrong_code() -> None:
    from backend.admin import auth

    secret = auth.generate_totp_secret()
    assert not auth.verify_totp(secret, "000000")


def test_get_totp_provisioning_uri_contains_username_and_issuer() -> None:
    from backend.admin import auth

    secret = auth.generate_totp_secret()
    uri = auth.get_totp_provisioning_uri(secret, "alice")
    assert "alice" in uri
    assert "otpauth://" in uri
    assert settings.admin_totp_issuer in uri
