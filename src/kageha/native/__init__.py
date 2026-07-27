"""Optional native (Rust/PyO3) acceleration with pure-Python fallbacks.

Hot paths may load a compiled extension when present and when the
``native-index`` feature / ``KAGEHA_NATIVE_INDEX`` flag allows it. CI and
default installs stay green with no Rust toolchain.

See ``docs/WEBUI.md`` — ship a Rust crate only when warm ``@`` query p95
exceeds the ~20ms budget on a large monorepo.
"""

from __future__ import annotations

from kageha.native.index import (
    index_backend,
    native_index_available,
    native_index_enabled,
    rank_paths,
)

__all__ = [
    "index_backend",
    "native_index_available",
    "native_index_enabled",
    "rank_paths",
]
