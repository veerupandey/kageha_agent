"""Persistent browser / research preferences (~/.kageha/browser.json)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from kageha.config import kageha_home
from kageha.harness.browser.backends import resolve_backend_spec


@dataclass
class BrowserPrefs:
    backend: str = "http"
    cdp: str = "http://127.0.0.1:9222"
    research_depth: str = "flash"
    auto_pack: bool = True
    # When True, optional browser pack is pulled into resolve_enabled_packs.
    enable_browser_pack: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def prefs_path() -> Path:
    return kageha_home() / "browser.json"


def load_browser_prefs() -> BrowserPrefs:
    path = prefs_path()
    if not path.is_file():
        return BrowserPrefs()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return BrowserPrefs()
    if not isinstance(raw, dict):
        return BrowserPrefs()
    return BrowserPrefs(
        backend=str(raw.get("backend") or "http"),
        cdp=str(raw.get("cdp") or "http://127.0.0.1:9222"),
        research_depth=str(raw.get("research_depth") or "flash"),
        auto_pack=bool(raw.get("auto_pack", True)),
        enable_browser_pack=bool(raw.get("enable_browser_pack", False)),
        extra=dict(raw.get("extra") or {}) if isinstance(raw.get("extra"), dict) else {},
    )


def save_browser_prefs(prefs: BrowserPrefs) -> Path:
    path = prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prefs.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def apply_browser_prefs(prefs: BrowserPrefs | None = None) -> BrowserPrefs:
    """Push prefs into process env so engine/pool/packs see them."""
    p = prefs or load_browser_prefs()
    spec = resolve_backend_spec(p.backend)
    if spec:
        if spec.env_browser_mode:
            os.environ["KAGEHA_BROWSER_MODE"] = spec.env_browser_mode
        if spec.env_headless:
            os.environ["KAGEHA_HEADLESS_BACKEND"] = spec.env_headless
        if spec.needs_pack and p.auto_pack:
            p.enable_browser_pack = True
    os.environ["KAGEHA_COMET_CDP"] = p.cdp
    os.environ["KAGEHA_HEADLESS_CDP"] = p.cdp
    os.environ["KAGEHA_RESEARCH_DEPTH"] = p.research_depth
    if p.enable_browser_pack and p.auto_pack:
        os.environ["KAGEHA_BROWSER_PACK"] = "1"
        _ensure_pack_token("browser")
    elif os.environ.get("KAGEHA_BROWSER_PACK") == "1" and not p.enable_browser_pack:
        os.environ.pop("KAGEHA_BROWSER_PACK", None)
    return p


def _ensure_pack_token(name: str) -> None:
    raw = (os.environ.get("KAGEHA_TOOL_PACKS") or "").strip()
    if not raw:
        os.environ["KAGEHA_TOOL_PACKS"] = name
        return
    if raw.lower() in {"all", "*"}:
        return
    parts = [p.strip().lower() for p in raw.replace(";", ",").split(",") if p.strip()]
    if name not in parts and f"-{name}" not in parts and f"no{name}" not in parts:
        parts.append(name)
        os.environ["KAGEHA_TOOL_PACKS"] = ",".join(parts)


def set_backend(
    backend: str,
    *,
    cdp: str | None = None,
    research_depth: str | None = None,
    enable_pack: bool | None = None,
) -> BrowserPrefs:
    spec = resolve_backend_spec(backend)
    if spec is None:
        raise ValueError(
            f"Unknown backend {backend!r}. Use /browser list for options."
        )
    prefs = load_browser_prefs()
    prefs.backend = spec.id
    if cdp:
        prefs.cdp = cdp.strip()
    if research_depth:
        prefs.research_depth = research_depth.strip().lower()
    if enable_pack is not None:
        prefs.enable_browser_pack = enable_pack
    elif spec.needs_pack:
        prefs.enable_browser_pack = True
    save_browser_prefs(prefs)
    return apply_browser_prefs(prefs)


def status_text() -> str:
    prefs = apply_browser_prefs()
    spec = resolve_backend_spec(prefs.backend)
    pack = "on" if prefs.enable_browser_pack else "off"
    lines = [
        "Browser prefs",
        f"  backend:  {prefs.backend}" + (f" ({spec.label})" if spec else ""),
        f"  kind:     {spec.kind if spec else '?'}",
        f"  cdp:      {prefs.cdp}",
        f"  research: {prefs.research_depth}",
        f"  pack:     browser={pack} (auto_pack={prefs.auto_pack})",
        f"  env mode: {os.environ.get('KAGEHA_BROWSER_MODE', '')}",
        f"  headless: {os.environ.get('KAGEHA_HEADLESS_BACKEND', '')}",
        f"  file:     {prefs_path()}",
    ]
    if spec:
        lines.append(f"  note:     {spec.description}")
    return "\n".join(lines)


# Apply saved prefs once on import so CLI/chat see them.
try:
    apply_browser_prefs()
except Exception:
    pass
