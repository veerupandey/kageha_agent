"""Install Agent Skills from local paths or GitHub (Anthropic / agentskills.io compatible)."""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from kageha.config import kageha_home
from kageha.memory.skills import Skill, SkillRegistry, _parse_skill_md

# Well-known marketplaces (Agent Skills / Claude-compatible layout)
KNOWN_REPOS = {
    "anthropics/skills": {
        "zip": "https://github.com/anthropics/skills/archive/refs/heads/main.zip",
        "note": "Official Anthropic example + document skills (pdf/docx/pptx/xlsx).",
    },
}


@dataclass
class InstallResult:
    installed: list[str]
    skipped: list[str]
    source: str
    dest_root: str


def _github_zip_url(owner: str, repo: str, ref: str = "main") -> str:
    return f"https://github.com/{owner}/{repo}/archive/refs/heads/{ref}.zip"


def parse_install_spec(spec: str) -> dict[str, str]:
    """Parse install specs.

    Supported forms:
      - /local/path/to/skill-or-repo
      - anthropics/skills
      - anthropics/skills/pdf
      - anthropics/skills@main
      - anthropics/skills@main/pdf
      - https://github.com/anthropics/skills
      - https://github.com/anthropics/skills/tree/main/skills/pdf
    """
    s = (spec or "").strip().rstrip("/")
    if not s:
        raise ValueError("empty install spec")

    local = Path(s).expanduser()
    if local.exists():
        return {"kind": "local", "path": str(local.resolve())}

    # Full GitHub URL
    if "github.com" in s:
        u = urlparse(s if "://" in s else "https://" + s)
        parts = [p for p in u.path.split("/") if p]
        if len(parts) < 2:
            raise ValueError(f"bad github url: {spec}")
        owner, repo = parts[0], parts[1].removesuffix(".git")
        ref = "main"
        skill = ""
        if len(parts) >= 4 and parts[2] == "tree":
            ref = parts[3]
            rest = parts[4:]
            if rest and rest[0] == "skills" and len(rest) >= 2:
                skill = rest[1]
            elif rest:
                skill = rest[-1]
        return {
            "kind": "github",
            "owner": owner,
            "repo": repo,
            "ref": ref,
            "skill": skill,
        }

    # owner/repo[@ref][/skill]
    if "@" in s:
        base, after = s.split("@", 1)
        if "/" not in base:
            raise ValueError(f"unrecognized spec {spec!r}")
        owner, repo = base.split("/", 1)
        if "/" in after:
            ref, skill = after.split("/", 1)
        else:
            ref, skill = after, ""
        return {
            "kind": "github",
            "owner": owner,
            "repo": repo,
            "ref": ref or "main",
            "skill": skill,
        }

    m = re.match(
        r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:/([A-Za-z0-9_.-]+))?$",
        s,
    )
    if not m:
        raise ValueError(
            f"unrecognized spec {spec!r}; use path, owner/repo, or owner/repo/skill"
        )
    return {
        "kind": "github",
        "owner": m.group(1),
        "repo": m.group(2),
        "ref": "main",
        "skill": m.group(3) or "",
    }


def _download_zip(url: str, dest_zip: Path) -> None:
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url, timeout=120) as resp:  # noqa: S310 — github archive URLs only
        dest_zip.write_bytes(resp.read())


def _extract_zip(zip_path: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)
    children = [p for p in dest_dir.iterdir() if p.is_dir()]
    if len(children) == 1:
        return children[0]
    return dest_dir


def find_skill_dirs(root: Path) -> list[Path]:
    """Find directories that contain SKILL.md (Agent Skills layout)."""
    found: list[Path] = []
    if (root / "SKILL.md").is_file():
        found.append(root)
    for skill_md in sorted(root.rglob("SKILL.md")):
        parent = skill_md.parent
        if any(part.startswith(".") for part in parent.parts):
            continue
        if "node_modules" in parent.parts:
            continue
        if parent not in found:
            found.append(parent)
    found.sort(key=lambda p: (len(p.parts), str(p)))
    return found


def _install_skill_dir(source: Path, *, force: bool = False) -> Skill:
    skill = _parse_skill_md(source / "SKILL.md")
    if not skill:
        raise ValueError(f"invalid SKILL.md in {source}")
    dest_name = skill.name
    dest = kageha_home() / "skills" / dest_name
    if dest.exists():
        if not force:
            raise FileExistsError(
                f"skill {dest_name!r} already installed at {dest}; use --force"
            )
        shutil.rmtree(dest)
    shutil.copytree(source, dest)
    out = _parse_skill_md(dest / "SKILL.md")
    if not out:
        raise RuntimeError(f"installed but failed to parse {dest}")
    return out


def _matches_filter(cand: Path, skill_filter: str) -> bool:
    if not skill_filter:
        return True
    sf = skill_filter.lower()
    if cand.name.lower() == sf:
        return True
    parsed = _parse_skill_md(cand / "SKILL.md")
    return bool(parsed and parsed.name.lower() == sf)


def _finish_install(
    candidates: list[Path],
    *,
    only_set: set[str],
    force: bool,
    source_label: str,
    reg: SkillRegistry,
) -> InstallResult:
    if not candidates:
        raise FileNotFoundError(f"no SKILL.md found in {source_label}")

    installed: list[str] = []
    skipped: list[str] = []
    for cand in candidates:
        parsed_skill = _parse_skill_md(cand / "SKILL.md")
        name = (parsed_skill.name if parsed_skill else cand.name).lower()
        if only_set and name not in only_set and cand.name.lower() not in only_set:
            skipped.append(name)
            continue
        try:
            skill = _install_skill_dir(cand, force=force)
            installed.append(skill.name)
        except FileExistsError:
            skipped.append(f"{name} (exists)")
        except Exception as e:  # noqa: BLE001
            skipped.append(f"{name} ({e})")

    reg.reload()
    return InstallResult(
        installed=installed,
        skipped=skipped,
        source=source_label,
        dest_root=str(kageha_home() / "skills"),
    )


def install_skills(
    spec: str,
    *,
    only: list[str] | None = None,
    force: bool = False,
    registry: SkillRegistry | None = None,
) -> InstallResult:
    """Install one or more Agent Skills from a local path or GitHub repo."""
    parsed = parse_install_spec(spec)
    reg = registry or SkillRegistry()
    only_set = {x.strip().lower() for x in (only or []) if x.strip()}

    if parsed["kind"] == "local":
        root = Path(parsed["path"])
        candidates = find_skill_dirs(root)
        # If a single skill filter via folder name is desired, only_set already handles it
        return _finish_install(
            candidates,
            only_set=only_set,
            force=force,
            source_label=str(root),
            reg=reg,
        )

    owner, repo = parsed["owner"], parsed["repo"]
    ref = parsed.get("ref") or "main"
    skill_filter = (parsed.get("skill") or "").lower()
    key = f"{owner}/{repo}"
    zip_url = KNOWN_REPOS.get(key, {}).get("zip") or _github_zip_url(owner, repo, ref)

    with tempfile.TemporaryDirectory(prefix="kageha-skills-") as tmp:
        tmp_path = Path(tmp)
        zpath = tmp_path / "repo.zip"
        _download_zip(zip_url, zpath)
        extracted = _extract_zip(zpath, tmp_path / "unpacked")
        search_root = extracted / "skills" if (extracted / "skills").is_dir() else extracted
        candidates = [
            c for c in find_skill_dirs(search_root) if _matches_filter(c, skill_filter)
        ]
        # Stage outside search (still inside tmp) so names are stable
        staging = tmp_path / "staging"
        staging.mkdir()
        staged: list[Path] = []
        for c in candidates:
            dest = staging / c.name
            n = 1
            while dest.exists():
                dest = staging / f"{c.name}_{n}"
                n += 1
            shutil.copytree(c, dest)
            staged.append(dest)

        return _finish_install(
            staged,
            only_set=only_set,
            force=force,
            source_label=f"github:{owner}/{repo}@{ref}",
            reg=reg,
        )


def list_remote_skills(spec: str = "anthropics/skills") -> list[dict[str, str]]:
    """Download repo zip and list skill names/descriptions (no install)."""
    parsed = parse_install_spec(spec)
    if parsed["kind"] != "github":
        raise ValueError("list_remote_skills expects a github owner/repo spec")
    owner, repo = parsed["owner"], parsed["repo"]
    ref = parsed.get("ref") or "main"
    key = f"{owner}/{repo}"
    zip_url = KNOWN_REPOS.get(key, {}).get("zip") or _github_zip_url(owner, repo, ref)
    rows: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="kageha-skills-list-") as tmp:
        tmp_path = Path(tmp)
        zpath = tmp_path / "repo.zip"
        _download_zip(zip_url, zpath)
        extracted = _extract_zip(zpath, tmp_path / "unpacked")
        search = extracted / "skills" if (extracted / "skills").is_dir() else extracted
        for cand in find_skill_dirs(search):
            skill = _parse_skill_md(cand / "SKILL.md")
            if not skill:
                continue
            rows.append(
                {
                    "name": skill.name,
                    "description": skill.description[:160],
                    "path": str(cand.relative_to(extracted)),
                }
            )
    rows.sort(key=lambda r: r["name"])
    return rows
