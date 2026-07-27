"""High-performance browser control for Kageha agents.

Design goals:
  1. Tiered I/O — web_fetch (HTTP) before launching Chromium.
  2. AX-tree snapshots with stable refs.
  3. Persistent multi-tab session + lock (no cold relaunch per step).
  4. Cheap act loop — snapshot without mandatory screenshots.
  5. Escape hatches — JS evaluate + raw CDP.

The hot path stays Playwright (mature CDP client). A Rust sidecar can later
implement the same BrowserEngine protocol if protocol parsing becomes the bottleneck.
"""

from __future__ import annotations

from kageha.harness.browser.engine import BrowserEngine, resolve_browser_mode, resolve_cdp_endpoint
from kageha.harness.browser.fetch import fetch_url
from kageha.harness.browser.prefs import apply_browser_prefs, load_browser_prefs, set_backend

__all__ = [
    "BrowserEngine",
    "fetch_url",
    "resolve_browser_mode",
    "resolve_cdp_endpoint",
    "apply_browser_prefs",
    "load_browser_prefs",
    "set_backend",
]
