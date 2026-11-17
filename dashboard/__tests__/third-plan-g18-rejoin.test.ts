/**
 * Third north-star fix plan, group 18 (a reply's home is the daemon): the
 * pure pieces the Chat page and the Build pane's chat share.
 *
 *  - CS10: a question that carries its reply's name (`turnId`) is answered
 *    ONLY by that reply (`GET /chat/turns/{id}`); the time rule stays for a
 *    question saved before the name was kept.
 *  - CS2: the 409 merge and every re-read match messages by `turnId`, so
 *    one reply is in a chat once (`mergeByTurn`).
 *  - CS3: the 60 s silence is told apart from every other failure
 *    (`StreamError.stalled`), so the page can ask before it says anything.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

import {
  answeringTurn,
  hasReplyFor,
  lookupTurn,
  mergeByTurn,
  stampQuestion,
} from "@/lib/turnRejoin";
import { STREAM_STALL_MS, StreamError, useChatStream } from "@/lib/useChatStream";

type Msg = { role: "user" | "assistant"; content: string; turnId?: string; savedBy?: string; steer?: boolean };

class NotFound extends Error {
  status = 404;
}

/** A stand-in for lib/api `get`: answers from a table, 404 for the rest. */
function getFrom(table: Record<string, unknown>) {
  const asked: string[] = [];
  const get = async <T,>(path: string): Promise<T> => {
    asked.push(path);
    const r = table[path];
    if (r === undefined) throw new NotFound(`unmocked GET ${path}`);
    if (r instanceof Error) throw r;
    return r as T;
  };
  return { get, asked };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("CS10: which reply answers a question", () => {
  const ASKED = "2026-11-16T10:00:00.000Z";
  // The reply BEFORE this question: it started 4 s before the question was
  // asked, inside the 10 s clock-skew allowance, so the time rule takes it.
  const before = { turn_id: "chat-before", status: "done", running: false, started_at: "2026-11-16T09:59:56Z" };

  it("a question whose own reply never started is NOT answered by the reply before it", async () => {
    const { get, asked } = getFrom({
      "/chat/turns?thread_id=t7&limit=1": { turns: [before] },
      // GET /chat/turns/chat-mine is unknown: 404.
    });
    const turn = await answeringTurn(get, "t7", ASKED, "chat-mine");
    expect(turn).toBeUndefined();
    expect(asked).toEqual(["/chat/turns/chat-mine"]);
  });

  it("a question with a name is answered by exactly that reply", async () => {
    const { get } = getFrom({
      "/chat/turns/chat-mine": { turn_id: "chat-mine", status: "running", running: true, started_at: null, thread_id: "t7" },
    });
    expect(await answeringTurn(get, "t7", ASKED, "chat-mine")).toMatchObject({ turn_id: "chat-mine", running: true });
  });

  it("a question saved before names were kept still uses the time rule", async () => {
    const { get, asked } = getFrom({ "/chat/turns?thread_id=t7&limit=1": { turns: [before] } });
    expect(await answeringTurn(get, "t7", ASKED)).toMatchObject({ turn_id: "chat-before" });
    expect(asked).toEqual(["/chat/turns?thread_id=t7&limit=1"]);
  });

  it("lookupTurn tells unknown (404) from unreachable", async () => {
    expect(await lookupTurn(getFrom({}).get, "x")).toEqual({ known: false, reachable: true });
    const down = getFrom({ "/chat/turns/x": Object.assign(new Error("offline"), { status: 0 }) });
    expect(await lookupTurn(down.get, "x")).toEqual({ known: false, reachable: false });
    const there = getFrom({ "/chat/turns/x": { status: "done", running: false } });
    expect(await lookupTurn(there.get, "x")).toEqual({
      known: true,
      turn: { turn_id: "x", status: "done", running: false },
    });
  });
});

describe("CS2: a reply is in its chat once (mergeByTurn)", () => {
  const q = (turnId?: string, content = "question"): Msg => ({ role: "user", content, ...(turnId ? { turnId } : {}) });
  const a = (turnId?: string, content = "answer", savedBy?: string): Msg => ({
    role: "assistant",
    content,
    ...(turnId ? { turnId } : {}),
    ...(savedBy ? { savedBy } : {}),
  });

  it("no names (an older chat): exactly today's append", () => {
    const stored = [q(), a(undefined, "first")];
    const sent = [a(undefined, "second")];
    expect(mergeByTurn(stored, sent)).toEqual([...stored, ...sent]);
  });

  it("the same reply saved by another window is not added again", () => {
    const stored = [q("t1"), a("t1", "the reply")];
    expect(mergeByTurn(stored, [a("t1", "the reply")])).toEqual(stored);
  });

  it("the daemon's plain line is REPLACED by the page's own message, in its place", () => {
    const daemon = a("t1", "the reply", "daemon");
    const page = { ...a("t1", "the reply"), toolsUsed: ["read_file"] } as Msg;
    const stored = [q("t0"), a("t0"), q("t1"), daemon];
    const out = mergeByTurn(stored, [page]);
    expect(out).toEqual([q("t0"), a("t0"), q("t1"), page]);
  });

  it("a page message is never replaced by a daemon line", () => {
    const stored = [q("t1"), a("t1", "rich")];
    expect(mergeByTurn(stored, [a("t1", "plain", "daemon")])).toEqual(stored);
  });

  it("two different replies are both kept, in order", () => {
    const stored = [q("t1"), a("t1", "one")];
    const sent = [q("t2", "next"), a("t2", "two")];
    expect(mergeByTurn(stored, sent)).toEqual([...stored, ...sent]);
  });

  it("a user message is never dropped, even one with a name the chat already has", () => {
    const stored = [q("t1"), a("t1")];
    const sent = [q("t1", "asked again"), { role: "user", content: "a note", steer: true, turnId: "t1" } as Msg];
    expect(mergeByTurn(stored, sent)).toEqual([...stored, ...sent]);
  });

  it("hasReplyFor reads only replies, never a question", () => {
    expect(hasReplyFor([q("t1")], "t1")).toBe(false);
    expect(hasReplyFor([q("t1"), a("t1")], "t1")).toBe(true);
    expect(hasReplyFor([q("t1"), a("t1")], "")).toBe(false);
  });

  it("stampQuestion names the question being answered, and nothing else", () => {
    const first = a("t0");
    const asked = q(undefined, "new");
    const out = stampQuestion([first, asked], "t9");
    expect(out[0]).toBe(first);
    expect(out[1]).toEqual({ ...asked, turnId: "t9" });
    // A history that does not end on a question is left as it is.
    const ended = [q("t0"), a("t0")];
    expect(stampQuestion(ended, "t9")).toBe(ended);
  });
});

describe("CS3: the silence is told apart from every other failure", () => {
  it("a stream that sends nothing for 60 s fails with `stalled`", async () => {
    vi.useFakeTimers();
    // A response that opens and then never sends a byte (fake clock: no sleep).
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(new ReadableStream<Uint8Array>({ start() {} }))),
    );
    const { result } = renderHook(() => useChatStream());
    let failure: unknown = null;
    let settled = false;
    act(() => {
      void result.current
        .run({ messages: [], turn_id: "chat-x" })
        .catch((e) => {
          failure = e;
        })
        .finally(() => {
          settled = true;
        });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(STREAM_STALL_MS + 10);
    });
    expect(settled).toBe(true);
    expect(failure).toBeInstanceOf(StreamError);
    expect((failure as StreamError).stalled).toBe(true);
  });

  it("any other failure is not `stalled`", () => {
    expect(new StreamError("boom", 500, false, false).stalled).toBe(false);
  });
});
