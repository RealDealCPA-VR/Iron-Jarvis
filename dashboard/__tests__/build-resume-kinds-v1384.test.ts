/**
 * v1.384.0 REVIEW — the PAGE half of resume, over a log holding EVERY kind of
 * frame (fixtures/build-resume-kinds-v1384.json, written by
 * tests/test_build_long_replies_review_v1384.py from the real TurnLog): an
 * approval card and its answer, a steer note read at a round boundary, a
 * RESET (the bubble cleared, new words after), a limit card, and an ERROR
 * end. For EVERY cut N: a stream that read the live frames up to N, put away
 * and carried on in a NEW hook from what it kept with the daemon's answer to
 * `?after=<position N>`, ends in the SAME state as one that read the whole
 * turn — text, thinking, steps, the card (answered = gone), the steer notes.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import fixture from "./fixtures/build-resume-kinds-v1384.json";
import { useChatStream, type StreamSeed, type ToolCard } from "@/lib/useChatStream";

vi.mock("@/lib/api", () => ({
  API_BASE: "http://d",
  ijToken: () => "",
  flattenDetail: (d: unknown) => String(d),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(m: string, s = 0) {
      super(m);
      this.status = s;
    }
  },
}));

type Frame = [string | null, string, string];
const FX = fixture as unknown as { stored: [string, string][]; live: Frame[]; resumes: Record<string, Frame[]> };

function sseText(frames: Frame[]): string {
  return frames.map(([id, e, d]) => `${id ? `id: ${id}\n` : ""}event: ${e}\ndata: ${d}\n\n`).join("");
}

function body(text: string): Response {
  const enc = new TextEncoder().encode(text);
  return new Response(
    new ReadableStream<Uint8Array>({
      start(c) {
        for (let i = 0; i < enc.length; i += 61) c.enqueue(enc.slice(i, i + 61));
        c.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

const resume = (resumed: boolean): Frame => [null, "resume", JSON.stringify({ resumed, running: true })];
const caughtUp: Frame = [null, "caught_up", JSON.stringify({ id: "" })];

function norm(s: StreamSeed | null) {
  if (!s) return null;
  return {
    text: s.text,
    thinking: s.thinking,
    tools: s.tools.map((t: ToolCard) => {
      const { startedAt: _a, endedAt: _b, ...rest } = t;
      return rest;
    }),
    approval: s.approval,
    steered: s.steered,
    committed: s.committed,
  };
}

async function runIt(hook: { current: ReturnType<typeof useChatStream> }, args: Parameters<ReturnType<typeof useChatStream>["run"]>) {
  await act(async () => {
    try {
      await hook.current.run(...args);
    } catch {
      /* the turn ends on its error frame */
    }
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("every kind of frame, cut anywhere, carried on", () => {
  it("the fixture holds a card, its answer, a steer note, a reset, a limit card and an error end", () => {
    const kinds = new Set(FX.live.map((f) => f[1]));
    for (const k of ["approval", "approval_resolved", "round", "reset", "account_limit", "error", "thinking", "token"])
      expect(kinds.has(k), k).toBe(true);
  });

  it("for EVERY N the carried-on state is the whole turn's", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => body(sseText([resume(false), ...FX.resumes["0"], caughtUp]))));
    const whole = renderHook(() => useChatStream({ textInState: false }));
    await runIt(whole.result, [undefined, undefined, undefined, { rejoin: "t", ids: true }]);
    const want = norm(whole.result.current.seedOf?.() ?? null);
    expect(want?.text).toBe("Changed cart.py; the receipt is next.");
    expect(want?.approval).toBeNull();
    expect(want?.steered).toEqual(["also fix the receipt"]);
    whole.unmount();
    for (let n = 1; n <= FX.live.length; n += 1) {
      const prefix = FX.live.slice(0, n);
      vi.stubGlobal("fetch", vi.fn(async () => body(sseText([resume(false), caughtUp, ...prefix]))));
      const a = renderHook(() => useChatStream({ textInState: false }));
      let last = "";
      await runIt(a.result, [
        { text: "go", ids: true },
        undefined,
        undefined,
        { path: "/build/sessions/s/turn", onPosition: (p) => (last = p) },
      ]);
      const kept = a.result.current.seedOf?.();
      a.unmount();
      // A cut on the error frame: the turn is over — nothing to carry on.
      if (prefix[n - 1][1] === "error") continue;
      expect(last, `position at ${n}`).toBe(prefix[n - 1][0]);
      vi.stubGlobal("fetch", vi.fn(async () => body(sseText([resume(true), ...FX.resumes[String(n)], caughtUp]))));
      const b = renderHook(() => useChatStream({ textInState: false, seed: kept }));
      await runIt(b.result, [undefined, undefined, undefined, { rejoin: "t", ids: true, resume: { after: last, seed: kept! } }]);
      expect(norm(b.result.current.seedOf?.() ?? null), `cut at ${n} (${prefix[n - 1][1]})`).toEqual(want);
      b.unmount();
    }
  }, 60_000);
});
