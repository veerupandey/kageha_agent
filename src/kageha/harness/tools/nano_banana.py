"""First-class Gemini Nano Banana image tools (always-on core pack).

Requires ``GEMINI_API_KEY``. Prefer these over ad-hoc curl / pip / Fal for
still images, carousels, and product-aware edits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from kageha.harness.tools.base import ToolRegistry, tool
from kageha.harness.tools.paths import rel_to_workspace
from kageha.models.nano_banana import (
    NANO_BANANA_MODELS,
    NanoBananaClient,
    _ext_for_mime,
    _mime_for_path,
    resolve_nano_banana_model,
)

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext


def _normalize_filename(filename: str, mime: str) -> str:
    name = (filename or "nano_banana.png").strip().lstrip("/")
    if not name:
        name = "nano_banana.png"
    if "/" not in name and not name.startswith("artifacts/"):
        name = f"artifacts/{name}"
    path = Path(name)
    if not path.suffix:
        path = path.with_suffix(_ext_for_mime(mime))
    return str(path).replace("\\", "/")


def _load_refs(
    ctx: "HarnessContext", image_paths: str
) -> list[tuple[bytes, str]] | str:
    """Parse comma/newline-separated workspace-relative paths into image bytes."""
    raw = (image_paths or "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]
    refs: list[tuple[bytes, str]] = []
    for rel in parts[:14]:
        src = ctx.workspace.path(rel)
        if not src.is_file():
            return f"ERROR: reference image not found: {rel}"
        refs.append((src.read_bytes(), _mime_for_path(str(src))))
    return refs


def register_nano_banana_tools(ctx: "HarnessContext") -> ToolRegistry:
    reg = ToolRegistry()
    client = NanoBananaClient()
    aliases = ", ".join(sorted(NANO_BANANA_MODELS))

    @tool(
        description=(
            "FIRST-CLASS image generation via Gemini Nano Banana (Google). "
            "Use this for Instagram carousels, product stills, ads, and any "
            "still-image deliverable — do NOT shell out to curl/pip or invent "
            "an SDK install. "
            f"model aliases: {aliases} (default banana2 = gemini-3.1-flash-image). "
            "aspect_ratio e.g. 1:1|4:5|9:16|16:9; image_size 0.5K|1K|2K|4K. "
            "Optional reference_images: comma-separated workspace paths (product "
            "shots for brand consistency). Requires GEMINI_API_KEY. Saves under artifacts/."
        ),
        risk_class="network",
    )
    async def nano_banana_generate(
        prompt: str,
        model: str = "banana2",
        filename: str = "nano_banana.png",
        aspect_ratio: str = "1:1",
        image_size: str = "1K",
        reference_images: str = "",
        google_search: bool = False,
    ) -> str:
        if not client.available:
            return (
                "ERROR: GEMINI_API_KEY not configured. "
                "Set GEMINI_API_KEY in .env (paid key required for Nano Banana)."
            )
        refs = _load_refs(ctx, reference_images)
        if isinstance(refs, str):
            return refs
        try:
            image = await client.generate(
                prompt,
                model=model,
                reference_images=refs,
                aspect_ratio=aspect_ratio or "1:1",
                image_size=image_size or "1K",
                use_google_search=bool(google_search),
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: Nano Banana generate failed: {exc}"

        rel = _normalize_filename(filename, image.mime_type)
        dest = ctx.workspace.path(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(image.data)
        return json.dumps(
            {
                "path": rel_to_workspace(dest, ctx.workspace.root),
                "bytes": dest.stat().st_size,
                "mime_type": image.mime_type,
                "model": image.model,
                "resolved_model": resolve_nano_banana_model(model),
                "interaction_id": image.interaction_id,
                "text": image.text,
            }
        )

    @tool(
        description=(
            "Edit or compose an image with Gemini Nano Banana using one or more "
            "reference images (product packshots, logos, style refs). "
            "image_paths: comma-separated workspace-relative paths. "
            "Prefer this over Fal when GEMINI_API_KEY is set and you need "
            "brand-consistent carousel / ad frames. Saves under artifacts/."
        ),
        risk_class="network",
    )
    async def nano_banana_edit(
        prompt: str,
        image_paths: str,
        model: str = "banana2",
        filename: str = "nano_banana_edit.png",
        aspect_ratio: str = "1:1",
        image_size: str = "1K",
    ) -> str:
        if not client.available:
            return (
                "ERROR: GEMINI_API_KEY not configured. "
                "Set GEMINI_API_KEY in .env (paid key required for Nano Banana)."
            )
        refs = _load_refs(ctx, image_paths)
        if isinstance(refs, str):
            return refs
        if not refs:
            return "ERROR: image_paths required (comma-separated workspace paths)"
        try:
            image = await client.generate(
                prompt,
                model=model,
                reference_images=refs,
                aspect_ratio=aspect_ratio or "1:1",
                image_size=image_size or "1K",
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: Nano Banana edit failed: {exc}"

        rel = _normalize_filename(filename, image.mime_type)
        dest = ctx.workspace.path(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(image.data)
        return json.dumps(
            {
                "path": rel_to_workspace(dest, ctx.workspace.root),
                "bytes": dest.stat().st_size,
                "mime_type": image.mime_type,
                "model": image.model,
                "interaction_id": image.interaction_id,
                "text": image.text,
            }
        )

    for fn in (nano_banana_generate, nano_banana_edit):
        if hasattr(fn, "name"):
            reg.register(fn)  # type: ignore[arg-type]
    return reg
