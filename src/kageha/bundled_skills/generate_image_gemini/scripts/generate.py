#!/usr/bin/env python3
"""Generate an image via Gemini MediaProvider (no media tool pack required)."""

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


async def _main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", default="")
    p.add_argument("--prompt", required=True)
    p.add_argument("--model", default="nano-banana-pro")
    p.add_argument("--filename", default="")
    p.add_argument("--aspect-ratio", default="1:1")
    p.add_argument("--image-size", default="2K")
    p.add_argument("--image-path", default="")
    args = p.parse_args()

    from kageha.harness.media import get_provider

    root = _workspace(args.workspace)
    dest = root / "artifacts"
    provider = get_provider("gemini")
    if provider is None:
        print(json.dumps({"ok": False, "error": "gemini provider missing"}))
        return 1
    result = await provider.generate_image(
        args.prompt,
        dest_dir=dest,
        filename=args.filename,
        model=args.model,
        aspect_ratio=args.aspect_ratio,
        image_size=args.image_size,
        image_path=args.image_path,
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
