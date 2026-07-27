"""Interactive WhatsApp setup wizard — ask number, write .env, optional start."""

from __future__ import annotations

import os
import re
from pathlib import Path

from kageha.channels.whatsapp import normalize_phone
from kageha.config import project_root


def env_file_candidates() -> list[Path]:
    """Prefer project .env, then cwd .env."""
    paths = [project_root() / ".env", Path.cwd() / ".env"]
    # dedupe
    seen: set[Path] = set()
    out: list[Path] = []
    for p in paths:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


def resolve_env_file() -> Path:
    for p in env_file_candidates():
        if p.is_file():
            return p
    # create project .env
    target = project_root() / ".env"
    if not target.is_file():
        target.write_text("# Kageha env\n", encoding="utf-8")
    return target


def read_env_value(key: str, path: Path | None = None) -> str:
    path = path or resolve_env_file()
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return ""


def upsert_env_key(key: str, value: str, path: Path | None = None) -> Path:
    """Set KEY=value in .env (replace existing line or append)."""
    path = path or resolve_env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    lines = text.splitlines()
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    replaced = False
    new_lines: list[str] = []
    for line in lines:
        if pattern.match(line):
            new_lines.append(f"{key}={value}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(f"{key}={value}")
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    os.environ[key] = value
    return path


def prompt_phone(existing: str = "") -> str:
    """Ask user for WhatsApp number; return normalized digits."""
    print(
        "\nKageha WhatsApp setup\n"
        "----------------------\n"
        "Which phone number should be allowed to talk to the agent?\n"
        "Use country code, digits only (example: 14155551234 for +1 415 555 1234).\n"
        "This is YOUR number (the one you will message from), not Meta Business IDs.\n",
        flush=True,
    )
    hint = f" [{existing}]" if existing else ""
    while True:
        raw = input(f"Your WhatsApp number{hint}: ").strip()
        if not raw and existing:
            raw = existing
        phone = normalize_phone(raw)
        if len(phone) < 8:
            print("Too short — include country code (e.g. 14155551234).", flush=True)
            continue
        if len(phone) > 15:
            print("Too long — check the number.", flush=True)
            continue
        confirm = input(f"Save allowlist as {phone}? [Y/n] ").strip().lower()
        if confirm in {"", "y", "yes"}:
            return phone
        print("Okay, try again.", flush=True)


def run_whatsapp_setup(*, start_after: bool | None = None) -> dict[str, str]:
    """Interactive setup: write WHATSAPP_ALLOWED_USERS to .env.

    Returns dict with phone, env_path, and optionally starts nothing (caller starts).
    """
    env_path = resolve_env_file()
    existing = normalize_phone(
        read_env_value("WHATSAPP_ALLOWED_USERS", env_path)
        or read_env_value("WHATSAPP_QR_ALLOWED_USERS", env_path)
        or os.environ.get("WHATSAPP_ALLOWED_USERS", "")
    )
    if existing in {"*", ""}:
        existing = ""

    print(
        "\nMode: WhatsApp QR (linked device) — OpenClaw-style.\n"
        "No Meta Business token needed. You will scan a QR next (if you start the bridge).\n",
        flush=True,
    )
    phone = prompt_phone(existing)
    path = upsert_env_key("WHATSAPP_ALLOWED_USERS", phone, env_path)
    # Clear accidental allow-all
    if read_env_value("WHATSAPP_ALLOW_ALL_USERS", path).lower() in {"1", "true", "yes"}:
        upsert_env_key("WHATSAPP_ALLOW_ALL_USERS", "false", path)

    print(f"\nSaved WHATSAPP_ALLOWED_USERS={phone}\n  → {path}\n", flush=True)

    if start_after is None:
        ans = input("Start WhatsApp QR bridge now? [Y/n] ").strip().lower()
        start_after = ans in {"", "y", "yes"}

    return {"phone": phone, "env_path": str(path), "start": "1" if start_after else "0"}


def needs_whatsapp_setup() -> bool:
    """True when allowlist is empty / not configured (and not allow-all)."""
    if os.environ.get("WHATSAPP_ALLOW_ALL_USERS", "").strip().lower() in {"1", "true", "yes"}:
        return False
    raw = (
        os.environ.get("WHATSAPP_QR_ALLOWED_USERS")
        or os.environ.get("WHATSAPP_ALLOWED_USERS")
        or read_env_value("WHATSAPP_QR_ALLOWED_USERS")
        or read_env_value("WHATSAPP_ALLOWED_USERS")
        or ""
    ).strip()
    if raw == "*":
        return False
    if not raw:
        return True
    return len(normalize_phone(raw.split(",")[0])) < 8
