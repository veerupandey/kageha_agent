"""Native steering system — .kageha/steering/ with inclusion modes.

Steering files provide persistent context that shapes agent behavior.
Three inclusion modes:

- always: Applied to every conversation (default)
- fileMatch: Loaded when files matching a glob pattern are in context
- manual: User explicitly invokes via /steer <name> or $steer_name

Frontmatter format (YAML between --- delimiters):
---
inclusion: always | fileMatch | manual
fileMatchPattern: '**/*.py'  # required when inclusion is fileMatch
description: Short description for catalog display
priority: 0-100  # higher = loaded earlier (default 50)
---
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class SteeringRule:
    """A single steering file's metadata and content."""

    name: str
    path: Path
    inclusion: str = "always"  # always, fileMatch, manual
    file_match_pattern: str = ""
    description: str = ""
    priority: int = 50
    content: str = ""

    @property
    def is_always(self) -> bool:
        return self.inclusion == "always"

    @property
    def is_file_match(self) -> bool:
        return self.inclusion == "fileMatch"

    @property
    def is_manual(self) -> bool:
        return self.inclusion == "manual"

    def matches_file(self, filepath: str) -> bool:
        """Check if a file path matches this rule's pattern."""
        if not self.is_file_match or not self.file_match_pattern:
            return False
        # Support ** glob patterns by converting to fnmatch-compatible form
        pattern = self.file_match_pattern
        # fnmatch doesn't handle ** well; use simple approach
        if "**/" in pattern:
            # "**/*.py" → match just the suffix
            suffix_pattern = pattern.split("**/")[-1]
            return fnmatch(filepath, suffix_pattern) or fnmatch(
                filepath.split("/")[-1] if "/" in filepath else filepath,
                suffix_pattern,
            )
        return fnmatch(filepath, pattern)


def parse_steering_file(path: Path) -> SteeringRule | None:
    """Parse a steering markdown file with optional YAML frontmatter."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None

    name = path.stem
    inclusion = "always"
    file_match_pattern = ""
    description = ""
    priority = 50
    content = raw

    # Parse frontmatter if present
    match = _FRONTMATTER_RE.match(raw)
    if match:
        try:
            fm = yaml.safe_load(match.group(1))
            if isinstance(fm, dict):
                inclusion = str(fm.get("inclusion", "always")).strip()
                file_match_pattern = str(fm.get("fileMatchPattern", "")).strip()
                description = str(fm.get("description", "")).strip()
                priority = int(fm.get("priority", 50))
        except (yaml.YAMLError, ValueError, TypeError):
            pass
        content = raw[match.end():]

    if inclusion not in ("always", "fileMatch", "manual"):
        inclusion = "always"

    return SteeringRule(
        name=name,
        path=path,
        inclusion=inclusion,
        file_match_pattern=file_match_pattern,
        description=description,
        priority=priority,
        content=content.strip(),
    )


@dataclass
class SteeringRegistry:
    """Manages all steering rules for a project."""

    rules: list[SteeringRule] = field(default_factory=list)

    def always_rules(self) -> list[SteeringRule]:
        """Get rules that are always included, sorted by priority."""
        return sorted(
            [r for r in self.rules if r.is_always],
            key=lambda r: -r.priority,
        )

    def match_files(self, filepaths: list[str]) -> list[SteeringRule]:
        """Get rules that match any of the given file paths."""
        matched = []
        for rule in self.rules:
            if rule.is_file_match:
                for fp in filepaths:
                    if rule.matches_file(fp):
                        matched.append(rule)
                        break
        return sorted(matched, key=lambda r: -r.priority)

    def manual_rules(self) -> list[SteeringRule]:
        """Get rules available for manual inclusion."""
        return sorted(
            [r for r in self.rules if r.is_manual],
            key=lambda r: r.name,
        )

    def get_by_name(self, name: str) -> SteeringRule | None:
        """Look up a rule by name."""
        for rule in self.rules:
            if rule.name == name:
                return rule
        return None

    def resolve_context(
        self,
        *,
        active_files: list[str] | None = None,
        manual_names: list[str] | None = None,
    ) -> str:
        """Resolve all applicable steering content for the current context.

        Returns concatenated steering content with section headers.
        """
        sections: list[str] = []

        # Always-on rules
        for rule in self.always_rules():
            if rule.content:
                sections.append(f"## Steering: {rule.name}\n{rule.content}")

        # File-matched rules
        if active_files:
            for rule in self.match_files(active_files):
                if rule.content:
                    sections.append(f"## Steering ({rule.name} — file match)\n{rule.content}")

        # Manually invoked rules
        if manual_names:
            for name in manual_names:
                rule = self.get_by_name(name)
                if rule and rule.content:
                    sections.append(f"## Steering ({rule.name} — manual)\n{rule.content}")

        return "\n\n".join(sections)

    def catalog(self) -> str:
        """Human-readable catalog of all available steering rules."""
        lines = ["Available steering rules:\n"]
        for rule in sorted(self.rules, key=lambda r: (r.inclusion, r.name)):
            mode = f"[{rule.inclusion}]"
            pattern = f" ({rule.file_match_pattern})" if rule.file_match_pattern else ""
            desc = f" — {rule.description}" if rule.description else ""
            lines.append(f"  {rule.name} {mode}{pattern}{desc}")
        return "\n".join(lines)


def load_steering_registry(
    project_root: Path | None = None,
    user_home: Path | None = None,
) -> SteeringRegistry:
    """Load steering rules from project and user directories.

    Searches:
    - .kageha/steering/ (project-level)
    - ~/.kageha/steering/ (user-level, lower priority)
    """
    from kageha.config import kageha_home

    rules: list[SteeringRule] = []
    seen_names: set[str] = set()

    # Project-level steering (higher priority)
    if project_root:
        project_dir = project_root / ".kageha" / "steering"
        if project_dir.is_dir():
            for path in sorted(project_dir.glob("*.md")):
                rule = parse_steering_file(path)
                if rule and rule.name not in seen_names:
                    rules.append(rule)
                    seen_names.add(rule.name)

    # User-level steering (lower priority)
    home = user_home or kageha_home()
    user_dir = home / "steering"
    if user_dir.is_dir():
        for path in sorted(user_dir.glob("*.md")):
            rule = parse_steering_file(path)
            if rule and rule.name not in seen_names:
                # User-level rules get lower priority unless explicitly set high
                if rule.priority == 50:
                    rule.priority = 30
                rules.append(rule)
                seen_names.add(rule.name)

    return SteeringRegistry(rules=rules)
