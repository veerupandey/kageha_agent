#!/usr/bin/env python3
"""Write carousel/prompts.json (research + product + reference → prompts).

Skill L3 script. Prefer skill_run from the agent:

  skill_run make_social_carousel scripts/write_prompts.py \\
    --workspace . --instruction "..." --slide-count 6 --aspect 4:5 \\
    --product-url URL --reference-url URL --brand-url URL
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow `python write_prompts.py` when cwd is scripts/
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _common import resolve_workspace  # noqa: E402


async def _main() -> int:
    p = argparse.ArgumentParser(description="Carousel Image Studio prompt writer")
    p.add_argument("--workspace", default="", help="Session workspace root")
    p.add_argument("--instruction", required=True, help="Creative brief")
    p.add_argument("--slide-count", type=int, default=6)
    p.add_argument("--aspect", default="4:5", dest="aspect_ratio")
    p.add_argument("--reference-dir", default="artifacts/reference")
    p.add_argument("--product-dir", default="artifacts/product")
    p.add_argument("--brand-url", default="")
    p.add_argument("--product-url", default="")
    p.add_argument("--reference-url", default="")
    p.add_argument("--no-web-search", action="store_true")
    p.add_argument("--model", default="")
    args = p.parse_args()

    from kageha.creative.carousel_studio import write_prompts

    root = resolve_workspace(args.workspace or None)
    out = await write_prompts(
        root,
        instruction=args.instruction,
        slide_count=args.slide_count,
        reference_dir=args.reference_dir,
        product_dir=args.product_dir,
        brand_url=args.brand_url,
        product_url=args.product_url,
        reference_url=args.reference_url,
        aspect_ratio=args.aspect_ratio,
        use_web_search=not args.no_web_search,
        model=args.model,
    )
    print(out)
    return 1 if out.startswith("ERROR:") else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
