import { afterEach, describe, expect, it, vi } from "vitest";
import { parseSseChunk, streamChat } from "./stream";

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
});
