"""Multi-screen setup wizard for public first-run (Textual or Rich)."""

from __future__ import annotations

import os
from typing import Any


def run_setup_wizard() -> dict[str, Any]:
    """Interactive first-run setup: model auth → optional API key → Gmail → channel."""
    try:
        return _run_textual()
    except Exception:  # noqa: BLE001
        return _run_rich()


def _run_model_auth_rich() -> dict[str, Any]:
    from kageha.models.auth_cli import run_model_auth_setup_step

    return run_model_auth_setup_step(interactive=True)


def _run_textual() -> dict[str, Any]:
    # Textual path still starts with Rich auth step (clearer multi-choice),
    # then optional Textual screens for Gmail/channel.
    results: dict[str, Any] = {"backend": "textual"}
    results["auth"] = _run_model_auth_rich()

    from textual.app import App, ComposeResult  # type: ignore
    from textual.containers import Vertical  # type: ignore
    from textual.widgets import Button, Footer, Header, Input, Label, Select, Static  # type: ignore

    class KagehaSetup(App[None]):
        CSS = """
        Screen { align: center middle; }
        #panel { width: 80; height: auto; border: solid $accent; padding: 1 2; }
        """

        def compose(self) -> ComposeResult:
            yield Header()
            with Vertical(id="panel"):
                yield Label("Kageha setup — remaining steps")
                yield Static(
                    "Model auth already handled. Optional: paste GEMINI_API_KEY "
                    "if you skipped subscription import."
                )
                yield Input(placeholder="GEMINI_API_KEY (optional)", id="gemini")
                yield Static("Google OAuth client JSON (Gmail connections)")
                yield Input(placeholder="~/Downloads/client_secret_….json", id="gjson")
                yield Static("Primary chat channel")
                yield Select(
                    [
                        ("Skip", "skip"),
                        ("Telegram", "telegram"),
                        ("WhatsApp QR", "whatsapp-qr"),
                        ("Discord", "discord"),
                        ("Slack", "slack"),
                    ],
                    id="channel",
                    value="skip",
                )
                yield Input(placeholder="Channel token / bot token (optional)", id="ctoken")
                yield Button("Save & finish", id="save", variant="primary")
                yield Static("", id="status")
            yield Footer()

        def on_button_pressed(self, event: Button.Pressed) -> None:  # type: ignore[name-defined]
            if event.button.id != "save":
                return
            gemini = self.query_one("#gemini", Input).value.strip()
            gjson = self.query_one("#gjson", Input).value.strip()
            channel = str(self.query_one("#channel", Select).value or "skip")
            ctoken = self.query_one("#ctoken", Input).value.strip()
            saved: list[str] = list(results.get("auth", {}).get("imported") or [])
            if gemini:
                _upsert("GEMINI_API_KEY", gemini)
                saved.append("GEMINI_API_KEY")
            if gjson:
                try:
                    from kageha.connections.setup import install_google_client_json

                    install_google_client_json(gjson)
                    saved.append("google-client.json")
                except Exception as exc:  # noqa: BLE001
                    self.query_one("#status", Static).update(f"Google JSON error: {exc}")
                    return
            if channel == "telegram" and ctoken:
                _upsert("TELEGRAM_BOT_TOKEN", ctoken)
                saved.append("TELEGRAM_BOT_TOKEN")
            elif channel == "discord" and ctoken:
                _upsert("DISCORD_BOT_TOKEN", ctoken)
                saved.append("DISCORD_BOT_TOKEN")
            elif channel == "slack" and ctoken:
                _upsert("SLACK_BOT_TOKEN", ctoken)
                saved.append("SLACK_BOT_TOKEN")
            results["saved"] = saved
            results["channel"] = channel
            self.query_one("#status", Static).update(
                f"Saved: {', '.join(saved) or 'nothing'}. Quit with q."
            )
            self.exit()

    KagehaSetup().run()
    return results


def _run_rich() -> dict[str, Any]:
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    console.print(
        Panel.fit(
            "[bold]Kageha setup[/bold]\n"
            "1) Model auth (ChatGPT/Codex · Gemini CLI/Antigravity · API key)\n"
            "2) Google OAuth client JSON (Gmail)\n"
            "3) Primary chat channel\n"
            "[dim]Cursor and Kiro IDE subscription OAuth are not available to third-party apps[/dim]",
            title="setup",
        )
    )
    auth = _run_model_auth_rich()
    saved: list[str] = list(auth.get("imported") or [])

    # Optional extra API key if they skipped auth import
    if not saved or auth.get("skipped"):
        gemini = console.input("GEMINI_API_KEY (Enter to skip): ").strip()
        if gemini:
            _upsert("GEMINI_API_KEY", gemini)
            saved.append("GEMINI_API_KEY")

    gjson = console.input(
        "Path to Google client_secret JSON (Enter to skip): "
    ).strip().strip("'\"")
    if gjson:
        from kageha.connections.setup import install_google_client_json

        install_google_client_json(gjson)
        saved.append("google-client.json")
        console.print("Next: [cyan]kageha connect login gmail[/cyan]")

    console.print(
        "Primary channel: [1] skip  [2] telegram  [3] discord  [4] slack  [5] whatsapp-qr"
    )
    choice = console.input("Choice [1]: ").strip() or "1"
    channel = {
        "2": "telegram",
        "3": "discord",
        "4": "slack",
        "5": "whatsapp-qr",
    }.get(choice, "skip")
    if channel in {"telegram", "discord", "slack"}:
        token = console.input(f"{channel} bot token: ").strip()
        if token:
            key = {
                "telegram": "TELEGRAM_BOT_TOKEN",
                "discord": "DISCORD_BOT_TOKEN",
                "slack": "SLACK_BOT_TOKEN",
            }[channel]
            _upsert(key, token)
            saved.append(key)
    console.print(f"[green]Done.[/green] Saved: {', '.join(saved) or 'nothing'}")
    return {"backend": "rich", "auth": auth, "saved": saved, "channel": channel}


def _upsert(key: str, value: str) -> None:
    from kageha.channels.whatsapp_setup import upsert_env_key

    upsert_env_key(key, value)
    os.environ[key] = value
