#!/usr/bin/env python3
"""QA Instagram carousel deliverables and write/update carousel/judge.md.

Local skill script (L3). Checks slide count, dimensions, prompts.json, and
optional product-lock checklist.

Examples:
  python scripts/qa_carousel.py --workspace .
  python scripts/qa_carousel.py --workspace . --expect-slides 6 --product-lock
  python scripts/qa_carousel.py --help
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _common import (
    canvas_size,
    expected_slide_count,
    find_slide,
    list_slide_files,
    load_prompts,
    packshots_present,
    prompts_path,
    resolve_workspace,
    slide_needs_product_lock,
)


def _require_pillow():
    try:
        from PIL import Image
    except ImportError as e:
        raise SystemExit(
            "ERROR: Pillow required. Install with: pip install pillow>=10"
        ) from e
    return Image


def _check(
    name: str, ok: bool, detail: str = ""
) -> dict[str, Any]:
    return {"item": name, "pass": ok, "detail": detail}


def run_qa(
    *,
    root: Path,
    expect_slides: int | None,
    product_lock: bool,
    write_judge: bool,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    prompts: dict[str, Any] | None = None
    prompts_file = prompts_path(root)

    has_prompts = prompts_file.is_file()
    checks.append(
        _check(
            "prompts.json present",
            has_prompts,
            str(prompts_file.relative_to(root)) if has_prompts else "missing carousel/prompts.json",
        )
    )
    if has_prompts:
        try:
            prompts = load_prompts(root)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            checks.append(_check("prompts.json valid JSON", False, str(e)))
            prompts = None
        else:
            checks.append(_check("prompts.json valid JSON", True))

    slides = list_slide_files(root)
    n_files = len(slides)
    expected = expect_slides
    if expected is None and prompts is not None:
        expected = expected_slide_count(prompts) or None
    if expected is None:
        expected = n_files

    checks.append(
        _check(
            "slide count",
            n_files == int(expected) and n_files > 0,
            f"found={n_files} expected={expected}",
        )
    )

    target_w, target_h = (1080, 1350)
    if prompts is not None:
        target_w, target_h = canvas_size(prompts)

    Image = _require_pillow()
    dim_ok = True
    dim_details: list[str] = []
    for path in slides:
        try:
            with Image.open(path) as im:
                w, h = im.size
        except OSError as e:
            dim_ok = False
            dim_details.append(f"{path.name}: unreadable ({e})")
            continue
        if (w, h) != (target_w, target_h):
            dim_ok = False
            dim_details.append(f"{path.name}: {w}x{h} != {target_w}x{target_h}")
    if not slides:
        dim_ok = False
        dim_details.append("no slide_*.jpg|png files")
    checks.append(
        _check(
            "dimensions match canvas",
            dim_ok,
            "; ".join(dim_details) or f"all {target_w}x{target_h}",
        )
    )

    # Product lock checklist (optional / auto when packshots exist)
    want_product = product_lock
    if prompts is not None and packshots_present(prompts, root):
        want_product = True
    if want_product and prompts is not None:
        locked = 0
        needed = 0
        missing: list[str] = []
        for i in range(1, expected_slide_count(prompts) + 1):
            slide = find_slide(prompts, i)
            if not slide:
                continue
            pip = str(slide.get("productImagePath") or "").strip()
            if pip or slide_needs_product_lock(slide):
                needed += 1
                if pip and (root / pip).is_file():
                    locked += 1
                else:
                    missing.append(f"slide_{i:02d}")
        ok = needed == 0 or (locked >= 1 and not missing)
        # Softer: require hook/product slides that need lock to have files
        ok = len(missing) == 0
        checks.append(
            _check(
                "product lock",
                ok,
                f"locked={locked} needed={needed}"
                + (f" missing={','.join(missing)}" if missing else ""),
            )
        )
    elif want_product:
        prod = root / "artifacts" / "product"
        has = prod.is_dir() and any(prod.iterdir())
        checks.append(
            _check(
                "product lock",
                has,
                "artifacts/product/ present" if has else "no packshots; prompts missing",
            )
        )

    research = (root / "carousel" / "research.md").is_file()
    checks.append(_check("research.md", research or prompts is not None, "optional when no brand research"))

    all_pass = all(c["pass"] for c in checks)
    report = {
        "ok": all_pass,
        "workspace": str(root),
        "slideFiles": [str(p.relative_to(root)) for p in slides],
        "canvas": {"width": target_w, "height": target_h},
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if write_judge:
        judge = root / "carousel" / "judge.md"
        judge.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Carousel judge",
            "",
            f"Generated: {report['timestamp']}",
            f"Overall: {'PASS' if all_pass else 'FAIL'}",
            "",
            "## Checklist",
            "",
        ]
        for c in checks:
            box = "x" if c["pass"] else " "
            detail = f" — {c['detail']}" if c["detail"] else ""
            status = "PASS" if c["pass"] else "FAIL"
            lines.append(f"- [{box}] {c['item']}: **{status}**{detail}")
        lines.extend(["", "## Slides", ""])
        for rel in report["slideFiles"]:
            lines.append(f"- `{rel}`")
        if not report["slideFiles"]:
            lines.append("- _(none)_")
        lines.append("")
        judge.write_text("\n".join(lines), encoding="utf-8")
        report["judge"] = str(judge.relative_to(root))

    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "QA carousel slides + prompts.json; write carousel/judge.md. "
            "Use after generate/compose."
        )
    )
    p.add_argument("--workspace", default="", help="Agent workspace root")
    p.add_argument(
        "--expect-slides",
        type=int,
        default=0,
        help="Expected slide count (0 = infer from prompts.json or file count)",
    )
    p.add_argument(
        "--product-lock",
        action="store_true",
        help="Require productImagePath files for product-facing slides",
    )
    p.add_argument(
        "--no-judge",
        action="store_true",
        help="Do not write carousel/judge.md",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Same as --no-judge (validate only)",
    )
    args = p.parse_args(argv)
    root = resolve_workspace(args.workspace or None)
    expect = int(args.expect_slides) if args.expect_slides and args.expect_slides > 0 else None
    write_judge = not args.no_judge and not args.dry_run
    try:
        report = run_qa(
            root=root,
            expect_slides=expect,
            product_lock=bool(args.product_lock),
            write_judge=write_judge,
        )
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
