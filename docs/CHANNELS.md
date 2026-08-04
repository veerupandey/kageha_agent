# Messaging channels

Kageha uses one normalized channel pipeline for Telegram and WhatsApp. Each
adapter receives a platform message, deduplicates it, binds it to a durable
Kageha session, runs the agent, and delivers text or generated artifacts back
through the originating channel.

## Telegram

Create a bot with `@BotFather`, then configure:

```bash
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_ALLOWED_USERS="123456789"
kageha channels run --telegram
```

If `TELEGRAM_BOT_TOKEN` is missing and the command is run from a terminal,
Kageha explains the `@BotFather` setup and securely prompts for the token. The
token is used only for that process and is not written to disk. Non-interactive
shells should provide `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USERS`
explicitly.

The adapter uses long polling, so local development does not require a public
HTTPS endpoint. `TELEGRAM_ALLOWED_USERS` is deny-by-default; set
`TELEGRAM_ALLOW_ALL_USERS=1` only for a controlled test bot. Telegram messages,
photos, documents, video, audio, and voice notes are accepted. Generated
artifacts are sent as Telegram media or documents.

## WhatsApp QR (experimental)

The QR adapter links a WhatsApp account as a companion device through the
WhatsApp Web protocol. It is intended for local experiments and personal
accounts, not high-volume or production business messaging.

Install the sidecar dependencies:

```bash
cd integrations/whatsapp-qr
npm install
cd ../..
export WHATSAPP_QR_ENABLED=1
export WHATSAPP_QR_ALLOWED_USERS="15551234567"
kageha channels run --whatsapp-qr
```

The linked WhatsApp account can also message its own chat. The bridge
normalizes device-qualified WhatsApp IDs to the phone number before applying
the allowlist. For normal private-chat testing, use a second WhatsApp account
and allow that sender's international number without `+`, spaces, or dashes.

If `WHATSAPP_QR_ALLOWED_USERS` is omitted in an interactive terminal, Kageha
prompts for it. Use the WhatsApp sender's international phone number without
the leading `+` or spaces. Access from other numbers is ignored by design.

Scan the QR shown in the terminal using WhatsApp → Linked devices. Credentials
are persisted under `~/.kageha/platforms/whatsapp/session` by default. Keep this
directory private and back it up only through an encrypted mechanism.

If the command appears idle, wait for the QR banner; the bridge may take a few
seconds to connect. If it exits with an `asyncio` stream or line-length error,
update to the current sidecar and restart the command. The bridge suppresses
Baileys diagnostic logs and renders the QR on the terminal separately from its
JSON event stream.

The QR path uses an unofficial WhatsApp Web integration and may disconnect or
be restricted by WhatsApp. Do not use it for bulk messaging, unsolicited
messages, or accounts whose loss would be costly. For business or production
use, implement the official WhatsApp Cloud API adapter instead.

## Keeping channels running

Channels are automatically started as a supervised background service when
`kageha chat`, `kageha webui`, or the long-lived app server starts and the
channel environment is configured. A one-shot `kageha run` does not start
channels. Set `KAGEHA_CHANNEL_AUTOSTART=0` to disable this behavior and run
`kageha channels run --whatsapp-qr` manually instead.

For supervised startup, configure the allowlist in the environment first so a
non-interactive background process does not have to prompt:

```bash
export WHATSAPP_QR_ENABLED=1
export WHATSAPP_QR_ALLOWED_USERS="15551234567"
kageha webui
```

For a reliable smoke test, send a message from a second WhatsApp account to the
linked account. Self-chat is also supported when WhatsApp forwards the event;
the adapter normalizes the linked account's device-qualified ID and guards
against outbound reply loops.

Inspect or stop a supervised listener with:

```bash
kageha channels status
kageha channels stop
```

If the listener was started directly with `kageha channels run`, stop it with
`Ctrl-C`; WebUI and chat detect that process and will not create a duplicate.

## Media and safety

- Inbound media is downloaded before the agent turn and referenced as a local
  input file.
- Outbound files are resolved only inside the completed Kageha session
  workspace.
- Channel identities are hashed in the durable queue; raw identities are not
  persisted there.
- Duplicate inbound events are ignored before starting an agent turn.
- Per-peer turns are serialized so two messages cannot mutate one session at
  the same time.
- Channel turns do not auto-approve risky tools. Approval-required work is
  reported back as blocked and should be continued from the Web UI or CLI.
- WhatsApp is deny-by-default: senders not listed in
  `WHATSAPP_QR_ALLOWED_USERS` are never passed to the agent and receive no
  agent reply. Keep `WHATSAPP_QR_ALLOW_ALL_USERS` unset except for isolated
  testing.

## Current limitations

- Telegram currently uses long polling; webhook mode is planned.
- WhatsApp QR is experimental and requires Node.js plus the sidecar install.
- Rich canvas interactions remain in the Web UI. Channels receive text,
  previews, and downloadable artifacts rather than the interactive canvas.
