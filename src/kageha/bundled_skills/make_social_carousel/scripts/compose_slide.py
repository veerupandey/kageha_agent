#!/usr/bin/env python3
"""Overlay exact canvaText on a background/product plate → Instagram canvas.

Local skill script (L3). Prefer ``generate_slide.py`` for AI plates; use this
when typography must be exact (Pillow compose).

Examples:
  python scripts/compose_slide.py --workspace . --slide 1 \\
      --bg artifacts/carousel/_raw_slide_01.jpg
  python scripts/compose_slide.py --help
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import (
    canvas_size,
    find_slide,
    load_prompts,
    resolve_workspace,
)


def _require_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
    except ImportError as e:
        raise SystemExit(
            "ERROR: Pillow required. Install with: pip install pillow>=10"
        ) from e
    from PIL import Image, ImageDraw, ImageFont

    return Image, ImageDraw, ImageFont


def _load_font(ImageFont, size: int):  # noqa: N803
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    lines: list[str] = []
    cur = words[0]
    for word in words[1:]:
        trial = f"{cur} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return lines


def _text_color(bg_mode: str) -> tuple[int, int, int]:
    return (255, 255, 255) if bg_mode == "dark" else (20, 30, 45)


def _canva_fields(slide: dict) -> dict[str, str]:
    canva = slide.get("canvaText") if isinstance(slide.get("canvaText"), dict) else {}
    copy = slide.get("copy") if isinstance(slide.get("copy"), dict) else {}
    return {
        "heading": str(canva.get("heading") or copy.get("headline") or "").strip(),
        "subtext": str(canva.get("subtext") or copy.get("subhead") or "").strip(),
        "bodyText": str(canva.get("bodyText") or copy.get("body") or "").strip(),
        "footer": str(canva.get("footer") or "").strip(),
    }


def compose(
    *,
    root: Path,
    slide_number: int,
    bg_path: Path | None,
    out_path: Path | None,
    fill: str,
    dry_run: bool,
) -> dict:
    Image, ImageDraw, ImageFont = _require_pillow()
    prompts = load_prompts(root)
    slide = find_slide(prompts, slide_number)
    if slide is None:
        raise SystemExit(f"ERROR: slide {slide_number} not found in prompts.json")

    width, height = canvas_size(prompts, slide)
    fields = _canva_fields(slide)
    nn = f"{slide_number:02d}"
    dest = out_path or (root / "artifacts" / "carousel" / f"slide_{nn}.jpg")
    dest = dest if dest.is_absolute() else (root / dest)

    plan = {
        "slideNumber": slide_number,
        "canvas": {"width": width, "height": height},
        "canvaText": fields,
        "background": str(bg_path) if bg_path else None,
        "output": str(dest.relative_to(root) if dest.is_relative_to(root) else dest),
        "dryRun": dry_run,
    }
    if dry_run:
        return plan

    if bg_path is not None:
        src = bg_path if bg_path.is_absolute() else (root / bg_path)
        if not src.is_file():
            raise SystemExit(f"ERROR: background not found: {src}")
        img = Image.open(src).convert("RGB")
        img = img.resize((width, height), Image.Resampling.LANCZOS)
    else:
        color = (245, 240, 230) if fill == "cream" else (18, 22, 28)
        img = Image.new("RGB", (width, height), color)

    draw = ImageDraw.Draw(img)
    # Simple luminance heuristic for text color
    sample = img.resize((1, 1)).getpixel((0, 0))
    lum = 0.299 * sample[0] + 0.587 * sample[1] + 0.114 * sample[2]
    color = _text_color("dark" if lum < 140 else "light")

    margin = int(width * 0.08)
    max_w = width - 2 * margin
    y = int(height * 0.12)

    heading_font = _load_font(ImageFont, size=max(42, width // 14))
    sub_font = _load_font(ImageFont, size=max(28, width // 22))
    body_font = _load_font(ImageFont, size=max(24, width // 26))
    footer_font = _load_font(ImageFont, size=max(20, width // 32))

    for text, font, gap in (
        (fields["heading"], heading_font, int(height * 0.04)),
        (fields["subtext"], sub_font, int(height * 0.03)),
        (fields["bodyText"], body_font, int(height * 0.025)),
    ):
        if not text:
            continue
        for line in _wrap_text(draw, text, font, max_w):
            tw = draw.textlength(line, font=font)
            x = (width - tw) / 2
            # soft shadow for readability
            draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0))
            draw.text((x, y), line, font=font, fill=color)
            y += int(font.size * 1.25)
        y += gap

    if fields["footer"]:
        tw = draw.textlength(fields["footer"], font=footer_font)
        draw.text(
            ((width - tw) / 2, height - margin - footer_font.size),
            fields["footer"],
            font=footer_font,
            fill=color,
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, format="JPEG", quality=92, optimize=True)
    plan["bytes"] = dest.stat().st_size
    plan["ok"] = True
    return plan


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Compose one carousel slide: overlay canvaText from "
            "carousel/prompts.json onto a background plate at locked canvas size "
            "(default 1080×1350 / 4:5)."
        )
    )
    p.add_argument(
        "--workspace",
        default="",
        help="Agent workspace root (contains carousel/ and artifacts/). "
        "Default: KAGEHA_WORKSPACE or cwd walk.",
    )
    p.add_argument("--slide", type=int, required=False, default=1, help="1-based slide number")
    p.add_argument(
        "--bg",
        default="",
        help="Background/product plate image (workspace-relative or absolute). "
        "Omit to use solid --fill.",
    )
    p.add_argument(
        "--out",
        default="",
        help="Output path (default artifacts/carousel/slide_NN.jpg)",
    )
    p.add_argument(
        "--fill",
        choices=("cream", "dark"),
        default="cream",
        help="Solid fill when --bg omitted (default cream)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate prompts/canvaText/canvas only; do not write image",
    )
    args = p.parse_args(argv)

    root = resolve_workspace(args.workspace or None)
    bg = Path(args.bg) if args.bg.strip() else None
    out = Path(args.out) if args.out.strip() else None
    try:
        result = compose(
            root=root,
            slide_number=int(args.slide),
            bg_path=bg,
            out_path=out,
            fill=args.fill,
            dry_run=bool(args.dry_run),
        )
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
