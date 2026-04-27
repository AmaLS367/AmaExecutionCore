from __future__ import annotations

from scripts.generate_admin_jwt_secret import (
    ENV_KEY,
    _upsert_env_value,
    generate_secret,
    upsert_admin_jwt_secret,
)


def test_generate_secret_returns_64_hex_chars() -> None:
    secret = generate_secret()

    assert len(secret) == 64
    int(secret, 16)


def test_upsert_env_value_adds_missing_key() -> None:
    updated, changed = _upsert_env_value("DEBUG=false\n", ENV_KEY, "abc", force=False)

    assert changed
    assert updated == "DEBUG=false\nADMIN_JWT_SECRET=abc\n"


def test_upsert_env_value_fills_blank_existing_key() -> None:
    updated, changed = _upsert_env_value("ADMIN_JWT_SECRET=\n", ENV_KEY, "abc", force=False)

    assert changed
    assert updated == "ADMIN_JWT_SECRET=abc\n"


def test_upsert_env_value_keeps_existing_key_without_force() -> None:
    updated, changed = _upsert_env_value("ADMIN_JWT_SECRET=old\n", ENV_KEY, "new", force=False)

    assert not changed
    assert updated == "ADMIN_JWT_SECRET=old\n"


def test_upsert_admin_jwt_secret_writes_file(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("DEBUG=false\n", encoding="utf-8")

    changed = upsert_admin_jwt_secret(env_file, force=False)
    content = env_file.read_text(encoding="utf-8")

    assert changed
    assert "DEBUG=false\n" in content
    assert "ADMIN_JWT_SECRET=" in content
