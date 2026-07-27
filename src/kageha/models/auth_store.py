"""Provider OAuth / subscription auth store (ChatGPT Codex, Gemini CLI, …).

Cursor and Kiro IDE subscription OAuth are **not** available to third-party
agents — there is no public API. Use API keys or import ChatGPT/Codex / Gemini
CLI logins instead.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kageha.config import kageha_home

_FILE_MODE = 0o600
_DIR_MODE = 0o700


@dataclass
class AuthProfile:
    provider: str
    kind: str  # oauth | api_key | cli_import
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float | None = None  # unix ts
    account_id: str = ""
    email: str = ""
    extra: dict[str, Any] | None = None
    source: str = ""

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "kind": self.kind,
            "account_id": self.account_id,
            "email": self.email,
            "expires_at": self.expires_at,
            "has_access_token": bool(self.access_token),
            "has_refresh_token": bool(self.refresh_token),
            "source": self.source,
            "expired": self.is_expired(),
        }

    def is_expired(self, skew_s: float = 60.0) -> bool:
        if not self.expires_at:
            return False
        return time.time() >= (self.expires_at - skew_s)


def auth_dir(*, create: bool = True) -> Path:
    d = kageha_home() / "auth"
    if create:
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, _DIR_MODE)
        except OSError:
            pass
    return d


def _path(provider: str, *, create_parent: bool = True) -> Path:
    safe = "".join(c for c in provider if c.isalnum() or c in "-_")
    if not safe or safe != provider:
        raise ValueError(f"invalid auth provider id: {provider!r}")
    return auth_dir(create=create_parent) / f"{safe}.json"


def save_profile(profile: AuthProfile) -> Path:
    path = _path(profile.provider)
    payload = {
        "provider": profile.provider,
        "kind": profile.kind,
        "access_token": profile.access_token,
        "refresh_token": profile.refresh_token,
        "expires_at": profile.expires_at,
        "account_id": profile.account_id,
        "email": profile.email,
        "extra": profile.extra or {},
        "source": profile.source,
        "updated_at": time.time(),
    }
    text = json.dumps(payload, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{profile.provider}.", dir=str(auth_dir()))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, _FILE_MODE)
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    try:
        os.chmod(path, _FILE_MODE)
    except OSError:
        pass
    return path


def load_profile(provider: str) -> AuthProfile | None:
    path = _path(provider, create_parent=False)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return AuthProfile(
        provider=str(data.get("provider") or provider),
        kind=str(data.get("kind") or "oauth"),
        access_token=str(data.get("access_token") or ""),
        refresh_token=str(data.get("refresh_token") or ""),
        expires_at=data.get("expires_at"),
        account_id=str(data.get("account_id") or ""),
        email=str(data.get("email") or ""),
        extra=dict(data.get("extra") or {}),
        source=str(data.get("source") or ""),
    )


def delete_profile(provider: str) -> bool:
    path = _path(provider, create_parent=False)
    if not path.is_file():
        return False
    path.unlink()
    return True


def list_profiles() -> list[AuthProfile]:
    root = auth_dir(create=False)
    if not root.is_dir():
        return []
    out: list[AuthProfile] = []
    for p in sorted(root.glob("*.json")):
        prof = load_profile(p.stem)
        if prof:
            out.append(prof)
    return out


def import_chatgpt_codex(path: Path | None = None) -> AuthProfile:
    """Import ChatGPT / Codex CLI OAuth from ``~/.codex/auth.json``."""
    src = path or (Path.home() / ".codex" / "auth.json")
    if not src.is_file():
        raise RuntimeError(
            f"Codex auth not found at {src}. Run: codex login\n"
            "Then: kageha models auth import chatgpt"
        )
    data = json.loads(src.read_text(encoding="utf-8"))
    tokens = data.get("tokens") or {}
    access = str(tokens.get("access_token") or "").strip()
    refresh = str(tokens.get("refresh_token") or "").strip()
    account = str(tokens.get("account_id") or "").strip()
    if not access:
        raise RuntimeError(f"No access_token in {src}")
    # last_refresh is ISO; Codex tokens typically last ~1h — refresh via CLI if expired
    expires_at = None
    last = data.get("last_refresh")
    if isinstance(last, str) and last:
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            expires_at = dt.timestamp() + 3600.0
        except ValueError:
            expires_at = None
    profile = AuthProfile(
        provider="chatgpt",
        kind="oauth",
        access_token=access,
        refresh_token=refresh,
        expires_at=expires_at,
        account_id=account,
        source=str(src),
        extra={"auth_mode": data.get("auth_mode")},
    )
    save_profile(profile)
    # Alias used by models.yaml provider id
    alias = AuthProfile(
        provider="openai-codex",
        kind=profile.kind,
        access_token=profile.access_token,
        refresh_token=profile.refresh_token,
        expires_at=profile.expires_at,
        account_id=profile.account_id,
        email=profile.email,
        source=profile.source,
        extra=profile.extra,
    )
    save_profile(alias)
    return profile


def import_gemini_cli(path: Path | None = None) -> AuthProfile:
    """Import Gemini CLI / Antigravity-shared OAuth from ``~/.gemini/oauth_creds.json``."""
    src = path or (Path.home() / ".gemini" / "oauth_creds.json")
    if not src.is_file():
        raise RuntimeError(
            f"Gemini CLI OAuth not found at {src}. Run: gemini  (and complete login)\n"
            "Or Antigravity sign-in, then: kageha models auth import gemini-cli"
        )
    data = json.loads(src.read_text(encoding="utf-8"))
    access = str(data.get("access_token") or "").strip()
    refresh = str(data.get("refresh_token") or "").strip()
    if not access:
        raise RuntimeError(f"No access_token in {src}")
    expiry_ms = data.get("expiry_date")
    expires_at = float(expiry_ms) / 1000.0 if expiry_ms else None
    email = ""
    accounts = Path.home() / ".gemini" / "google_accounts.json"
    if accounts.is_file():
        try:
            acc = json.loads(accounts.read_text(encoding="utf-8"))
            if isinstance(acc, dict):
                email = str(acc.get("active") or acc.get("email") or "")
            elif isinstance(acc, list) and acc:
                email = str(acc[0])
        except (OSError, json.JSONDecodeError):
            pass
    profile = AuthProfile(
        provider="gemini-cli",
        kind="oauth",
        access_token=access,
        refresh_token=refresh,
        expires_at=expires_at,
        email=email,
        source=str(src),
        extra={"scope": data.get("scope"), "token_type": data.get("token_type")},
    )
    save_profile(profile)
    # Antigravity on this machine shares the same Gemini OAuth cache
    anti = AuthProfile(
        provider="antigravity",
        kind=profile.kind,
        access_token=profile.access_token,
        refresh_token=profile.refresh_token,
        expires_at=profile.expires_at,
        email=profile.email,
        source=profile.source,
        extra=profile.extra,
    )
    save_profile(anti)
    return profile


def resolve_access_token(
    provider: str,
    *,
    import_missing: bool = True,
) -> tuple[str, dict[str, str]]:
    """Return (access_token, extra_headers) for a provider auth profile.

    Lazily imports from Codex / Gemini CLI caches when profiles are missing
    but the local CLI login files exist (so ``/model gpt-codex`` works after
    ``codex login`` without a separate import step).
    """
    # Prefer explicit profile; fall back to aliases
    order = [provider]
    if provider in {"openai", "openai-codex"}:
        for alias in ("openai-codex", "chatgpt"):
            if alias not in order:
                order.append(alias)
    if provider == "chatgpt":
        if "openai-codex" not in order:
            order.append("openai-codex")
    if provider in {"gemini", "google", "gemini-cli", "antigravity"}:
        for alias in ("gemini-cli", "antigravity"):
            if alias not in order:
                order.append(alias)

    wants_codex = any(p in order for p in ("chatgpt", "openai-codex", "openai"))
    wants_gemini = any(p in order for p in ("gemini-cli", "antigravity", "gemini", "google"))

    def _has_usable(pid: str) -> bool:
        prof = load_profile(pid)
        return bool(prof and prof.access_token and not prof.is_expired())

    # Lazy import when nothing usable is stored yet
    if (
        import_missing
        and wants_codex
        and not any(_has_usable(p) for p in ("chatgpt", "openai-codex"))
    ):
        if (Path.home() / ".codex" / "auth.json").is_file():
            try:
                import_chatgpt_codex()
            except (OSError, RuntimeError):
                pass
    if (
        import_missing
        and wants_gemini
        and not any(_has_usable(p) for p in ("gemini-cli", "antigravity"))
    ):
        if (Path.home() / ".gemini" / "oauth_creds.json").is_file():
            try:
                import_gemini_cli()
            except (OSError, RuntimeError):
                pass

    for pid in order:
        prof = load_profile(pid)
        if not prof or not prof.access_token:
            continue
        # Refresh from CLI caches when our expiry heuristic says stale.
        # Still return the token afterward — Codex last_refresh+1h is approximate;
        # the API is the source of truth (and refresh may have updated the file).
        if (
            import_missing
            and prof.is_expired()
            and pid in {"chatgpt", "openai-codex"}
        ):
            try:
                prof = import_chatgpt_codex()
            except (OSError, RuntimeError):
                pass
        if (
            import_missing
            and prof.is_expired()
            and pid in {"gemini-cli", "antigravity"}
        ):
            try:
                prof = import_gemini_cli()
            except (OSError, RuntimeError):
                pass
        if not prof.access_token:
            continue
        headers: dict[str, str] = {}
        if prof.account_id and pid in {"chatgpt", "openai-codex", "openai"}:
            headers["ChatGPT-Account-ID"] = prof.account_id
        return prof.access_token, headers
    return "", {}


def probe_local_logins() -> dict[str, Any]:
    """Detect local CLI logins without reading token bodies into logs."""
    codex = Path.home() / ".codex" / "auth.json"
    gemini = Path.home() / ".gemini" / "oauth_creds.json"
    anti_dir = Path.home() / ".gemini" / "antigravity"
    return {
        "chatgpt_codex_cli": codex.is_file(),
        "gemini_cli_oauth": gemini.is_file(),
        "antigravity_data_dir": anti_dir.is_dir(),
        "cursor_oauth": False,  # no public third-party Cursor model OAuth
        "codex_path": str(codex) if codex.is_file() else "",
        "gemini_path": str(gemini) if gemini.is_file() else "",
        "note_cursor": (
            "Cursor and Kiro IDE subscriptions cannot be used as a model provider "
            "OAuth source. Use API keys, ChatGPT/Codex import, or Gemini CLI / "
            "Antigravity import."
        ),
    }
