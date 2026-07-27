"""First-class PDF extract/meta tools (optional pypdf; pdftotext fallback)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from kageha.harness.tools.base import ToolRegistry, tool

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext


def _resolve_pdf(ctx: "HarnessContext", path: str) -> Path | None:
    raw = Path(path).expanduser()
    if raw.is_file():
        return raw.resolve()
    # Missing absolute paths are not workspace-relative; avoid path-escape errors.
    if raw.is_absolute():
        return None
    try:
        cand = ctx.workspace.path(path)
    except ValueError:
        return None
    if cand.is_file():
        return cand.resolve()
    return None


def _extract_pypdf(src: Path, max_pages: int | None) -> tuple[str, int, dict]:
    from pypdf import PdfReader  # type: ignore

    reader = PdfReader(str(src))
    total = len(reader.pages)
    limit = total if max_pages is None or max_pages <= 0 else min(total, max_pages)
    parts: list[str] = []
    for i in range(limit):
        text = reader.pages[i].extract_text() or ""
        parts.append(f"--- page {i + 1} ---\n{text}")
    meta = reader.metadata or {}
    info = {
        "title": getattr(meta, "title", None) or (meta.get("/Title") if isinstance(meta, dict) else None),
        "author": getattr(meta, "author", None) or (meta.get("/Author") if isinstance(meta, dict) else None),
        "pages": total,
        "engine": "pypdf",
    }
    return "\n\n".join(parts).strip(), total, info


def _extract_pdftotext(src: Path, max_pages: int | None) -> tuple[str, int, dict]:
    cmd = ["pdftotext", "-layout"]
    if max_pages and max_pages > 0:
        cmd.extend(["-f", "1", "-l", str(max_pages)])
    cmd.extend([str(src), "-"])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pdftotext failed")
    text = proc.stdout.strip()
    # page count best-effort
    pages = text.count("\f") + (1 if text else 0)
    return text.replace("\f", "\n\n--- page break ---\n\n"), pages, {"pages": pages, "engine": "pdftotext"}


def register_pdf_tools(ctx: "HarnessContext") -> ToolRegistry:
    reg = ToolRegistry()

    @tool(description="Extract text from a PDF into pdf/extract.txt. path may be absolute or workspace-relative.")
    async def pdf_extract(path: str, max_pages: int = 0) -> str:
        src = _resolve_pdf(ctx, path)
        if src is None:
            return f"ERROR: PDF not found: {path}"
        limit = max_pages if max_pages and max_pages > 0 else None
        text = ""
        info: dict = {}
        try:
            text, total, info = _extract_pypdf(src, limit)
        except ImportError:
            if not shutil.which("pdftotext"):
                return (
                    "ERROR: pypdf not installed and pdftotext missing. "
                    "Run: uv sync --extra pdf (or install poppler pdftotext)."
                )
            try:
                text, total, info = _extract_pdftotext(src, limit)
            except Exception as e:  # noqa: BLE001
                return f"ERROR: pdftotext failed: {e}"
        except Exception as e:  # noqa: BLE001
            if shutil.which("pdftotext"):
                try:
                    text, total, info = _extract_pdftotext(src, limit)
                except Exception as e2:  # noqa: BLE001
                    return f"ERROR: PDF extract failed ({e}); pdftotext also failed ({e2})"
            else:
                return f"ERROR: PDF extract failed: {e}"

        out = ctx.workspace.path("pdf/extract.txt")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text or "", encoding="utf-8")
        preview = (text or "")[:3000]
        return json.dumps(
            {
                "source": str(src),
                "extract_path": "pdf/extract.txt",
                "bytes": out.stat().st_size,
                "pages": info.get("pages"),
                "engine": info.get("engine"),
                "preview": preview,
            },
            indent=2,
            default=str,
        )

    @tool(description="Return PDF metadata (title, author, page count) for a local PDF path.")
    async def pdf_meta(path: str) -> str:
        src = _resolve_pdf(ctx, path)
        if src is None:
            return f"ERROR: PDF not found: {path}"
        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(str(src))
            meta = reader.metadata or {}
            title = getattr(meta, "title", None)
            author = getattr(meta, "author", None)
            if isinstance(meta, dict):
                title = title or meta.get("/Title")
                author = author or meta.get("/Author")
            return json.dumps(
                {
                    "source": str(src),
                    "pages": len(reader.pages),
                    "title": title,
                    "author": author,
                    "engine": "pypdf",
                },
                indent=2,
                default=str,
            )
        except ImportError:
            if shutil.which("pdfinfo"):
                proc = subprocess.run(["pdfinfo", str(src)], capture_output=True, text=True, timeout=60)
                return proc.stdout or proc.stderr or "ERROR: pdfinfo empty"
            return "ERROR: pypdf not installed. Run: uv sync --extra pdf"
        except Exception as e:  # noqa: BLE001
            return f"ERROR: pdf_meta failed: {e}"

    for t in (pdf_extract, pdf_meta):
        if hasattr(t, "name"):
            reg.register(t)  # type: ignore[arg-type]
    return reg
