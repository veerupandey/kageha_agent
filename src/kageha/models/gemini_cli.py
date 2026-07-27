"""Gemini via first-party ``gemini`` CLI (Antigravity / Gemini CLI login).

This does **not** send OAuth tokens to the public Gemini API (unsupported /
account-risk). It shells to the installed ``gemini`` binary, which uses the
user's local Gemini CLI / Antigravity session.
"""

from __future__ import annotations

import asyncio
import os
import shutil

from kageha.models.base import ChatMessage, ChatResponse, ChatUsage, ToolSpec


def gemini_cli_available() -> bool:
    return bool(shutil.which("gemini"))


def antigravity_session_present() -> bool:
    home = os.path.expanduser("~")
    return (
        os.path.isfile(os.path.join(home, ".gemini", "oauth_creds.json"))
        or os.path.isdir(os.path.join(home, ".gemini", "antigravity"))
    )


class GeminiCliModel:
    """Thin wrapper: ``gemini -p … -m …`` (text in → text out; limited tools)."""

    def __init__(
        self,
        *,
        model_id: str,
        provider: str,
        model: str,
        timeout: float = 180.0,
    ) -> None:
        self.model_id = model_id
        self.provider = provider
        self.model = model
        self.timeout = timeout
        self.api_key = ""  # unused; session is owned by gemini CLI

    def _flatten(self, messages: list[ChatMessage]) -> str:
        parts: list[str] = []
        for m in messages:
            if m.role == "system":
                parts.append(f"[system]\n{m.content}")
            elif m.role == "user":
                parts.append(m.content or "")
            elif m.role == "assistant":
                parts.append(f"[assistant]\n{m.content or ''}")
            elif m.role == "tool":
                parts.append(f"[tool {m.name or ''}]\n{m.content or ''}")
        return "\n\n".join(p for p in parts if p).strip()

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        effort: str | None = None,
    ) -> ChatResponse:
        del temperature, max_tokens, effort
        exe = shutil.which("gemini")
        if not exe:
            raise RuntimeError(
                "gemini CLI not found. Install Gemini CLI and sign in "
                "(Antigravity or `gemini`), then retry."
            )
        # Never pretend tools work on this path — models invent "tools missing"
        # and burn the whole computer-use / browser loop. Fail over to an API model.
        if tools:
            names = ", ".join(t.name for t in tools[:8])
            raise RuntimeError(
                "Antigravity/gemini-cli cannot execute Kageha native tool loops "
                f"(requested: {names}{'…' if len(tools) > 8 else ''}). "
                "Use GEMINI_API_KEY + /model gemini-flash (or gemini-pro)."
            )
        prompt = self._flatten(messages)
        cmd = [
            exe,
            "-p",
            prompt,
            "-m",
            self.model,
            "-o",
            "text",
            # Omit --approval-mode plan: gemini-cli ≥0.29 requires experimental.plan
            # for that mode and otherwise hard-fails, forcing Kageha onto API fallbacks.
        ]
        # Force Antigravity / Gemini CLI OAuth — do not let GEMINI_API_KEY /
        # GOOGLE_API_KEY steal the request onto the public Generative Language API.
        env = os.environ.copy()
        for key in (
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "GOOGLE_GENAI_USE_VERTEXAI",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_LOCATION",
            "GCLOUD_PROJECT",
        ):
            env.pop(key, None)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            out_b, err_b = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise RuntimeError(f"gemini CLI timed out after {self.timeout}s") from exc
        text = (out_b or b"").decode("utf-8", errors="replace").strip()
        err = (err_b or b"").decode("utf-8", errors="replace").strip()
        if proc.returncode != 0 and not text:
            raise RuntimeError(
                f"gemini CLI failed ({proc.returncode}): {err[:500] or 'no output'}"
            )
        if not text and err:
            text = err
        return ChatResponse(
            message=ChatMessage(
                role="assistant",
                content=text or "(empty response from gemini CLI)",
            ),
            usage=ChatUsage(),
            model=self.model,
            raw={"cli": "gemini", "stderr": err[:500]},
        )

    async def smoke(self) -> str:
        response = await self.chat(
            [ChatMessage(role="user", content="Reply with exactly OK.")],
            max_tokens=8,
            temperature=0.0,
        )
        return (response.message.content or "").strip()
