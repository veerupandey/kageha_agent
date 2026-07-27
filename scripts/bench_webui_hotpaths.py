#!/usr/bin/env python3
"""Microbench WebUI hot paths: file_index query + SSE payload map.

Baselines for the optional native-index gate (see docs/WEBUI.md):

  - Warm ``@`` / file query p95  < 20ms
  - Cold index build (this repo)  informational (100k-file budget is <2s)

Usage::

    uv run python scripts/bench_webui_hotpaths.py
    uv run python scripts/bench_webui_hotpaths.py --check-budget
    uv run python scripts/bench_webui_hotpaths.py --root /path/to/repo --iters 200

Exit 0 always unless ``--check-budget`` and a measured budget fails.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any


def _percentile(sorted_ms: list[float], p: float) -> float:
    if not sorted_ms:
        return 0.0
    if len(sorted_ms) == 1:
        return sorted_ms[0]
    k = (len(sorted_ms) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_ms) - 1)
    if f == c:
        return sorted_ms[f]
    return sorted_ms[f] + (sorted_ms[c] - sorted_ms[f]) * (k - f)


def _stats(samples_ms: list[float]) -> dict[str, float]:
    ordered = sorted(samples_ms)
    return {
        "n": float(len(ordered)),
        "min_ms": ordered[0] if ordered else 0.0,
        "p50_ms": _percentile(ordered, 50),
        "p95_ms": _percentile(ordered, 95),
        "max_ms": ordered[-1] if ordered else 0.0,
        "mean_ms": statistics.fmean(ordered) if ordered else 0.0,
    }


def _bench_file_index(root: Path, *, iters: int, queries: list[str], limit: int) -> dict[str, Any]:
    from kageha.native import index_backend, native_index_available, native_index_enabled
    from kageha.project.file_index import FileIndex, reset_file_indexes_for_tests

    reset_file_indexes_for_tests()
    idx = FileIndex(root)

    t0 = time.perf_counter()
    n_files = idx.rebuild()
    cold_ms = (time.perf_counter() - t0) * 1000.0

    # Warm path: index already built; measure query only.
    per_query: dict[str, dict[str, Any]] = {}
    all_warm: list[float] = []
    for q in queries:
        samples: list[float] = []
        for _ in range(iters):
            t1 = time.perf_counter()
            hits = idx.query(q, limit=limit)
            samples.append((time.perf_counter() - t1) * 1000.0)
        st = _stats(samples)
        all_warm.extend(samples)
        per_query[q if q else "(empty)"] = {
            **st,
            "top": [h["path"] for h in hits[:5]],
            "hit_count": len(hits),
        }

    return {
        "root": str(root),
        "indexed": n_files,
        "truncated": idx.truncated,
        "backend": index_backend(),
        "native_available": native_index_available(),
        "native_enabled": native_index_enabled(),
        "cold_build_ms": round(cold_ms, 3),
        "warm_query": {**_stats(all_warm), "by_query": per_query},
        "budget_warm_p95_ms": 20.0,
        "budget_cold_build_ms": 2000.0,
    }


def _sample_sse_payloads() -> list[tuple[str, dict[str, Any]]]:
    """Representative runtime events for SSE map microbench."""
    big_ax = "AX " + ("node " * 2000)
    return [
        (
            "tool_started",
            {
                "tool": "bash",
                "args_preview": "ls -la src/kageha",
                "side_effect": "filesystem",
            },
        ),
        (
            "tool_completed",
            {
                "tool": "bash",
                "state": "success",
                "duration_ms": 42,
                "result": "ok\n" * 80,
            },
        ),
        (
            "tool_started",
            {
                "tool": "computer_click",
                "args_preview": "click 12",
                "ax_tree": big_ax,
                "snapshot": big_ax,
            },
        ),
        (
            "tool_completed",
            {
                "tool": "computer_get_state",
                "state": "success",
                "ax_tree": big_ax,
                "base64_png": "A" * 5000,
            },
        ),
        (
            "planned",
            {
                "source": "agent",
                "current_stage": "execute",
                "plan": [
                    {"id": "1", "description": "Inspect files", "tools": ["bash"]},
                    {"id": "2", "description": "Edit", "tools": ["write"]},
                ],
                "goals": [{"id": "g1", "description": "Ship Phase E bench"}],
            },
        ),
    ]


def _bench_sse_map(*, iters: int) -> dict[str, Any]:
    from kageha.webui.server import _sse_payload_view, _stream_event_view

    samples = _sample_sse_payloads()
    view_ms: list[float] = []
    payload_ms: list[float] = []
    for _ in range(iters):
        for kind, payload in samples:
            t0 = time.perf_counter()
            _stream_event_view(kind, payload)
            view_ms.append((time.perf_counter() - t0) * 1000.0)

            t1 = time.perf_counter()
            _sse_payload_view(kind, payload)
            payload_ms.append((time.perf_counter() - t1) * 1000.0)

    return {
        "events_per_iter": len(samples),
        "stream_event_view": _stats(view_ms),
        "sse_payload_view": _stats(payload_ms),
        "note": "Informational; paint budget (<16ms) is client-side.",
    }


def _fmt_stats(st: dict[str, float]) -> str:
    return (
        f"n={int(st['n'])}  min={st['min_ms']:.3f}  p50={st['p50_ms']:.3f}  "
        f"p95={st['p95_ms']:.3f}  max={st['max_ms']:.3f}  mean={st['mean_ms']:.3f} ms"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Project root to index (default: repo root containing this script)",
    )
    parser.add_argument("--iters", type=int, default=100, help="Warm query iterations per q")
    parser.add_argument("--sse-iters", type=int, default=200, help="SSE map outer iterations")
    parser.add_argument("--limit", type=int, default=40, help="file_index query limit")
    parser.add_argument(
        "--query",
        action="append",
        dest="queries",
        default=None,
        help="Query string (repeatable). Default: server, file_index, '', native",
    )
    parser.add_argument(
        "--check-budget",
        action="store_true",
        help="Exit 1 if warm query p95 exceeds 20ms (Phase E promote-to-Rust gate)",
    )
    parser.add_argument("--json", action="store_true", help="Emit full JSON report to stdout")
    args = parser.parse_args(argv)

    script_dir = Path(__file__).resolve().parent
    root = (args.root or script_dir.parent).resolve()
    queries = args.queries or ["server", "file_index", "", "native"]

    file_report = _bench_file_index(
        root, iters=max(1, args.iters), queries=queries, limit=args.limit
    )
    sse_report = _bench_sse_map(iters=max(1, args.sse_iters))

    report = {
        "file_index": file_report,
        "sse_map": sse_report,
        "phase_e": {
            "rust_crate_shipped": False,
            "facade": "kageha.native",
            "feature_flag": "native-index (optional-deps stub) + KAGEHA_NATIVE_INDEX env",
            "promote_when": "warm file query p95 > 20ms on ~100k-file monorepo after ignore rules",
        },
    }

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return _exit_code(file_report, check=args.check_budget)

    fi = file_report
    print("=== WebUI hotpath microbench ===")
    print(f"root:     {fi['root']}")
    print(f"indexed:  {fi['indexed']} files (truncated={fi['truncated']})")
    print(
        f"backend:  {fi['backend']}  "
        f"(native_available={fi['native_available']} enabled={fi['native_enabled']})"
    )
    print(f"cold build: {fi['cold_build_ms']:.3f} ms  (budget < {fi['budget_cold_build_ms']:.0f} ms)")
    warm = fi["warm_query"]
    print(f"warm query: {_fmt_stats(warm)}  (budget p95 < {fi['budget_warm_p95_ms']:.0f} ms)")
    for q, st in warm["by_query"].items():
        top = ", ".join(st.get("top") or [])
        print(f"  q={q!r}: p95={st['p95_ms']:.3f} ms  hits={st['hit_count']}  top=[{top}]")

    print("--- SSE map ---")
    print(f"stream_event_view: {_fmt_stats(sse_report['stream_event_view'])}")
    print(f"sse_payload_view:  {_fmt_stats(sse_report['sse_payload_view'])}")
    print(f"note: {sse_report['note']}")
    print("--- Phase E ---")
    print("Rust crate: not shipped (façade + bench + native-index stub only)")
    print("Promote crates/kageha-index when warm p95 fails the 20ms budget.")

    return _exit_code(file_report, check=args.check_budget)


def _exit_code(file_report: dict[str, Any], *, check: bool) -> int:
    if not check:
        return 0
    p95 = float(file_report["warm_query"]["p95_ms"])
    budget = float(file_report["budget_warm_p95_ms"])
    if p95 > budget:
        print(
            f"BUDGET FAIL: warm query p95 {p95:.3f} ms > {budget:.0f} ms "
            "(consider shipping crates/kageha-index)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
