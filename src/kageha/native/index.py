"""File-index ranking façade: optional Rust, always Python fallback.

The compiled module (when shipped) is expected to expose::

    search(entries: list[tuple[str, float]], q: str, limit: int) -> list[dict]

where each entry is ``(posix_relative_path, mtime)`` and each result is
``{"path": str, "score": float}`` ranked best-first — matching
``FileIndex.query``'s contract.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

# Module names tried for the optional PyO3 / maturin wheel.
_RUST_CANDIDATES = (
    "kageha_index_native",
    "kageha.native._index_rs",
)


class _NativeSearch(Protocol):
    def search(
        self,
        entries: list[tuple[str, float]],
        q: str,
        limit: int,
    ) -> list[dict[str, Any]]: ...


_RUST_MOD: _NativeSearch | None | bool = False  # False = not probed yet


def native_index_enabled() -> bool:
    """Return True unless the operator forced pure Python off.

    Env ``KAGEHA_NATIVE_INDEX``:
      - ``0`` / ``off`` / ``false`` / ``no`` → force Python
      - unset / ``1`` / ``on`` / ``true`` / ``yes`` → allow native if installed
    """
    raw = (os.environ.get("KAGEHA_NATIVE_INDEX") or "").strip().lower()
    if raw in {"0", "off", "false", "no", "python"}:
        return False
    return True


def _probe_rust() -> _NativeSearch | None:
    global _RUST_MOD
    if _RUST_MOD is not False:
        return _RUST_MOD if _RUST_MOD is not None else None
    mod: _NativeSearch | None = None
    for name in _RUST_CANDIDATES:
        try:
            if "." in name:
                # Nested: kageha.native._index_rs
                from importlib import import_module

                candidate = import_module(name)
            else:
                candidate = __import__(name)
        except ImportError:
            continue
        search = getattr(candidate, "search", None)
        if callable(search):
            mod = candidate  # type: ignore[assignment]
            break
    _RUST_MOD = mod
    return mod


def native_index_available() -> bool:
    """True if a compiled index extension is importable (regardless of flag)."""
    return _probe_rust() is not None


def index_backend() -> str:
    """Active ranking backend: ``rust`` or ``python``."""
    if native_index_enabled() and native_index_available():
        return "rust"
    return "python"


def reset_native_index_probe_for_tests() -> None:
    """Clear the cached import probe (test helper)."""
    global _RUST_MOD
    _RUST_MOD = False


def _python_rank(
    entries: list[tuple[str, float]],
    q: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    import time

    # Local import avoids import cycles with FileIndex.query → rank_paths.
    from kageha.project.file_index import score_path

    lim = max(1, min(int(limit), 500))
    now = time.time()
    scored: list[tuple[float, str]] = []
    for path, mtime in entries:
        s = score_path(path, q, mtime=mtime, now=now)
        if s < 0:
            continue
        scored.append((s, path))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [{"path": path, "score": round(score, 3)} for score, path in scored[:lim]]


def rank_paths(
    entries: list[tuple[str, float]],
    q: str = "",
    *,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Rank ``(path, mtime)`` entries; Rust when enabled+present, else Python."""
    lim = max(1, min(int(limit), 500))
    if native_index_enabled():
        rust = _probe_rust()
        if rust is not None:
            try:
                out = rust.search(list(entries), q or "", lim)
                if isinstance(out, list):
                    return out
            except Exception:
                # Never break the WebUI on a native bug — fall through.
                pass
    return _python_rank(entries, q or "", limit=lim)
