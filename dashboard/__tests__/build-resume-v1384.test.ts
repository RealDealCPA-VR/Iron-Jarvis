/**
 * v1.384.0 — the PAGE half of "no lag or jump on return".
 *
 * THE PROPERTY, on a REAL recorded Build turn (fixtures/build-resume-v1384.json,
 * written by tests/test_build_resume_v1384.py from the real app — thinking
 * and words in small pieces, so the daemon MERGED them; tool calls; a helper):
 * for EVERY cut point N, the chat stream (lib/useChatStream) that read the
 * live frames up to N, was put away (its `seedOf`), and carried on in a NEW
 * hook from that state with the daemon's answer to `?after=<position N>`,
 * ends in the SAME state as one that read the whole turn — the reply's text,
 * its thinking, every step, the open card and the app asks — and the page's
 * own fold (the helpers) agrees too. Nothing lost, nothing twice.
 *
 * And the two things around it: the carry-on asks with `?after=` (not the
 * whole turn), and a WHOLE replay (`resumed: false`) is folded and drawn in
 * ONE pass — nothing of it shows until `caught_up`.
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import fixture from "./fixtures/build-resume-v1384.json";
import { useChatStream, type StreamSeed, type ToolCard } from "@/lib/useChatStream";
import { applyHelperFrame, type HelperView } from "@/lib/buildHelpers";

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
  return frames
    .map(([id, e, d]) => `${id ? `id: ${id}\n` : ""}event: ${e}\ndata: ${d}\n\n`)
    .join("");
}

function body(text: string, cut?: number): Response {
  // Delivered in uneven chunks (a real body arrives in pieces).
  const enc = new TextEncoder().encode(text);
  return new Response(
    new ReadableStream<Uint8Array>({
      start(c) {
        const step = cut ?? 97;
        for (let i = 0; i < enc.length; i += step) c.enqueue(enc.slice(i, i + step));
        c.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

const resume = (resumed: boolean, running: boolean): Frame => [null, "resume", JSON.stringify({ resumed, running })];
const caughtUp: Frame = [null, "caught_up", JSON.stringify({ id: "" })];

/** The state the screen draws, without the client's own clock readings. */
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
    mcpAsks: s.mcpAsks,
    steered: s.steered,
    committed: s.committed,
    truncated: s.truncated,
  };
}

function helpersOf(frames: Frame[]): HelperView[] {
  let out: HelperView[] = [];
  for (const [, e, d] of frames) if (e === "helper") out = applyHelperFrame(out, JSON.parse(d), 1);
  return out;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

async function full(): Promise<StreamSeed | null> {
  vi.stubGlobal("fetch", vi.fn(async () => body(sseText([resume(false, false), ...FX.resumes["0"], caughtUp]))));
  const { result } = renderHook(() => useChatStream({ textInState: false }));
  await act(async () => {
    await result.current.run(undefined, undefined, undefined, { rejoin: "t", ids: true });
  });
  return result.current.seedOf?.() ?? null;
}

describe("cut anywhere, carry on, and the state is the whole turn's", () => {
  it("the fixture is a real merged turn with steps and a helper", () => {
    const live = FX.live.map((f) => f[1]);
    expect(live.filter((e) => e === "token").length).toBeGreaterThan(FX.stored.filter((f) => f[0] === "token").length);
    expect(live).toContain("helper");
    expect(live).toContain("tool_call");
    expect(live).toContain("thinking");
  });

  it("for EVERY N: live[:N], put away, then ?after=<position N> == the whole turn", async () => {
    const want = norm(await full());
    expect(want?.text).toContain("All done");
    expect(want?.tools.length).toBeGreaterThan(2);
    const wantHelpers = helpersOf(FX.live);
    expect(wantHelpers.length).toBe(1);
    for (let n = 1; n <= FX.live.length; n += 1) {
      const prefix = FX.live.slice(0, n);
      // A: the POST that started it, read up to N, then the user left.
      vi.stubGlobal("fetch", vi.fn(async () => body(sseText([resume(false, true), caughtUp, ...prefix]))));
      const a = renderHook(() => useChatStream({ textInState: false }));
      let last = "";
      await act(async () => {
        await a.result.current.run({ text: "go", ids: true }, undefined, undefined, {
          path: "/build/sessions/s/turn",
          onPosition: (p) => {
            last = p;
          },
        });
      });
      expect(last).toBe(prefix[n - 1][0]);
      const kept = a.result.current.seedOf?.();
      expect(kept).toBeTruthy();
      a.unmount();
      // B: the return — a NEW hook drawn from what was kept, carrying on.
      const answer = FX.resumes[String(n)];
      const fetchB = vi.fn(async () => body(sseText([resume(true, false), ...answer, caughtUp])));
      vi.stubGlobal("fetch", fetchB);
      const b = renderHook(() => useChatStream({ textInState: false, seed: kept }));
      // Drawn at once, before anything is read.
      expect(b.result.current.streaming).toBe(true);
      expect(b.result.current.textStore?.get()).toBe(kept!.text);
      await act(async () => {
        await b.result.current.run(undefined, undefined, undefined, {
          rejoin: "t",
          ids: true,
          resume: { after: last, seed: kept! },
        });
      });
      const url = String((fetchB.mock.calls[0] as unknown[])[0]);
      expect(url).toBe(`http://d/chat/turns/t/stream?after=${encodeURIComponent(last)}`);
      expect(norm(b.result.current.seedOf?.() ?? null), `cut at ${n}`).toEqual(want);
      // The page's own fold (the helpers) agrees with the whole turn too.
      expect(helpersOf([...prefix, ...answer]), `helpers, cut at ${n}`).toEqual(wantHelpers);
      b.unmount();
    }
  }, 60_000);

  it("a carry-on the daemon answers with the WHOLE turn starts from nothing (never twice)", async () => {
    const want = norm(await full());
    const prefix = FX.live.slice(0, 20);
    vi.stubGlobal("fetch", vi.fn(async () => body(sseText([resume(false, true), caughtUp, ...prefix]))));
    const a = renderHook(() => useChatStream({ textInState: false }));
    await act(async () => {
      await a.result.current.run({ text: "go" }, undefined, undefined, { path: "/x" });
    });
    const kept = a.result.current.seedOf!()!;
    a.unmount();
    // The daemon no longer holds that position: resumed false + everything.
    vi.stubGlobal("fetch", vi.fn(async () => body(sseText([resume(false, false), ...FX.resumes["0"], caughtUp]))));
    const b = renderHook(() => useChatStream({ textInState: false, seed: kept }));
    await act(async () => {
      await b.result.current.run(undefined, undefined, undefined, {
        rejoin: "t",
        resume: { after: prefix[19][0]!, seed: kept },
      });
    });
    expect(norm(b.result.current.seedOf!())).toEqual(want);
  });

  it("an older daemon's answer with no resume frame is folded from nothing too", async () => {
    const want = norm(await full());
    const kept = { ...(await full())!, text: "STALE TEXT", tools: [] };
    vi.stubGlobal("fetch", vi.fn(async () => body(sseText(FX.stored.map(([e, d]) => [null, e, d] as Frame)))));
    const b = renderHook(() => useChatStream({ textInState: false, seed: kept }));
    await act(async () => {
      await b.result.current.run(undefined, undefined, undefined, {
        rejoin: "t",
        resume: { after: "3:0", seed: kept },
      });
    });
    expect(norm(b.result.current.seedOf!())).toEqual(want);
  });
});

describe("a whole replay is drawn in ONE pass", () => {
  it("nothing of the stored part shows until caught_up, then all of it", async () => {
    let ctl!: ReadableStreamDefaultController<Uint8Array>;
    const enc = new TextEncoder();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            new ReadableStream<Uint8Array>({
              start(c) {
                ctl = c;
              },
            }),
            { status: 200 },
          ),
      ),
    );
    const { result } = renderHook(() => useChatStream({ textInState: false }));
    let done!: Promise<unknown>;
    act(() => {
      done = result.current.run(undefined, undefined, undefined, { rejoin: "t", ids: true });
    });
    const stored = FX.resumes["0"];
    const half = Math.floor(stored.length / 2);
    const notDone = stored.filter((f) => f[1] !== "done");
    // The first half of the stored part arrives (its own network chunk).
    await act(async () => {
      ctl.enqueue(enc.encode(sseText([resume(false, true), ...notDone.slice(0, half)])));
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(result.current.tools).toEqual([]);
    expect(result.current.textStore?.get()).toBe("");
    // The rest, and caught_up: everything at once.
    await act(async () => {
      ctl.enqueue(enc.encode(sseText([...notDone.slice(half), caughtUp])));
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(result.current.tools.length).toBe(notDone.filter((f) => f[1] === "tool_call" && JSON.parse(f[2]).status === "started").length);
    expect(result.current.textStore?.get()).toContain("Round 0");
    // A LIVE frame after caught_up is drawn as it comes.
    await act(async () => {
      ctl.enqueue(enc.encode(sseText([["99:5", "token", JSON.stringify({ text: " more" })]])));
      await new Promise((r) => setTimeout(r, 40));
    });
    expect(result.current.textStore?.get()).toMatch(/ more$/);
    await act(async () => {
      ctl.close();
      await done;
    });
  });
});
