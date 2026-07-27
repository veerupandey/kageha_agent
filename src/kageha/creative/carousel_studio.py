"""Carousel image studio library for make_social_carousel skill scripts.

Lives under ``kageha.creative`` (not harness tools). Call via skill_run:
  scripts/write_prompts.py / scripts/generate_slide.py
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

from kageha.config import env_key
from kageha.harness.tools.paths import rel_to_workspace

DEFAULT_MODEL = os.environ.get("KAGEHA_CAROUSEL_PROMPT_MODEL", "gemini-2.5-flash")
GEMINI_BASE = os.environ.get(
    "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
)
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_MAX_OUTPUT_TOKENS = 16384

# Instagram / social canvas sizes used in generation hints + prompt locks
_ASPECT_PIXELS: dict[str, tuple[int, int]] = {
    "4:5": (1080, 1350),
    "1:1": (1080, 1080),
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "3:4": (1080, 1440),
    "4:3": (1440, 1080),
    "2:3": (1080, 1620),
    "3:2": (1620, 1080),
    "5:4": (1350, 1080),
}

_PRODUCT_HINT_RE = re.compile(
    r"\b(product|pack(?:aging|shot)?|tin|sku|label|bottle|jar|box|pouch|"
    r"brand|shopify|merch|hero|cover)\b",
    re.I,
)
_PRODUCT_SLIDE_RE = re.compile(
    r"\b(product|pack(?:aging|shot)?|tin|sku|label|bottle|jar|box|pouch|"
    r"hero|cover|packshot)\b",
    re.I,
)


def _aspect_pixels(aspect_ratio: str) -> tuple[int, int]:
    ar = (aspect_ratio or "4:5").strip()
    return _ASPECT_PIXELS.get(ar, (1080, 1350))


def _normalize_product_image_path(path_str: str, root: Path) -> str:
    """Normalize productImagePath to a workspace-relative posix string."""
    pip = (path_str or "").strip().replace("\\", "/")
    if not pip or ".." in pip:
        return ""
    p = Path(pip)
    if p.is_absolute():
        return rel_to_workspace(p, root)
    return pip


def _resolve_existing_product_path(
    path_str: str,
    *,
    root: Path,
    product_dir: Path,
    allowed_names: set[str],
) -> str:
    """Keep only model-provided paths that exist under product_dir."""
    pip = _normalize_product_image_path(path_str, root)
    if not pip:
        return ""
    candidate = (root / pip).resolve()
    try:
        candidate.relative_to(product_dir.resolve())
    except ValueError:
        # Allow bare filename that lives in product_dir
        by_name = product_dir / Path(pip).name
        if by_name.is_file() and by_name.name in allowed_names:
            return rel_to_workspace(by_name, root)
        return ""
    if not candidate.is_file():
        by_name = product_dir / Path(pip).name
        if by_name.is_file() and by_name.name in allowed_names:
            return rel_to_workspace(by_name, root)
        return ""
    if candidate.name not in allowed_names and allowed_names:
        # Still accept if file exists under product_dir (extra files ok)
        pass
    return rel_to_workspace(candidate, root)


def _is_product_carousel(
    instruction: str, product_url: str, *, n_products: int
) -> bool:
    """True when this run must lock real product packshots."""
    if n_products <= 0:
        return False
    if (product_url or "").strip():
        return True
    return bool(_PRODUCT_HINT_RE.search(instruction or ""))


def _slide_needs_product_lock(slide: dict[str, Any]) -> bool:
    """Heuristic: product-facing / cover / packaging slides must lock packshots."""
    role = str(slide.get("role") or "").strip().lower()
    if role == "hook":
        return True
    blob = " ".join(
        [
            str(slide.get("title") or ""),
            str(slide.get("prompt") or ""),
            str(slide.get("designNotes") or ""),
            json.dumps(slide.get("copy") or {}),
        ]
    )
    return bool(_PRODUCT_SLIDE_RE.search(blob))


def _repair_canva_copy_spacing(
    canva_text: dict[str, Any] | None, copy: dict[str, Any] | None
) -> dict[str, Any]:
    """Sync obvious canvaText vs copy spacing mismatches; normalize whitespace."""
    ct = dict(canva_text) if isinstance(canva_text, dict) else {}
    cp = copy if isinstance(copy, dict) else {}

    def _norm(s: Any) -> str:
        return re.sub(r"\s+", " ", str(s or "")).strip()

    heading = _norm(ct.get("heading"))
    headline = _norm(cp.get("headline"))
    if headline:
        mashed_heading = heading.replace(" ", "").lower()
        mashed_headline = headline.replace(" ", "").lower()
        # Prefer spaced headline when heading is mashed / missing spaces
        if mashed_heading and mashed_heading == mashed_headline:
            if (" " not in heading and " " in headline) or heading != headline:
                heading = headline
        elif not heading:
            heading = headline
    if heading:
        ct["heading"] = heading

    for key, copy_key in (
        ("subtext", "supportingText"),
        ("bodyText", "supportingText"),
        ("footer", "caption"),
    ):
        val = _norm(ct.get(key))
        alt = _norm(cp.get(copy_key))
        if alt and val:
            if val.replace(" ", "").lower() == alt.replace(" ", "").lower():
                if " " not in val and " " in alt:
                    val = alt
        elif alt and not val and key in ("subtext", "bodyText"):
            val = alt
        if val:
            ct[key] = val
        elif key in ct:
            ct[key] = _norm(ct.get(key))
    return ct


def _inject_format_lock(prompt: str, *, aspect_ratio: str, width: int, height: int) -> str:
    """Ensure every slide prompt states canvas format (hard lock)."""
    p = (prompt or "").strip()
    lock = (
        f"FORMAT LOCK (mandatory): Instagram portrait canvas {aspect_ratio} "
        f"exactly {width}×{height}px. Do not output square or story unless specified. "
        "Full-bleed or cream-card layout must fill this frame with correct safe margins."
    )
    if f"{width}×{height}" in p or f"{width}x{height}" in p.lower():
        if aspect_ratio in p:
            return p
    return f"{p}\n\n{lock}".strip()


def _parse_json_blob(text: str) -> dict[str, Any] | None:
    t = (text or "").strip()
    if not t:
        return None
    try:
        data = json.loads(t)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", t)
    if m:
        try:
            data = json.loads(m.group(1))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
    start, end = t.find("{"), t.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(t[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
    return None


def _load_folder_images(
    folder: Path, *, limit: int = 10, label: str = "image"
) -> list[dict[str, str]]:
    """Load image files from a folder as Gemini inlineData payloads."""
    if not folder.is_dir():
        return []
    files = sorted(
        [p for p in folder.iterdir() if p.suffix.lower() in _IMAGE_EXTS and p.is_file()],
        key=lambda p: p.name,
    )[:limit]
    out: list[dict[str, str]] = []
    for p in files:
        mime = {
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(p.suffix.lower(), "image/jpeg")
        out.append(
            {
                "mimeType": mime,
                "data": base64.b64encode(p.read_bytes()).decode("ascii"),
                "name": p.name,
                "relpath": f"{label}/{p.name}",
                "kind": label,
            }
        )
    return out


async def _fetch_url_research(url: str, *, max_chars: int = 6000) -> str:
    """Fetch brand/product page text via Jina (ReelAI-style research)."""
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return ""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; KagehaCarousel/1.0)"}
    try:
        async with httpx.AsyncClient(
            timeout=45.0, follow_redirects=True, headers=headers
        ) as client:
            resp = await client.get(f"https://r.jina.ai/{u}")
            if resp.status_code >= 400 or not (resp.text or "").strip():
                return ""
            text = re.sub(r"\s+", " ", resp.text).strip()
            return text[:max_chars]
    except Exception:  # noqa: BLE001
        return ""


def _system_prompt(
    *,
    instruction: str,
    slide_count: int,
    aspect_ratio: str,
    brand_url: str,
    product_url: str,
    reference_url: str,
    n_refs: int,
    n_products: int,
    product_names: list[str],
    research_notes: str,
    use_web_search: bool,
) -> str:
    is_carousel = slide_count > 1
    format_desc = (
        f"a {slide_count}-slide Instagram carousel"
        if is_carousel
        else "a single Instagram post image"
    )
    width, height = _aspect_pixels(aspect_ratio)
    ref_block = ""
    if n_refs:
        ref_block = f"""
## REFERENCE POST SCREENSHOTS — HARD RECREATE (CRITICAL)
{n_refs} screenshot(s) of a reference Instagram carousel/post are attached (kind=reference).
For EACH frame, extract and record in referenceAnalysis:
- exact on-slide text OR explicitly "no text"
- text placement / hierarchy (top title, center body, bottom footer)
- layout type (full-bleed photo, cream card, split, illustration+type, etc.)
- image content / illustration style / colors
- slide purpose (hook / tip / comparison / proof / CTA)

Then RECREATE the SAME slide flow for the target brand — HARD REQUIREMENTS:
- Match slide COUNT exactly ({slide_count} unless user overrode) — never fewer
- Match text-vs-no-text per slide index (no-text ref → empty canvaText)
- Match structure/hierarchy/margins — not stolen logos
- Rewrite ALL copy in the target brand voice — never paste Instagram text verbatim
- Keep the same design system across every slide (palette, type, margins)
"""
    product_block = ""
    if n_products:
        names = ", ".join(product_names[:8]) or "product_*.jpg"
        first = product_names[0] if product_names else "product_00.jpg"
        product_block = f"""
## REAL PRODUCT IMAGES — HARD PRODUCT TRUTH (CRITICAL)
{n_products} real product packshot(s) are attached (kind=product): {names}
- NEVER invent packaging, tin artwork, labels, or logo marks
- Downstream generation uses Gemini Nano Banana Pro WITH the packshot attached via
  gemini_generate_image(image_path=productImagePath) — the model MUST see the real still
- You MUST set productImagePath on EVERY product-facing slide (hook/cover/hero + any
  slide showing packaging/tin/label). Use workspace paths like:
  artifacts/product/{first}
- Only omit productImagePath on pure illustration / typography-only value slides that
  do not show the physical product
- Describe lighting/composition around the REAL product look — not a fantasy SKU
"""
    research_block = ""
    if research_notes.strip():
        research_block = f"""
## RESEARCH NOTES (fetched from brand/product pages — ReelAI Image Studio research)
Brand URL: {brand_url or '(none)'}
Product URL: {product_url or brand_url or '(none)'}
Reference URL: {reference_url or '(none)'}

Extract brand voice, palette cues, product names/SKU claims, and category norms.
Do not invent fake citations. Fold findings into researchSummary + brandExecutionSummary.

--- BEGIN FETCHED NOTES ---
{research_notes.strip()[:5500]}
--- END FETCHED NOTES ---
"""
    elif use_web_search:
        research_block = f"""
## RESEARCH (Google Search grounding + your knowledge)
- Brand site: {brand_url or '(infer from instruction)'}
- Product URL: {product_url or '(none)'}
- Reference post URL: {reference_url or '(none)'}
Research brand voice, palette cues, product facts, and category norms.
Do not invent fake citations. Summarize in researchSummary.
"""
    return f"""You are the ReelAI Image Studio Prompt Writer — expert creative director and
prompt engineer for production Instagram carousels.

Job: research + product truth + reference recreate → detailed prompts for {format_desc}.
Downstream slides are generated with **Gemini Nano Banana Pro**
(`gemini_generate_image` model=nano-banana-pro → gemini-3-pro-image).
Write prompts that model can render cleanly (clear layout, exact text, high contrast).

## USER INSTRUCTION
{instruction}

{ref_block}
{product_block}
{research_block}

## STORY ARC (ReelAI Image Studio)
- Slide 1 = HOOK — scroll-stopping claim / curiosity / bold visual
- Slides 2…{max(1, slide_count - 1)} = VALUE — one tip, myth, step, or proof each
- Slide {slide_count} = CTA — save / follow / shop / clear next action
- When recreating a reference, prefer the reference's arc over this default if it differs
- Headlines: 3–8 words. Body: ≤ ~30 words. Mobile-first.

## DESIGN SYSTEM (one document, not N random posts)
Lock across every slide: palette (hex), background, illustration style, type hierarchy,
margins/safe zones, grid, lighting/texture language.

## FORMAT LOCK (HARD — every slide)
- Aspect ratio: {aspect_ratio}
- Pixel canvas: {width}×{height} (Instagram portrait default is 4:5 / 1080×1350)
- Every `prompt` MUST state "{aspect_ratio}" and "{width}×{height}"
- Do not invent square (1:1) or story (9:16) unless the user asked

## OUTPUT REQUIREMENTS
- Generate EXACTLY {slide_count} slides (never fewer)
- Each `prompt`: 200–500 words, standalone, production-ready for Nano Banana Pro
- Include composition, background, subject, lighting, color hexes, EXACT on-slide text
  with placement, font style, and "crisp legible typography, no misspellings"
- Fill `canvaText` with EXACT strings (empty strings if reference slide has no text);
  spacing in canvaText.heading MUST match copy.headline
- Include `negativePrompt` + `designNotes` + `productImagePath` when locking packaging
- Negatives: watermark, logo mashups, tiny unreadable text, cluttered collage,
  invented packaging, fake labels, random QR codes, wrong aspect ratio

Return ONLY JSON (no markdown fences):
{{
  "creativeDirection": "...",
  "brandExecutionSummary": "...",
  "referenceAnalysis": "... or null",
  "researchSummary": "... or null",
  "productTruthSummary": "... or null",
  "designSystem": {{
    "palette": ["#…"],
    "typography": "...",
    "margins": "...",
    "illustrationStyle": "..."
  }},
  "generationHint": "gemini_generate_image model=nano-banana-pro aspect_ratio={aspect_ratio}",
  "slides": [
    {{
      "slideNumber": 1,
      "role": "hook|value|cta",
      "title": "...",
      "prompt": "full image prompt for Nano Banana Pro including {aspect_ratio} {width}×{height}...",
      "negativePrompt": "...",
      "copy": {{"headline": "...", "supportingText": "...", "caption": "..."}},
      "designNotes": "...",
      "productImagePath": "artifacts/product/… or null",
      "canvaText": {{"heading": "...", "subtext": "...", "bodyText": "...", "footer": "1/{slide_count}"}}
    }}
  ]
}}
"""


def _normalize_slides(
    raw_slides: list[Any],
    *,
    n: int,
    root: Path,
    product_dir: Path,
    prod_names: list[str],
    aspect_ratio: str,
    width: int,
    height: int,
    force_product_lock: bool,
) -> list[dict[str, Any]]:
    """Parse, validate product paths, repair copy, inject format — no invented paths."""
    allowed = set(prod_names)
    default_product = (
        rel_to_workspace(product_dir / prod_names[0], root) if prod_names else ""
    )
    slides: list[dict[str, Any]] = []
    for idx, s in enumerate(raw_slides):
        if not isinstance(s, dict) or not str(s.get("prompt") or "").strip():
            continue
        slide_no = int(s.get("slideNumber") or idx + 1)
        role = str(s.get("role") or "").strip().lower()
        if role not in {"hook", "value", "cta"}:
            if slide_no == 1:
                role = "hook"
            elif len(slides) + 1 >= n:
                role = "cta"
            else:
                role = "value"
        copy = s.get("copy") if isinstance(s.get("copy"), dict) else {}
        canva = _repair_canva_copy_spacing(
            s.get("canvaText") if isinstance(s.get("canvaText"), dict) else {},
            copy,
        )
        pip = _resolve_existing_product_path(
            str(s.get("productImagePath") or ""),
            root=root,
            product_dir=product_dir,
            allowed_names=allowed,
        )
        draft = {
            "slideNumber": slide_no,
            "role": role,
            "title": str(s.get("title") or f"Slide {idx + 1}").strip(),
            "prompt": str(s["prompt"]).strip(),
            "negativePrompt": (
                str(s["negativePrompt"]).strip() if s.get("negativePrompt") else None
            ),
            "copy": copy,
            "designNotes": str(s.get("designNotes") or "").strip(),
            "productImagePath": pip or None,
            "canvaText": canva,
        }
        # HARD assign real packshots when products exist and slide is product-facing.
        # Never invent paths when product_dir is empty.
        if not pip and default_product and _slide_needs_product_lock(draft):
            draft["productImagePath"] = default_product
        draft["prompt"] = _inject_format_lock(
            draft["prompt"],
            aspect_ratio=aspect_ratio,
            width=width,
            height=height,
        )
        slides.append(draft)
        if len(slides) >= n:
            break
    if slides:
        if len(slides) == 1:
            slides[0]["role"] = "hook"
        else:
            slides[0]["role"] = "hook"
            slides[-1]["role"] = "cta"
        # Product carousel: hook always locks first packshot when products exist
        if force_product_lock and default_product and not slides[0].get("productImagePath"):
            slides[0]["productImagePath"] = default_product
    return slides


def _load_carousel_prompts(root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Load carousel/prompts.json; return (data, error)."""
    path = root / "carousel" / "prompts.json"
    if not path.is_file():
        return None, (
            "ERROR: carousel/prompts.json missing. Run write_prompts.py first."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return None, f"ERROR: cannot read carousel/prompts.json: {e}"
    if not isinstance(data, dict):
        return None, "ERROR: carousel/prompts.json must be a JSON object"
    return data, None


def _find_slide(prompts: dict[str, Any], slide_number: int) -> dict[str, Any] | None:
    slides = prompts.get("slides")
    if not isinstance(slides, list):
        return None
    for s in slides:
        if not isinstance(s, dict):
            continue
        try:
            n = int(s.get("slideNumber") or 0)
        except (TypeError, ValueError):
            continue
        if n == slide_number:
            return s
    # Fallback: 1-based index into list
    if 1 <= slide_number <= len(slides):
        s = slides[slide_number - 1]
        return s if isinstance(s, dict) else None
    return None


def _slide_aspect_ratio(prompts: dict[str, Any], slide: dict[str, Any]) -> str:
    """Aspect from slide, else prompts.json top-level, else 4:5."""
    for key in ("aspectRatio", "aspect_ratio"):
        raw = slide.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    for key in ("aspectRatio", "aspect_ratio"):
        raw = prompts.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return "4:5"


def _packshots_present(prompts: dict[str, Any], root: Path) -> bool:
    imgs = prompts.get("productImages")
    if isinstance(imgs, list) and any(str(x or "").strip() for x in imgs):
        return True
    if prompts.get("productLockRequired"):
        prod = root / "artifacts" / "product"
        if prod.is_dir() and any(
            p.is_file() and p.suffix.lower() in _IMAGE_EXTS for p in prod.iterdir()
        ):
            return True
    return False


def plan_carousel_slide_generation(
    prompts: dict[str, Any],
    slide_number: int,
    *,
    root: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve generation args for one slide, or an ERROR string.

    HARD RULES:
    - Always use aspect_ratio from prompts.json (default 4:5)
    - If productImagePath set OR (product carousel with packshots + hook/product
      role), image_path is required and the file must exist
    """
    if slide_number < 1:
        return None, f"ERROR: slide_number must be >= 1 (got {slide_number})"
    slide = _find_slide(prompts, slide_number)
    if slide is None:
        return None, (
            f"ERROR: slide {slide_number} not found in carousel/prompts.json"
        )
    prompt = str(slide.get("prompt") or "").strip()
    if not prompt:
        return None, f"ERROR: slide {slide_number} has empty prompt"

    ar = _slide_aspect_ratio(prompts, slide)
    pip = str(slide.get("productImagePath") or "").strip().replace("\\", "/")
    if pip and ".." in pip:
        return None, f"ERROR: invalid productImagePath on slide {slide_number}: {pip}"

    needs_product = bool(pip) or (
        _packshots_present(prompts, root) and _slide_needs_product_lock(slide)
    )
    if needs_product:
        if not pip:
            return None, (
                f"ERROR: slide {slide_number} requires image_path (hook/product role "
                "with packshots present) but productImagePath is missing. Re-run "
                "write_prompts.py so productImagePath is set, then retry "
                "generate_slide.py."
            )
        src = (root / pip).resolve()
        try:
            src.relative_to(root.resolve())
        except ValueError:
            return None, (
                f"ERROR: productImagePath escapes workspace on slide {slide_number}: "
                f"{pip}"
            )
        if not src.is_file():
            return None, (
                f"ERROR: productImagePath missing on disk for slide {slide_number}: "
                f"{pip}. Import packshots (import_product_images) or fix the path "
                "in carousel/prompts.json."
            )

    nn = f"{slide_number:02d}"
    return (
        {
            "slideNumber": slide_number,
            "prompt": prompt,
            "aspect_ratio": ar,
            "image_path": pip or "",
            "filename": f"carousel/slide_{nn}.jpg",
            "model": "nano-banana-pro",
            "role": str(slide.get("role") or ""),
            "productLocked": bool(pip),
        },
        None,
    )


def _workspace_ctx(root: Path):
    """Minimal harness context bound to an existing workspace root."""
    from kageha.harness.approvals import ApprovalGate
    from kageha.harness.runtime import HarnessContext
    from kageha.harness.sandbox import SessionWorkspace
    from kageha.models.registry import ModelRegistry
    from kageha.models.router import ModelRouter

    ws = SessionWorkspace(run_id=root.name or "skill", root=Path(root).resolve())
    return HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
    )


async def _invoke_gemini_generate_image(root: Path, **kwargs: Any) -> str:
    """Call gemini_generate_image via the shared media tool pack."""
    from kageha.harness.tools.media import register_media_tools

    ctx = _workspace_ctx(root)
    media = register_media_tools(ctx)
    gemini = media.get("gemini_generate_image")
    if gemini is None:
        return "ERROR: gemini_generate_image tool unavailable"
    return await gemini.call(**kwargs)


async def _gemini_generate_json(
    *,
    api_key: str,
    model_id: str,
    parts: list[dict[str, Any]],
    use_web_search: bool,
    max_output_tokens: int = _MAX_OUTPUT_TOKENS,
) -> tuple[dict[str, Any] | None, str, str | None]:
    """Call Gemini; return (parsed_dict_or_none, raw_text, error_or_none)."""
    tools_cfg: list[dict[str, Any]] = []
    if use_web_search:
        tools_cfg.append({"google_search": {}})

    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": max_output_tokens,
        },
    }
    if tools_cfg:
        payload["tools"] = tools_cfg

    url = f"{GEMINI_BASE}/models/{model_id}:generateContent"
    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code == 400 and tools_cfg:
            payload.pop("tools", None)
            resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            return (
                None,
                "",
                f"ERROR: Gemini {resp.status_code}: "
                f"{(resp.text or '')[:600].replace(chr(10), ' ')}",
            )
        data = resp.json()

    candidates = data.get("candidates") or [{}]
    parts_out = ((candidates[0].get("content") or {}).get("parts")) or []
    text = "".join(
        (p.get("text") or "")
        for p in parts_out
        if p.get("text") and not p.get("thought")
    )
    parsed = _parse_json_blob(text)
    return parsed, text, None


async def write_prompts(
    root: Path,
    *,
    instruction: str,
    slide_count: int = 6,
    reference_dir: str = "artifacts/reference",
    product_dir: str = "artifacts/product",
    brand_url: str = "",
    product_url: str = "",
    reference_url: str = "",
    aspect_ratio: str = "4:5",
    use_web_search: bool = True,
    model: str = "",
) -> str:
    """Write carousel/prompts.json (+ research.md, prompts.md). Skill script entry."""
    api_key = env_key("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return "ERROR: GEMINI_API_KEY not configured"

    root = Path(root).resolve()
    n = max(1, min(int(slide_count or 6), 10))
    ar = (aspect_ratio or "4:5").strip() or "4:5"
    width, height = _aspect_pixels(ar)
    ref_path = root / (reference_dir or "artifacts/reference")
    prod_path = root / (product_dir or "artifacts/product")
    refs = _load_folder_images(ref_path, limit=10, label="reference")
    products = _load_folder_images(prod_path, limit=8, label="product")
    if refs and (n == 6 or n == len(refs)):
        n = len(refs)

    research_chunks: list[str] = []
    fetch_urls: list[str] = []
    for u in (brand_url, product_url):
        u = (u or "").strip()
        if u and u not in fetch_urls:
            fetch_urls.append(u)
    for u in fetch_urls:
        note = await _fetch_url_research(u)
        if note:
            research_chunks.append(f"### Source: {u}\n{note}")
    research_notes = "\n\n".join(research_chunks)

    model_id = (model or DEFAULT_MODEL).strip()
    prod_names = [p["name"] for p in products]
    force_product = _is_product_carousel(
        instruction, product_url or "", n_products=len(products)
    )
    sys_text = _system_prompt(
        instruction=instruction,
        slide_count=n,
        aspect_ratio=ar,
        brand_url=brand_url or "",
        product_url=product_url or "",
        reference_url=reference_url or "",
        n_refs=len(refs),
        n_products=len(products),
        product_names=prod_names,
        research_notes=research_notes,
        use_web_search=bool(use_web_search),
    )

    parts: list[dict[str, Any]] = []
    if refs:
        parts.append(
            {
                "text": (
                    f"[REFERENCE FRAMES — {len(refs)} images follow; "
                    "HARD recreate: match count, text-vs-no-text, layout roles]"
                )
            }
        )
        for img in refs:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": img["mimeType"],
                        "data": img["data"],
                    }
                }
            )
            parts.append({"text": f"(reference frame: {img['name']})"})
    if products:
        parts.append(
            {
                "text": (
                    f"[REAL PRODUCT PACKSHOTS — {len(products)} images follow; "
                    "HARD product truth: set productImagePath; never invent packaging]"
                )
            }
        )
        for img in products:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": img["mimeType"],
                        "data": img["data"],
                    }
                }
            )
            parts.append(
                {
                    "text": (
                        f"(product image: artifacts/product/{img['name']} — "
                        "REQUIRED productImagePath on hook/product slides)"
                    )
                }
            )
    parts.append({"text": sys_text})

    parsed, text, err = await _gemini_generate_json(
        api_key=api_key,
        model_id=model_id,
        parts=parts,
        use_web_search=bool(use_web_search),
    )
    if err:
        return err
    if not parsed or not isinstance(parsed.get("slides"), list):
        return (
            "ERROR: prompt writer returned invalid JSON. "
            f"Raw preview: {text[:500]!r}"
        )

    slides = _normalize_slides(
        parsed["slides"],
        n=n,
        root=root,
        product_dir=prod_path,
        prod_names=prod_names,
        aspect_ratio=ar,
        width=width,
        height=height,
        force_product_lock=force_product,
    )
    if not slides:
        return "ERROR: prompt writer produced no usable slides"

    if len(slides) < n:
        missing = n - len(slides)
        retry_prompt = (
            f"Complete the carousel JSON. You returned {len(slides)} of {n} slides. "
            f"Return ONLY JSON with EXACTLY the {missing} MISSING slides "
            f"(slideNumbers {len(slides) + 1}…{n}), same designSystem/format "
            f"({ar} / {width}×{height}). "
            f"Existing slides titles: {[s['title'] for s in slides]!r}. "
            "Keep productImagePath on product-facing slides when packshots exist."
        )
        retry_parts: list[dict[str, Any]] = [
            {
                "text": (
                    f"Partial carousel so far:\n{json.dumps({'slides': slides}, indent=2)[:6000]}\n\n"
                    f"{retry_prompt}"
                )
            }
        ]
        if products:
            for img in products[:3]:
                retry_parts.append(
                    {
                        "inlineData": {
                            "mimeType": img["mimeType"],
                            "data": img["data"],
                        }
                    }
                )
        parsed2, _text2, err2 = await _gemini_generate_json(
            api_key=api_key,
            model_id=model_id,
            parts=retry_parts,
            use_web_search=False,
            max_output_tokens=8192,
        )
        if not err2 and parsed2 and isinstance(parsed2.get("slides"), list):
            extra = _normalize_slides(
                parsed2["slides"],
                n=missing,
                root=root,
                product_dir=prod_path,
                prod_names=prod_names,
                aspect_ratio=ar,
                width=width,
                height=height,
                force_product_lock=force_product,
            )
            for i, s in enumerate(extra):
                s["slideNumber"] = len(slides) + i + 1
            slides.extend(extra)
            if len(slides) > n:
                slides = slides[:n]
            if len(slides) >= 2:
                slides[0]["role"] = "hook"
                slides[-1]["role"] = "cta"
        if len(slides) < n:
            return (
                f"ERROR: prompt writer returned {len(slides)} of {n} slides "
                f"after retry. Need exactly {n}. Re-run write_prompts.py "
                "with a clearer instruction or lower slide_count."
            )

    locked = [s for s in slides if s.get("productImagePath")]
    if force_product and products and not locked:
        return (
            "ERROR: product images exist under "
            f"{product_dir} ({len(products)} file(s)) but no slide has a valid "
            "productImagePath. Product/brand carousels MUST lock real packshots "
            "via productImagePath for hook/product slides. Re-run after ensuring "
            "import_product_images succeeded."
        )

    design_system = parsed.get("designSystem")
    if not isinstance(design_system, dict):
        design_system = {}

    research_summary = str(parsed.get("researchSummary") or "").strip()
    if research_notes and not research_summary:
        research_summary = (
            "Fetched brand/product page notes were provided to the prompt writer "
            f"from: {', '.join(fetch_urls)}."
        )

    generation_hint = (
        f"skill_run make_social_carousel scripts/generate_slide.py "
        f"--workspace . --slide N → nano-banana-pro aspect_ratio={ar} "
        f"canvas={width}x{height}"
    )
    result = {
        "creativeDirection": str(parsed.get("creativeDirection") or "").strip(),
        "brandExecutionSummary": str(parsed.get("brandExecutionSummary") or "").strip()
        or None,
        "referenceAnalysis": str(parsed.get("referenceAnalysis") or "").strip() or None,
        "researchSummary": research_summary or None,
        "productTruthSummary": str(parsed.get("productTruthSummary") or "").strip()
        or None,
        "designSystem": design_system or None,
        "aspectRatio": ar,
        "canvasPixels": {"width": width, "height": height},
        "generationHint": generation_hint,
        "slideCount": len(slides),
        "referenceFrames": [r["name"] for r in refs],
        "productImages": [
            rel_to_workspace(prod_path / p["name"], root) for p in products
        ],
        "researchSources": fetch_urls,
        "promptWriterModel": model_id,
        "imageModel": "nano-banana-pro",
        "productLockRequired": force_product,
        "slides": slides,
    }

    out_dir = root / "carousel"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "prompts.json"
    md_path = out_dir / "prompts.md"
    research_path = out_dir / "research.md"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    research_md_parts = [
        "# Carousel research (ReelAI Image Studio)",
        "",
        f"**Brand URL:** {brand_url or '_n/a_'}",
        f"**Product URL:** {product_url or '_n/a_'}",
        f"**Reference URL:** {reference_url or '_n/a_'}",
        f"**Format:** {ar} ({width}×{height})",
        f"**Product images:** {len(products)} under `{product_dir}`",
        f"**Reference frames:** {len(refs)} under `{reference_dir}`",
        f"**Product lock required:** {force_product}",
        "",
    ]
    if research_summary:
        research_md_parts += ["## Summary", research_summary, ""]
    if result.get("productTruthSummary"):
        research_md_parts += [
            "## Product truth",
            result["productTruthSummary"] or "",
            "",
        ]
    if research_notes:
        research_md_parts += ["## Fetched page notes", research_notes, ""]
    research_path.write_text(
        "\n".join(research_md_parts).strip() + "\n", encoding="utf-8"
    )

    lines = [
        "# Carousel prompts (ReelAI Image Studio)",
        "",
        f"**Slides:** {len(slides)} · **Aspect:** {ar} ({width}×{height})",
        "**Generate with:** `skill_run make_social_carousel scripts/generate_slide.py "
        f"--workspace . --slide N` · Nano Banana Pro · aspect `{ar}`",
        f"**Product images:** {len(products)} · **Reference frames:** {len(refs)}",
        "",
        f"## Creative direction\n{result['creativeDirection'] or '_n/a_'}",
        "",
    ]
    if result.get("designSystem"):
        lines += [
            "## Design system",
            "```json",
            json.dumps(result["designSystem"], indent=2),
            "```",
            "",
        ]
    if result.get("researchSummary"):
        lines += ["## Research", result["researchSummary"] or "", ""]
    if result.get("productTruthSummary"):
        lines += ["## Product truth", result["productTruthSummary"] or "", ""]
    if result.get("referenceAnalysis"):
        lines += ["## Reference analysis", result["referenceAnalysis"] or "", ""]
    for s in slides:
        role = s.get("role") or "value"
        lines += [
            f"## Slide {s['slideNumber']}: {s['title']} ({role})",
            "",
            "### Prompt",
            s["prompt"],
            "",
        ]
        if s.get("productImagePath"):
            lines += [
                "### Product image (REQUIRED as image_path)",
                s["productImagePath"],
                "",
            ]
        if s.get("negativePrompt"):
            lines += ["### Negative", s["negativePrompt"], ""]
        copy = s.get("copy") or {}
        if copy:
            lines += ["### Copy", json.dumps(copy, indent=2), ""]
    md_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    return json.dumps(
        {
            "ok": True,
            "slideCount": len(slides),
            "aspectRatio": ar,
            "canvasPixels": {"width": width, "height": height},
            "paths": {
                "json": rel_to_workspace(json_path, root),
                "md": rel_to_workspace(md_path, root),
                "research": rel_to_workspace(research_path, root),
            },
            "productImages": result["productImages"],
            "productLockRequired": force_product,
            "slidesWithProductLock": [
                {
                    "slideNumber": s["slideNumber"],
                    "productImagePath": s["productImagePath"],
                }
                for s in slides
                if s.get("productImagePath")
            ],
            "referenceFrames": result["referenceFrames"],
            "researchSources": fetch_urls,
            "titles": [s["title"] for s in slides],
            "creativeDirection": result["creativeDirection"][:400],
            "next": (
                f"For each slide: skill_run make_social_carousel "
                f"scripts/generate_slide.py --workspace . --slide N "
                f"→ artifacts/carousel/slide_NN.jpg "
                f"(model=nano-banana-pro, aspect_ratio='{ar}'). "
                f"Canvas {width}×{height}."
            ),
        },
        indent=2,
    )


async def generate_slide(root: Path, slide_number: int) -> str:
    """Generate one slide from carousel/prompts.json with product + format gates."""
    root = Path(root).resolve()
    prompts, err = _load_carousel_prompts(root)
    if err:
        return err
    assert prompts is not None
    plan, plan_err = plan_carousel_slide_generation(
        prompts, int(slide_number), root=root
    )
    if plan_err:
        return plan_err
    assert plan is not None

    out = await _invoke_gemini_generate_image(
        root,
        prompt=plan["prompt"],
        model=plan["model"],
        filename=plan["filename"],
        aspect_ratio=plan["aspect_ratio"],
        image_path=plan["image_path"],
    )
    if out.startswith("ERROR:"):
        return out
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return out
    if isinstance(data, dict):
        data["slideNumber"] = plan["slideNumber"]
        data["aspectRatio"] = plan["aspect_ratio"]
        data["productLocked"] = plan["productLocked"]
        data["source"] = "generate_slide.py"
        return json.dumps(data, indent=2)
    return out
