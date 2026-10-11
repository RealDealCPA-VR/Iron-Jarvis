/**
 * v1.332.0: "Local only" chats, the wire half of the dashboard (L2).
 *
 * The daemon refuses a cloud model in a local-only chat with `code:
 * "local_only"` plus the provider and model it did not send to: as the
 * stream's `error` frame, or as an HTTP 409 JSON body (POST /chat, and the
 * stream route itself if it refuses before streaming). The page can only
 * offer "Send to … once" when those three fields survive the transport, so
 * each hop is pinned here with the REAL modules (nothing mocked but fetch):
 *  - `sseEventFrom("error", …)` keeps code / provider / model;
 *  - `streamSSE` reads them off a non-ok JSON body;
 *  - `StreamError` carries them (and "" when absent);
 *  - `api()`'s ApiError keeps the refused request's JSON `body`;
 *  - lib/localOnly reads both shapes;
 *  - follow-ups send `local_only` (only when on) and the chat's `thread_id`.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { StreamError, sseEventFrom, streamSSE, useChatStream } from "@/lib/useChatStream";
import { ApiError, api } from "@/lib/api";
import { followupBody } from "@/lib/followups";
import { localOnlyRefusalFrom } from "@/lib/localOnly";

const SENTENCE = "This chat is local only, so nothing was sent to Claude. Pick a local model, or allow Claude for this one message.";
const REFUSAL_BODY = {
  detail: SENTENCE,
  code: "local_only",
  provider: "claude-cli",
  model: "claude-opus-4-8",
};

afterEach(() => vi.unstubAllGlobals());

describe("the stream's error frame", () => {
  it("keeps code, provider and model on a local-only refusal", () => {
    expect(sseEventFrom("error", { ...REFUSAL_BODY })).toEqual({ type: "error", ...REFUSAL_BODY });
  });

  it("CONTROL: an ordinary error frame stays exactly as it was", () => {
    expect(sseEventFrom("error", { detail: "the model timed out" })).toEqual({
      type: "error",
      detail: "the model timed out",
    });
  });

  it("a refused stream request (409 JSON before any frame) yields the same fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 409,
        statusText: "Conflict",
        json: async () => REFUSAL_BODY,
      })),
    );
    const out: unknown[] = [];
    for await (const ev of streamSSE("/chat/stream", { messages: [] })) out.push(ev);
    expect(out).toEqual([{ type: "error", ...REFUSAL_BODY, status: 409 }]);
  });

  it("CONTROL: a 4xx without a code adds nothing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 400, statusText: "Bad", json: async () => ({ detail: "empty" }) })),
    );
    const out: unknown[] = [];
    for await (const ev of streamSSE("/chat/stream", { messages: [] })) out.push(ev);
    expect(out).toEqual([{ type: "error", detail: "empty", status: 400 }]);
  });
});

describe("StreamError", () => {
  it("carries the refusal, and the page reads it", () => {
    const e = new StreamError(SENTENCE, 0, false, false, "", {
      code: "local_only",
      provider: "claude-cli",
      model: "claude-opus-4-8",
    });
    expect([e.code, e.provider, e.model]).toEqual(["local_only", "claude-cli", "claude-opus-4-8"]);
    expect(localOnlyRefusalFrom(e)).toEqual({ detail: SENTENCE, provider: "claude-cli", model: "claude-opus-4-8" });
  });

  it("CONTROL: an ordinary stream failure has empty fields and is no refusal", () => {
    const e = new StreamError("boom", 0, false, false);
    expect([e.code, e.provider, e.model]).toEqual(["", "", ""]);
    expect(localOnlyRefusalFrom(e)).toBeNull();
  });
});

describe("the hook's run() throws the refusal it read", () => {
  function sseResponse(frames: string[]) {
    const enc = new TextEncoder();
    const queue = frames.map((f) => enc.encode(f));
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      body: {
        getReader: () => ({
          read: async () => (queue.length ? { value: queue.shift(), done: false } : { done: true }),
          cancel: async () => undefined,
        }),
      },
    } as unknown as Response;
  }

  it("a refusal frame rejects with a StreamError carrying code, provider and model", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => sseResponse([`event: error
data: ${JSON.stringify(REFUSAL_BODY)}

`])),
    );
    const { result } = renderHook(() => useChatStream());
    let caught: unknown = null;
    await act(async () => {
      try {
        await result.current.run({ messages: [] });
      } catch (e) {
        caught = e;
      }
    });
    expect(caught).toBeInstanceOf(StreamError);
    const e = caught as StreamError;
    expect([e.message, e.code, e.provider, e.model]).toEqual([
      SENTENCE,
      "local_only",
      "claude-cli",
      "claude-opus-4-8",
    ]);
    expect(localOnlyRefusalFrom(e)?.provider).toBe("claude-cli");
  });
});

describe("api()'s ApiError keeps a refused request's body", () => {
  it("a 409 local_only from POST /chat reaches the page with its provider and model", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 409, statusText: "Conflict", json: async () => REFUSAL_BODY })),
    );
    let caught: unknown = null;
    try {
      await api("/chat", { method: "POST" });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).message).toBe(SENTENCE);
    expect((caught as ApiError).status).toBe(409);
    expect((caught as ApiError).body).toEqual(REFUSAL_BODY);
    expect(localOnlyRefusalFrom(caught)).toEqual({
      detail: SENTENCE,
      provider: "claude-cli",
      model: "claude-opus-4-8",
    });
  });

  it("CONTROL: a body that is not JSON leaves body unset", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 409,
        statusText: "Conflict",
        json: async () => {
          throw new SyntaxError("not json");
        },
      })),
    );
    const caught = await api("/chat", { method: "POST" }).catch((e: unknown) => e);
    expect((caught as ApiError).status).toBe(409);
    expect((caught as ApiError).body).toBeUndefined();
    expect(localOnlyRefusalFrom(caught)).toBeNull();
  });
});

describe("follow-ups", () => {
  const MSGS = [
    { role: "user", content: "q" },
    { role: "assistant", content: "a" },
  ];

  it("send local_only (when on) and the chat's thread_id", () => {
    expect(followupBody(MSGS, "ollama", "qwen3:14b", { localOnly: true, threadId: "t1" })).toEqual({
      messages: MSGS,
      provider: "ollama",
      model: "qwen3:14b",
      local_only: true,
      thread_id: "t1",
    });
  });

  it("CONTROL: off and unsaved, the body is exactly what it was", () => {
    expect(followupBody(MSGS, "ollama", "qwen3:14b", { localOnly: false })).toEqual({
      messages: MSGS,
      provider: "ollama",
      model: "qwen3:14b",
    });
    expect(followupBody(MSGS)).toEqual({ messages: MSGS });
  });
});
