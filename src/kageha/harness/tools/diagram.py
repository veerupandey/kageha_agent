"""Flexible diagram rendering — Mermaid, Excalidraw, PlantUML, Graphviz → PNG/SVG.

Primary backend: Kroki (https://kroki.io). Mermaid also falls back to mermaid.ink.
For painterly / mood boards use fal_generate_image / siliconflow_image instead.
"""

from __future__ import annotations

import base64
import json
import re
import zlib
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from kageha.harness.tools.base import ToolRegistry, tool

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext

# Kroki diagram types we expose
_KROKI_TYPES = frozenset({
    "mermaid",
    "excalidraw",
    "plantuml",
    "graphviz",
    "dot",
    "blockdiag",
    "seqdiag",
    "actdiag",
    "nwdiag",
    "packetdiag",
    "rackdiag",
    "c4plantuml",
    "erd",
    "ditaa",
    "nomnoml",
    "svgbob",
    "wavedrom",
    "bytefield",
    "pikchr",
    "structurizr",
    "vega",
    "vegalite",
})

_EXT_FOR = {"png": ".png", "svg": ".svg", "pdf": ".pdf"}


def _guess_kind(source: str) -> str:
    s = (source or "").strip()
    low = s.lower()
    if low.startswith("{") and ("excalidraw" in low or '"type":"excalidraw"' in low.replace(" ", "")):
        return "excalidraw"
    if low.startswith("@startuml") or low.startswith("@startmindmap"):
        return "plantuml"
    if "digraph" in low or low.startswith("graph ") or low.startswith("strict digraph"):
        return "graphviz"
    # Mermaid heuristics
    if re.search(
        r"^\s*(flowchart|sequenceDiagram|classDiagram|stateDiagram|erDiagram|"
        r"gantt|pie|mindmap|timeline|gitGraph|C4Context|quadrantChart)",
        s,
        flags=re.M | re.I,
    ):
        return "mermaid"
    if "-->" in s or "==>" in s or "participant " in low:
        return "mermaid"
    return "mermaid"


def _normalize_kind(kind: str, source: str) -> str:
    k = (kind or "auto").strip().lower()
    if k in {"auto", "detect", ""}:
        k = _guess_kind(source)
    if k == "dot":
        k = "graphviz"
    if k not in _KROKI_TYPES:
        raise ValueError(
            f"unsupported diagram kind {kind!r}; try mermaid|excalidraw|plantuml|graphviz"
        )
    return k


def _kroki_deflate_encode(source: str) -> str:
    """Kroki GET URL payload: zlib compress + urlsafe base64 (no padding)."""
    compressed = zlib.compress(source.encode("utf-8"), 9)
    return base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")


async def _render_kroki(
    kind: str,
    source: str,
    fmt: str,
    *,
    base_url: str = "https://kroki.io",
) -> bytes:
    fmt = fmt.lower()
    if fmt not in _EXT_FOR:
        raise ValueError("format must be png|svg|pdf")
    # Prefer POST (large diagrams); fall back to GET
    url = f"{base_url.rstrip('/')}/{kind}/{fmt}"
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.post(
            url,
            content=source.encode("utf-8"),
            headers={"Content-Type": "text/plain"},
        )
        if resp.status_code >= 400:
            # GET fallback with encoded payload
            encoded = _kroki_deflate_encode(source)
            get_url = f"{base_url.rstrip('/')}/{kind}/{fmt}/{encoded}"
            resp = await client.get(get_url)
        resp.raise_for_status()
        return resp.content


async def _render_mermaid_ink(source: str, fmt: str) -> bytes:
    """Public mermaid.ink fallback (png|svg)."""
    # mermaid.ink uses standard base64 of the source
    b64 = base64.urlsafe_b64encode(source.encode("utf-8")).decode("ascii")
    path = "svg" if fmt == "svg" else "img"
    url = f"https://mermaid.ink/{path}/{b64}"
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def register_diagram_tools(ctx: "HarnessContext") -> ToolRegistry:
    reg = ToolRegistry()
    ws = ctx.workspace

    @tool(
        description=(
            "Render a structured diagram to PNG/SVG. "
            "kind=auto|mermaid|excalidraw|plantuml|graphviz (and other Kroki types). "
            "source = Mermaid text, Excalidraw JSON, PlantUML, or DOT. "
            "Use for architecture, flows, sequences, ERDs, whiteboard sketches. "
            "For artistic/illustrated diagrams use fal_generate_image instead."
        ),
        risk_class="network",
    )
    async def render_diagram(
        source: str,
        kind: str = "auto",
        format: str = "png",
        filename: str = "",
        title: str = "",
    ) -> str:
        src = (source or "").strip()
        if not src:
            return "ERROR: source is empty"
        try:
            diagram_kind = _normalize_kind(kind, src)
        except ValueError as e:
            return f"ERROR: {e}"
        fmt = (format or "png").lower().strip()
        if fmt not in _EXT_FOR:
            return "ERROR: format must be png|svg|pdf"

        stem = filename.strip()
        if not stem:
            safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", (title or diagram_kind).strip())[:40] or "diagram"
            stem = f"diagrams/{safe}{_EXT_FOR[fmt]}"
        elif not stem.endswith(_EXT_FOR[fmt]):
            # ensure extension
            stem = stem.rsplit(".", 1)[0] + _EXT_FOR[fmt]
        if not stem.startswith("diagrams/") and not stem.startswith("artifacts/"):
            stem = f"diagrams/{Path(stem).name}"

        # Persist source next to output for editability
        src_ext = {
            "mermaid": ".mmd",
            "excalidraw": ".excalidraw.json",
            "plantuml": ".puml",
            "graphviz": ".dot",
        }.get(diagram_kind, ".txt")
        src_rel = str(Path(stem).with_suffix(src_ext))
        try:
            ws.write_text(src_rel, src if src.endswith("\n") else src + "\n")
        except Exception:  # noqa: BLE001
            src_rel = ""

        errors: list[str] = []
        data: bytes | None = None
        backend = ""
        try:
            data = await _render_kroki(diagram_kind, src, fmt)
            backend = "kroki"
        except Exception as e:  # noqa: BLE001
            errors.append(f"kroki: {e}")
            if diagram_kind == "mermaid" and fmt in {"png", "svg"}:
                try:
                    data = await _render_mermaid_ink(src, fmt)
                    backend = "mermaid.ink"
                except Exception as e2:  # noqa: BLE001
                    errors.append(f"mermaid.ink: {e2}")

        if not data:
            return (
                "ERROR: diagram render failed. "
                + "; ".join(errors)
                + ". Tip: simplify source, or use fal_generate_image for illustrative art."
            )

        dest = ws.path(stem)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return json.dumps(
            {
                "ok": True,
                "path": str(dest.relative_to(ws.root)),
                "source_path": src_rel,
                "kind": diagram_kind,
                "format": fmt,
                "backend": backend,
                "bytes": len(data),
            }
        )

    @tool(
        description=(
            "Write diagram source only (no render). "
            "kind=mermaid|excalidraw|plantuml|graphviz. "
            "Saves under diagrams/ for later render_diagram."
        )
    )
    async def write_diagram_source(
        source: str,
        kind: str = "mermaid",
        filename: str = "",
    ) -> str:
        src = (source or "").strip()
        if not src:
            return "ERROR: source is empty"
        try:
            diagram_kind = _normalize_kind(kind or "auto", src)
        except ValueError:
            diagram_kind = (kind or "mermaid").lower()
        ext = {
            "mermaid": ".mmd",
            "excalidraw": ".excalidraw.json",
            "plantuml": ".puml",
            "graphviz": ".dot",
        }.get(diagram_kind, ".txt")
        name = filename.strip() or f"diagrams/draft{ext}"
        if not name.startswith("diagrams/"):
            name = f"diagrams/{Path(name).name}"
        if not Path(name).suffix:
            name = name + ext
        p = ws.write_text(name, src if src.endswith("\n") else src + "\n")
        return json.dumps({"ok": True, "path": str(p.relative_to(ws.root)), "kind": diagram_kind})

    @tool(
        description=(
            "Suggest which diagram backend to use for a task description. "
            "Returns JSON: {mode, kind, reason} where mode is "
            "structured|image_model|either."
        )
    )
    async def choose_diagram_mode(task: str) -> str:
        t = (task or "").lower()
        # Structured wins for precise topology / editable source
        structured_hints = [
            "architecture",
            "flowchart",
            "sequence",
            "erd",
            "entity",
            "state machine",
            "pipeline",
            "org chart",
            "uml",
            "wireframe",
            "whiteboard",
            "excalidraw",
            "mermaid",
            "system design",
            "data flow",
            "class diagram",
        ]
        image_hints = [
            "illustration",
            "artistic",
            "watercolor",
            "poster",
            "mood",
            "isometric art",
            "infographic art",
            "storybook",
            "hero image",
            "photoreal",
        ]
        s_score = sum(1 for h in structured_hints if h in t)
        i_score = sum(1 for h in image_hints if h in t)
        kind = "mermaid"
        if any(x in t for x in ("whiteboard", "sketch", "excalidraw", "hand-drawn", "sticky")):
            kind = "excalidraw"
        elif any(x in t for x in ("uml", "plantuml", "sequence diagram")):
            kind = "plantuml" if "uml" in t and "sequence" not in t else "mermaid"
        elif any(x in t for x in ("graphviz", "dot language", "dependency graph")):
            kind = "graphviz"

        if i_score > s_score and i_score > 0:
            mode = "image_model"
            reason = "Task asks for illustrative/artistic imagery more than editable topology."
        elif s_score > 0:
            mode = "structured"
            reason = "Task needs precise structure — prefer Mermaid/Excalidraw/PlantUML."
        else:
            mode = "either"
            reason = (
                "Ambiguous — default to Mermaid for clarity; "
                "use fal_generate_image if the user wants decorative art."
            )
        return json.dumps(
            {
                "mode": mode,
                "kind": kind,
                "reason": reason,
                "tools": {
                    "structured": ["render_diagram", "write_diagram_source"],
                    "image_model": ["fal_generate_image", "siliconflow_image"],
                },
            }
        )

    for t in (render_diagram, write_diagram_source, choose_diagram_mode):
        if hasattr(t, "name"):
            reg.register(t)  # type: ignore[arg-type]
    return reg
