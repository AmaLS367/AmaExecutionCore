"""CLI script: create the first AdminUser and print the TOTP QR code.

Usage:
    python scripts/create_admin.py

The script prompts for a username and password, hashes the password with
bcrypt, generates a TOTP secret, persists the new AdminUser row, and
prints the provisioning URI together with an ASCII QR code for scanning
with Google Authenticator (or any TOTP app).

Run once on a fresh deployment; re-running with the same username will
raise a unique-constraint error from PostgreSQL.
"""
from __future__ import annotations

import asyncio
import getpass
import sys
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.admin import auth as admin_auth
from backend.admin.models import AdminUser
from backend.config import settings


async def _insert_admin(username: str, password: str) -> str:
    secret = admin_auth.generate_totp_secret()
    engine = create_async_engine(settings.database_url)
    def _check_table(connection: Any) -> bool:
        from sqlalchemy import inspect
        return bool(inspect(connection).has_table("admin_users"))

    async with engine.begin() as conn:
        has_table = await conn.run_sync(_check_table)
        if not has_table:
            raise RuntimeError("admin_users table not found. Run alembic upgrade head first.")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = AdminUser(
            username=username,
            password_hash=admin_auth.hash_password(password),
            totp_secret=secret,
        )
        session.add(user)
        await session.commit()
    await engine.dispose()
    return secret


def _print_qr(uri: str) -> None:
    try:
        import qrcode

        qr = qrcode.QRCode()
        qr.add_data(uri)
        qr.make()
        qr.print_ascii(invert=True)
    except Exception:
        print("(qrcode library unavailable — scan the URI below manually)")


def main() -> None:
    print("=== AmaExecutionCore — Create Admin User ===")
    username = input("Username: ").strip()
    if not username:
        print("Username cannot be empty.", file=sys.stderr)
        sys.exit(1)

    password = getpass.getpass("Password: ")
    password_confirm = getpass.getpass("Confirm password: ")
    if password != password_confirm:
        print("Passwords do not match.", file=sys.stderr)
        sys.exit(1)
    if len(password) < 12:
        print("Password must be at least 12 characters.", file=sys.stderr)
        sys.exit(1)

    try:
        secret = asyncio.run(_insert_admin(username, password))
    except Exception as exc:
        print(f"Failed to create admin user: {exc}", file=sys.stderr)
        sys.exit(1)

    uri = admin_auth.get_totp_provisioning_uri(secret, username)
    print("\n✓ Admin user created successfully.\n")
    print("Scan this QR code with Google Authenticator / Authy:")
    _print_qr(uri)
    print(f"\nOr enter the secret manually: {secret}")
    print(f"Provisioning URI: {uri}\n")
    print("Keep the secret safe — it cannot be recovered after this point.")


if __name__ == "__main__":
    main()
