---
name: memory
description: Use Kageha durable memory efficiently — trust the digest, fetch by id when needed, mutate only on explicit user requests.
---

# memory

Canonical authority is SQLite. Vectors accelerate recall only. Each turn already
injects a compact **Memory digest** (and optional **Memory index** pointers) via
system context — not via `SYSTEM_PROMPT` / the on-disk `MEMORY.md` file.

## Efficient use

1. **Read the digest (and index pointers) first.** Do not call tools if they already answer the need.
2. **`memory_fetch(id)`** when a digest/episode/index line is truncated or you need full text.
3. **`memory_recall(query)`** only for a *different* question than the current turn.
4. **Mutate only on explicit user asks:**
   - remember → `memory_remember`
   - correct → `memory_correct(id, replacement)`
   - forget → `memory_forget(id|text)` (exact id preferred)
5. **Audit sparingly:** `memory_explain` / `memory_forgotten` when the user asks why.

## Trust rules

- Current user text and verified turn evidence outrank stored memory.
- Never store secrets, raw tool/web dumps, or assistant guesses as confirmed.
- Ambiguous forget/correct targets → list ids and ask which one.
- Procedures belong in skills, not silent memory growth.
