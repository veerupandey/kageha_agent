#!/usr/bin/env node
/**
 * Kageha WhatsApp Web bridge (Baileys).
 *
 * Protocol: JSON lines on stdout (events) / stdin (commands).
 * QR is also rendered on stderr via qrcode-terminal.
 *
 * Events → stdout:
 *   {"type":"status","status":"starting"|"connecting"|"open"|"close",...}
 *   {"type":"qr","qr":"<raw>"}
 *   {"type":"ready","me":"15551234567"}
 *   {"type":"message","from":"1555...","text":"...","id":"...","chat":"..."}
 *   {"type":"sent","to":"...","ok":true}
 *   {"type":"error","error":"..."}
 *   {"type":"pong"}
 *
 * Commands ← stdin:
 *   {"type":"send","to":"15551234567","text":"hello"}
 *   {"type":"send_image","to":"1555...","path":"/abs/file.png","caption":"...","chat":"..."}
 *   {"type":"ping"}
 *   {"type":"logout"}
 */

import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
} from "@whiskeysockets/baileys";
import { Boom } from "@hapi/boom";
import qrcode from "qrcode-terminal";
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import pino from "pino";

const authDir =
  process.env.KAGEHA_WA_AUTH_DIR ||
  path.join(process.env.HOME || ".", ".kageha", "platforms", "whatsapp", "session");

fs.mkdirSync(authDir, { recursive: true });

// Keep stdout JSONL-clean — Baileys sometimes console.logs session blobs.
const _stderrWrite = (...args) => {
  try {
    process.stderr.write(args.map(String).join(" ") + "\n");
  } catch {
    /* ignore */
  }
};
console.log = _stderrWrite;
console.info = _stderrWrite;
console.debug = _stderrWrite;
console.warn = _stderrWrite;

function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function jidToPhone(jid) {
  if (!jid) return "";
  // 15551234567:xx@lid or 15551234567@s.whatsapp.net
  const user = String(jid).split("@")[0] || "";
  return user.split(":")[0].replace(/\D/g, "");
}

function phoneToJid(phone) {
  const digits = String(phone || "").replace(/\D/g, "");
  return `${digits}@s.whatsapp.net`;
}

function extractText(msg) {
  const m = msg.message || {};
  if (m.conversation) return m.conversation;
  if (m.extendedTextMessage?.text) return m.extendedTextMessage.text;
  if (m.imageMessage?.caption) return m.imageMessage.caption;
  if (m.videoMessage?.caption) return m.videoMessage.caption;
  if (m.buttonsResponseMessage?.selectedDisplayText) {
    return m.buttonsResponseMessage.selectedDisplayText;
  }
  if (m.listResponseMessage?.title) return m.listResponseMessage.title;
  return "";
}

let sock = null;
let shuttingDown = false;
/** Texts we just sent — Baileys echoes them as fromMe; ignore to stop reply loops. */
const recentOutbound = new Map(); // text -> timestamp ms

function noteOutbound(text) {
  const t = String(text || "");
  if (!t) return;
  const now = Date.now();
  recentOutbound.set(t, now);
  for (const [k, ts] of recentOutbound) {
    if (now - ts > 180000) recentOutbound.delete(k);
  }
}

function isOurEcho(text) {
  const t = String(text || "").trim();
  if (!t) return false;
  // Status / bot boilerplate (never treat as user tasks)
  if (
    /^(⏳|⛔|🔐)/.test(t) ||
    t.startsWith("Still working on your previous") ||
    t.startsWith("Kageha needs approval") ||
    t.startsWith("Didn't understand") ||
    t.startsWith("Error:") ||
    (t.startsWith("Number ") && t.includes("not allowlisted"))
  ) {
    return true;
  }
  const now = Date.now();
  if (recentOutbound.has(t)) return true;
  for (const [k, ts] of recentOutbound) {
    if (now - ts > 180000) continue;
    if (t === k || (k.length > 20 && t.startsWith(k.slice(0, 80)))) return true;
  }
  return false;
}

async function start() {
  emit({ type: "status", status: "starting", authDir });
  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  let version;
  try {
    const latest = await fetchLatestBaileysVersion();
    version = latest.version;
  } catch {
    version = undefined;
  }

  sock = makeWASocket({
    auth: state,
    version,
    printQRInTerminal: false,
    logger: pino({ level: "silent" }),
    browser: ["Kageha", "Chrome", "1.0.0"],
    syncFullHistory: false,
    markOnlineOnConnect: false,
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", (update) => {
    const { connection, lastDisconnect, qr } = update;
    if (qr) {
      emit({ type: "qr", qr });
      try {
        process.stderr.write("\nScan this QR with WhatsApp → Linked devices:\n\n");
        qrcode.generate(qr, { small: true }, (out) => {
          process.stderr.write(out + "\n");
        });
      } catch (e) {
        process.stderr.write(`(qr render failed: ${e})\n`);
      }
    }
    if (connection) {
      emit({ type: "status", status: connection });
    }
    if (connection === "open") {
      const me = jidToPhone(sock.user?.id || "");
      emit({ type: "ready", me });
      process.stderr.write(`\n[kageha-wa] Linked device ready (me=${me || "?"})\n`);
    }
    if (connection === "close") {
      const err = lastDisconnect?.error;
      const statusCode =
        (err && err.output && err.output.statusCode) ||
        new Boom(err)?.output?.statusCode;
      const loggedOut = statusCode === DisconnectReason.loggedOut;
      emit({
        type: "closed",
        statusCode,
        loggedOut,
        error: String(lastDisconnect?.error || "closed"),
      });
      if (!shuttingDown && !loggedOut) {
        process.stderr.write("[kageha-wa] disconnected — reconnecting…\n");
        setTimeout(() => start().catch((e) => emit({ type: "error", error: String(e) })), 1500);
      } else if (loggedOut) {
        process.stderr.write("[kageha-wa] logged out — delete session and re-scan QR\n");
        process.exit(2);
      }
    }
  });

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    const mePhone = jidToPhone(sock.user?.id || "");
    // Default "all": accept your own outbound 1:1 DMs (Message yourself / @lid).
    // KAGEHA_WA_SELF_CHAT=0 disables; =1 only when chat JID matches your number.
    const selfMode = (process.env.KAGEHA_WA_SELF_CHAT || "all").toLowerCase();

    for (const msg of messages || []) {
      try {
        const chat = msg.key?.remoteJid || "";
        const text = extractText(msg).trim();
        process.stderr.write(
          `[kageha-wa] upsert type=${type} fromMe=${Boolean(msg.key?.fromMe)} ` +
            `chat=${chat} text=${JSON.stringify(text.slice(0, 60))}\n`
        );

        if (!msg.message) continue;
        if (!chat || chat === "status@broadcast") continue;
        // Channels / newsletters / broadcasts — never treat as agent chat.
        if (
          chat.endsWith("@newsletter") ||
          chat.endsWith("@broadcast") ||
          chat.includes("@newsletter")
        ) {
          continue;
        }
        if (chat.endsWith("@g.us") && process.env.KAGEHA_WA_GROUPS !== "1") {
          continue;
        }

        const chatPhone = jidToPhone(
          chat.endsWith("@g.us") ? msg.key.participant || "" : chat
        );
        const isSelfJid =
          Boolean(mePhone) &&
          (chatPhone === mePhone ||
            chat === (sock.user?.id || "") ||
            chat.startsWith(`${mePhone}@`));

        if (msg.key?.fromMe) {
          if (selfMode === "0") continue;
          if (selfMode === "1" && !isSelfJid) continue;
          // Never re-process our own bot replies (causes Still-working loops)
          if (isOurEcho(text)) {
            process.stderr.write(`[kageha-wa] skip echo: ${JSON.stringify(text.slice(0, 50))}\n`);
            continue;
          }
          // selfMode "all": user typing in Message yourself (not our outbound)
        }

        if (!text) continue;

        const from = msg.key.fromMe
          ? mePhone || chatPhone || "self"
          : chatPhone;
        if (!from) continue;

        emit({
          type: "message",
          from,
          text,
          id: msg.key.id || "",
          chat,
          ts: Number(msg.messageTimestamp || 0),
          self: Boolean(msg.key.fromMe),
        });
        process.stderr.write(
          `[kageha-wa] INBOUND from=${from} self=${Boolean(msg.key.fromMe)} text=${text.slice(0, 80)}\n`
        );
      } catch (e) {
        emit({ type: "error", error: `message parse: ${e}` });
      }
    }
  });
}

async function handleCommand(cmd) {
  if (!cmd || typeof cmd !== "object") return;
  if (cmd.type === "ping") {
    emit({ type: "pong" });
    return;
  }
  if (cmd.type === "logout") {
    shuttingDown = true;
    try {
      await sock?.logout();
    } catch {
      /* ignore */
    }
    emit({ type: "status", status: "logged_out" });
    process.exit(0);
  }
  if (cmd.type === "send" || cmd.type === "send_image") {
    if (!sock) {
      emit({ type: "error", error: "not connected" });
      return;
    }
    // Prefer exact chat JID (e.g. xxx@lid self-chat); fallback to phone@s.whatsapp.net
    const to =
      (cmd.chat && String(cmd.chat).includes("@") && String(cmd.chat)) ||
      phoneToJid(cmd.to);
    try {
      if (cmd.type === "send_image") {
        const filePath = String(cmd.path || "");
        if (!filePath || !fs.existsSync(filePath)) {
          emit({ type: "error", error: `image missing: ${filePath}`, to: jidToPhone(to) || to });
          return;
        }
        const caption = String(cmd.caption || "").slice(0, 1024);
        const buf = fs.readFileSync(filePath);
        if (caption) noteOutbound(caption);
        noteOutbound(`[image:${path.basename(filePath)}]`);
        await sock.sendMessage(to, {
          image: buf,
          caption: caption || undefined,
          mimetype: mimeForImage(filePath),
        });
        emit({
          type: "sent",
          to: jidToPhone(to) || to,
          chat: to,
          ok: true,
          kind: "image",
          path: filePath,
        });
        process.stderr.write(
          `[kageha-wa] sent image to=${jidToPhone(to) || to} path=${filePath}\n`
        );
        return;
      }
      const text = String(cmd.text || "").slice(0, 4000);
      noteOutbound(text);
      await sock.sendMessage(to, { text });
      emit({ type: "sent", to: jidToPhone(to) || to, chat: to, ok: true });
    } catch (e) {
      emit({
        type: "error",
        error: `send failed: ${e}`,
        to: jidToPhone(to) || to,
        chat: to,
      });
    }
  }
}

function mimeForImage(filePath) {
  const ext = path.extname(String(filePath || "")).toLowerCase();
  if (ext === ".png") return "image/png";
  if (ext === ".webp") return "image/webp";
  if (ext === ".gif") return "image/gif";
  return "image/jpeg";
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", (line) => {
  line = line.trim();
  if (!line) return;
  try {
    handleCommand(JSON.parse(line));
  } catch (e) {
    emit({ type: "error", error: `bad command: ${e}` });
  }
});

process.on("SIGINT", () => {
  shuttingDown = true;
  process.exit(0);
});

start().catch((e) => {
  emit({ type: "error", error: String(e) });
  process.exit(1);
});
