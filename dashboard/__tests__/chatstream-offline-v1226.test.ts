/**
 * v1.226.0 (F-F-7) — the daemon dying MID-TURN yields an `offline` error.
 *
 * The pre-fetch catch in streamSSE already flagged a transport failure as
 * `{detail:"daemon offline", status:0, offline:true}`, but the reader-loop
 * catch (the daemon dropping while tokens stream) yielded the raw transport
 * text ("Failed to fetch") with no status/offline, so the chat page showed
 * that text instead of the OfflineHint. A TypeError from reader.read() is
 * now the same offline event; any other error keeps its message.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { streamSSE } from "@/lib/useChatStream";

function responseWithReader(read: () => Promise<{ value?: Uint8Array; done: boolean }>) {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    body: {
      getReader: () => ({ read, cancel: async () => undefined }),
    },
  } as unknown as Response;
}

async function collect(path = "/chat/stream") {
  const out: unknown[] = [];
  for await (const ev of streamSSE(path, { message: "hi" })) out.push(ev);
  return out;
}

afterEach(() => vi.unstubAllGlobals());

describe("streamSSE reader-loop failure (v1.226.0)", () => {
  it("a TypeError from reader.read() is an offline error (status 0, offline:true)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        responseWithReader(async () => {
          throw new TypeError("Failed to fetch");
        }),
      ),
    );
    const events = await collect();
    expect(events).toEqual([
      { type: "error", detail: "daemon offline", status: 0, offline: true },
    ]);
  });

  it("a parser fault on a bad frame (`data: null`) is NOT flagged offline", async () => {
    const frames = [new TextEncoder().encode("event: token\ndata: null\n\n")];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        responseWithReader(async () =>
          frames.length ? { value: frames.shift(), done: false } : { done: true },
        ),
      ),
    );
    const events = await collect();
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ type: "error" });
    expect((events[0] as { offline?: boolean }).offline).toBeUndefined();
    expect((events[0] as { detail: string }).detail).not.toBe("daemon offline");
  });

  it("a non-ok response with a list-shaped 422 detail is flattened (C4)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 422,
        statusText: "Unprocessable Entity",
        json: async () => ({
          detail: [{ loc: ["body", "message"], msg: "Input should be a valid string" }],
        }),
      })),
    );
    const events = await collect();
    expect(events).toEqual([
      { type: "error", detail: "message: Input should be a valid string", status: 422 },
    ]);
  });

  it("ANY read() rejection is transport (offline) — the classification is by call site, not error class", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        responseWithReader(async () => {
          throw new Error("socket reset");
        }),
      ),
    );
    const events = await collect();
    expect(events).toEqual([
      { type: "error", detail: "daemon offline", status: 0, offline: true },
    ]);
  });
});
