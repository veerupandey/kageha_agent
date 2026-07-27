"""Project brain: AGENTS.md / KAGEHA.md / CLAUDE.md / .cursorrules + .kageha/rules."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Prefer first match among these root instruction files (no duplication).
_ROOT_INSTRUCTION_CANDIDATES = (
    "AGENTS.md",
    "KAGEHA.md",
    "CLAUDE.md",
    ".cursorrules",
    ".cursor/rules.md",
)

_MAX_ROOT_CHARS = 12_000
_MAX_RULE_CHARS = 4_000
_MAX_RULES = 24
_MAX_TOTAL_CHARS = 24_000

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_GLOBS_RE = re.compile(r"(?im)^\s*globs?\s*:\s*(.+)$")


@dataclass
class ProjectRule:
    name: str
    body: str
    globs: list[str] = field(default_factory=list)
    path: str = ""


@dataclass
class ProjectBrain:
    project_root: Path
    root_file: str = ""
    root_text: str = ""
    rules: list[ProjectRule] = field(default_factory=list)
    command_names: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.root_text.strip() and not self.rules


def resolve_project_root(raw: str | Path | None) -> Path | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    path = Path(text).expanduser()
    try:
        path = path.resolve()
    except OSError:
        return None
    if not path.is_dir():
        return None
    return path


def _read_text(path: Path, limit: int) -> str:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    raw = raw.strip()
    if len(raw) > limit:
        return raw[: limit - 20].rstrip() + "\n…[truncated]"
    return raw


def _parse_rule_file(path: Path) -> ProjectRule | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    globs: list[str] = []
    body = raw
    m = _FRONTMATTER_RE.match(raw)
    if m:
        fm = m.group(1)
        body = raw[m.end() :]
        gm = _GLOBS_RE.search(fm)
        if gm:
            val = gm.group(1).strip().strip("[]")
            globs = [p.strip().strip("'\"") for p in val.split(",") if p.strip()]
    body = body.strip()
    if not body:
        return None
    if len(body) > _MAX_RULE_CHARS:
        body = body[: _MAX_RULE_CHARS - 20].rstrip() + "\n…[truncated]"
    return ProjectRule(
        name=path.stem,
        body=body,
        globs=globs,
        path=str(path),
    )


def _load_root_instructions(root: Path) -> tuple[str, str]:
    for name in _ROOT_INSTRUCTION_CANDIDATES:
        path = root / name
        if path.is_file():
            return name, _read_text(path, _MAX_ROOT_CHARS)
    # Cursor can store many rule files under .cursor/rules/
    cursor_rules = root / ".cursor" / "rules"
    if cursor_rules.is_dir():
        chunks: list[str] = []
        for path in sorted(cursor_rules.glob("**/*"))[:12]:
            if path.suffix.lower() not in {".md", ".mdc", ".txt"}:
                continue
            text = _read_text(path, _MAX_RULE_CHARS)
            if text:
                chunks.append(f"### {path.name}\n{text}")
        if chunks:
            joined = "\n\n".join(chunks)
            if len(joined) > _MAX_ROOT_CHARS:
                joined = joined[: _MAX_ROOT_CHARS - 20].rstrip() + "\n…[truncated]"
            return ".cursor/rules/", joined
    return "", ""


def _load_kageha_rules(root: Path) -> list[ProjectRule]:
    rules_dir = root / ".kageha" / "rules"
    if not rules_dir.is_dir():
        return []
    out: list[ProjectRule] = []
    for path in sorted(rules_dir.glob("**/*")):
        if path.suffix.lower() not in {".md", ".mdc", ".txt"}:
            continue
        rule = _parse_rule_file(path)
        if rule:
            out.append(rule)
        if len(out) >= _MAX_RULES:
            break
    return out


def _list_commands(root: Path) -> list[str]:
    cmd_dir = root / ".kageha" / "commands"
    if not cmd_dir.is_dir():
        return []
    names: list[str] = []
    for path in sorted(cmd_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in {".md", ".txt"}:
            names.append(path.stem)
    return names


def load_project_brain(project_root: str | Path | None) -> ProjectBrain | None:
    root = resolve_project_root(project_root)
    if root is None:
        return None
    root_file, root_text = _load_root_instructions(root)
    rules = _load_kageha_rules(root)
    commands = _list_commands(root)
    brain = ProjectBrain(
        project_root=root,
        root_file=root_file,
        root_text=root_text,
        rules=rules,
        command_names=commands,
    )
    return None if brain.empty else brain


def select_rules(
    brain: ProjectBrain,
    *,
    touched_paths: list[str] | None = None,
) -> list[ProjectRule]:
    """Return always-on rules plus glob-matched rules for touched paths.

    When ``touched_paths`` is omitted/empty, include glob rules too so
    project conventions still apply on the first turn (Claude/Cursor-like).
    """
    paths = [p.replace("\\", "/") for p in (touched_paths or [])]
    selected: list[ProjectRule] = []
    for rule in brain.rules:
        if not rule.globs:
            selected.append(rule)
            continue
        if not paths:
            # First turn / no edits yet — still load scoped rules.
            selected.append(rule)
            continue
        for pattern in rule.globs:
            from fnmatch import fnmatch

            if any(fnmatch(p, pattern) or fnmatch(Path(p).name, pattern) for p in paths):
                selected.append(rule)
                break
    return selected


def render_project_brain(
    brain: ProjectBrain | None,
    *,
    touched_paths: list[str] | None = None,
) -> str:
    if brain is None or brain.empty:
        return ""
    parts: list[str] = ["## Project instructions"]
    parts.append(f"Project root: `{brain.project_root}`")
    if brain.root_file and brain.root_text:
        parts.append(f"### From {brain.root_file}")
        parts.append(brain.root_text)
    rules = select_rules(brain, touched_paths=touched_paths)
    if rules:
        parts.append("### Project rules")
        for rule in rules:
            label = rule.name
            if rule.globs:
                label += f" (globs: {', '.join(rule.globs)})"
            parts.append(f"#### {label}")
            parts.append(rule.body)
    if brain.command_names:
        parts.append(
            "### Project slash commands\n"
            "Available via `/project:<name>` or `/cmd <name>`: "
            + ", ".join(f"`{n}`" for n in brain.command_names)
        )
    text = "\n\n".join(parts).strip()
    if len(text) > _MAX_TOTAL_CHARS:
        return text[: _MAX_TOTAL_CHARS - 20].rstrip() + "\n…[truncated]"
    return text


def load_project_command(project_root: str | Path | None, name: str) -> str | None:
    root = resolve_project_root(project_root)
    if root is None:
        return None
    stem = (name or "").strip().lstrip("/")
    if stem.lower().startswith("project:"):
        stem = stem.split(":", 1)[1].strip()
    if not stem or "/" in stem or "\\" in stem or ".." in stem:
        return None
    for ext in (".md", ".txt"):
        path = root / ".kageha" / "commands" / f"{stem}{ext}"
        if path.is_file():
            text = _read_text(path, _MAX_ROOT_CHARS)
            return text or None
    return None
