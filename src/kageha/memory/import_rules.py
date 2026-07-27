"""Import portable project rule files into scoped memory instructions."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_HEADING_RE = re.compile(r"(?m)^#{1,3}\s+(.+)$")

_ROOT_RULE_NAMES = (
    "AGENTS.override.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/CLAUDE.md",
)


def auto_sync_rules_enabled() -> bool:
    """Whether turn-start hash-gated rule sync is enabled (default: on/auto)."""
    raw = (os.environ.get("KAGEHA_MEMORY_AUTO_SYNC_RULES") or "auto").strip().lower()
    return raw not in {"off", "0", "false", "no"}


def rule_files_fingerprint(project_root: str | Path) -> str:
    """Stable content hash of discovered rule files (paths + bodies)."""
    root = Path(project_root).expanduser().resolve()
    digest = hashlib.sha256()
    saw = False
    for path in discover_rule_files(root):
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = path.name
        try:
            body = path.read_bytes()
        except OSError:
            continue
        if len(body) > 200_000:
            body = body[:200_000]
        digest.update(rel.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(body)
        digest.update(b"\0")
        saw = True
    return digest.hexdigest() if saw else ""


@dataclass(frozen=True)
class RuleChunk:
    source: str
    title: str
    content: str


def discover_rule_files(project_root: str | Path) -> list[Path]:
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        return []
    found: list[Path] = []
    for name in _ROOT_RULE_NAMES:
        path = root / name
        if path.is_file():
            found.append(path)
    rules_dir = root / ".cursor" / "rules"
    if rules_dir.is_dir():
        found.extend(sorted(rules_dir.glob("*.mdc")))
        found.extend(sorted(p for p in rules_dir.glob("*.md") if p not in found))
    # Preserve discovery order while dropping duplicates.
    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in found:
        if path in seen:
            continue
        seen.add(path)
        ordered.append(path)
    return ordered


def _strip_frontmatter(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1).strip()


def split_rule_chunks(
    path: Path,
    text: str,
    *,
    source_label: str = "",
    max_chars: int = 1200,
    max_chunks: int = 8,
) -> list[RuleChunk]:
    body = _strip_frontmatter(text)
    if not body:
        return []
    rel = source_label or path.name

    headings = list(_HEADING_RE.finditer(body))
    chunks: list[RuleChunk] = []
    if not headings:
        cleaned = " ".join(body.split())
        if cleaned:
            chunks.append(
                RuleChunk(
                    source=rel,
                    title=path.stem,
                    content=cleaned[:max_chars],
                )
            )
        return chunks[:max_chunks]

    for index, match in enumerate(headings):
        start = match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        section = " ".join(body[start:end].split())
        if not section:
            continue
        title = match.group(1).strip()
        chunks.append(
            RuleChunk(
                source=rel,
                title=title,
                content=f"{title}: {section}"[:max_chars],
            )
        )
        if len(chunks) >= max_chunks:
            break
    return chunks


def collect_rule_chunks(project_root: str | Path) -> list[RuleChunk]:
    root = Path(project_root).expanduser().resolve()
    chunks: list[RuleChunk] = []
    for path in discover_rule_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if len(text) > 200_000:
            text = text[:200_000]
        try:
            label = str(path.relative_to(root))
        except ValueError:
            label = path.name
        chunks.extend(split_rule_chunks(path, text, source_label=label))
    return chunks
