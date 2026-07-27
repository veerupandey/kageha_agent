"""Import real product packshots + social reference frames (ReelAI shell / Comet)."""

from __future__ import annotations

import base64
import html as html_lib
import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

from kageha.harness.tools.base import ToolRegistry, tool
from kageha.harness.tools.paths import rel_to_workspace

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext

SHELL = os.environ.get("KAGEHA_REELAI_SHELL", "http://localhost:3721")
COMET_CDP = os.environ.get("KAGEHA_COMET_CDP", "http://127.0.0.1:9222")
COMET_PROFILE = os.path.expanduser(
    os.environ.get(
        "KAGEHA_COMET_PROFILE",
        "~/Library/Application Support/Comet",
    )
)


def register_product_import_tools(ctx: "HarnessContext") -> ToolRegistry:
    reg = ToolRegistry()

    @tool(
        description=(
            "Download real product gallery images from a Shopify/product URL into "
            "artifacts/product/. Prefer this over generating fake packaging."
        )
    )
    async def import_product_images(product_url: str, max_images: int = 8) -> str:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True, headers=headers) as client:
            # Prefer Jina markdown (same as ReelAI) then page HTML
            urls: list[str] = []
            try:
                jina = await client.get(f"https://r.jina.ai/{product_url}")
                if jina.status_code == 200:
                    urls.extend(_extract_image_urls(jina.text))
            except Exception:
                pass
            page = await client.get(product_url)
            page.raise_for_status()
            urls.extend(_extract_image_urls(page.text))
            # Dedupe + prefer product-named assets
            urls = _rank_product_urls(urls, product_url)
            urls = urls[: max(1, min(int(max_images), 16))]
            out_dir = ctx.workspace.path("artifacts/product")
            out_dir.mkdir(parents=True, exist_ok=True)
            saved = []
            for i, u in enumerate(urls):
                try:
                    img = await client.get(u)
                    img.raise_for_status()
                    ext = _ext_from_url(u)
                    dest = out_dir / f"product_{i:02d}{ext}"
                    dest.write_bytes(img.content)
                    saved.append(
                        {
                            "path": rel_to_workspace(dest, ctx.workspace.root),
                            "url": u,
                            "bytes": len(img.content),
                        }
                    )
                except Exception as e:  # noqa: BLE001
                    saved.append({"url": u, "error": str(e)})
        return json.dumps({"count": len(saved), "images": saved}, indent=2)

    @tool(
        description=(
            "Capture Instagram/TikTok reference frames via ReelAI agent shell "
            "(POST /screenshot-carousel on localhost:3721). Uses GraphQL/embed — "
            "works without login for public posts."
        )
    )
    async def capture_social_reference(url: str, max_slides: int = 8) -> str:
        async with httpx.AsyncClient(timeout=90.0) as client:
            try:
                health = await client.get(f"{SHELL}/health")
                health.raise_for_status()
            except Exception as e:  # noqa: BLE001
                return (
                    f"ERROR: ReelAI shell not reachable at {SHELL} ({e}). "
                    "Start it with: cd ReelAI && npm run agent:server"
                )
            resp = await client.post(
                f"{SHELL}/screenshot-carousel",
                json={"url": url, "maxSlides": int(max_slides)},
            )
            resp.raise_for_status()
            data = resp.json()
        out_dir = ctx.workspace.path("artifacts/reference")
        out_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for i, shot in enumerate(data.get("screenshots") or []):
            raw = base64.b64decode(shot.get("base64") or "")
            mime = shot.get("mimeType") or "image/jpeg"
            ext = ".jpg" if "jpeg" in mime else ".png"
            dest = out_dir / f"ref_{i+1}{ext}"
            dest.write_bytes(raw)
            saved.append(
                {
                    "path": rel_to_workspace(dest, ctx.workspace.root),
                    "label": shot.get("label"),
                    "bytes": len(raw),
                }
            )
        return json.dumps(
            {
                "source": data.get("source"),
                "kind": data.get("kind"),
                "count": len(saved),
                "warning": data.get("warning"),
                "frames": saved,
            },
            indent=2,
        )

    @tool(
        description=(
            "Browse a URL with logged-in Comet/Chrome via CDP in a new tab "
            "(does not navigate the user's active tab), screenshot, then close that tab. "
            "Requires Comet started with remote debugging, e.g. "
            "`open -a Comet --args --remote-debugging-port=9222`. "
            "Falls back to persistent Comet profile if CDP unavailable (Comet must be closed)."
        ),
        risk_class="browser",
    )
    async def browse_logged_in(url: str, filename: str = "artifacts/logged_in.png") -> str:
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            return f"ERROR: playwright not installed ({e})"

        dest = ctx.workspace.path(filename)
        dest.parent.mkdir(parents=True, exist_ok=True)
        text_preview = ""
        mode = ""

        async with async_playwright() as pw:
            browser = None
            context = None
            # 1) CDP to running Comet/Chrome
            try:
                browser = await pw.chromium.connect_over_cdp(COMET_CDP)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                mode = f"cdp:{COMET_CDP}"
            except Exception:
                browser = None
                context = None

            # 2) Persistent Comet profile (only if CDP failed)
            if context is None:
                profile = Path(COMET_PROFILE)
                if not profile.is_dir():
                    return (
                        f"ERROR: Cannot reach Comet CDP at {COMET_CDP} and profile missing "
                        f"at {profile}. Start Comet with --remote-debugging-port=9222 "
                        "while logged into the requested site."
                    )
                try:
                    context = await pw.chromium.launch_persistent_context(
                        user_data_dir=str(profile),
                        channel="chrome",
                        headless=False,
                        args=["--disable-blink-features=AutomationControlled"],
                    )
                    mode = f"persistent:{profile}"
                    browser = None
                except Exception as e:  # noqa: BLE001
                    return (
                        f"ERROR: CDP failed and persistent Comet profile locked/unavailable ({e}). "
                        "Quit Comet, or relaunch with --remote-debugging-port=9222."
                    )

            # Always open a dedicated tab — never navigate the user's active page.
            page = await context.new_page()
            try:
                try:
                    await page.bring_to_front()
                except Exception:
                    pass
                await page.goto(url, wait_until="domcontentloaded", timeout=90000)
                await page.wait_for_timeout(2500)
                title = await page.title()
                try:
                    text_preview = (await page.inner_text("body"))[:4000]
                except Exception:
                    text_preview = ""
                await page.screenshot(path=str(dest), full_page=False)
            finally:
                # One-shot helper: close the agent tab; leave the user's Comet alone.
                if mode.startswith("cdp"):
                    try:
                        if not page.is_closed():
                            await page.close()
                    except Exception:
                        pass
            # Don't close CDP browser (user's Comet); close only if we launched persistent
            if mode.startswith("persistent"):
                await context.close()

        return json.dumps(
            {
                "mode": mode,
                "title": title,
                "path": str(dest.relative_to(ctx.workspace.root)),
                "preview": text_preview[:1500],
            }
        )

    for t in (import_product_images, capture_social_reference, browse_logged_in):
        if hasattr(t, "name"):
            reg.register(t)  # type: ignore[arg-type]
    return reg


def _extract_image_urls(text: str) -> list[str]:
    found = re.findall(r"https://[^\s\"'<>]+", text)
    out: list[str] = []
    for u in found:
        u = html_lib.unescape(u).split(" ")[0].rstrip(").,]")
        u = u.split("&amp;")[0]
        low = u.lower()
        if not any(x in low for x in (".jpg", ".jpeg", ".png", ".webp")):
            continue
        if any(x in low for x in ("logo", "icon", "sprite", "favicon", "payment")):
            continue
        # Strip width variants to base when possible
        if "cdn.shopify.com" in u:
            u = u.split("?")[0] + (("?" + u.split("?", 1)[1].split("&")[0]) if "?" in u and "v=" in u else "")
            # Prefer clean versioned URL without width
            if "width=" in u:
                base = u.split("?")[0]
                m = re.search(r"[?&]v=([^&]+)", u)
                u = f"{base}?v={m.group(1)}" if m else base
        if u not in out:
            out.append(u)
    return out


def _rank_product_urls(urls: list[str], product_url: str) -> list[str]:
    slug = urlparse(product_url).path.rstrip("/").split("/")[-1].lower()
    tokens = [t for t in re.split(r"[-_]", slug) if len(t) > 2]
    # Prefer high-res variants
    preferred = []
    for u in urls:
        if "width=1600" in u or "width=900" in u:
            preferred.append(u)
        else:
            preferred.append(u)
    urls = preferred

    def score(u: str) -> tuple[int, int]:
        low = u.lower()
        s = 0
        if "cdn.shopify.com" in low:
            s += 5
        for t in tokens:
            if t in low:
                s += 4
        # Strong boost when filename contains the product slug tokens together
        if "classic" in low and "ceremonial" in low:
            s += 8
        if any(x in low for x in ("b5251891", "view-1", "packshot", "hero")):
            s += 3
        # Deprioritize other SKUs / wrong product lines
        if any(x in low for x in ("roasted", "barista-grade", "signature")):
            s -= 10
        if any(x in low for x in ("why-", "taste-profile", "focus-calm", "not-all")):
            s -= 2  # infographics ok later, not first
        if "width=1600" in low:
            s += 2
        if "width=200" in low:
            s -= 5
        return (-s, len(u))

    # Dedupe by basename without width
    seen: set[str] = set()
    ranked = []
    for u in sorted(urls, key=score):
        base = re.sub(r"[?&]width=\d+", "", u)
        base = re.sub(r"[?&]format=\w+", "", base)
        key = base.split("/")[-1].split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        ranked.append(u)
    return ranked


def _ext_from_url(u: str) -> str:
    path = urlparse(u).path.lower()
    for ext in (".png", ".webp", ".jpeg", ".jpg"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"
