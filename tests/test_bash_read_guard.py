"""bash read builtin guard must not trip on Python .read()."""

from __future__ import annotations

import re


_BASH_READ_GUARD = re.compile(r"(?<![.\w])\bread\b(?!\s*\()")


def test_bash_read_guard_allows_python_read_calls() -> None:
    for cmd in (
        'python -c \'print(open("x").read())\'',
        "path.read_text()",
        "resp.read(); db.read_text()",
        "self.rfile.read(1024)",
    ):
        assert _BASH_READ_GUARD.search(cmd) is None, cmd


def test_bash_read_guard_blocks_interactive_read() -> None:
    for cmd in (
        'read -p "name?" x',
        "while read line; do :; done",
        "cat file | read a",
    ):
        assert _BASH_READ_GUARD.search(cmd) is not None, cmd
