import type { ChatStreamBody, StreamHandlers } from "./types";

interface SseFrame {
  event: string;
  data: Record<string, unknown>;
}

function parseSseChunk(buffer: string): { frames: SseFrame[]; rest: string } {
  const frames: SseFrame[] = [];
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";
  for (const block of parts) {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) continue;
    try {
      const data = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
      frames.push({ event, data });
    } catch {
      /* ignore malformed frame */
    }
  }
  return { frames, rest };
}

export { parseSseChunk };

export async function streamChat(
  body: ChatStreamBody,
  handlers: StreamHandlers,
  { signal }: { signal?: AbortSignal } = {},
): Promise<Record<string, unknown>> {
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  const ctype = res.headers.get("content-type") || "";
  // Some proxies (Vite) may strip Content-Type while still streaming SSE.
  // Accept 200 + readable body when the type is missing or is event-stream.
  const looksLikeSse =
    ctype.includes("text/event-stream") ||
    ctype.includes("text/plain") ||
    ctype === "";
  if (!res.ok || !res.body || (!looksLikeSse && !ctype.includes("json"))) {
    const errBody = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(errBody.error || `Stream failed (${res.status})`);
  }
  // If we got JSON instead of SSE (buffered error / non-stream path), surface it.
  if (ctype.includes("application/json") && !ctype.includes("event-stream")) {
    const errBody = (await res.json().catch(() => ({}))) as {
      error?: string;
      message?: string;
    };
    throw new Error(
      errBody.error || errBody.message || `Stream failed (${res.status})`,
    );
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let donePayload: Record<string, unknown> | null = null;
  let lastError: string | null = null;
  let assembled = "";
  let sawFrame = false;
  let lastStatus = "";

  const handleFrame = (frame: SseFrame) => {
    sawFrame = true;
    if (frame.event === "status") {
      lastStatus = String(frame.data.label || "Working…");
      handlers.onStatus?.(lastStatus, frame.data);
    } else if (frame.event === "event") {
      handlers.onEvent?.(frame.data);
    } else if (frame.event === "tool_card") {
      handlers.onToolCard?.(frame.data);
      handlers.onEvent?.({ kind: "tool_card", ...frame.data });
    } else if (
      frame.event === "computer_frame" ||
      frame.event === "artifact_thumb"
    ) {
      handlers.onComputerFrame?.(frame.data, frame.event);
      handlers.onEvent?.({ kind: frame.event, ...frame.data });
    } else if (frame.event === "delta") {
      assembled += String(frame.data.text || "");
      handlers.onDelta?.(assembled);
    } else if (frame.event === "message") {
      const full = String(frame.data.text || "");
      assembled = full;
      handlers.onMessage?.(full, Boolean(frame.data.partial));
    } else if (frame.event === "done") {
      donePayload = frame.data;
      handlers.onDone?.(frame.data);
    } else if (frame.event === "error") {
      lastError = String(frame.data.error || "stream error");
      handlers.onError?.(lastError);
    }
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSseChunk(buffer);
      buffer = parsed.rest;
      for (const frame of parsed.frames) handleFrame(frame);
    }
    // Flush any trailing partial bytes the TextDecoder still holds, so a
    // multi-byte char split at the final chunk boundary isn't dropped.
    buffer += decoder.decode();
    for (const frame of parseSseChunk(buffer).frames) handleFrame(frame);
  } finally {
    // Release the response body stream even if a handler throws or the
    // fetch is aborted, so it isn't left locked until GC.
    reader.cancel().catch(() => {});
  }

  // An explicit error frame always surfaces — don't let a later `done`
  // silently swallow it.
  if (lastError) throw new Error(lastError);
  if (!donePayload) {
    if (signal?.aborted) {
      return { status: "cancelled", message: assembled };
    }
    // Soft-recover: treat leftover assembled text as the answer.
    if (assembled) {
      return { status: "success", message: assembled };
    }
    // Truly empty body (no SSE frames at all).
    if (!sawFrame && !buffer.trim()) {
      throw new Error("Stream failed (empty response)");
    }
    // Got status/events then the connection dropped before `done`
    // (common when the webui/app-server restarts mid-turn).
    const where = lastStatus ? ` after “${lastStatus}”` : "";
    throw new Error(
      `Stream ended without result${where}. Retry — the server may have restarted.`,
    );
  }
  return donePayload;
}
