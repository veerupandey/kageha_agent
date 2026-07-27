"""Load custom tool packs from user/project tool directories."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator

from kageha.config import tools_dirs
from kageha.harness.tools.base import ToolRegistry

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext

WarnFn = Callable[[str, BaseException], None]


def iter_user_tool_modules() -> Iterator[tuple[str, Path]]:
    """Yield ``(label, path)`` for each candidate tool module/package."""
    for root in tools_dirs():
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if path.name.startswith("_") or path.name.startswith("."):
                continue
            if path.is_file() and path.suffix == ".py":
                yield (f"{root.name}/{path.name}", path)
            elif path.is_dir() and (
                (path / "__init__.py").is_file() or (path / "register.py").is_file()
            ):
                yield (f"{root.name}/{path.name}", path)


def load_user_tool_dirs(
    ctx: "HarnessContext",
    *,
    on_error: WarnFn | None = None,
) -> list[tuple[str, ToolRegistry]]:
    """Import ``register(ctx)`` from each discovered user tool module/package.

    Conventions per directory in :func:`kageha.config.tools_dirs`:
    - ``foo.py`` with ``def register(ctx) -> ToolRegistry``
    - ``foo/__init__.py`` (or ``foo/register.py``) exporting ``register``
    """
    loaded: list[tuple[str, ToolRegistry]] = []
    for label, path in iter_user_tool_modules():
        try:
            mod = _load_module(path)
            if mod is None:
                continue
            register = getattr(mod, "register", None)
            if not callable(register):
                continue
            extra = register(ctx)
            if isinstance(extra, ToolRegistry):
                loaded.append((label, extra))
        except Exception as e:  # noqa: BLE001
            if on_error is not None:
                on_error(label, e)
            else:
                raise
    return loaded


def _load_module(path: Path) -> Any | None:
    if path.is_file() and path.suffix == ".py":
        return _import_file(path)
    if path.is_dir():
        init = path / "__init__.py"
        reg = path / "register.py"
        if init.is_file():
            return _import_file(init, module_name=f"kageha_user_tools_{path.name}")
        if reg.is_file():
            return _import_file(reg, module_name=f"kageha_user_tools_{path.name}_register")
    return None


def _import_file(path: Path, *, module_name: str | None = None) -> Any:
    name = module_name or f"kageha_user_tools_{path.stem}_{abs(hash(str(path))) % 10_000_000}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    if path.name == "__init__.py":
        mod.__path__ = [str(path.parent)]  # type: ignore[attr-defined]
    spec.loader.exec_module(mod)
    return mod
