"""Generate ADMIN_JWT_SECRET and upsert it into an env file.

Usage:
    python scripts/generate_admin_jwt_secret.py
    python scripts/generate_admin_jwt_secret.py --env-file /opt/amaexecutioncore/.env
    python scripts/generate_admin_jwt_secret.py --force
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

ENV_KEY = "ADMIN_JWT_SECRET"
SECRET_BYTES = 32


def generate_secret() -> str:
    return secrets.token_hex(SECRET_BYTES)


def _upsert_env_value(content: str, key: str, value: str, *, force: bool) -> tuple[str, bool]:
    lines = content.splitlines(keepends=True)
    updated_lines: list[str] = []
    found = False
    changed = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or not stripped.startswith(f"{key}="):
            updated_lines.append(line)
            continue

        found = True
        current = line.split("=", 1)[1].strip()
        if current and not force:
            updated_lines.append(line)
            continue

        newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        updated_lines.append(f"{key}={value}{newline}")
        changed = True

    if not found:
        prefix = "" if not content or content.endswith(("\n", "\r\n")) else "\n"
        updated_lines.append(f"{prefix}{key}={value}\n")
        changed = True

    return "".join(updated_lines), changed


def upsert_admin_jwt_secret(env_file: Path, *, force: bool) -> bool:
    secret = generate_secret()
    content = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    updated, changed = _upsert_env_value(content, ENV_KEY, secret, force=force)
    if changed:
        env_file.write_text(updated, encoding="utf-8")
    return changed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate ADMIN_JWT_SECRET and write it to a .env file.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Path to env file. Defaults to .env in the current directory.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing non-empty ADMIN_JWT_SECRET.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    changed = upsert_admin_jwt_secret(args.env_file, force=args.force)
    if changed:
        print(f"{ENV_KEY} written to {args.env_file}")
        print("Recreate the bot container to apply it: docker compose up -d bot")
        return 0

    print(f"{ENV_KEY} already exists in {args.env_file}; use --force to rotate it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
