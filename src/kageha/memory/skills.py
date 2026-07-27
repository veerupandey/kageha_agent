"""Skill registry — agentskills.io / Anthropic Agent Skills compatible."""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from kageha.config import kageha_home, skills_dirs

# agentskills.io prefers hyphens; Kageha also allows underscores for legacy skills.
_NAME_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")

# Autoload / embedding floors (clearer than ad-hoc magic numbers).
# Token+trigger score must clear AUTOLOAD_MIN before L2 body injection.
DEFAULT_AUTOLOAD_MIN_SCORE = 3.0
# Cosine similarity must clear these before an embedding hit ranks / boosts.
EMBED_HIT_FLOOR = 0.22
EMBED_BOOST_FLOOR = 0.30
# Per-trigger phrase contribution when the phrase appears in the task.
TRIGGER_PHRASE_SCORE = 4.0
TRIGGER_MULTIWORD_BONUS = 2.0

# Mutually exclusive skill families: first winner blocks siblings.
EXCLUSIVE_SKILL_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"computer_use", "web_browse", "web_research"}),
)

# Intent cues for research (HTTP research_run) vs interactive browse (CDP).
# Keep these specific — avoid "what is" / "who is" (too many casual Q&A false positives).
_RESEARCH_CUES: tuple[str, ...] = (
    "research",
    "look up",
    "look this up",
    "find sources",
    "find source",
    "cite sources",
    "with citations",
    "sourced answer",
    "news about",
    "background on",
    "investigate",
    "research_run",
)
_BROWSE_CUES: tuple[str, ...] = (
    "open the site",
    "open website",
    "open url",
    "log in",
    "login",
    "sign in",
    "fill the form",
    "fill form",
    "browse to",
    "navigate to",
    "use comet",
    "browser_connect",
    "logged-in",
    "logged in",
    "interact with the page",
    "click through",
)

# Explicit opt-outs — do not autoload computer_use when the user rejects it.
_COMPUTER_NEGATION_CUES: tuple[str, ...] = (
    "do not need computer",
    "don't need computer",
    "dont need computer",
    "without computer",
    "no computer use",
    "not computer use",
    "skip computer",
    "don't use computer",
    "do not use computer",
)


def _whole_word_in(haystack: str, needle: str) -> bool:
    """True when ``needle`` appears as a whole token (not inside useful/callback)."""
    n = (needle or "").strip().lower()
    if not n or not haystack:
        return False
    return re.search(rf"(?<![a-z0-9_]){re.escape(n)}(?![a-z0-9_])", haystack) is not None


@dataclass
class AutoLoadResult:
    """Bodies injected into context plus skill names to activate."""

    text: str
    names: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    body: str
    trusted: bool = True
    license: str = ""
    compatibility: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    # Chat router: phrase → {kind: key|launch|status, …} for one-shot bypass.
    fast_paths: dict[str, dict[str, str]] = field(default_factory=dict)
    # Substrings that mark a session as in-domain for those fast paths.
    fast_path_when: list[str] = field(default_factory=list)
    # Intent phrases for automatic L1/L2 matching (declarative, per-skill).
    triggers: list[str] = field(default_factory=list)
    # Path globs — implicit match only when path_hints intersect.
    paths: list[str] = field(default_factory=list)
    # When True, skip implicit autoload (explicit /skill or $skill only).
    disable_model_invocation: bool = False

    @property
    def catalog_line(self) -> str:
        extra = ""
        if self.allowed_tools:
            extra = f" [tools: {' '.join(self.allowed_tools[:8])}]"
        if self.triggers:
            extra += f" [triggers:{len(self.triggers)}]"
        if self.paths:
            extra += f" [paths:{len(self.paths)}]"
        if self.disable_model_invocation:
            extra += " [manual]"
        if self.fast_paths:
            extra += f" [fast-path:{len(self.fast_paths)}]"
        return f"- {self.name}: {self.description}{extra}"

    def resource_roots(self) -> dict[str, Path]:
        return {
            "scripts": self.path / "scripts",
            "references": self.path / "references",
            "assets": self.path / "assets",
            "templates": self.path / "templates",
        }

    def list_resources(self) -> list[str]:
        out: list[str] = []
        for label, root in self.resource_roots().items():
            if not root.is_dir():
                continue
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    out.append(f"{label}/{p.relative_to(root).as_posix()}")
        return out


_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)


def _parse_allowed_tools(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        return [t for t in raw.split() if t.strip()]
    return []


def _parse_fast_path_action(raw: Any) -> dict[str, str] | None:
    """Normalize a fast-path value to ``{kind, key|app}``.

    Accepted forms:
    - ``key:Pause`` / ``launch:youtube`` / ``status``
    - ``{kind: key, key: Pause}``
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        kind = str(raw.get("kind") or "").strip().lower()
        if kind == "key" and raw.get("key"):
            return {"kind": "key", "key": str(raw["key"]).strip()}
        if kind == "launch" and (raw.get("app") or raw.get("name")):
            return {
                "kind": "launch",
                "app": str(raw.get("app") or raw.get("name")).strip(),
            }
        if kind == "status":
            return {"kind": "status"}
        return None
    text = str(raw).strip()
    if not text:
        return None
    low = text.lower()
    if low == "status":
        return {"kind": "status"}
    if ":" in text:
        kind, _, rest = text.partition(":")
        kind = kind.strip().lower()
        rest = rest.strip()
        if kind == "key" and rest:
            return {"kind": "key", "key": rest}
        if kind in {"launch", "app"} and rest:
            return {"kind": "launch", "app": rest}
    return None


def _parse_fast_paths(raw: Any) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    if not isinstance(raw, dict):
        return out
    for phrase, action_raw in raw.items():
        phrase_s = str(phrase or "").strip().lower()
        if not phrase_s:
            continue
        action = _parse_fast_path_action(action_raw)
        if action:
            out[phrase_s] = action
    return out


def _parse_fast_path_when(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def _parse_triggers(raw: Any) -> list[str]:
    """Normalize ``triggers`` frontmatter to lowercase phrase list."""
    if raw is None:
        return []
    if isinstance(raw, str):
        # Allow comma- or newline-separated single string.
        parts = re.split(r"[\n,]+", raw)
        return [p.strip().lower() for p in parts if p.strip()]
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            text = str(item or "").strip().lower()
            if text:
                out.append(text)
        return out
    return []


def _parse_paths(raw: Any) -> list[str]:
    """Normalize ``paths`` / ``globs`` frontmatter to glob pattern list."""
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = re.split(r"[\n,]+", raw)
        return [p.strip() for p in parts if p.strip()]
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def _parse_disable_model_invocation(meta: dict[str, Any]) -> bool:
    """``disable-model-invocation`` / ``allow_implicit_invocation: false``."""
    if bool(meta.get("disable-model-invocation") or meta.get("disable_model_invocation")):
        return True
    if meta.get("allow_implicit_invocation") is False:
        return True
    if meta.get("allow-implicit-invocation") is False:
        return True
    policy = meta.get("policy")
    if isinstance(policy, dict) and policy.get("allow_implicit_invocation") is False:
        return True
    return False


# Slash tokens that are never treated as skill names (modes / WebUI / packs).
_RESERVED_SLASH_TOKENS = frozenset(
    {
        "plan",
        "goal",
        "normal",
        "multitask",
        "new",
        "task",
        "tabs",
        "ask",
        "auto",
        "permissions",
        "labs",
        "browser",
        "research",
        "computer",
        "comet",
        "cmd",
        "memory",
        "artifacts",
        "jobs",
        "workbench",
        "skill",
        "skills",
        "help",
        "stop",
        "clear",
        "model",
        "models",
    }
)


def resolve_skill_name(registry: "SkillRegistry", token: str) -> str | None:
    """Resolve ``token`` to an installed skill name (hyphen/underscore tolerant)."""
    raw = (token or "").strip().lower()
    if not raw:
        return None
    # Alias: /computer <task> → computer_use skill
    if raw == "computer":
        raw = "computer_use"
    if registry.get(raw):
        return registry.get(raw).name  # type: ignore[union-attr]
    alt = raw.replace("-", "_")
    if alt != raw and registry.get(alt):
        return registry.get(alt).name  # type: ignore[union-attr]
    alt2 = raw.replace("_", "-")
    if alt2 != raw and registry.get(alt2):
        return registry.get(alt2).name  # type: ignore[union-attr]
    return None


def parse_skill_invocations(
    message: str, registry: "SkillRegistry | None" = None
) -> list[str]:
    """Extract explicit skill invocations: ``/skill name``, ``$name``, ``/name``.

    Explicit invocations bypass the autoload score floor and
    ``disable-model-invocation``.

    Special case: ``/computer <task>`` (not an admin subcommand) activates
    ``computer_use`` — same as ``/computer_use <task>``.
    """
    reg = registry or SkillRegistry()
    text = message or ""
    found: list[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        name = resolve_skill_name(reg, token)
        if name and name not in seen:
            seen.add(name)
            found.append(name)

    for m in re.finditer(
        r"(?i)(?:^|\s)/(?:skill|skills)\s+([a-z0-9][a-z0-9_-]*)", text
    ):
        _add(m.group(1))
    for m in re.finditer(r"(?i)(?:^|\s)\$([a-z0-9][a-z0-9_-]*)\b", text):
        _add(m.group(1))

    # /computer <natural language> → computer_use (admin verbs stay reserved)
    try:
        from kageha.chat.computer_commands import is_computer_admin_command

        low = text.strip().lower()
        if low.startswith("/computer") and not is_computer_admin_command(text):
            _add("computer_use")
    except Exception:  # noqa: BLE001
        pass

    for m in re.finditer(r"(?i)(?:^|\s)/([a-z0-9][a-z0-9_-]*)\b", text):
        token = m.group(1).lower()
        if token in _RESERVED_SLASH_TOKENS or token in {"skill", "skills"}:
            continue
        _add(token)
    return found


def strip_skill_invocations(message: str, names: list[str] | None = None) -> str:
    """Remove explicit skill slash/$ tokens from the user message."""
    text = message or ""
    text = re.sub(
        r"(?i)(?:^|\s)/(?:skill|skills)\s+[a-z0-9][a-z0-9_-]*", " ", text
    )
    if names:
        for name in names:
            tokens = {name, name.replace("_", "-"), name.replace("-", "_")}
            if name == "computer_use":
                tokens.add("computer")
            for token in tokens:
                text = re.sub(
                    rf"(?i)(?:^|\s)\${re.escape(token)}\b", " ", text
                )
                text = re.sub(
                    rf"(?i)(?:^|\s)/{re.escape(token)}\b", " ", text
                )
    else:
        text = re.sub(r"(?i)(?:^|\s)\$[a-z0-9][a-z0-9_-]*\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_path_hints(message: str, *, project_root: str | Path | None = None) -> list[str]:
    """Collect path-like tokens from the message for ``paths:`` scoping."""
    text = message or ""
    hints: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        value = (raw or "").strip().strip("'\"")
        if not value or value in seen:
            return
        seen.add(value)
        hints.append(value)
        # Also add basename for simple ``*.py`` patterns.
        base = Path(value).name
        if base and base not in seen:
            seen.add(base)
            hints.append(base)

    for m in re.finditer(r"@([^\s,;]+)", text):
        _add(m.group(1))
    for m in re.finditer(
        r"(?i)\b([\w./\\-]+\.(?:py|ts|tsx|js|jsx|md|json|ya?ml|toml|css|html|go|rs|java))\b",
        text,
    ):
        _add(m.group(1))
    if project_root:
        root = str(project_root).rstrip("/\\")
        if root and root not in seen:
            hints.append(root)
    return hints


def skill_paths_allow(skill: "Skill", path_hints: list[str] | None) -> bool:
    """Return whether ``skill.paths`` permits implicit matching for these hints."""
    if not skill.paths:
        return True
    if not path_hints:
        return False
    for hint in path_hints:
        hint_norm = hint.replace("\\", "/")
        base = Path(hint_norm).name
        for pattern in skill.paths:
            pat = pattern.strip()
            if not pat:
                continue
            if fnmatch.fnmatch(hint_norm, pat) or fnmatch.fnmatch(base, pat):
                return True
            # Also try matching against trailing path segments.
            if "/" not in pat.rstrip("*") and fnmatch.fnmatch(base, pat):
                return True
    return False


def autoload_min_score() -> float:
    """Minimum combined match score required to auto-inject a skill body (L2)."""
    raw = (os.environ.get("KAGEHA_SKILL_AUTOLOAD_MIN") or "").strip()
    if not raw:
        return DEFAULT_AUTOLOAD_MIN_SCORE
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_AUTOLOAD_MIN_SCORE


def _trigger_score(triggers: list[str], query_lower: str) -> float:
    """Score declarative trigger phrases against the task text.

    Matches use whole-word boundaries so ``call`` ≠ ``callback`` and
    skill-name token ``use`` ≠ ``useful``.
    """
    if not triggers or not query_lower:
        return 0.0
    score = 0.0
    hit_phrases: set[str] = set()
    for phrase in triggers:
        p = (phrase or "").strip().lower()
        if not p or p in hit_phrases:
            continue
        if _whole_word_in(query_lower, p) or (
            (" " in p or "-" in p) and p in query_lower
        ):
            hit_phrases.add(p)
            score += TRIGGER_PHRASE_SCORE
            if " " in p or "-" in p:
                score += TRIGGER_MULTIWORD_BONUS
            continue
        # Multi-token soft hit: all significant tokens present (order-free).
        parts = [t for t in re.findall(r"[a-z0-9_]+", p) if len(t) > 2]
        if len(parts) >= 2 and all(_whole_word_in(query_lower, t) for t in parts):
            hit_phrases.add(p)
            score += TRIGGER_PHRASE_SCORE * 0.75
    return score


def _research_browse_adjustment(skill_name: str, query_lower: str) -> float:
    """Prefer research_run path vs CDP browse path based on intent cues."""
    research = any(c in query_lower for c in _RESEARCH_CUES)
    browse = any(c in query_lower for c in _BROWSE_CUES)
    if skill_name == "web_research":
        if research and not browse:
            return 8.0
        if browse and not research:
            return -6.0
        if research and browse:
            return 3.0  # mixed: slight research lean; exclusive group still picks one
    if skill_name == "web_browse":
        if browse and not research:
            return 8.0
        if research and not browse:
            return -6.0
        if research and browse:
            return 5.0  # interactive cues win ties toward browse
    return 0.0


def _parse_skill_md(path: Path) -> Skill | None:
    text = path.read_text(errors="replace")
    name = path.parent.name
    description = ""
    body = text
    license_s = ""
    compatibility = ""
    metadata: dict[str, Any] = {}
    allowed: list[str] = []
    fast_paths: dict[str, dict[str, str]] = {}
    fast_when: list[str] = []
    triggers: list[str] = []
    paths: list[str] = []
    disable_model = False
    m = _FRONT.match(text)
    if m:
        meta = yaml.safe_load(m.group(1)) or {}
        if not isinstance(meta, dict):
            meta = {}
        body = m.group(2)
        name = str(meta.get("name") or name)
        description = str(meta.get("description") or "")
        license_s = str(meta.get("license") or "")
        compatibility = str(meta.get("compatibility") or "")
        md = meta.get("metadata") or {}
        metadata = dict(md) if isinstance(md, dict) else {}
        allowed = _parse_allowed_tools(
            meta.get("allowed-tools") or meta.get("allowed_tools")
        )
        fast_paths = _parse_fast_paths(
            meta.get("fast-path") or meta.get("fast_path")
        )
        fast_when = _parse_fast_path_when(
            meta.get("fast-path-when") or meta.get("fast_path_when")
        )
        triggers = _parse_triggers(meta.get("triggers"))
        paths = _parse_paths(meta.get("paths") or meta.get("globs"))
        disable_model = _parse_disable_model_invocation(meta)
    if not description:
        for line in body.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                description = line[:200]
                break
    return Skill(
        name=name,
        description=description or name,
        path=path.parent,
        body=body,
        license=license_s,
        compatibility=compatibility,
        metadata=metadata,
        allowed_tools=allowed,
        fast_paths=fast_paths,
        fast_path_when=fast_when,
        triggers=triggers,
        paths=paths,
        disable_model_invocation=disable_model,
    )


def collect_skill_fast_paths(
    registry: SkillRegistry | None = None,
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Merge ``fast-path`` maps from all skills (later skills override phrases)."""
    reg = registry or SkillRegistry()
    phrases: dict[str, dict[str, str]] = {}
    when: list[str] = []
    for skill in sorted(reg.skills.values(), key=lambda s: s.name):
        phrases.update(skill.fast_paths)
        when.extend(skill.fast_path_when)
    # de-dupe when markers preserving order
    seen: set[str] = set()
    uniq_when: list[str] = []
    for item in when:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq_when.append(item)
    return phrases, uniq_when


def validate_skill(skill: Skill) -> list[str]:
    """Return list of validation errors (empty = OK). agentskills.io rules."""
    errs: list[str] = []
    if not skill.name:
        errs.append("name missing")
    elif len(skill.name) > 64:
        errs.append("name longer than 64 chars")
    elif not _NAME_RE.match(skill.name):
        errs.append(
            "name must be lowercase letters, numbers, hyphens "
            "(no leading/trailing hyphen)"
        )
    if not skill.description:
        errs.append("description missing")
    elif len(skill.description) > 1024:
        errs.append("description longer than 1024 chars")
    if not (skill.path / "SKILL.md").is_file():
        errs.append("SKILL.md missing")
    if skill.compatibility and len(skill.compatibility) > 500:
        errs.append("compatibility longer than 500 chars")
    return errs


class SkillRegistry:
    def __init__(self) -> None:
        self.skills: dict[str, Skill] = {}
        self.reload()

    def reload(self) -> None:
        found: dict[str, Skill] = {}
        for root in skills_dirs():
            for skill_md in root.glob("*/SKILL.md"):
                if skill_md.parent.name in {"archive", ".archive"}:
                    continue
                skill = _parse_skill_md(skill_md)
                if skill:
                    found[skill.name] = skill
        self.skills = found

    def catalog(self, *, limit: int | None = None, query: str | None = None) -> str:
        """L1 skill list. With ``query``, rank by intent match then append the rest."""
        items = list(self.skills.values())
        if query and query.strip():
            scored = self.match_scored(query, limit=len(items) or 1)
            ranked = [s for _, s in scored]
            seen = {s.name for s in ranked}
            rest = sorted(
                (s for s in items if s.name not in seen),
                key=lambda s: s.name,
            )
            items = ranked + rest
        if limit is not None:
            items = items[:limit]
        if not items:
            return "(no skills)"
        return "\n".join(s.catalog_line for s in items)

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def match_scored(
        self, query: str, *, limit: int = 3, include_embeddings: bool = True
    ) -> list[tuple[float, Skill]]:
        """Rank skills as ``(score, skill)`` for L1/L2 intent matching.

        Score = token overlap + declarative ``triggers`` + family heuristics
        + optional embedding boost. Clear floors:
        - embedding hit: cosine > EMBED_HIT_FLOOR
        - embedding boost starts: cosine > EMBED_BOOST_FLOOR
        - L2 autoload: lexical score >= autoload_min_score() (embeddings
          reorder eligible skills but cannot invent weak false positives)
        """
        q = (query or "").lower()
        tokens = {t for t in re.findall(r"[a-z0-9_]+", q) if len(t) > 2}
        token_scores: dict[str, float] = {}
        for skill in self.skills.values():
            blob = f"{skill.name} {skill.description}".lower()
            score = float(sum(1 for t in tokens if t in blob))
            if skill.name.lower() in q or skill.name.lower().replace("_", "-") in q:
                score += 5
            # Whole-word name tokens only — "use" must not match "useful".
            for word in skill.name.lower().replace("-", "_").split("_"):
                if len(word) > 2 and _whole_word_in(q, word):
                    score += 2
            score += _trigger_score(skill.triggers, q)
            score += _research_browse_adjustment(skill.name, q)
            computer_negated = skill.name == "computer_use" and any(
                cue in q for cue in _COMPUTER_NEGATION_CUES
            )
            # Native macOS desktop computer-use.
            try:
                from kageha.harness.tools.computer_ready import task_wants_computer

                desktop_intent = task_wants_computer(q)
            except Exception:  # noqa: BLE001
                desktop_intent = False
            if skill.name == "computer_use" and not computer_negated and (
                desktop_intent
                or any(
                    k in q
                    for k in (
                        "computer_use",
                        "computer_get_state",
                        "computer_click",
                        "macos",
                        "mac os",
                        "desktop",
                        "native app",
                        "calculator",
                        "textedit",
                        "finder",
                        "click buttons",
                        "ax tree",
                        "accessibility",
                        "cua-driver",
                        "not browser",
                        "slack",
                    )
                )
            ):
                score += 12
            if computer_negated:
                score = 0.0
            # Prefer computer_use over web_browse for desktop apps (no URL).
            if skill.name == "web_browse" and desktop_intent and not any(
                cue in q for cue in _COMPUTER_NEGATION_CUES
            ):
                score = max(0.0, score - 12)
            elif skill.name == "web_browse" and any(
                k in q
                for k in (
                    "computer_use",
                    "computer_get_state",
                    "computer_click",
                    "not browser",
                    "native app",
                    "macos calculator",
                    "desktop app",
                )
            ):
                score = max(0.0, score - 8)
            if score > 0:
                token_scores[skill.name] = score

        combined: dict[str, float] = dict(token_scores)
        if include_embeddings:
            embed_scores: dict[str, float] = {}
            try:
                from kageha.memory.skill_embeddings import get_skill_embedding_index

                index = get_skill_embedding_index()
                if index.ensure(self.skills):
                    for hit in index.search(query, limit=max(limit * 3, 8)):
                        if hit.score > EMBED_HIT_FLOOR:
                            embed_scores[hit.name] = hit.score
            except Exception:  # noqa: BLE001
                embed_scores = {}

            # Embeddings boost existing lexical hits; solo semantic hits need a
            # high cosine so they do not autoload from vague casual chat.
            for name, sim in embed_scores.items():
                boost = max(0.0, (sim - EMBED_BOOST_FLOOR) * 20.0)
                if boost <= 0:
                    continue
                if name in combined:
                    combined[name] += boost
                elif sim >= 0.55:
                    combined[name] = boost

        ranked = sorted(
            (
                (score, self.skills[name])
                for name, score in combined.items()
                if name in self.skills and score > 0
            ),
            key=lambda x: (-x[0], x[1].name),
        )
        return ranked[:limit]

    def match(self, query: str, *, limit: int = 3) -> list[Skill]:
        """Rank skills by token/triggers + optional Gemini embedding similarity."""
        return [s for _, s in self.match_scored(query, limit=limit)]

    def _format_skill_chunk(self, skill: Skill) -> str:
        meta_bits = []
        if skill.allowed_tools:
            meta_bits.append(f"allowed-tools: {' '.join(skill.allowed_tools)}")
        if skill.compatibility:
            meta_bits.append(f"compatibility: {skill.compatibility}")
        if skill.disable_model_invocation:
            meta_bits.append("invocation: explicit-only")
        if skill.paths:
            meta_bits.append(f"paths: {', '.join(skill.paths[:6])}")
        header = f"### skill:{skill.name}\n{skill.description}\n"
        if meta_bits:
            header += "(" + "; ".join(meta_bits) + ")\n"
        return header + f"\n{skill.body.strip()}\n"

    def auto_load_for_task(
        self,
        task: str,
        *,
        limit: int = 2,
        max_chars: int = 6000,
        force_names: list[str] | None = None,
        path_hints: list[str] | None = None,
    ) -> AutoLoadResult:
        """Return top skill bodies + names for progressive disclosure (L2).

        Only skills whose *lexical* score (tokens/triggers/heuristics) clears
        ``autoload_min_score()`` are injected — unless listed in
        ``force_names`` (explicit ``/skill`` / ``$skill``), which bypass the
        floor and ``disable-model-invocation``.

        Path-scoped skills (``paths:`` / ``globs:``) only autoload when
        ``path_hints`` match; forced skills ignore path scope.
        """
        min_score = autoload_min_score()
        forced: list[str] = []
        seen_force: set[str] = set()
        for token in force_names or []:
            name = resolve_skill_name(self, token)
            if name and name not in seen_force:
                seen_force.add(name)
                forced.append(name)

        match_task = strip_skill_invocations(task, forced) if forced else (task or "")
        if not match_task.strip():
            match_task = task or ""

        lexical = self.match_scored(
            match_task, limit=max(limit * 3, 8), include_embeddings=False
        )
        eligible: set[str] = set()
        for score, skill in lexical:
            if skill.name in seen_force:
                continue
            if skill.disable_model_invocation:
                continue
            if not skill_paths_allow(skill, path_hints):
                continue
            if score >= min_score:
                eligible.add(skill.name)

        scored = [
            (score, skill)
            for score, skill in self.match_scored(
                match_task, limit=max(limit * 3, 8)
            )
            if skill.name in eligible
        ]

        selected: list[tuple[float, Skill]] = []
        # Forced skills always load first (bypass floor / disable / paths).
        for name in forced:
            skill = self.get(name)
            if skill:
                selected.append((999.0, skill))

        blocked: set[str] = set(seen_force)
        exclusive_primary: str | None = None
        # If a forced skill is in an exclusive family, block siblings.
        for _, skill in selected:
            for group in EXCLUSIVE_SKILL_GROUPS:
                if skill.name in group:
                    blocked.update(group - {skill.name})
                    if exclusive_primary is None:
                        exclusive_primary = skill.name

        if exclusive_primary is None:
            for score, skill in scored:
                if skill.name in blocked:
                    continue
                selected.append((score, skill))
                for group in EXCLUSIVE_SKILL_GROUPS:
                    if skill.name in group:
                        blocked.update(group - {skill.name})
                        if exclusive_primary is None:
                            exclusive_primary = skill.name
                if exclusive_primary is not None:
                    break
                # Keep room for forced skills already selected.
                if len(selected) >= max(limit, len(forced)):
                    break

        if exclusive_primary is not None:
            # Keep all forced + the exclusive winner (may already be forced).
            chosen = [
                (sc, sk)
                for sc, sk in selected
                if sk.name in seen_force or sk.name == exclusive_primary
            ]
        else:
            # Forced + up to `limit` matched (forced don't count against limit).
            matched = [(sc, sk) for sc, sk in selected if sk.name not in seen_force]
            chosen = [(sc, sk) for sc, sk in selected if sk.name in seen_force]
            chosen.extend(matched[:limit])

        if not chosen:
            return AutoLoadResult(text="", names=[], scores={})

        parts = [
            "## Auto-loaded skills (follow these procedures; do not invent steps)",
            f"Matched for task: {(match_task or task)[:200]}",
            "",
        ]
        if forced:
            parts.insert(
                2,
                "Explicit invocation: " + ", ".join(f"/{n}" for n in forced),
            )
        used = 0
        names: list[str] = []
        score_map: dict[str, float] = {}
        for score, skill in chosen:
            chunk = self._format_skill_chunk(skill)
            if used + len(chunk) > max_chars and used > 0:
                parts.append(f"(truncated: skipped remaining body for {skill.name})")
                break
            if used + len(chunk) > max_chars:
                chunk = chunk[: max_chars - used] + "\n…(truncated)\n"
            parts.append(chunk)
            used += len(chunk)
            names.append(skill.name)
            score_map[skill.name] = score
            try:
                from kageha.memory.curator import record_skill_use

                record_skill_use(skill.name, path=skill.path)
            except Exception:  # noqa: BLE001
                pass
        return AutoLoadResult(text="\n".join(parts), names=names, scores=score_map)

    def load_body(self, name: str) -> str:
        skill = self.get(name)
        if not skill:
            return f"ERROR: unknown skill {name}"
        try:
            from kageha.memory.curator import record_skill_use

            record_skill_use(skill.name, path=skill.path)
        except Exception:  # noqa: BLE001
            pass
        header = f"# skill:{skill.name}\n{skill.description}\n\n"
        if skill.allowed_tools:
            header += f"allowed-tools: {' '.join(skill.allowed_tools)}\n\n"
        resources = skill.list_resources()
        if resources:
            header += "Resources:\n" + "\n".join(f"- {r}" for r in resources[:40]) + "\n\n"
        return header + skill.body

    def read_resource(self, name: str, relpath: str) -> str:
        skill = self.get(name)
        if not skill:
            return f"ERROR: unknown skill {name}"
        rel = (relpath or "").strip().lstrip("./")
        if not rel or ".." in rel.split("/"):
            return "ERROR: invalid path"
        allowed_prefixes = ("scripts/", "references/", "assets/", "templates/")
        if not rel.startswith(allowed_prefixes):
            return (
                "ERROR: path must be under scripts/, references/, assets/, or templates/"
            )
        path = (skill.path / rel).resolve()
        if not str(path).startswith(str(skill.path.resolve())):
            return "ERROR: path escapes skill directory"
        if not path.is_file():
            return f"ERROR: file not found: {rel}"
        # Binary-ish: report size
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip"}:
            return f"[binary {path.suffix} {path.stat().st_size} bytes] path={rel}"
        text = path.read_text(errors="replace")
        if len(text) > 100_000:
            return text[:100_000] + "\n...[truncated]"
        return text

    def resolve_script(self, name: str, script: str) -> Path | str:
        skill = self.get(name)
        if not skill:
            return f"ERROR: unknown skill {name}"
        rel = (script or "").strip().lstrip("./")
        if not rel:
            return "ERROR: empty script"
        if not rel.startswith("scripts/"):
            rel = f"scripts/{rel}"
        if ".." in rel.split("/"):
            return "ERROR: invalid script path"
        path = (skill.path / rel).resolve()
        if not str(path).startswith(str((skill.path / "scripts").resolve())):
            return "ERROR: script must be under scripts/"
        if not path.is_file():
            return f"ERROR: script not found: {rel}"
        return path

    def add_local(self, source: Path) -> Skill:
        source = source.resolve()
        if source.is_file() and source.name == "SKILL.md":
            source = source.parent
        if not (source / "SKILL.md").is_file():
            raise FileNotFoundError(f"No SKILL.md in {source}")
        parsed = _parse_skill_md(source / "SKILL.md")
        folder = parsed.name if parsed else source.name
        dest = kageha_home() / "skills" / folder
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest)
        self.reload()
        skill = self.skills.get(folder) or self.get(parsed.name if parsed else folder)
        if not skill:
            for s in self.skills.values():
                if s.path == dest:
                    return s
            raise RuntimeError("Skill installed but not discovered")
        return skill

    def create_stub(self, name: str, description: str = "") -> Path:
        dest = kageha_home() / "skills" / name
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "scripts").mkdir(exist_ok=True)
        (dest / "references").mkdir(exist_ok=True)
        (dest / "assets").mkdir(exist_ok=True)
        md = (
            f"---\nname: {name}\ndescription: {description or name}\n"
            f"license: MIT\nmetadata:\n  author: kageha\n---\n\n"
            f"# {name}\n\nDescribe the procedure here.\n\n"
            "## Steps\n\n1. ...\n\n## Verification\n\n- ...\n"
        )
        (dest / "SKILL.md").write_text(md)
        try:
            from kageha.memory.curator import ensure_created

            ensure_created(name)
        except Exception:  # noqa: BLE001
            pass
        self.reload()
        return dest

    def remove(self, name: str) -> None:
        skill = self.get(name)
        if not skill:
            raise KeyError(name)
        user_root = kageha_home() / "skills"
        if not str(skill.path).startswith(str(user_root)):
            raise PermissionError("Can only remove user-installed skills")
        from kageha.memory.curator import is_pinned

        if is_pinned(name):
            raise PermissionError(
                f"Skill {name} is pinned — unpin it under ~/.kageha before remove"
            )
        shutil.rmtree(skill.path)
        self.reload()

    def manage(
        self,
        action: str,
        name: str,
        content: str = "",
        *,
        require_hitl_create: bool = True,
        approved: bool = False,
    ) -> str:
        """Hermes-compatible skill_manage actions."""
        if action == "create":
            if require_hitl_create and not approved:
                return "NEEDS_APPROVAL: agent skill create requires HITL"
            dest = self.create_stub(name)
            if content:
                (dest / "SKILL.md").write_text(content)
            self.reload()
            return f"Created skill {name} at {dest}"
        if action == "edit":
            skill = self.get(name)
            if not skill:
                return f"ERROR: unknown skill {name}"
            if require_hitl_create and not approved:
                return "NEEDS_APPROVAL: agent skill edit requires HITL"
            (skill.path / "SKILL.md").write_text(content)
            self.reload()
            return f"Edited skill {name}"
        if action == "patch":
            skill = self.get(name)
            if not skill:
                return f"ERROR: unknown skill {name}"
            if require_hitl_create and not approved:
                return "NEEDS_APPROVAL: agent skill patch requires HITL"
            if "<<<>>>" not in content:
                return "ERROR: patch content must be OLD<<<>>>NEW"
            old, new = content.split("<<<>>>", 1)
            text = (skill.path / "SKILL.md").read_text()
            if old not in text:
                return "ERROR: old string not found"
            (skill.path / "SKILL.md").write_text(text.replace(old, new, 1))
            self.reload()
            return f"Patched skill {name}"
        if action == "delete":
            if require_hitl_create and not approved:
                return "NEEDS_APPROVAL: agent skill delete requires HITL"
            from kageha.memory.curator import is_pinned

            if is_pinned(name):
                return (
                    f"ERROR: skill {name} is pinned — "
                    f"unpin skill {name} under ~/.kageha before delete"
                )
            self.remove(name)
            return f"Deleted skill {name}"
        if action == "observe":
            skill = self.get(name)
            if not skill:
                return f"ERROR: unknown skill {name}"
            if require_hitl_create and not approved:
                return "NEEDS_APPROVAL: agent skill observe requires HITL"
            note = (content or "").strip()
            if not note:
                return "ERROR: observe content (notes) is required"
            md_path = skill.path / "SKILL.md"
            text = md_path.read_text() if md_path.is_file() else ""
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            entry = f"\n- ({stamp}) {note}\n"
            if "## Observations" in text:
                text = text.rstrip() + entry
            else:
                text = text.rstrip() + "\n\n## Observations\n" + entry
            md_path.write_text(text if text.endswith("\n") else text + "\n")
            self.reload()
            return f"Recorded observation on skill {name}"
        if action == "refine":
            skill = self.get(name)
            if not skill:
                return f"ERROR: unknown skill {name}"
            if require_hitl_create and not approved:
                return "NEEDS_APPROVAL: agent skill refine requires HITL"
            body = (content or "").strip()
            if not body:
                return "ERROR: refine content is required"
            md_path = skill.path / "SKILL.md"
            text = md_path.read_text() if md_path.is_file() else ""
            # Prefer surgical patch when OLD<<<>>>NEW is provided.
            if "<<<>>>" in body:
                old, new = body.split("<<<>>>", 1)
                if old not in text:
                    return "ERROR: old string not found"
                text = text.replace(old, new, 1)
            else:
                stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                block = f"\n### {stamp}\n\n{body}\n"
                if "## Refinements" in text:
                    text = text.rstrip() + "\n" + block
                else:
                    text = text.rstrip() + "\n\n## Refinements\n" + block
            md_path.write_text(text if text.endswith("\n") else text + "\n")
            self.reload()
            return f"Refined skill {name}"
        if action == "write_file":
            skill = self.get(name)
            if not skill:
                return f"ERROR: unknown skill {name}"
            if require_hitl_create and not approved:
                return "NEEDS_APPROVAL: agent skill write_file requires HITL"
            if "\n---\n" not in content:
                return "ERROR: write_file content must be relpath\\n---\\nbody"
            rel, body = content.split("\n---\n", 1)
            rel = rel.strip()
            if not rel.startswith(("scripts/", "references/", "assets/", "templates/")):
                return "ERROR: file must be under scripts/, references/, assets/, or templates/"
            path = skill.path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
            return f"Wrote {rel}"
        if action == "list_resources":
            skill = self.get(name)
            if not skill:
                return f"ERROR: unknown skill {name}"
            res = skill.list_resources()
            return "\n".join(res) if res else "(no resources)"
        return (
            f"ERROR: unknown action {action}. "
            "Use: create, edit, patch, delete, observe, refine, write_file, list_resources"
        )
