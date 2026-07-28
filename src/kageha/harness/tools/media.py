"""Fal image/video tools (optional ``media`` pack). Requires FAL_KEY / FAL_API_KEY."""

from __future__ import annotations

import base64
import json
import os
from typing import TYPE_CHECKING, Any

import httpx

from kageha.harness.tools.base import ToolRegistry, tool
from kageha.harness.tools.paths import rel_to_workspace
from kageha.models.fal import FalClient

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext

FAL_IMAGE_MODELS = {
    "flux": "fal-ai/flux/dev",
    "flux-schnell": "fal-ai/flux/schnell",
}


def default_fal_image_model() -> str:
    """Default image model: ``KAGEHA_FAL_IMAGE_MODEL`` or ``flux-schnell``."""
    raw = (os.environ.get("KAGEHA_FAL_IMAGE_MODEL") or "").strip()
    if raw in FAL_IMAGE_MODELS or raw.startswith("fal-ai/"):
        return raw
    if raw:
        # Unknown alias — still return it so the tool can error clearly.
        return raw
    return "flux-schnell"
FAL_EDIT_MODELS = {
    "kontext": "fal-ai/flux-pro/kontext",
    "flux-kontext": "fal-ai/flux-pro/kontext",
}
FAL_I2V_MODELS = {
    "wan": "fal-ai/wan/v2.7/image-to-video",
    "wan22": "fal-ai/wan/v2.2-a14b/image-to-video",
    "kling": "fal-ai/kling-video/v2.6/pro/image-to-video",
    "kling3": "fal-ai/kling-video/v3/pro/image-to-video",
    "minimax": "fal-ai/minimax-video/image-to-video",
}
FAL_T2V_MODELS = {
    "wan": "fal-ai/wan/v2.2-a14b/text-to-video",
    "wan22": "fal-ai/wan/v2.2-a14b/text-to-video",
    "minimax": "fal-ai/minimax-video/video-01",
}


def register_media_tools(ctx: "HarnessContext") -> ToolRegistry:
    reg = ToolRegistry()
    fal = FalClient()

    async def _download(url: str, dest) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)

    def _image_url(result: dict[str, Any]) -> str | None:
        if result.get("images"):
            return result["images"][0].get("url")
        image = result.get("image")
        if isinstance(image, dict):
            return image.get("url")
        if isinstance(image, str):
            return image
        return None

    def _video_url(result: dict[str, Any]) -> str | None:
        video = result.get("video")
        if isinstance(video, dict):
            return video.get("url")
        if isinstance(video, str):
            return video
        return result.get("video_url")

    @tool(
        description=(
            "Generate an image with Fal (model=flux|flux-schnell or fal-ai/…). "
            "Requires FAL_KEY or FAL_API_KEY. Saves under artifacts/."
        ),
        risk_class="network",
    )
    async def fal_generate_image(
        prompt: str, model: str = "", filename: str = "still.png"
    ) -> str:
        if not fal.available:
            return "ERROR: FAL_KEY not configured (set FAL_KEY or FAL_API_KEY)"
        chosen = (model or "").strip() or default_fal_image_model()
        model_id = FAL_IMAGE_MODELS.get(chosen, chosen)
        if not model_id.startswith("fal-ai/"):
            return f"ERROR: model not allowlisted: {chosen}"
        result = await fal.run(
            model_id, {"prompt": prompt, "image_size": "square_hd"}
        )
        url = _image_url(result) if isinstance(result, dict) else None
        if not url:
            return f"ERROR: no image url in response: {json.dumps(result)[:500]}"
        dest = ctx.workspace.path(f"artifacts/{filename}")
        await _download(url, dest)
        return json.dumps(
            {
                "path": rel_to_workspace(dest, ctx.workspace.root),
                "url": url,
                "model": model_id,
            }
        )

    @tool(
        description=(
            "Edit an image with Fal Kontext (model=kontext). "
            "image_path is relative to the session workspace."
        ),
        risk_class="network",
    )
    async def fal_edit_image(
        image_path: str,
        prompt: str,
        model: str = "kontext",
        filename: str = "edited.jpg",
    ) -> str:
        if not fal.available:
            return "ERROR: FAL_KEY not configured (set FAL_KEY or FAL_API_KEY)"
        model_id = FAL_EDIT_MODELS.get(model, model)
        if not model_id.startswith("fal-ai/"):
            return f"ERROR: model not allowlisted: {model}"
        src = ctx.workspace.path(image_path)
        if not src.is_file():
            return f"ERROR: image not found: {image_path}"
        mime = "image/png" if src.suffix.lower() == ".png" else "image/jpeg"
        data_uri = f"data:{mime};base64,{base64.b64encode(src.read_bytes()).decode()}"
        result = await fal.run(model_id, {"prompt": prompt, "image_url": data_uri})
        url = _image_url(result) if isinstance(result, dict) else None
        if not url:
            return f"ERROR: no image url in response: {json.dumps(result)[:500]}"
        dest = ctx.workspace.path(f"artifacts/{filename}")
        await _download(url, dest)
        return json.dumps(
            {
                "path": rel_to_workspace(dest, ctx.workspace.root),
                "url": url,
                "model": model_id,
            }
        )

    @tool(
        description=(
            "Image-to-video via Fal. model=wan|wan22|minimax|kling|kling3. "
            "Requires FAL_KEY. Saves under artifacts/."
        ),
        risk_class="network",
    )
    async def fal_image_to_video(
        image_path: str,
        prompt: str,
        model: str = "wan",
        duration_seconds: int = 4,
        filename: str = "reel.mp4",
    ) -> str:
        if not fal.available:
            return "ERROR: FAL_KEY not configured (set FAL_KEY or FAL_API_KEY)"
        model_id = FAL_I2V_MODELS.get(model, model)
        if not model_id.startswith("fal-ai/"):
            return f"ERROR: model not allowlisted: {model}"
        src = ctx.workspace.path(image_path)
        if not src.is_file():
            return f"ERROR: image not found: {image_path}"
        mime = "image/png" if src.suffix.lower() == ".png" else "image/jpeg"
        data_uri = (
            f"data:{mime};base64,{base64.b64encode(src.read_bytes()).decode()}"
        )
        payload: dict[str, Any] = {"prompt": prompt, "image_url": data_uri}
        if "kling" in model_id:
            payload["duration"] = str(max(5, min(10, int(duration_seconds))))
        elif "minimax" in model_id:
            payload["prompt_optimizer"] = True
        else:
            payload["num_frames"] = max(33, int(duration_seconds) * 16)
        result = await fal.run(model_id, payload)
        url = _video_url(result) if isinstance(result, dict) else None
        if not url:
            return f"ERROR: no video url: {json.dumps(result)[:800]}"
        dest = ctx.workspace.path(f"artifacts/{filename}")
        await _download(url, dest)
        return json.dumps(
            {
                "path": rel_to_workspace(dest, ctx.workspace.root),
                "url": url,
                "model": model_id,
                "bytes": dest.stat().st_size,
            }
        )

    @tool(
        description=(
            "Text-to-video via Fal. model=wan|wan22|minimax. "
            "Requires FAL_KEY. Saves under artifacts/."
        ),
        risk_class="network",
    )
    async def fal_text_to_video(
        prompt: str,
        model: str = "wan",
        duration_seconds: int = 5,
        filename: str = "t2v.mp4",
    ) -> str:
        if not fal.available:
            return "ERROR: FAL_KEY not configured (set FAL_KEY or FAL_API_KEY)"
        model_id = FAL_T2V_MODELS.get(model, model)
        if not model_id.startswith("fal-ai/"):
            return f"ERROR: model not allowlisted: {model}"
        payload: dict[str, Any] = {"prompt": prompt}
        if "minimax" in model_id:
            payload["prompt_optimizer"] = True
        else:
            payload["num_frames"] = max(33, int(duration_seconds) * 16)
        result = await fal.run(model_id, payload)
        url = _video_url(result) if isinstance(result, dict) else None
        if not url:
            return f"ERROR: no video url: {json.dumps(result)[:800]}"
        dest = ctx.workspace.path(f"artifacts/{filename}")
        await _download(url, dest)
        return json.dumps(
            {
                "path": rel_to_workspace(dest, ctx.workspace.root),
                "url": url,
                "model": model_id,
                "bytes": dest.stat().st_size,
            }
        )

    for fn in (
        fal_generate_image,
        fal_edit_image,
        fal_image_to_video,
        fal_text_to_video,
    ):
        if hasattr(fn, "name"):
            reg.register(fn)  # type: ignore[arg-type]
    return reg
