"""Built-in shell/file/todo tools registered via entry point."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kageha.harness.approvals import ApprovalDecision, ApprovalRequest, cli_ask_human
from kageha.harness.sandbox import run_shell
from kageha.harness.tools.base import ToolRegistry, tool

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext


def register(ctx: "HarnessContext") -> ToolRegistry:
    reg = ToolRegistry()
    ws = ctx.workspace
    gate = ctx.approvals
    todos_path = ws.root / "todo.md"
    human_question_lock = asyncio.Lock()
    human_question_state: dict[str, str] = {}

    def _cwd() -> Path:
        return ctx.coding_root()

    @tool(
        description=(
            "Escalate this session to a deeper agent mode on the next turn. "
            "mode: plan (default, gentle research+plan), spec (clarify+DAG), "
            "or goal (autonomous until SUCCESS). "
            "Use only for large multi-deliverable work. "
            "For ordinary asks, just use tools and stop when done."
        ),
        risk_class="safe",
    )
    async def escalate_plan(reason: str = "", mode: str = "plan") -> str:
        from kageha.chat.turn_manager import ESCALATE_PLAN_FLAG
        from kageha.loop.mode_policy import normalize_agent_mode, write_agent_mode_flag

        note = (reason or "model requested deeper mode").strip()[:500]
        agent_mode = normalize_agent_mode(mode or "plan")
        if agent_mode == "normal":
            agent_mode = "plan"
        (ws.root / ESCALATE_PLAN_FLAG).write_text(note + "\n", encoding="utf-8")
        write_agent_mode_flag(ws.root, agent_mode)
        return (
            f"Escalated: the next turn will use agent_mode={agent_mode} "
            "(full plan→verify loop). "
            "For this turn, break the work into clear steps yourself and continue "
            f"with tools. Reason: {note}"
        )

    @tool(
        description=(
            "Request human Approve/Deny for HIGH-RISK gates only. Always blocks "
            "(ignores tool auto-approve). ALLOWED uses: Plan/Spec Build after a "
            "real plan.md, computer_use / OS input, destructive shell, messaging "
            "sends, or skill installs. Do NOT call for ordinary reads, searches, "
            "or simple chat. Prefer ask_human for clarifications. "
            "risk_class must be one of: plan, computer_input, destructive, "
            "shell_network_or_destructive, messaging, skill_write, elevated."
        ),
        risk_class="hitl",
    )
    async def request_approval(
        reason: str,
        action: str = "request_approval",
        risk_class: str = "plan",
    ) -> str:
        detail = (reason or "").strip()
        if not detail:
            return json.dumps({"status": "error", "error": "reason is required"})
        act = (action or "request_approval").strip() or "request_approval"
        risk = (risk_class or "plan").strip().lower() or "plan"
        allowed = {
            "plan",
            "computer_input",
            "destructive",
            "shell_network_or_destructive",
            "messaging",
            "skill_write",
            "elevated",
        }
        if risk not in allowed:
            return json.dumps(
                {
                    "status": "error",
                    "error": (
                        f"risk_class={risk!r} not allowed for request_approval. "
                        f"Use one of: {', '.join(sorted(allowed))}. "
                        "Do not ask approval for routine tools."
                    ),
                    "approved": False,
                }
            )
        ok = await gate.require_explicit(
            ApprovalRequest(
                action=act,
                detail=detail[:4000],
                risk_class=risk,
                default=ApprovalDecision.ASK,
            )
        )
        payload = {
            "status": (
                "approved"
                if ok
                else ("suggested" if gate.last_feedback else "denied")
            ),
            "approved": bool(ok),
            "action": act,
            "risk_class": risk,
        }
        if gate.last_feedback and not ok:
            payload["feedback"] = gate.last_feedback
            payload["instruction"] = (
                "Follow the user's suggestion; do not repeat the denied action."
            )
        return json.dumps(payload)

    @tool(
        description=(
            "Ask ONE blocking clarification in the live terminal. Use only when a "
            "reasonable assumption would materially change the result. For binary "
            "questions provide short yes_label and no_label strings; the user can "
            "reply y/n. Never call repeatedly in one turn. Do not use bash `read`. "
            "Optionally save the answer to a workspace-relative path."
        ),
        risk_class="hitl",
    )
    async def ask_human(
        question: str,
        yes_label: str = "",
        no_label: str = "",
        save_path: str = "",
    ) -> str:
        key = re.sub(r"[^a-z0-9]+", " ", question.lower()).strip()
        async with human_question_lock:
            if "key" in human_question_state:
                if human_question_state["key"] == key:
                    return json.dumps(
                        {
                            "answer": human_question_state.get("answer", ""),
                            "reused": True,
                            "instruction": "Proceed; do not ask this question again.",
                        }
                    )
                return json.dumps(
                    {
                        "status": "clarification_limit",
                        "instruction": (
                            "One clarification was already asked this turn. "
                            "Use that answer or make a reasonable assumption and proceed."
                        ),
                    }
                )

            dest = save_path.strip()
            if ctx.meta.get("defer_human_input") or os.environ.get(
                "KAGEHA_CHAT_MODE"
            ) == "1":
                human_question_state["key"] = key
                human_question_state["answer"] = ""
                return json.dumps(
                    {
                        "status": "needs_user_input",
                        "question": question.strip(),
                        "yes_label": yes_label.strip(),
                        "no_label": no_label.strip(),
                    }
                )
            answer = await cli_ask_human(
                question,
                yes_label=yes_label,
                no_label=no_label,
                dest_hint=dest,
            )
            human_question_state["key"] = key
            human_question_state["answer"] = answer
            if dest:
                p = ws.path(dest)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(
                    answer + ("\n" if answer and not answer.endswith("\n") else "")
                )
                return json.dumps({"answer": answer, "saved": dest})
            return json.dumps({"answer": answer})

    @tool(
        description=(
            "Run a shell command. Privilege ladder (ask the human when you need more):\n"
            "1) default — OS sandbox, NO network\n"
            "2) network=true — sandbox + internet (HITL ask; prefer this for pip/curl/"
            "uv when first-class tools cannot help)\n"
            "3) elevated=true — FULL HOST ESCAPE outside the sandbox (ALWAYS asks the "
            "human, even with --auto-approve). Use only when sandbox blocks writes/"
            "exec that network=true cannot fix.\n"
            "Prefer first-class tools first: download_file, web_fetch, "
            "nano_banana_generate/edit, install_python_packages. "
            "Do NOT elevated=true just for network. Deliverables → $KAGEHA_ARTIFACTS/."
        )
    )
    async def bash(
        command: str,
        network: bool = False,
        elevated: bool = False,
    ) -> str:
        # Interactive shell prompts are invisible (stdout piped) — force ask_human
        if re.search(r"\bread\b|\bread\s+-p\b", command):
            return (
                "ERROR: Do not use bash `read` for human input — prompts are invisible. "
                "Call ask_human(question=..., save_path=...) instead."
            )
        # Steer common failure modes to first-class tools before hitting the sandbox.
        low = (command or "").lower()
        if re.search(r"\b(curl|wget)\b", low) and re.search(
            r"\.(png|jpe?g|gif|webp|mp4|pdf|zip)(\b|$)|/artifacts/|kageha_artifacts",
            low,
        ):
            return (
                "ERROR: Prefer download_file(url=..., path='artifacts/…') for binary "
                "downloads — it bypasses shell sandbox write/network limits. "
                "Do not use curl/wget for images or artifacts."
            )
        if re.search(
            r"\b(pip3?\s+install|python3?\s+-m\s+pip\s+install|uv\s+pip\s+install)\b",
            low,
        ) and re.search(r"google-genai|google\.generativeai|openai|fal[-_]client", low):
            return (
                "ERROR: Do not pip-install image/LLM SDKs. Use nano_banana_generate / "
                "nano_banana_edit (Gemini Nano Banana) or fal_* tools instead."
            )

        want_elevated = bool(elevated)
        decision = gate.classify_shell(command)
        # Classifier or explicit network=true → request sandbox+network (not host escape).
        want_network = bool(network) or decision != ApprovalDecision.AUTO
        if want_elevated and want_network:
            # Elevated already has host network; keep both flags for audit clarity.
            pass

        if want_elevated:
            # Elevated is a principal escalation — always ask a human (ignore auto-approve).
            detail = (
                "[ELEVATED — HOST ESCAPE]\n"
                "This runs OUTSIDE the OS sandbox with full host privileges.\n"
                "Prefer bash(network=true) if you only need internet inside the sandbox.\n"
                f"\nCommand:\n{command}"
            )
            ok = await gate.require_explicit(
                ApprovalRequest(
                    action="bash_elevated",
                    detail=detail,
                    risk_class="shell_elevated",
                    default=ApprovalDecision.ASK,
                )
            )
            if not ok:
                return (
                    gate.denial_message("elevated shell")
                    + " Hint: retry with network=true (sandbox+internet) if that is enough, "
                    "or use download_file / install_python_packages / nano_banana_*."
                )
            allow_network = True
        elif want_network:
            detail = (
                "[SANDBOX + NETWORK]\n"
                "Allow internet inside the OS sandbox (still jailed; not host escape).\n"
                "Approve for pip/uv/curl/apt-style work when first-class tools cannot help.\n"
                f"\nCommand:\n{command}"
            )
            ok = await gate.require(
                ApprovalRequest(
                    action="bash",
                    detail=detail,
                    risk_class="shell_network_or_destructive",
                    default=ApprovalDecision.ASK,
                )
            )
            if not ok:
                return (
                    gate.denial_message("network shell")
                    + " Hint: ask the user to approve sandbox+network, or use "
                    "download_file / install_python_packages / nano_banana_* instead of elevated."
                )
            allow_network = True
        else:
            allow_network = False

        session_root = str(ctx.session_root())
        result = await run_shell(
            command,
            cwd=_cwd(),
            allow_network=allow_network,
            elevated=want_elevated,
            env={
                "KAGEHA_SESSION": session_root,
                "KAGEHA_ARTIFACTS": str(Path(session_root) / "artifacts"),
            },
            security_profile=str(
                ctx.meta.get("security_profile") or "approval_fallback"
            ),
        )
        hint = _bash_failure_hint(
            command,
            exit_code=result.exit_code,
            stderr=result.stderr,
            allow_network=allow_network,
            elevated=want_elevated,
        )
        payload: dict[str, Any] = {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "elevated": want_elevated,
            "sandboxed": result.sandboxed,
            "allow_network": allow_network,
            "security_profile": result.security_profile,
            "cwd": str(_cwd()),
            "session": session_root,
        }
        if hint:
            payload["hint"] = hint
        return json.dumps(payload)

    @tool(
        description=(
            "Read a text file by line slice. Prefer paths relative to the project "
            "root (or session workspace when no project is bound). Absolute paths "
            "under prior ~/.kageha sessions are also allowed (read-only). "
            "Defaults: offset=1 (1-based line), limit≈400 lines "
            "(KAGEHA_READ_FILE_LINE_LIMIT). Pass a larger limit or raise offset "
            "to continue; truncated output includes total line count."
        )
    )
    async def read_file(path: str, offset: int = 1, limit: int = 0) -> str:
        from kageha.config import read_file_line_limit
        from kageha.harness.inputs import resolve_readable_path

        root = _cwd()
        raw = (path or "").strip()
        try:
            if raw and not raw.startswith("/") and not raw.startswith("~"):
                candidate = (root / raw).resolve()
                if str(candidate).startswith(str(root)) and candidate.is_file():
                    p = candidate
                else:
                    p = resolve_readable_path(ws, path)
            else:
                p = resolve_readable_path(ws, path)
                # Also allow absolute reads inside the project/worktree root.
                if not p.is_file() and raw.startswith("/"):
                    abs_p = Path(raw).expanduser().resolve()
                    if str(abs_p).startswith(str(root)) and abs_p.is_file():
                        p = abs_p
        except (FileNotFoundError, ValueError) as e:
            # Last chance: project-relative absolute under coding root.
            try:
                abs_p = Path(raw).expanduser().resolve()
                if str(abs_p).startswith(str(root)) and abs_p.is_file():
                    p = abs_p
                else:
                    return f"ERROR: {e}"
            except Exception:  # noqa: BLE001
                return f"ERROR: {e}"
        text = p.read_text(errors="replace")
        lines = text.splitlines(keepends=True)
        total = len(lines)
        start = max(1, int(offset or 1))
        max_lines = int(limit) if int(limit or 0) > 0 else read_file_line_limit()
        end = min(total, start - 1 + max_lines)
        if start > total and total > 0:
            return (
                f"...[offset {start} past end; file has {total} lines; "
                "use a smaller offset]"
            )
        body = "".join(lines[start - 1 : end]) if total else ""
        truncated = start > 1 or end < total
        if truncated:
            next_off = end + 1
            cont = (
                f" use offset={next_off} limit={max_lines} to continue"
                if end < total
                else ""
            )
            body = (
                body
                + f"\n...[lines {start}-{end} of {total};"
                f"{cont} — raise limit or page with offset/limit]"
            )
        # Hard char safety net (router also envelopes).
        if len(body) > 100_000:
            body = (
                body[:100_000]
                + f"\n...[truncated chars; file has {total} lines — "
                "use offset/limit to page]"
            )
        # Hint if they used an absolute path that was also seeded
        if path.startswith("/") or path.startswith("~"):
            name = Path(path).expanduser().name
            seeded = ws.root / "inputs" / name
            if seeded.is_file():
                body = (
                    body
                    + f"\n\n[note: same file is also at workspace path inputs/{name}]"
                )
        return body

    @tool(
        description=(
            "Write a text file. User deliverables (artifacts/*.pptx, *.pdf, "
            "*.html, media) bind to the agent session workspace so the WebUI "
            "can download them. Source/code paths use the project root when "
            "bound. Creates parents. For large files, write in chunks."
        )
    )
    async def write_file(path: str, content: str) -> str:
        try:
            p = ctx.resolve_write_path(path)
        except ValueError as e:
            return f"ERROR: {e}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        ctx.note_touched(p)
        try:
            rel = str(p.relative_to(ctx.session_root()))
        except ValueError:
            try:
                rel = str(p.relative_to(_cwd()))
            except ValueError:
                rel = str(p)
        return f"Wrote {len(content)} bytes to {rel}"

    @tool(description="Apply a simple search/replace edit to a file.")
    async def edit_file(path: str, old_string: str, new_string: str) -> str:
        try:
            p = ctx.resolve_write_path(path)
        except ValueError as e:
            return f"ERROR: {e}"
        if not p.is_file():
            return f"ERROR: file not found: {path}"
        text = p.read_text()
        if old_string not in text:
            return "ERROR: old_string not found"
        count = text.count(old_string)
        if count != 1:
            return f"ERROR: old_string matched {count} times; must be unique"
        p.write_text(text.replace(old_string, new_string, 1))
        ctx.note_touched(p)
        return f"Edited {path}"

    @tool(
        description=(
            "List entries under a relative directory in the project root "
            "(or session workspace when unbound). Default is shallow (immediate "
            "children only). Set recursive=true for a deep walk (capped; prefer "
            "narrower paths). Optional glob filters names (e.g. '*.py')."
        )
    )
    async def list_dir(
        path: str = ".",
        recursive: bool = False,
        glob: str = "",
    ) -> str:
        from kageha.config import list_dir_max_entries

        root = _cwd()
        target = (root / (path or ".")).resolve()
        if not str(target).startswith(str(root)):
            return "ERROR: path escapes project root"
        if not target.exists():
            return "(empty)"
        if target.is_file():
            return str(target.relative_to(root))
        pattern = (glob or "").strip() or "*"
        max_entries = list_dir_max_entries()
        if recursive:
            children = sorted(target.rglob(pattern))
        elif pattern == "*":
            children = sorted(target.iterdir())
        else:
            children = sorted(target.glob(pattern))
        entries: list[str] = []
        for child in children:
            try:
                rel = str(child.relative_to(root))
            except ValueError:
                continue
            if child.is_dir():
                rel = rel.rstrip("/") + "/"
            entries.append(rel)
            if len(entries) >= max_entries:
                mode = "recursive" if recursive else "shallow"
                entries.append(
                    f"…[truncated at {max_entries} ({mode}); "
                    "narrow path, set recursive=false, or use glob]"
                )
                break
        return "\n".join(entries) if entries else "(empty)"

    @tool(description="Replace the session todo.md checklist (markdown). Use to track plan progress.")
    async def todo_write(markdown: str) -> str:
        todos_path.write_text(markdown)
        return f"Updated todo.md ({len(markdown)} chars)"

    @tool(description="Read the current todo.md checklist.")
    async def todo_read() -> str:
        if not todos_path.is_file():
            return "(no todos yet)"
        return todos_path.read_text()

    @tool(
        description=(
            "Search the web (auto: Brave / Tavily / Perplexity when keyed, "
            "else Gemini Google Search, then DuckDuckGo). "
            "Returns numbered citeable hits [1]… with title, URL, snippet, "
            "plus a compact sources list. Cite claims with those ids."
        )
    )
    async def web_search(query: str) -> str:
        return await _web_search(query)

    @tool(
        description=(
            "Run several web searches IN PARALLEL. "
            "queries_json is a JSON array of search strings (max 8). "
            "Prefer this (or multiple web_search in one turn) over serial search loops. "
            "Returns searches[] plus a merged sources[] citation list."
        )
    )
    async def parallel_web_search(queries_json: str) -> str:
        import asyncio

        from kageha.research.citations import (
            attach_sources,
            citations_from_tool_result,
            merge_citations,
        )

        try:
            queries = json.loads(queries_json or "[]")
        except json.JSONDecodeError as e:
            return f"ERROR: queries_json must be JSON array: {e}"
        if not isinstance(queries, list) or not queries:
            return "ERROR: queries_json must be a non-empty JSON array of strings"
        qs = [str(q).strip() for q in queries if str(q).strip()][:8]
        if not qs:
            return "ERROR: no valid queries"

        async def one(q: str) -> dict[str, str]:
            return {"query": q, "results": await _web_search(q)}

        out = await asyncio.gather(*[one(q) for q in qs])
        searches = list(out)
        sources = merge_citations(
            [
                c
                for row in searches
                for c in citations_from_tool_result(
                    "web_search", str(row.get("results") or "")
                )
            ]
        )
        payload = attach_sources({"ok": True, "searches": searches}, sources)
        return json.dumps(payload, indent=2)[:14000]

    @tool(
        description=(
            "Fetch a public http(s) URL and extract main text + links (no Chromium). "
            "Prefer this over browser_open for docs, blogs, READMEs, and static pages. "
            "Use browser_* only for JS apps, logins, or multi-step UI. "
            "For binary files (images/pdf/zip) use download_file instead. "
            "Result includes title/url — cite as a source in the final answer."
        )
    )
    async def web_fetch(url: str, max_chars: int = 12000) -> str:
        from kageha.harness.browser.fetch import fetch_url
        from kageha.research.citations import parse_fetch_citation, sources_marker

        text = await fetch_url(url, max_chars=max(500, min(50000, int(max_chars))))
        cite = parse_fetch_citation(text, fallback_url=url)
        if cite is None:
            return text
        return text + sources_marker([cite])

    @tool(
        description=(
            "Download a binary http(s) URL into the session workspace (default "
            "artifacts/). FIRST-CLASS replacement for curl/wget — uses in-process "
            "HTTP (no shell sandbox). Use for product images, PDFs, zip files, etc. "
            "path examples: artifacts/product.png or artifacts/refs/hero.jpg."
        ),
        risk_class="network",
    )
    async def download_file(
        url: str,
        path: str = "",
        max_mb: float = 50.0,
    ) -> str:
        return await _download_file(ctx, url=url, path=path, max_mb=max_mb)

    @tool(
        description=(
            "Install Python packages into the project ``.kageha_pkgs`` directory "
            "(sandboxed, network allowed). FIRST-CLASS replacement for "
            "`pip install` / elevated bash. Returns PYTHONPATH to prepend when "
            "running scripts. Prefer nano_banana_* / fal_* over installing "
            "google-genai or image SDKs. packages: space or comma-separated "
            "(e.g. 'pillow reportlab')."
        ),
        risk_class="network",
    )
    async def install_python_packages(packages: str) -> str:
        return await _install_python_packages(ctx, packages=packages)

    for t in (
        escalate_plan,
        request_approval,
        ask_human,
        bash,
        read_file,
        write_file,
        edit_file,
        list_dir,
        todo_write,
        todo_read,
        web_search,
        parallel_web_search,
        web_fetch,
        download_file,
        install_python_packages,
    ):
        # decorator returns Tool instances when used as @tool on async def — but
        # assignment above captures Tool objects from decorator return.
        if hasattr(t, "name"):
            reg.register(t)  # type: ignore[arg-type]
    return reg


def _bash_failure_hint(
    command: str,
    *,
    exit_code: int,
    stderr: str,
    allow_network: bool,
    elevated: bool,
) -> str:
    if exit_code == 0 or elevated:
        return ""
    err = (stderr or "").lower()
    cmd = (command or "").lower()
    networkish = any(
        tok in err
        for tok in (
            "operation not permitted",
            "network is unreachable",
            "could not resolve",
            "nodename nor servname",
            "connection refused",
            "failed to establish",
            "network is down",
            "permission denied",
        )
    )
    if not networkish and exit_code not in {1, 6, 7, 126, 127}:
        return ""
    hints: list[str] = []
    if re.search(r"\b(curl|wget)\b", cmd):
        hints.append("Use download_file(url, path='artifacts/…') instead of curl/wget.")
    if re.search(r"\bpip3?\b|\buv\s+pip\b|python3?\s+-m\s+pip", cmd):
        hints.append(
            "Use install_python_packages('pkg1 pkg2') — installs into .kageha_pkgs "
            "with network. For Gemini images use nano_banana_generate (no pip)."
        )
    if not allow_network and networkish:
        hints.append(
            "Network denied in sandbox. Retry bash(..., network=true) to ASK the "
            "human for sandbox+internet, or use download_file / "
            "install_python_packages / nano_banana_*. Use elevated=true only if the "
            "sandbox itself blocks the work (always prompts the human)."
        )
    elif allow_network and not elevated and networkish:
        hints.append(
            "Sandbox+network still failed. Prefer first-class tools; if the jail "
            "blocks writes/exec, retry bash(..., elevated=true) — that ALWAYS asks "
            "the human for host escape."
        )
    if "operation not permitted" in err and "artifacts" in cmd:
        hints.append(
            "Writing session artifacts via shell may be blocked — use download_file "
            "or write_file paths under artifacts/."
        )
    return " ".join(hints)


async def _download_file(
    ctx: "HarnessContext",
    *,
    url: str,
    path: str,
    max_mb: float,
) -> str:
    import httpx
    from urllib.parse import unquote, urlparse

    from kageha.harness.tools.paths import rel_to_workspace

    raw_url = (url or "").strip()
    if not raw_url.lower().startswith(("http://", "https://")):
        return "ERROR: url must be http(s)"
    cap = max(0.25, min(200.0, float(max_mb or 50.0)))
    max_bytes = int(cap * 1024 * 1024)

    dest_rel = (path or "").strip()
    if not dest_rel:
        name = Path(unquote(urlparse(raw_url).path)).name or "download.bin"
        dest_rel = f"artifacts/{name}"
    try:
        dest = ctx.resolve_write_path(dest_rel)
    except ValueError as e:
        return f"ERROR: {e}"

    try:
        async with httpx.AsyncClient(
            timeout=120.0, follow_redirects=True, trust_env=False
        ) as client:
            async with client.stream("GET", raw_url) as resp:
                if resp.status_code >= 400:
                    return f"ERROR: HTTP {resp.status_code} for {raw_url}"
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        return f"ERROR: download exceeds {cap:g} MB limit"
                    chunks.append(chunk)
                data = b"".join(chunks)
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: download failed: {exc}"

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    ctx.note_touched(dest)
    return json.dumps(
        {
            "path": rel_to_workspace(dest, ctx.session_root()),
            "bytes": len(data),
            "url": raw_url,
            "max_mb": cap,
        }
    )


async def _install_python_packages(ctx: "HarnessContext", *, packages: str) -> str:
    raw = (packages or "").strip()
    pkgs = [p.strip() for p in raw.replace(",", " ").split() if p.strip()]
    if not pkgs:
        return "ERROR: packages required (space or comma-separated)"
    blocked = {
        "google-genai",
        "google.generativeai",
        "google-generativeai",
        "fal-client",
        "fal_client",
    }
    bad = [p for p in pkgs if p.lower().replace("_", "-") in blocked or p.lower() in blocked]
    if bad:
        return (
            "ERROR: do not install "
            + ", ".join(bad)
            + ". Use nano_banana_generate/edit or fal_* tools instead."
        )

    target = (ctx.coding_root() / ".kageha_pkgs").resolve()
    target.mkdir(parents=True, exist_ok=True)
    # Prefer uv when present; fall back to python -m pip --target.
    uv = shutil.which("uv")
    if uv:
        cmd = (
            f"{shlex.quote(uv)} pip install --target {shlex.quote(str(target))} "
            + " ".join(shlex.quote(p) for p in pkgs)
        )
    else:
        py = shutil.which("python3") or shutil.which("python") or "python3"
        cmd = (
            f"{shlex.quote(py)} -m pip install --target {shlex.quote(str(target))} "
            + " ".join(shlex.quote(p) for p in pkgs)
        )

    session_root = str(ctx.session_root())
    result = await run_shell(
        cmd,
        cwd=ctx.coding_root(),
        allow_network=True,
        elevated=False,
        timeout=300.0,
        env={
            "KAGEHA_SESSION": session_root,
            "KAGEHA_ARTIFACTS": str(Path(session_root) / "artifacts"),
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        },
        security_profile=str(ctx.meta.get("security_profile") or "approval_fallback"),
    )
    if result.exit_code != 0:
        return json.dumps(
            {
                "ok": False,
                "exit_code": result.exit_code,
                "stderr": result.stderr[-2000:],
                "stdout": result.stdout[-1000:],
                "hint": (
                    "Install failed inside the sandbox. Retry with fewer packages, "
                    "or avoid SDKs — use nano_banana_* / download_file instead."
                ),
            }
        )
    return json.dumps(
        {
            "ok": True,
            "packages": pkgs,
            "target": str(target),
            "pythonpath": str(target),
            "usage": (
                f"Run scripts with: PYTHONPATH={target}:$PYTHONPATH python your_script.py "
                "(or export PYTHONPATH before bash)."
            ),
            "stdout": result.stdout[-1500:],
        }
    )


_SEARCH_API_ORDER = ("brave", "tavily", "perplexity")


def _web_search_chain(backend: str) -> list[str]:
    """Ordered providers to try for a resolved ``web_search_backend()`` value."""
    from kageha.config import web_search_keyed_providers

    if backend == "ddg":
        return ["ddg"]
    if backend == "gemini_only":
        return ["gemini"]
    if backend == "gemini":
        return ["gemini", "ddg"]
    if backend in _SEARCH_API_ORDER:
        # Primary first (even if key missing → clear ERROR), then other keyed
        # search APIs, then Gemini grounding, then DDG.
        rest = [p for p in web_search_keyed_providers() if p != backend]
        return [backend, *rest, "gemini", "ddg"]
    return ["gemini", "ddg"]


async def _run_web_search_provider(name: str, query: str) -> str:
    if name == "brave":
        return await _brave_web_search(query)
    if name == "tavily":
        return await _tavily_web_search(query)
    if name == "perplexity":
        return await _perplexity_web_search(query)
    if name == "gemini":
        return await _gemini_web_search(query)
    if name == "ddg":
        return await _ddg_search(query)
    return f"ERROR: unknown search provider {name!r}"


def _normalize_search_ok(text: str) -> str:
    """Number hits + attach compact sources marker (no-op on errors)."""
    from kageha.research.citations import normalize_search_output

    return normalize_search_output(text)


async def _web_search(query: str) -> str:
    """Keyed search APIs → Gemini Google Search → DuckDuckGo fallback chain."""
    from kageha.config import web_search_backend

    backend = web_search_backend()
    q = (query or "").strip()
    if not q:
        return "ERROR: empty query"

    chain = _web_search_chain(backend)
    errors: list[str] = []
    last = ""
    for name in chain:
        out = await _run_web_search_provider(name, q)
        last = out or last
        if out and not out.startswith("ERROR:"):
            if not errors:
                return _normalize_search_ok(out)
            # Preserve prior note styles for common two-step fallbacks.
            gemini_err = next((e for e in errors if "gemini" in e.lower()), None)
            if gemini_err and name == "ddg" and len(errors) == 1:
                if out.startswith("No results"):
                    return f"{gemini_err}\n\nDDG fallback: {out}"
                return _normalize_search_ok(
                    f"{out}\n\n(note: Gemini search unavailable — {gemini_err[:180]})"
                )
            if len(errors) == 1 and name == "gemini":
                return _normalize_search_ok(f"{out}\n\n(note: {errors[0][:160]})")
            note = "; ".join(errors)[:240]
            return _normalize_search_ok(f"{out}\n\n(note: {note})")
        if out and out.startswith("ERROR:"):
            errors.append(out)

    if backend == "gemini_only":
        return last or f"ERROR: Gemini web search failed for {q!r}"
    return last or f"ERROR: web search failed for {q!r}"


async def _brave_web_search(query: str) -> str:
    """Brave Search API — https://api.search.brave.com/res/v1/web/search."""
    import os

    import httpx

    from kageha.config import web_search_brave_key

    api_key = web_search_brave_key()
    if not api_key:
        return "ERROR: BRAVE_API_KEY not configured"

    count = 8
    try:
        count = max(1, min(20, int(os.environ.get("KAGEHA_BRAVE_COUNT", "8"))))
    except ValueError:
        count = 8

    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }
    params = {
        "q": query,
        "count": count,
        "extra_snippets": "true",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code >= 400:
                return (
                    f"ERROR: Brave search HTTP {resp.status_code}: "
                    f"{(resp.text or '')[:400].replace(chr(10), ' ')}"
                )
            data = resp.json()
    except Exception as e:  # noqa: BLE001
        return f"ERROR: Brave search failed: {e}"

    results = ((data.get("web") or {}).get("results")) or []
    lines: list[str] = []
    for item in results[:count]:
        title = str(item.get("title") or "").strip()
        link = str(item.get("url") or "").strip()
        desc = str(item.get("description") or "").strip()
        extras = item.get("extra_snippets") or []
        if isinstance(extras, list) and extras and not desc:
            desc = str(extras[0] or "").strip()
        if not title and not link:
            continue
        line = f"- {title or link}"
        if link:
            line += f"\n  {link}"
        if desc:
            line += f"\n  {desc}"
        lines.append(line)
    if not lines:
        return f"ERROR: Brave search returned no web results for {query!r}"
    return "\n".join(lines)[:12000]


async def _tavily_web_search(query: str) -> str:
    """Tavily Search API — https://api.tavily.com/search."""
    import os

    import httpx

    from kageha.config import web_search_tavily_key

    api_key = web_search_tavily_key()
    if not api_key:
        return "ERROR: TAVILY_API_KEY not configured"

    count = 8
    try:
        count = max(1, min(20, int(os.environ.get("KAGEHA_TAVILY_COUNT", "8"))))
    except ValueError:
        count = 8

    url = "https://api.tavily.com/search"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "query": query,
        "max_results": count,
        "search_depth": (os.environ.get("KAGEHA_TAVILY_DEPTH") or "basic").strip()
        or "basic",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                return (
                    f"ERROR: Tavily search HTTP {resp.status_code}: "
                    f"{(resp.text or '')[:400].replace(chr(10), ' ')}"
                )
            data = resp.json()
    except Exception as e:  # noqa: BLE001
        return f"ERROR: Tavily search failed: {e}"

    results = data.get("results") or []
    lines: list[str] = []
    for item in results[:count]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        link = str(item.get("url") or "").strip()
        desc = str(item.get("content") or item.get("snippet") or "").strip()
        if not title and not link:
            continue
        line = f"- {title or link}"
        if link:
            line += f"\n  {link}"
        if desc:
            line += f"\n  {desc}"
        lines.append(line)
    answer = str(data.get("answer") or "").strip()
    if not lines and not answer:
        return f"ERROR: Tavily search returned no web results for {query!r}"
    body = "\n".join(lines)
    if answer:
        body = (body + f"\n\nSummary:\n{answer[:2500]}").strip()
    return body[:12000]


async def _perplexity_web_search(query: str) -> str:
    """Perplexity Search API — https://api.perplexity.ai/search."""
    import os

    import httpx

    from kageha.config import web_search_perplexity_key

    api_key = web_search_perplexity_key()
    if not api_key:
        return "ERROR: PERPLEXITY_API_KEY not configured"

    count = 8
    try:
        count = max(1, min(20, int(os.environ.get("KAGEHA_PERPLEXITY_COUNT", "8"))))
    except ValueError:
        count = 8

    url = "https://api.perplexity.ai/search"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "query": query,
        "max_results": count,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                return (
                    f"ERROR: Perplexity search HTTP {resp.status_code}: "
                    f"{(resp.text or '')[:400].replace(chr(10), ' ')}"
                )
            data = resp.json()
    except Exception as e:  # noqa: BLE001
        return f"ERROR: Perplexity search failed: {e}"

    results = data.get("results") or []
    lines: list[str] = []
    for item in results[:count]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        link = str(item.get("url") or "").strip()
        desc = str(item.get("snippet") or item.get("content") or "").strip()
        if not title and not link:
            continue
        line = f"- {title or link}"
        if link:
            line += f"\n  {link}"
        if desc:
            line += f"\n  {desc}"
        lines.append(line)
    if not lines:
        return f"ERROR: Perplexity search returned no web results for {query!r}"
    return "\n".join(lines)[:12000]


async def _gemini_web_search(query: str) -> str:
    """Grounded search via Gemini google_search tool."""
    import os

    import httpx

    from kageha.config import env_key, web_search_gemini_model

    api_key = env_key("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return "ERROR: GEMINI_API_KEY not configured"

    model = web_search_gemini_model()
    base = (
        os.environ.get("GEMINI_BASE_URL")
        or "https://generativelanguage.googleapis.com/v1beta"
    ).rstrip("/")
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            "Search the web for this query and return a concise "
                            "bullet list of the best sources. For each item include "
                            "title, URL, and a one-line snippet. Query:\n"
                            f"{query}"
                        )
                    }
                ],
            }
        ],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 2048,
        },
    }
    url = f"{base}/models/{model}:generateContent"
    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                return (
                    f"ERROR: Gemini search HTTP {resp.status_code}: "
                    f"{(resp.text or '')[:400].replace(chr(10), ' ')}"
                )
            data = resp.json()
    except Exception as e:  # noqa: BLE001
        return f"ERROR: Gemini search failed: {e}"

    candidates = data.get("candidates") or [{}]
    cand0 = candidates[0] if candidates else {}
    parts = ((cand0.get("content") or {}).get("parts")) or []
    text_bits = [
        str(p.get("text") or "").strip()
        for p in parts
        if p.get("text") and not p.get("thought")
    ]
    text = "\n".join(t for t in text_bits if t).strip()

    # Prefer grounding chunks (structured citations) when present.
    gm = cand0.get("groundingMetadata") or data.get("groundingMetadata") or {}
    chunks = gm.get("groundingChunks") or []
    lines: list[str] = []
    for chunk in chunks[:10]:
        web = chunk.get("web") or {}
        title = str(web.get("title") or "").strip()
        uri = str(web.get("uri") or "").strip()
        if not title and not uri:
            continue
        line = f"- {title or uri}"
        if uri:
            line += f"\n  {uri}"
        lines.append(line)
    if lines:
        body = "\n".join(lines)
        if text:
            body += f"\n\nSummary:\n{text[:2500]}"
        return body[:12000]
    if text:
        return text[:12000]
    return f"ERROR: Gemini search returned no grounded results for {query!r}"


async def _ddg_search(query: str) -> str:
    """DuckDuckGo HTML search used by web_search / parallel_web_search."""
    import re
    from urllib.parse import parse_qs, unquote, urlparse

    import httpx

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def clean(s: str) -> str:
        return re.sub(r"<[^>]+>", "", s).strip()

    def unwrap_ddg(href: str) -> str:
        href = href.replace("&amp;", "&")
        if href.startswith("//"):
            href = "https:" + href
        if "uddg=" in href:
            qs = parse_qs(urlparse(href).query)
            if qs.get("uddg"):
                return unquote(qs["uddg"][0])
        return href

    def parse_html_results(html: str) -> list[str]:
        titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, flags=re.S)
        hrefs = re.findall(r'class="result__a"[^>]*href="([^"]+)"', html)
        snippets = re.findall(
            r'class="result__snippet"[^>]*>(.*?)</(?:a|td)>', html, flags=re.S
        )
        lines: list[str] = []
        for i, t in enumerate(titles[:8]):
            link = unwrap_ddg(hrefs[i]) if i < len(hrefs) else ""
            sn = clean(snippets[i]) if i < len(snippets) else ""
            line = f"- {clean(t)}"
            if link:
                line += f"\n  {link}"
            if sn:
                line += f"\n  {sn}"
            lines.append(line)
        return lines

    def parse_lite_results(html: str) -> list[str]:
        links = re.findall(
            r'<a[^>]+rel="nofollow"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            flags=re.S | re.I,
        )
        lines: list[str] = []
        for href, t in links[:8]:
            title = clean(t)
            if not title or title.lower() in {"cached", "more results"}:
                continue
            lines.append(f"- {title}\n  {href}")
        return lines

    async with httpx.AsyncClient(
        timeout=25.0, follow_redirects=True, headers=headers
    ) as client:
        # Bare POST without UA often gets DDG bot-challenge (empty "No results").
        try:
            resp = await client.get(
                "https://html.duckduckgo.com/html/", params={"q": query}
            )
            resp.raise_for_status()
            lines = parse_html_results(resp.text)
            if lines:
                return "\n".join(lines)
        except Exception as e:  # noqa: BLE001
            ddg_err = str(e)
        else:
            ddg_err = "empty/challenged"

        try:
            resp2 = await client.post(
                "https://lite.duckduckgo.com/lite/", data={"q": query}
            )
            resp2.raise_for_status()
            lines = parse_lite_results(resp2.text)
            if lines:
                return "\n".join(lines)
        except Exception as e:  # noqa: BLE001
            return f"No results (DuckDuckGo failed: {ddg_err}; lite: {e})"

    return (
        f"No results (DuckDuckGo blocked or empty for {query!r}). "
        "Try browser_open on a search URL."
    )


def load_entry_point_tools(ctx: "HarnessContext") -> ToolRegistry:
    """Load builtins + enabled first-party packs (core by default).

    Optional packs (browser, media, kb, …) load only when enabled via
    ``KAGEHA_TOOL_PACKS`` or ``tools.yaml`` ``packs`` (see
    ``kageha.harness.tool_packs``).

    Import/register failures are recorded on ``ctx.meta['tool_load_warnings']``.
    Set ``KAGEHA_STRICT_TOOLS=1`` to raise on the first pack failure.
    """
    import os
    from importlib.metadata import entry_points

    from kageha.harness.tool_packs import (
        pack_imports_for,
        resolve_enabled_packs,
        summarize_packs,
    )
    from kageha.harness.tool_policy import load_tools_policy

    reg = register(ctx)
    warnings: list[str] = list(ctx.meta.get("tool_load_warnings") or [])
    strict = os.environ.get("KAGEHA_STRICT_TOOLS", "").strip() in {"1", "true", "yes"}

    def _warn(msg: str) -> None:
        warnings.append(msg)

    def _fail_or_warn(label: str, exc: BaseException) -> None:
        msg = f"{label}: {type(exc).__name__}: {exc}"
        _warn(msg)
        if strict:
            raise RuntimeError(f"strict tool load failed: {msg}") from exc

    policy = load_tools_policy()
    enabled = resolve_enabled_packs(policy=policy)
    ctx.meta["tool_packs_enabled"] = list(enabled)
    ctx.meta["tool_packs_summary"] = summarize_packs(enabled)

    packs: list[tuple[str, Any]] = []
    for label, path in pack_imports_for(enabled):
        mod_name, attr = path.split(":", 1)
        try:
            import importlib

            mod = importlib.import_module(mod_name)
            packs.append((label, getattr(mod, attr)))
        except Exception as e:  # noqa: BLE001
            _fail_or_warn(f"import pack '{label}'", e)

    loaded: list[str] = []
    for label, pack in packs:
        try:
            extra = pack(ctx)
            if isinstance(extra, ToolRegistry):
                for t in extra.tools.values():
                    reg.register(t)
            loaded.append(label)
        except Exception as e:  # noqa: BLE001
            _fail_or_warn(f"register pack '{label}'", e)

    ctx.meta["tool_packs_loaded"] = loaded

    try:
        eps = entry_points(group="kageha.tools")
    except TypeError:
        eps = entry_points().get("kageha.tools", [])  # type: ignore[arg-type]
    for ep in eps:
        if ep.name == "core":
            continue
        try:
            fn = ep.load()
            extra = fn(ctx)
            if isinstance(extra, ToolRegistry):
                for t in extra.tools.values():
                    reg.register(t)
        except Exception as e:  # noqa: BLE001
            _fail_or_warn(f"entry_point '{ep.name}'", e)

    # User / project tool directories (~/.kageha/tools, .kageha/tools, KAGEHA_TOOLS_PATH)
    try:
        from kageha.harness.tools.user_tools import load_user_tool_dirs

        def _user_err(label: str, exc: BaseException) -> None:
            _fail_or_warn(f"user_tools '{label}'", exc)

        for _label, extra in load_user_tool_dirs(ctx, on_error=_user_err):
            for t in extra.tools.values():
                reg.register(t)
    except Exception as e:  # noqa: BLE001
        _fail_or_warn("user_tools", e)

    ctx.meta["tool_load_warnings"] = warnings
    ctx.meta["tool_count"] = len(reg.names())
    return reg
