"""Singleton lock and lifecycle helpers for channel listener processes."""

from __future__ import annotations

import os
from pathlib import Path

from kageha.config import kageha_home

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback is intentionally conservative.
    fcntl = None  # type: ignore[assignment]


class ChannelProcessLock:
    """Hold an OS-level lock so multiple channel listeners cannot overlap."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (kageha_home() / "daemon" / "channels.lock")
        self._file = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+")
        if fcntl is None:
            return
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._file.close()
            self._file = None
            raise RuntimeError(
                "another Kageha channel listener is already running; "
                "use `kageha channels status` or stop it with Ctrl-C"
            ) from exc
        self._file.seek(0)
        self._file.truncate()
        self._file.write(str(os.getpid()))
        self._file.flush()

    def release(self) -> None:
        if self._file is None:
            return
        if fcntl is not None:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None

    def __enter__(self) -> "ChannelProcessLock":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def channel_process_is_running(path: Path | None = None) -> bool:
    lock = ChannelProcessLock(path)
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock.path.open("a+")
    if fcntl is None:
        handle.close()
        return False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        handle.close()
