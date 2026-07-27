#!/usr/bin/env python3
"""Generate one carousel slide from carousel/prompts.json (product + format gates).

Skill L3 script:

  skill_run make_social_carousel scripts/generate_slide.py \\
    --workspace . --slide 1
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _common import resolve_workspace  # noqa: E402


async def _main() -> int:
    p = argparse.ArgumentParser(description="Generate one gated carousel slide")
    p.add_argument("--workspace", default="", help="Session workspace root")
    p.add_argument("--slide", type=int, required=True, help="1-based slide number")
    args = p.parse_args()

    from kageha.creative.carousel_studio import generate_slide

    root = resolve_workspace(args.workspace or None)
    out = await generate_slide(root, args.slide)
    print(out)
    return 1 if out.startswith("ERROR:") else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
