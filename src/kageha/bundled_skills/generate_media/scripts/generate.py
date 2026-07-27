#!/usr/bin/env python3
"""Provider-agnostic media generation via MediaProvider registry."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def _workspace(raw: str) -> Path:
    if raw:
        return Path(raw).expanduser().resolve()
    env = os.environ.get("KAGEHA_WORKSPACE", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd().resolve()


def _pick_auto(kind: str) -> str:
    from kageha.config import env_key

    if kind == "image":
        if env_key("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY"):
            return "gemini"
        if env_key("FAL_KEY") or env_key("FAL_API_KEY"):
            return "fal"
        return "gemini"
    return "fal"


async def _main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", default="")
    p.add_argument("--kind", choices=("image", "t2v"), default="image")
    p.add_argument("--provider", default="auto")
    p.add_argument("--prompt", required=True)
    p.add_argument("--model", default="")
    p.add_argument("--filename", default="")
    p.add_argument("--aspect-ratio", default="1:1")
    p.add_argument("--image-size", default="2K")
    p.add_argument("--list-providers", action="store_true")
    args = p.parse_args()

    from kageha.harness.media import get_provider, list_providers

    if args.list_providers:
        print(json.dumps({"providers": list_providers()}, indent=2))
        return 0

    name = args.provider.strip().lower()
    if name in {"", "auto"}:
        name = _pick_auto(args.kind)
    provider = get_provider(name)
    if provider is None:
        print(json.dumps({"ok": False, "error": f"unknown provider {name!r}"}))
        return 1

    root = _workspace(args.workspace)
    dest = root / "artifacts"
    if args.kind == "image":
        result = await provider.generate_image(
            args.prompt,
            dest_dir=dest,
            filename=args.filename or "still.png",
            model=args.model or ("nano-banana-pro" if name == "gemini" else "flux"),
            aspect_ratio=args.aspect_ratio,
            image_size=args.image_size,
        )
    else:
        result = await provider.text_to_video(
            args.prompt,
            dest_dir=dest,
            filename=args.filename or "clip.mp4",
            model=args.model or "wan",
        )
    if result.get("ok") and result.get("path"):
        try:
            result["path"] = str(Path(result["path"]).relative_to(root))
        except ValueError:
            pass
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
