#!/usr/bin/env python3
"""Shared helpers for local compose/QA scripts (skill L3)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

ASPECT_PIXELS: dict[str, tuple[int, int]] = {
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

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

_PRODUCT_SLIDE_RE = re.compile(
    r"\b(product|pack(?:aging|shot)?|tin|sku|label|bottle|jar|box|pouch|"
    r"hero|cover|packshot)\b",
    re.I,
)


def resolve_workspace(explicit: str | None = None) -> Path:
    """Resolve session workspace root (not the skill scripts/ dir)."""
    if explicit and str(explicit).strip():
        return Path(explicit).expanduser().resolve()
    for key in ("KAGEHA_WORKSPACE", "WORKSPACE_ROOT"):
        env = os.environ.get(key, "").strip()
        if env:
            return Path(env).expanduser().resolve()
    cwd = Path.cwd().resolve()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / "carousel" / "prompts.json").is_file():
            return candidate
        if (candidate / "artifacts" / "carousel").is_dir():
            return candidate
    return cwd


def aspect_pixels(aspect_ratio: str) -> tuple[int, int]:
    ar = (aspect_ratio or "4:5").strip()
    return ASPECT_PIXELS.get(ar, (1080, 1350))


def load_prompts(root: Path) -> dict[str, Any]:
    path = root / "carousel" / "prompts.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("carousel/prompts.json must be a JSON object")
    return data


def prompts_path(root: Path) -> Path:
    return root / "carousel" / "prompts.json"


def slide_aspect_ratio(prompts: dict[str, Any], slide: dict[str, Any]) -> str:
    for key in ("aspectRatio", "aspect_ratio"):
        raw = slide.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    for key in ("aspectRatio", "aspect_ratio"):
        raw = prompts.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return "4:5"


def canvas_size(
    prompts: dict[str, Any], slide: dict[str, Any] | None = None
) -> tuple[int, int]:
    pixels = prompts.get("canvasPixels")
    if isinstance(pixels, dict):
        try:
            w = int(pixels.get("width") or 0)
            h = int(pixels.get("height") or 0)
            if w > 0 and h > 0:
                return w, h
        except (TypeError, ValueError):
            pass
    ar = slide_aspect_ratio(prompts, slide or {})
    return aspect_pixels(ar)


def find_slide(prompts: dict[str, Any], slide_number: int) -> dict[str, Any] | None:
    slides = prompts.get("slides")
    if not isinstance(slides, list):
        return None
    for s in slides:
        if not isinstance(s, dict):
            continue
        n = s.get("slideNumber")
        if n is None:
            n = s.get("slide")
        try:
            if int(n) == slide_number:
                return s
        except (TypeError, ValueError):
            continue
    if 1 <= slide_number <= len(slides):
        s = slides[slide_number - 1]
        return s if isinstance(s, dict) else None
    return None


def expected_slide_count(prompts: dict[str, Any]) -> int:
    slides = prompts.get("slides")
    if isinstance(slides, list) and slides:
        return len(slides)
    for key in ("slideCount", "slide_count", "n"):
        raw = prompts.get(key)
        try:
            n = int(raw)
            if n > 0:
                return n
        except (TypeError, ValueError):
            continue
    return 0


def list_slide_files(root: Path) -> list[Path]:
    folder = root / "artifacts" / "carousel"
    if not folder.is_dir():
        return []
    files = [
        p
        for p in folder.iterdir()
        if p.is_file()
        and p.suffix.lower() in IMAGE_EXTS
        and p.name.lower().startswith("slide_")
    ]
    return sorted(files, key=lambda p: p.name)


def packshots_present(prompts: dict[str, Any], root: Path) -> bool:
    imgs = prompts.get("productImages")
    if isinstance(imgs, list) and any(str(x or "").strip() for x in imgs):
        return True
    prod = root / "artifacts" / "product"
    if prod.is_dir() and any(
        p.is_file() and p.suffix.lower() in IMAGE_EXTS for p in prod.iterdir()
    ):
        return True
    return False


def slide_needs_product_lock(slide: dict[str, Any]) -> bool:
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
