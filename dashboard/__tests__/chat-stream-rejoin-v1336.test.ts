/**
 * v1.336.0 — the real stream reader rejoins a turn with a GET.
 * `streamSSE(path, undefined, …)` reads `GET path` (no body, no JSON type);
 * any body still POSTs exactly as before.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async () => {
  class ApiError extends Error {
    status: number;
    constructor(m: string, s = 0) {
      super(m);
      this.status = s;
    }
  }
  return { API_BASE: "http://d", ijToken: () => "tok", ApiError, flattenDetail: (d: unknown) => String(d) };
});

import { streamSSE } from "@/lib/useChatStream";

function sseResponse(text: string): Response {
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(new TextEncoder().encode(text));
      c.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

afterEach(() => vi.unstubAllGlobals());

describe("streamSSE", () => {
  it("rejoins with a GET and reads the same frames", async () => {
    const fetchMock = vi.fn(async () =>
      sseResponse('event: token\ndata: {"text": "hel"}\n\nevent: token\ndata: {"text": "lo"}\n\n'),
    );
    vi.stubGlobal("fetch", fetchMock);
    const got: unknown[] = [];
    for await (const ev of streamSSE("/chat/turns/t1/stream", undefined)) got.push(ev);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://d/chat/turns/t1/stream");
    expect(init.method).toBe("GET");
    expect(init.body).toBeUndefined();
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok");
    expect(got).toEqual([
      { type: "token", text: "hel" },
      { type: "token", text: "lo" },
    ]);
  });

  it("a body still POSTs as JSON", async () => {
    const fetchMock = vi.fn(async () => sseResponse(""));
    vi.stubGlobal("fetch", fetchMock);
    for await (const _ of streamSSE("/chat/stream", { a: 1 })) void _;
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"a":1}');
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });
});
