"""In-memory project file index for ``@`` path search.

Pure-Python v1: walks a project root, respects ``.gitignore`` plus common noise
directories, and ranks matches with simple substring / segment heuristics.
"""

from __future__ import annotations

import fnmatch
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Always skipped directory names (case-sensitive, as on disk).
DEFAULT_NOISE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".coverage",
        "htmlcov",
        ".eggs",
        ".idea",
        ".vscode",
        ".next",
        ".nuxt",
        ".turbo",
        "coverage",
        "target",
        ".parcel-cache",
        ".sass-cache",
        ".cache",
        "vendor",
    }
)

DEFAULT_NOISE_FILE_GLOBS: tuple[str, ...] = (
    "*.pyc",
    "*.pyo",
    "*.so",
    "*.dylib",
    "*.o",
    "*.a",
    "*.class",
    "*.egg-info",
    ".DS_Store",
    "Thumbs.db",
)

# Hard cap so huge monorepos stay responsive.
DEFAULT_MAX_FILES = 50_000


def resolve_index_root(root: str | Path | None = None) -> Path:
    """Resolve project root: explicit arg → ``KAGEHA_PROJECT_ROOT`` → cwd."""
    candidates: list[str | Path | None] = [
        root,
        os.environ.get("KAGEHA_PROJECT_ROOT"),
        Path.cwd(),
    ]
    for raw in candidates:
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        path = Path(text).expanduser()
        try:
            path = path.resolve()
        except OSError:
            continue
        if path.is_dir():
            return path
    return Path.cwd().resolve()


@dataclass(frozen=True, slots=True)
class _IgnoreRule:
    pattern: str
    negated: bool
    directory_only: bool
    anchored: bool


def _parse_gitignore_lines(text: str) -> list[_IgnoreRule]:
    rules: list[_IgnoreRule] = []
    for raw in text.splitlines():
        line = raw.rstrip("\n\r")
        if not line or line.lstrip().startswith("#"):
            continue
        # Escaped \# is a literal; otherwise strip trailing spaces unless escaped.
        if line.endswith("\\ "):
            line = line[:-2] + " "
        else:
            line = line.rstrip()
        negated = False
        if line.startswith("!"):
            negated = True
            line = line[1:]
        if not line:
            continue
        directory_only = line.endswith("/")
        if directory_only:
            line = line[:-1]
        anchored = line.startswith("/")
        if anchored:
            line = line[1:]
        # Collapse leading **/ for matching convenience.
        if line.startswith("**/"):
            line = line[3:]
            anchored = False
        rules.append(
            _IgnoreRule(
                pattern=line,
                negated=negated,
                directory_only=directory_only,
                anchored=anchored,
            )
        )
    return rules


def _gitignore_match(rel_posix: str, rule: _IgnoreRule, *, is_dir: bool) -> bool:
    if rule.directory_only and not is_dir:
        return False
    path = rel_posix
    name = path.rsplit("/", 1)[-1]
    pat = rule.pattern
    if rule.anchored:
        return fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(path, pat.rstrip("/"))
    # Unanchored: match basename or any path suffix / segment.
    if "/" not in pat:
        return fnmatch.fnmatch(name, pat) or any(
            fnmatch.fnmatch(seg, pat) for seg in path.split("/")
        )
    return fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(path, "*/" + pat)


class GitIgnoreFilter:
    """Root ``.gitignore`` + hardcoded noise for directory walks."""

    def __init__(
        self,
        root: Path,
        *,
        noise_dirs: frozenset[str] = DEFAULT_NOISE_DIRS,
        noise_file_globs: tuple[str, ...] = DEFAULT_NOISE_FILE_GLOBS,
        extra_gitignore: str | None = None,
    ) -> None:
        self.root = root
        self.noise_dirs = noise_dirs
        self.noise_file_globs = noise_file_globs
        rules: list[_IgnoreRule] = []
        gi = root / ".gitignore"
        if gi.is_file():
            try:
                rules.extend(_parse_gitignore_lines(gi.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
        if extra_gitignore:
            rules.extend(_parse_gitignore_lines(extra_gitignore))
        self.rules = rules

    def ignore_dir(self, name: str, rel_posix: str) -> bool:
        if name in self.noise_dirs:
            return True
        return self._ignored(rel_posix, is_dir=True)

    def ignore_file(self, name: str, rel_posix: str) -> bool:
        for glob in self.noise_file_globs:
            if fnmatch.fnmatch(name, glob):
                return True
        return self._ignored(rel_posix, is_dir=False)

    def _ignored(self, rel_posix: str, *, is_dir: bool) -> bool:
        ignored = False
        for rule in self.rules:
            if _gitignore_match(rel_posix, rule, is_dir=is_dir):
                ignored = not rule.negated
        return ignored


def _chars_in_order(needle: str, haystack: str) -> bool:
    """True if every char of ``needle`` appears in order in ``haystack``."""
    if not needle:
        return True
    i = 0
    for ch in haystack:
        if ch == needle[i]:
            i += 1
            if i >= len(needle):
                return True
    return False


def score_path(
    path: str,
    query: str,
    *,
    mtime: float = 0.0,
    now: float | None = None,
) -> float:
    """Rank a relative path against ``query``. Higher is better; ``-1`` = no match."""
    q = (query or "").strip().lower().replace("\\", "/")
    path_norm = path.replace("\\", "/")
    path_l = path_norm.lower()
    name = path_l.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    segments = path_l.split("/")
    clock = time.time() if now is None else now

    if not q:
        # Empty query: prefer recent, then shorter paths.
        age_s = max(0.0, clock - (mtime or 0.0))
        return 1_000.0 - min(age_s / 3600.0, 720.0) - len(path_norm) * 0.01

    score = 0.0
    if name == q or stem == q:
        score = 1000.0
    elif stem.startswith(q):
        score = 900.0
    elif name.startswith(q):
        score = 850.0
    elif q in name:
        score = 700.0
    elif any(seg == q for seg in segments):
        score = 600.0
    elif any(seg.startswith(q) for seg in segments):
        score = 500.0
    elif q in path_l:
        score = 350.0
    elif _chars_in_order(q, path_l):
        score = 80.0
    else:
        return -1.0

    # Prefer matches nearer the filename / end of path.
    idx = path_l.rfind(q) if q in path_l else -1
    if idx >= 0:
        score += 40.0 * (idx / max(len(path_l), 1))

    # Shorter paths win slight ties.
    score -= len(path_norm) * 0.08

    # Mild recency bias (mtime known).
    if mtime > 0:
        age_h = max(0.0, (clock - mtime) / 3600.0)
        score -= min(age_h, 24.0 * 60.0) * 0.005

    return score


@dataclass(slots=True)
class IndexedFile:
    path: str  # posix-relative to root
    mtime: float


class FileIndex:
    """In-memory path list with warm/rebuild + fuzzy query."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        max_files: int = DEFAULT_MAX_FILES,
    ) -> None:
        self.root = resolve_index_root(root)
        self.max_files = max(1, int(max_files))
        self._files: list[IndexedFile] = []
        self._built_at: float = 0.0
        self._truncated: bool = False
        self._lock = threading.RLock()

    @property
    def size(self) -> int:
        return len(self._files)

    @property
    def truncated(self) -> bool:
        return self._truncated

    @property
    def built_at(self) -> float:
        return self._built_at

    def warm(self) -> int:
        """Build the index if empty; return file count."""
        with self._lock:
            if self._files:
                return len(self._files)
            return self._rebuild_unlocked()

    def rebuild(self) -> int:
        """Force a full rescan."""
        with self._lock:
            return self._rebuild_unlocked()

    def set_root(self, root: str | Path | None) -> Path:
        """Point at a new root and clear the index (lazy rebuild on next warm/query)."""
        with self._lock:
            resolved = resolve_index_root(root)
            if resolved != self.root:
                self.root = resolved
                self._files = []
                self._built_at = 0.0
                self._truncated = False
            return self.root

    def query(self, q: str = "", *, limit: int = 40) -> list[dict[str, Any]]:
        """Return ``[{path, score}, ...]`` ranked best-first.

        Ranking goes through ``kageha.native.rank_paths`` so an optional Rust
        extension can accelerate the hot loop; pure Python is always the default
        fallback (no toolchain required).
        """
        lim = max(1, min(int(limit), 500))
        with self._lock:
            if not self._files:
                self._rebuild_unlocked()
            entries = [(item.path, item.mtime) for item in self._files]
        # Rank outside the rebuild lock so native/Python scoring doesn't block warm.
        from kageha.native.index import rank_paths

        return rank_paths(entries, q, limit=lim)

    def _rebuild_unlocked(self) -> int:
        filt = GitIgnoreFilter(self.root)
        files: list[IndexedFile] = []
        truncated = False
        root = self.root
        try:
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                dir_path = Path(dirpath)
                try:
                    rel_dir = dir_path.relative_to(root).as_posix()
                except ValueError:
                    dirnames[:] = []
                    continue
                if rel_dir == ".":
                    rel_dir = ""

                # Prune ignored directories in-place.
                keep: list[str] = []
                for name in dirnames:
                    if name in filt.noise_dirs or name.startswith(".git"):
                        continue
                    child_rel = f"{rel_dir}/{name}" if rel_dir else name
                    if filt.ignore_dir(name, child_rel):
                        continue
                    keep.append(name)
                dirnames[:] = keep

                for name in filenames:
                    rel = f"{rel_dir}/{name}" if rel_dir else name
                    if filt.ignore_file(name, rel):
                        continue
                    full = dir_path / name
                    try:
                        st = full.stat()
                    except OSError:
                        continue
                    if not os.path.isfile(full):
                        continue
                    files.append(IndexedFile(path=rel, mtime=float(st.st_mtime)))
                    if len(files) >= self.max_files:
                        truncated = True
                        dirnames[:] = []
                        break
                if truncated:
                    break
        except OSError:
            pass

        files.sort(key=lambda f: f.path)
        self._files = files
        self._truncated = truncated
        self._built_at = time.time()
        return len(files)


# Process-wide cache keyed by resolved root (used by the Web UI).
_GLOBAL: dict[str, FileIndex] = {}
_GLOBAL_LOCK = threading.Lock()


def get_file_index(root: str | Path | None = None) -> FileIndex:
    """Return a shared ``FileIndex`` for ``root``, warming on first use."""
    resolved = resolve_index_root(root)
    key = str(resolved)
    with _GLOBAL_LOCK:
        idx = _GLOBAL.get(key)
        if idx is None:
            idx = FileIndex(resolved)
            _GLOBAL[key] = idx
    idx.warm()
    return idx


def reset_file_indexes_for_tests() -> None:
    """Clear the process-wide cache (test helper)."""
    with _GLOBAL_LOCK:
        _GLOBAL.clear()
