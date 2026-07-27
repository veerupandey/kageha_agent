"""Warm headless browser pool for blink-speed page extract.

Backends (``KAGEHA_HEADLESS_BACKEND``):
  auto       — prefer external CDP (Lightpanda/Chrome), else warm Chromium
  http       — no browser (caller should use web_fetch only)
  chromium   — launch once, keep warm Playwright Chromium
  lightpanda — connect to Lightpanda CDP (``lightpanda serve``)
  cdp        — connect to ``KAGEHA_HEADLESS_CDP`` (any CDP endpoint)

Lightpanda (Zig, CDP-compatible) is the fastest local headless when installed:
  lightpanda serve --host 127.0.0.1 --port 9222
  KAGEHA_HEADLESS_BACKEND=lightpanda
  KAGEHA_HEADLESS_CDP=http://127.0.0.1:9222
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


DEFAULT_HEADLESS_CDP = "http://127.0.0.1:9222"


def headless_backend() -> str:
    raw = (os.environ.get("KAGEHA_HEADLESS_BACKEND") or "auto").strip().lower()
    if raw in {"auto", "http", "chromium", "lightpanda", "cdp", "none", "off"}:
        return "http" if raw in {"none", "off"} else raw
    return "auto"


def headless_cdp_endpoint() -> str:
    return (
        os.environ.get("KAGEHA_HEADLESS_CDP")
        or os.environ.get("KAGEHA_COMET_CDP")
        or DEFAULT_HEADLESS_CDP
    ).strip()


def headless_max_pages() -> int:
    raw = (os.environ.get("KAGEHA_HEADLESS_MAX_PAGES") or "4").strip()
    try:
        return max(1, min(8, int(raw)))
    except ValueError:
        return 4


def _require_playwright() -> Any:
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError as e:
        raise ImportError(
            "Headless pool needs Playwright. Run: uv sync --extra browser && "
            "uv run playwright install chromium"
        ) from e
    return async_playwright


@dataclass
class HeadlessPool:
    """Process-wide warm browser for parallel page extracts."""

    backend: str = field(default_factory=headless_backend)
    cdp: str = field(default_factory=headless_cdp_endpoint)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _pw: Any = None
    _browser: Any = None
    _owned: bool = False  # True if we launched Chromium
    _ready: bool = False
    _last_error: str = ""

    async def close(self) -> None:
        async with self._lock:
            await self._close_unlocked()

    async def _close_unlocked(self) -> None:
        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                await self._pw.stop()
        except Exception:
            pass
        self._browser = None
        self._pw = None
        self._owned = False
        self._ready = False

    async def _probe_cdp(self, endpoint: str) -> bool:
        import httpx

        # Accept http://host:port or ws:// — Playwright connect_over_cdp accepts both.
        http_ep = endpoint
        if http_ep.startswith("ws://"):
            http_ep = "http://" + http_ep[len("ws://") :]
        elif http_ep.startswith("wss://"):
            http_ep = "https://" + http_ep[len("wss://") :]
        base = http_ep.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                r = await client.get(f"{base}/json/version")
                return r.status_code == 200
        except Exception:
            return False

    async def ensure(self) -> Any:
        """Return a connected Playwright Browser (warm)."""
        async with self._lock:
            if self._ready and self._browser is not None:
                return self._browser

            backend = headless_backend()
            self.backend = backend
            if backend == "http":
                raise RuntimeError("headless backend=http (no browser pool)")

            async_playwright = _require_playwright()
            pw = await async_playwright().start()
            browser = None
            owned = False
            cdp = headless_cdp_endpoint()
            self.cdp = cdp

            try:
                if backend in {"lightpanda", "cdp"} or (
                    backend == "auto" and await self._probe_cdp(cdp)
                ):
                    browser = await pw.chromium.connect_over_cdp(cdp)
                    owned = False
                    self.backend = "cdp" if backend == "auto" else backend
                else:
                    # Warm Chromium launch (auto fallback or explicit chromium).
                    browser = await pw.chromium.launch(
                        headless=True,
                        args=[
                            "--disable-gpu",
                            "--disable-dev-shm-usage",
                            "--no-first-run",
                            "--disable-background-networking",
                        ],
                    )
                    owned = True
                    self.backend = "chromium"
            except Exception as e:  # noqa: BLE001
                await pw.stop()
                self._last_error = str(e)
                raise RuntimeError(
                    f"Headless pool failed ({backend} @ {cdp}): {e}. "
                    "Install Playwright chromium, or run Lightpanda: "
                    "`lightpanda serve --host 127.0.0.1 --port 9222` "
                    "and set KAGEHA_HEADLESS_BACKEND=lightpanda"
                ) from e

            # Replace any previous session.
            await self._close_unlocked()
            self._pw = pw
            self._browser = browser
            self._owned = owned
            self._ready = True
            self._last_error = ""
            return browser

    async def extract_urls(
        self,
        urls: list[str],
        *,
        max_chars: int = 6000,
        timeout_ms: int = 20000,
    ) -> list[dict[str, str]]:
        """Open URLs in parallel pages on the warm browser; return extracts."""
        clean = [u.strip() for u in urls if u and str(u).strip()][: headless_max_pages()]
        if not clean:
            return []
        browser = await self.ensure()
        # Prefer an existing context (CDP) or create one (launched chromium).
        if browser.contexts:
            context = browser.contexts[0]
            created_context = False
        else:
            context = await browser.new_context()
            created_context = True

        sem = asyncio.Semaphore(headless_max_pages())

        async def one(url: str) -> dict[str, str]:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                return {"url": url, "ok": "false", "error": "unsupported scheme", "text": ""}
            async with sem:
                page = await context.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    title = ""
                    try:
                        title = await page.title()
                    except Exception:
                        pass
                    text = ""
                    try:
                        text = (await page.inner_text("body"))[: max(200, max_chars)]
                    except Exception as e:  # noqa: BLE001
                        return {
                            "url": url,
                            "ok": "false",
                            "error": str(e),
                            "title": title,
                            "text": "",
                            "backend": self.backend,
                        }
                    return {
                        "url": page.url or url,
                        "ok": "true",
                        "title": title,
                        "text": text,
                        "backend": self.backend,
                        "error": "",
                    }
                except Exception as e:  # noqa: BLE001
                    return {
                        "url": url,
                        "ok": "false",
                        "error": str(e),
                        "title": "",
                        "text": "",
                        "backend": self.backend,
                    }
                finally:
                    try:
                        await page.close()
                    except Exception:
                        pass

        try:
            return list(await asyncio.gather(*[one(u) for u in clean]))
        finally:
            if created_context:
                try:
                    await context.close()
                except Exception:
                    pass


# Singleton warm pool for the process
_POOL: HeadlessPool | None = None
_POOL_LOCK = asyncio.Lock()


async def get_pool() -> HeadlessPool:
    global _POOL
    async with _POOL_LOCK:
        if _POOL is None:
            _POOL = HeadlessPool()
        return _POOL


async def shutdown_pool() -> None:
    global _POOL
    async with _POOL_LOCK:
        if _POOL is not None:
            await _POOL.close()
            _POOL = None
