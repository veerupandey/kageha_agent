"""Image / video generation tools: Fal, Gemini Nano Banana Pro, SiliconFlow."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from kageha.config import env_key
from kageha.harness.tools.base import ToolRegistry, tool
from kageha.harness.tools.paths import rel_to_workspace
from kageha.models.fal import FalClient

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext

# Allowlisted Fal models for reels / stills
FAL_IMAGE_MODELS = {
    "flux": "fal-ai/flux/dev",
    "flux-schnell": "fal-ai/flux/schnell",
}
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

# Google Gemini image models (Nano Banana family)
# Docs: https://ai.google.dev/gemini-api/docs/models/gemini-3-pro-image
GEMINI_IMAGE_MODELS = {
    "nano-banana-pro": "gemini-3-pro-image",
    "nano_banana_pro": "gemini-3-pro-image",
    "gemini-3-pro-image": "gemini-3-pro-image",
    "nano-banana-2": "gemini-3.1-flash-image",
    "nano_banana_2": "gemini-3.1-flash-image",
    "gemini-3.1-flash-image": "gemini-3.1-flash-image",
    "nano-banana": "gemini-2.5-flash-image",
    "nano_banana": "gemini-2.5-flash-image",
    "gemini-2.5-flash-image": "gemini-2.5-flash-image",
}
DEFAULT_GEMINI_IMAGE_MODEL = "gemini-3-pro-image"
GEMINI_BASE = (
    env_key("GEMINI_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta"
).rstrip("/")


def register_media_tools(ctx: "HarnessContext") -> ToolRegistry:
    reg = ToolRegistry()
    fal = FalClient()

    async def _download(url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        return dest

    def _extract_image_url(result: dict[str, Any]) -> str | None:
        if result.get("images"):
            return result["images"][0].get("url")
        image = result.get("image")
        if isinstance(image, dict):
            return image.get("url")
        if isinstance(image, str):
            return image
        return None

    def _extract_video_url(result: dict[str, Any]) -> str | None:
        video = result.get("video")
        if isinstance(video, dict):
            return video.get("url")
        if isinstance(video, str):
            return video
        return result.get("video_url")

    def _mime_to_ext(mime: str) -> str:
        m = (mime or "").lower()
        if "jpeg" in m or "jpg" in m:
            return ".jpg"
        if "webp" in m:
            return ".webp"
        if "gif" in m:
            return ".gif"
        return ".png"

    @tool(
        description=(
            "Generate an image with Fal (model=flux|flux-schnell or fal-ai/…). "
            "Requires FAL_KEY. Saves under artifacts/."
        ),
        risk_class="network",
    )
    async def fal_generate_image(
        prompt: str, model: str = "flux", filename: str = "still.png"
    ) -> str:
        if not fal.available:
            return "ERROR: FAL_KEY not configured (set FAL_KEY or FAL_API_KEY)"
        model_id = FAL_IMAGE_MODELS.get(model, model)
        if not model_id.startswith("fal-ai/"):
            return f"ERROR: model not allowlisted: {model}"
        result = await fal.run(model_id, {"prompt": prompt, "image_size": "portrait_16_9"})
        url = _extract_image_url(result) if isinstance(result, dict) else None
        if not url:
            return f"ERROR: no image url in response: {json.dumps(result)[:500]}"
        dest = ctx.workspace.path(f"artifacts/{filename}")
        await _download(url, dest)
        return json.dumps(
            {"path": str(dest.relative_to(ctx.workspace.root)), "url": url, "model": model_id}
        )

    @tool(
        description=(
            "Edit a real product still with Fal Kontext (model=kontext). "
            "Use to build a PRE-POUR hero: milk+ice only while keeping exact tin/label. "
            "Never invent packaging — only edit lighting/drink state around the real tin."
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
        locked = (
            prompt
            + " Keep the exact product packaging, label typography, colors, and logo from the source image. "
            "Do not invent or redraw brand marks."
        )
        result = await fal.run(model_id, {"prompt": locked, "image_url": data_uri})
        url = _extract_image_url(result) if isinstance(result, dict) else None
        if not url:
            return f"ERROR: no image url in response: {json.dumps(result)[:500]}"
        dest = ctx.workspace.path(f"artifacts/{filename}")
        await _download(url, dest)
        return json.dumps(
            {"path": str(dest.relative_to(ctx.workspace.root)), "url": url, "model": model_id}
        )

    @tool(
        description=(
            "Image-to-video via Fal. model=wan|wan22|minimax|kling|kling3. "
            "image_path is relative to session workspace. duration_seconds ~4–5. "
            "For pour reels, seed MUST be milk+ice only (no green already poured)."
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
        b64 = base64.b64encode(src.read_bytes()).decode()
        data_uri = f"data:{mime};base64,{b64}"
        locked = (
            prompt
            + " Keep the exact product packaging, labels, and colors from the source image. "
            "Modern lifestyle motion only. No traditional tea-ceremony set dressing, "
            "no invented logos or calligraphy."
        )
        payload: dict[str, Any] = {
            "prompt": locked,
            "image_url": data_uri,
        }
        if "kling" in model_id:
            payload["duration"] = str(max(5, min(10, int(duration_seconds))))
        elif "minimax" in model_id:
            payload["prompt_optimizer"] = True
        else:
            payload["num_frames"] = max(33, int(duration_seconds) * 16)
        result = await fal.run(model_id, payload)
        url = _extract_video_url(result) if isinstance(result, dict) else None
        if not url:
            return f"ERROR: no video url: {json.dumps(result)[:800]}"
        dest = ctx.workspace.path(f"artifacts/{filename}")
        await _download(url, dest)
        return json.dumps(
            {
                "path": str(dest.relative_to(ctx.workspace.root)),
                "url": url,
                "model": model_id,
                "bytes": dest.stat().st_size,
            }
        )

    @tool(
        description=(
            "Text-to-video via Fal. model=wan|wan22|minimax (or fal-ai/… allowlisted). "
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
        url = _extract_video_url(result) if isinstance(result, dict) else None
        if not url:
            return f"ERROR: no video url: {json.dumps(result)[:800]}"
        dest = ctx.workspace.path(f"artifacts/{filename}")
        await _download(url, dest)
        return json.dumps(
            {
                "path": str(dest.relative_to(ctx.workspace.root)),
                "url": url,
                "model": model_id,
                "bytes": dest.stat().st_size,
            }
        )

    @tool(
        description=(
            "Generate an image with Gemini Nano Banana Pro "
            "(default model=nano-banana-pro → gemini-3-pro-image). "
            "Aliases: nano-banana-pro (best), nano-banana-2 (gemini-3.1-flash-image, faster), "
            "nano-banana (gemini-2.5-flash-image). For carousels prefer skill_run "
            "make_social_carousel scripts/generate_slide.py (gates product + aspect). "
            "Requires GEMINI_API_KEY. Pass aspect_ratio (carousel default 4:5). "
            "When productImagePath is set, image_path locks the real packshot. "
            "Saves under artifacts/."
        ),
        risk_class="network",
    )
    async def gemini_generate_image(
        prompt: str,
        model: str = "nano-banana-pro",
        filename: str = "",
        image_path: str = "",
        aspect_ratio: str = "1:1",
        image_size: str = "2K",
    ) -> str:
        api_key = env_key("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            return "ERROR: GEMINI_API_KEY not configured"
        key = (model or "nano-banana-pro").strip()
        model_id = GEMINI_IMAGE_MODELS.get(key, GEMINI_IMAGE_MODELS.get(key.lower(), key))
        if model_id not in GEMINI_IMAGE_MODELS.values() and not model_id.startswith(
            "gemini-"
        ):
            return (
                f"ERROR: unknown Gemini image model {model!r}. "
                f"Use one of: {', '.join(sorted(set(GEMINI_IMAGE_MODELS)))}"
            )

        parts: list[dict[str, Any]] = []
        locked_product = False
        if image_path.strip():
            src = ctx.workspace.path(image_path.strip())
            if not src.is_file():
                return f"ERROR: image not found: {image_path}"
            mime = "image/png" if src.suffix.lower() == ".png" else "image/jpeg"
            if src.suffix.lower() == ".webp":
                mime = "image/webp"
            parts.append(
                {
                    "inlineData": {
                        "mimeType": mime,
                        "data": base64.b64encode(src.read_bytes()).decode(),
                    }
                }
            )
            locked_product = True
            parts.append(
                {
                    "text": (
                        "PRODUCT LOCK: The attached image is the REAL product packshot. "
                        "Use this exact packaging/label/artwork — never invent or restyle "
                        "the tin/bottle/label. Compose the scene around this product still."
                    )
                }
            )
        ar = (aspect_ratio or "").strip() or "1:1"
        size = (image_size or "").strip().upper() or "2K"
        if size not in {"1K", "2K", "4K"}:
            size = "2K"
        format_prefix = (
            f"OUTPUT FORMAT (mandatory): aspect ratio {ar}. "
            "Fill the full frame; do not crop to a different ratio.\n\n"
        )
        parts.append({"text": format_prefix + prompt})

        image_cfg: dict[str, Any] = {"aspectRatio": ar}
        # Gemini 3 image models honor imageSize (1K/2K/4K); ignore if rejected.
        if model_id.startswith("gemini-3"):
            image_cfg["imageSize"] = size
        gen_cfg: dict[str, Any] = {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": image_cfg,
        }

        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": gen_cfg,
        }
        url = f"{GEMINI_BASE}/models/{model_id}:generateContent"
        headers = {
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            # Some model builds want IMAGE only
            if resp.status_code == 400 and "responseModalities" in (resp.text or ""):
                fallback_config: dict[str, Any] = {
                    "responseModalities": ["IMAGE"],
                    "imageConfig": dict(image_cfg),
                }
                payload["generationConfig"] = fallback_config
                resp = await client.post(url, headers=headers, json=payload)
            # Older builds may reject imageSize — retry aspectRatio only
            if resp.status_code == 400 and "imageSize" in (resp.text or ""):
                slim_cfg = {
                    "responseModalities": gen_cfg.get("responseModalities")
                    or ["TEXT", "IMAGE"],
                    "imageConfig": {"aspectRatio": ar},
                }
                payload["generationConfig"] = slim_cfg
                resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                return (
                    f"ERROR: Gemini {resp.status_code}: "
                    f"{(resp.text or '')[:600].replace(chr(10), ' ')}"
                )
            data = resp.json()

        candidates = data.get("candidates") or []
        if not candidates:
            return f"ERROR: no candidates: {json.dumps(data)[:500]}"
        out_parts = ((candidates[0].get("content") or {}).get("parts")) or []
        text_bits: list[str] = []
        image_bytes: bytes | None = None
        image_mime = "image/png"
        for p in out_parts:
            if p.get("text") and not p.get("thought"):
                text_bits.append(str(p["text"]))
            inline = p.get("inlineData") or p.get("inline_data")
            if isinstance(inline, dict) and inline.get("data"):
                image_mime = str(inline.get("mimeType") or inline.get("mime_type") or image_mime)
                image_bytes = base64.b64decode(inline["data"])
        if not image_bytes:
            preview = " ".join(text_bits)[:400]
            return f"ERROR: no image in response. text={preview!r}"

        name = filename.strip()
        if not name:
            name = f"gemini_{model_id.replace('/', '_').replace('.', '_')}{_mime_to_ext(image_mime)}"
        elif "." not in Path(name).name:
            name = name + _mime_to_ext(image_mime)
        dest = ctx.workspace.path(f"artifacts/{name}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(image_bytes)
        return json.dumps(
            {
                "path": rel_to_workspace(dest, ctx.workspace.root),
                "model": model_id,
                "bytes": len(image_bytes),
                "mime": image_mime,
                "aspectRatio": ar,
                "imageSize": size if "imageSize" in image_cfg else None,
                "productLocked": locked_product,
                "imagePath": image_path.strip() or None,
                "text": " ".join(text_bits).strip()[:500] or None,
            }
        )

    @tool(
        description="Download a remote image/video URL into session artifacts/.",
        risk_class="network",
    )
    async def download_media(url: str, filename: str) -> str:
        dest = ctx.workspace.path(f"artifacts/{filename}")
        await _download(url, dest)
        return json.dumps(
            {"path": str(dest.relative_to(ctx.workspace.root)), "bytes": dest.stat().st_size}
        )

    @tool(
        description=(
            "Speak text aloud with Gemini TTS. Writes a WAV under artifacts/. "
            "Model/voice from models.yaml voice: (or KAGEHA_VOICE_MODEL / "
            "KAGEHA_VOICE_NAME). Needs GEMINI_API_KEY."
        ),
        risk_class="network",
    )
    async def gemini_tts(
        text: str,
        filename: str = "speech.wav",
        voice: str = "",
        model: str = "",
        style: str = "",
    ) -> str:
        from kageha.models.voice import resolve_voice_config, synthesize_gemini_tts

        cfg = resolve_voice_config()
        if cfg is None:
            return "ERROR: GEMINI_API_KEY missing (required for Gemini TTS)"
        spoken = (text or "").strip()
        if not spoken:
            return "ERROR: text is empty"
        if style.strip():
            spoken = f"{style.strip().rstrip(':')}: {spoken}"
        try:
            pcm = await synthesize_gemini_tts(
                spoken,
                model=(model or cfg.model).strip(),
                voice=(voice or cfg.voice).strip() or cfg.voice,
                api_key=cfg.api_key,
                base_url=cfg.base_url,
            )
        except Exception as e:  # noqa: BLE001
            return f"ERROR: gemini_tts failed: {e}"
        dest = ctx.workspace.path(f"artifacts/{filename}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_pcm_wav(dest, pcm, sample_rate=24000)
        return json.dumps(
            {
                "path": str(dest.relative_to(ctx.workspace.root)),
                "bytes": dest.stat().st_size,
                "model": (model or cfg.model),
                "voice": (voice or cfg.voice),
            }
        )

    @tool(
        description="Generate image via SiliconFlow images API (OpenAI-compatible).",
        risk_class="network",
    )
    async def siliconflow_image(
        prompt: str,
        filename: str = "sf_still.png",
        model: str = "black-forest-labs/FLUX.1-schnell",
    ) -> str:
        key = env_key("SILICONFLOW_API_KEY")
        base = env_key("SILICONFLOW_BASE_URL") or "https://api.siliconflow.com/v1"
        if not key:
            return "ERROR: SILICONFLOW_API_KEY missing"
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{base.rstrip('/')}/images/generations",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "prompt": prompt, "image_size": "768x1344"},
            )
            resp.raise_for_status()
            data = resp.json()
        url = None
        if data.get("images"):
            url = data["images"][0].get("url")
        elif data.get("data"):
            url = data["data"][0].get("url")
        if not url:
            return f"ERROR: {json.dumps(data)[:500]}"
        dest = ctx.workspace.path(f"artifacts/{filename}")
        await _download(url, dest)
        return json.dumps({"path": str(dest.relative_to(ctx.workspace.root)), "url": url})

    for t in (
        fal_generate_image,
        fal_edit_image,
        fal_image_to_video,
        fal_text_to_video,
        gemini_generate_image,
        gemini_tts,
        download_media,
        siliconflow_image,
    ):
        if hasattr(t, "name"):
            reg.register(t)  # type: ignore[arg-type]
    return reg


def _write_pcm_wav(path: Path, pcm: bytes, *, sample_rate: int = 24000) -> None:
    """Write 16-bit mono PCM as a WAV file."""
    import wave

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
