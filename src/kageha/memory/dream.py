"""Optional LLM dream pass for consolidate (default off; SQLite stays authority)."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from kageha.memory.models import MemoryState
from kageha.memory.store import MemoryStore

_DREAM_PROMPT = """\
You consolidate agent memory. Propose ONLY safe supersedes of near-duplicate confirmed claims.
Return ONLY JSON:
{"supersede":[{"keep_id":"...","drop_id":"...","reason":"near duplicate"}]}

Rules:
- Keep at most 8 supersedes. Use [] if nothing should change.
- Prefer the clearer, newer, or more specific claim as keep_id.
- Never invent ids. Only use ids from the provided list.
- Do not merge unrelated facts. Do not rewrite content.
"""


def llm_dream_mode() -> str:
    raw = (os.environ.get("KAGEHA_MEMORY_LLM_DREAM") or "off").strip().lower()
    if raw in {"auto", "on", "off", "1", "0", "true", "false"}:
        if raw in {"1", "true"}:
            return "on"
        if raw in {"0", "false"}:
            return "off"
        return raw
    return "off"


def llm_dream_enabled() -> bool:
    mode = llm_dream_mode()
    if mode == "off":
        return False
    if mode == "on":
        return True
    # auto: same gate as LLM extract (chat-capable key present).
    try:
        from kageha.config import env_key
        from kageha.models.registry import ModelRegistry

        reg = ModelRegistry.load()
        for name in ("gemini", "openai", "anthropic", "siliconflow"):
            pc = reg.providers.get(name)
            if pc and env_key(pc.api_key_env):
                return True
    except Exception:
        return False
    return False


def parse_dream_payload(text: str) -> list[dict[str, str]]:
    raw = (text or "").strip()
    if not raw:
        return []
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE)
    if fence:
        raw = fence.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    items = data.get("supersede") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: list[dict[str, str]] = []
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        keep = str(item.get("keep_id") or "").strip()
        drop = str(item.get("drop_id") or "").strip()
        reason = str(item.get("reason") or "dream supersede").strip()[:200]
        if not keep or not drop or keep == drop:
            continue
        out.append({"keep_id": keep, "drop_id": drop, "reason": reason})
    return out


def apply_dream_actions(
    store: MemoryStore,
    actions: list[dict[str, str]],
    *,
    known_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Apply validated supersedes. Returns detail rows."""
    confirmed = {
        r.id: r
        for r in store.list_memories(state=MemoryState.CONFIRMED.value, limit=1000)
    }
    allowed = known_ids if known_ids is not None else set(confirmed)
    applied: list[dict[str, Any]] = []
    for action in actions:
        keep_id = action["keep_id"]
        drop_id = action["drop_id"]
        if keep_id not in allowed or drop_id not in allowed:
            continue
        keep = confirmed.get(keep_id)
        drop = confirmed.get(drop_id)
        if keep is None or drop is None:
            continue
        if keep.scope_type != drop.scope_type or keep.scope_key != drop.scope_key:
            continue
        store.update_state(
            drop_id,
            MemoryState.SUPERSEDED.value,
            supersedes_id=keep_id,
        )
        confirmed.pop(drop_id, None)
        applied.append(
            {
                "action": "dream_supersede",
                "kept": keep_id,
                "dropped": drop_id,
                "reason": action.get("reason") or "",
            }
        )
    return applied


async def dream_consolidate_async(store: MemoryStore) -> dict[str, Any]:
    if not llm_dream_enabled():
        return {"enabled": False, "applied": 0, "details": []}
    rows = store.list_memories(state=MemoryState.CONFIRMED.value, limit=80)
    if len(rows) < 2:
        return {"enabled": True, "applied": 0, "details": [], "reason": "too_few"}
    rows.sort(key=lambda r: r.updated_at or r.created_at, reverse=True)
    catalog = []
    for rec in rows[:60]:
        one = " ".join(rec.content.split())
        if len(one) > 160:
            one = one[:159] + "…"
        catalog.append(
            {
                "id": rec.id,
                "kind": rec.kind,
                "scope": f"{rec.scope_type}:{rec.scope_key}",
                "content": one,
            }
        )
    from kageha.models.base import ChatMessage
    from kageha.models.registry import ModelRegistry
    from kageha.models.router import ModelRouter

    role = (os.environ.get("KAGEHA_MEMORY_MODEL_ROLE") or "fast_worker").strip()
    router = ModelRouter(ModelRegistry.load())
    _model, resp = await router.chat(
        [
            ChatMessage(role="system", content=_DREAM_PROMPT),
            ChatMessage(
                role="user",
                content="Claims:\n" + json.dumps(catalog, indent=2),
            ),
        ],
        tools=None,
        role=role,
        task_id="memory-dream",
        temperature=0.0,
        max_tokens=900,
    )
    actions = parse_dream_payload(resp.message.content or "")
    details = apply_dream_actions(
        store,
        actions,
        known_ids={row["id"] for row in catalog},
    )
    return {"enabled": True, "applied": len(details), "details": details}


def dream_consolidate(
    store: MemoryStore,
    *,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Sync wrapper for consolidate/daemon (hard-bounded)."""
    if not llm_dream_enabled():
        return {"enabled": False, "applied": 0, "details": []}
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FuturesTimeout

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        return {"enabled": True, "applied": 0, "details": [], "reason": "async_loop"}

    try:
        bound = float(
            timeout_s
            if timeout_s is not None
            else os.environ.get("KAGEHA_MEMORY_LLM_DREAM_TIMEOUT", "12")
        )
    except ValueError:
        bound = 12.0
    bound = max(2.0, min(45.0, bound))

    def _run() -> dict[str, Any]:
        return asyncio.run(dream_consolidate_async(store))

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_run).result(timeout=bound)
    except (FuturesTimeout, Exception) as exc:
        return {
            "enabled": True,
            "applied": 0,
            "details": [],
            "error": type(exc).__name__,
        }
