import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import readline from "node:readline";
import qrcode from "qrcode-terminal";
import makeWASocket, { DisconnectReason, downloadMediaMessage, useMultiFileAuthState } from "@whiskeysockets/baileys";
import mime from "mime-types";
import P from "pino";

const logger = P({ level: "silent" });

const args = process.argv.slice(2);
const valueAfter = (flag, fallback) => {
  const index = args.indexOf(flag);
  return index >= 0 ? args[index + 1] : fallback;
};
const authDir = valueAfter("--auth-dir", path.join(process.cwd(), ".kageha-whatsapp"));
const inboundDir = valueAfter("--inbound-dir", path.join(authDir, "inbound"));
fs.mkdirSync(inboundDir, { recursive: true });
const sentMessageIds = new Set();

const emit = (value) => process.stdout.write(`${JSON.stringify(value)}\n`);
const rememberSent = (result) => {
  const id = result?.key?.id;
  if (id) {
    sentMessageIds.add(id);
    setTimeout(() => sentMessageIds.delete(id), 120000).unref();
  }
};
const bareJid = (jid) => String(jid || "").split("@")[0].split(":")[0];
const isBroadcastJid = (jid) => {
  const value = String(jid || "");
  return value.endsWith("@newsletter") || value.endsWith("@broadcast");
};
const sendMedia = async (sock, to, runId, relative) => {
  const sessionRoot = path.resolve(process.env.KAGEHA_HOME || path.join(process.env.HOME || ".", ".kageha"), "sessions", runId);
  const file = path.resolve(sessionRoot, relative);
  if (!file.startsWith(sessionRoot) || !fs.existsSync(file)) return;
  const content = fs.readFileSync(file);
  const contentType = mime.lookup(file) || "application/octet-stream";
  const payload = contentType.startsWith("image/") ? { image: content } :
    contentType.startsWith("video/") ? { video: content } :
    contentType.startsWith("audio/") ? { audio: content } : { document: content, fileName: path.basename(file) };
  rememberSent(await sock.sendMessage(to, payload));
};

const start = async () => {
  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  const sock = makeWASocket({ auth: state, logger, printQRInTerminal: false, markOnlineOnConnect: false });
  sock.ev.on("creds.update", saveCreds);
  sock.ev.on("connection.update", ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      emit({ type: "qr", value: qr });
      qrcode.generate(qr, { small: true }, (display) => process.stderr.write(`${display}\n`));
    }
    if (connection === "close") {
      const code = lastDisconnect?.error?.output?.statusCode;
      if (code !== DisconnectReason.loggedOut) start().catch((error) => emit({ type: "error", error: String(error) }));
    }
    if (connection === "open") emit({ type: "ready" });
  });
  sock.ev.on("messages.upsert", async ({ messages }) => {
    for (const message of messages) {
      if (!message.message) continue;
      const from = message.key.remoteJid;
      if (!from || from.endsWith("@g.us") || isBroadcastJid(from)) continue;
      const ownJid = sock.user?.id || state.creds.me?.id || "";
      const ownIds = [ownJid, state.creds.me?.id, state.creds.me?.lid]
        .filter(Boolean)
        .map(bareJid);
      const alternateJid = message.key.remoteJidAlt;
      const isSelfChat = [from, alternateJid]
        .filter(Boolean)
        .some((jid) => ownIds.includes(bareJid(jid)));
      if (message.key.fromMe && (!isSelfChat || sentMessageIds.has(message.key.id))) {
        sentMessageIds.delete(message.key.id);
        continue;
      }
      // Self-chat events may carry the linked account's device-qualified JID
      // (for example `number:device@s.whatsapp.net`). Always reduce it to the
      // phone number so it matches WHATSAPP_QR_ALLOWED_USERS.
      const senderJid = isSelfChat ? (state.creds.me?.id || ownJid) : message.key.senderPn || message.key.participantPn || from;
      const sender = bareJid(senderJid);
      emit({ type: "message_received", from: sender, jid: from });
      const content = message.message.conversation || message.message.extendedTextMessage?.text ||
        message.message.imageMessage?.caption || message.message.documentMessage?.caption || "";
      const media = [];
      const mediaNode = message.message.imageMessage || message.message.videoMessage ||
        message.message.audioMessage || message.message.documentMessage;
      if (mediaNode) {
        const buffer = await downloadMediaMessage(message, "buffer", {});
        const filename = mediaNode.fileName || `${message.key.id}.bin`;
        const target = path.join(inboundDir, `${Date.now()}-${filename.replace(/[^A-Za-z0-9._-]/g, "_")}`);
        fs.writeFileSync(target, buffer);
        media.push({ kind: mediaNode.mimetype?.split("/")[0] || "file", filename, content_type: mediaNode.mimetype, external_id: message.key.id, local_path: target, size_bytes: buffer.length });
      }
      emit({ type: "message", id: message.key.id, from: sender, text: content, media });
    }
  });
  const rl = readline.createInterface({ input: process.stdin });
  rl.on("line", async (line) => {
    try {
      const command = JSON.parse(line);
      if (command.type !== "send") return;
      if (command.text) {
        rememberSent(await sock.sendMessage(`${command.to}@s.whatsapp.net`, { text: command.text }));
      }
      for (const artifact of command.artifacts || []) await sendMedia(sock, `${command.to}@s.whatsapp.net`, command.run_id, artifact);
    } catch (error) { emit({ type: "send_error", error: String(error) }); }
  });
};

start().catch((error) => { emit({ type: "error", error: String(error) }); process.exitCode = 1; });
