import { afterEach, describe, expect, it, vi } from "vitest";
import { parseSseChunk, streamChat, StreamDroppedError } from "./stream";

function sseBody(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  let i = 0;
  return new ReadableStream({
    pull(controller) {
      if (i >= chunks.length) {
        controller.close();
        return;
      }
      controller.enqueue(enc.encode(chunks[i++]));
    },
  });
}

describe("parseSseChunk", () => {
  it("parses status frames", () => {
    const { frames, rest } = parseSseChunk(
      'event: status\ndata: {"label":"Starting…"}\n\n',
    );
    expect(rest).toBe("");
    expect(frames).toEqual([
      { event: "status", data: { label: "Starting…" } },
    ]);
  });
});

describe("streamChat", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("throws empty response only when body has no frames", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(sseBody([]), {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
      ),
    );
    await expect(
      streamChat({ thread_id: "t", session_id: "s", message: "hi" }, {}),
    ).rejects.toThrow(/empty response/);
  });

  it("reports premature close after status, not empty response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          sseBody([
            'event: status\ndata: {"phase":"starting","label":"Starting…"}\n\n',
          ]),
          {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          },
        ),
      ),
    );
    await expect(
      streamChat({ thread_id: "t", session_id: "s", message: "hi" }, {}),
    ).rejects.toThrow(/ended without result.*Starting/);
  });

  it("returns done payload when present", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          sseBody([
            'event: status\ndata: {"label":"Starting…"}\n\n',
            'event: done\ndata: {"status":"success","message":"Hello"}\n\n',
          ]),
          {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          },
        ),
      ),
    );
    const done = await streamChat({ thread_id: "t", session_id: "s", message: "hi" }, {});
    expect(done).toEqual({ status: "success", message: "Hello" });
  });

  it("surfaces an error frame even when followed by done", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          sseBody([
            'event: error\ndata: {"error":"boom"}\n\n',
            'event: done\ndata: {"status":"success","message":"Hello"}\n\n',
          ]),
          {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          },
        ),
      ),
    );
    await expect(
      streamChat({ thread_id: "t", session_id: "s", message: "hi" }, {}),
    ).rejects.toThrow("boom");
  });

  it("throws StreamDroppedError when connection drops after a turn_id", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          sseBody([
            'event: status\ndata: {"phase":"running","label":"Working…","turn_id":"turn-42","session_id":"s1","thread_id":"t1"}\n\n',
          ]),
          {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          },
        ),
      ),
    );
    try {
      await streamChat(
        { thread_id: "t", session_id: "s", message: "hi" },
        {},
      );
      expect.unreachable("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(StreamDroppedError);
      expect((err as StreamDroppedError).turnId).toBe("turn-42");
      expect((err as StreamDroppedError).sessionId).toBe("s1");
      expect((err as StreamDroppedError).threadId).toBe("t1");
    }
  });
});
