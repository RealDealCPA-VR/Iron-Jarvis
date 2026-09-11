/**
 * v1.246.0 — a chat turn never hangs silently (the client half).
 *
 * The user's report: the chat "gets hung up from time to time". streamSSE had
 * no limit of any kind, so a turn whose daemon stopped answering spun forever
 * under the same pulsing "Thinking…" a healthy turn showed. Now:
 *   - no bytes at all (the daemon's keepalive included) for `stallMs` ends the
 *     turn with words; keepalives reset that clock;
 *   - no response at all within `prepMs` ends it with words;
 *   - the caller's own abort stays silent, exactly as before;
 *   - the bubble says what it is waiting on and for how long.
 */
import { act, render, renderHook, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  prepDetail,
  stallDetail,
  streamSSE,
  useChatStream,
  type SSEEvent,
  type StreamWatch,
} from "@/lib/useChatStream";
import { formatElapsed, QuietNote, TurnClock } from "@/components/chat/TurnClock";

function readSrc(rel: string): string {
  return readFileSync(path.join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");
}

const enc = new TextEncoder();
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** A body that emits `chunks` `gapMs` apart, then closes or hangs forever. */
function bodyOf(chunks: string[], gapMs: number, end: "close" | "hang") {
  return new ReadableStream<Uint8Array>({
    async start(controller) {
      try {
        for (const c of chunks) {
          await sleep(gapMs);
          controller.enqueue(enc.encode(c));
        }
        if (end === "close") controller.close();
      } catch {
        /* cancelled by the watchdog mid-emit */
      }
    },
  });
}

async function collect(
  watch: StreamWatch,
  signal?: AbortSignal,
): Promise<SSEEvent[]> {
  const out: SSEEvent[] = [];
  for await (const ev of streamSSE("/chat/stream", { messages: [] }, signal, watch))
    out.push(ev);
  return out;
}

/** A fetch that never answers until its signal aborts. */
function neverAnswers() {
  return vi.fn(
    (_url: string, init: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init.signal?.addEventListener("abort", () => {
          const e = new Error("aborted");
          e.name = "AbortError";
          reject(e);
        });
      }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("streamSSE watchdog (v1.246.0)", () => {
  it("a stream that goes silent ends with words, not a spinner", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(bodyOf(['event: round\ndata: {"round":0}\n\n'], 0, "hang")),
      ),
    );
    const events = await collect({ stallMs: 80 });
    expect(events[0]).toEqual({ type: "round", round: 0 });
    expect(events.at(-1)).toEqual({ type: "error", detail: stallDetail(80), status: 0 });
    expect((events.at(-1) as { offline?: boolean }).offline).toBeUndefined();
  });

  it("a response that begins and then sends nothing at all is stopped too", async () => {
    // No first chunk to arm the clock from — the watchdog must start at open.
    vi.stubGlobal("fetch", vi.fn(async () => new Response(bodyOf([], 0, "hang"))));
    const events = await collect({ stallMs: 80 });
    expect(events).toEqual([{ type: "error", detail: stallDetail(80), status: 0 }]);
  });

  it("keepalives keep a slow turn alive past the stall limit", async () => {
    const beats = Array.from({ length: 6 }, () => ": keepalive\n\n");
    const done = `event: done\ndata: ${JSON.stringify({ reply: "made it" })}\n\n`;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(bodyOf([...beats, done], 30, "close"))),
    );
    // 7 chunks x 30 ms = 210 ms of turn with NO frame — far past 80 ms.
    const events = await collect({ stallMs: 80 });
    expect(events).toEqual([{ type: "done", reply: "made it" }]);
  });

  it("a preparation that never answers is stopped with words", async () => {
    vi.stubGlobal("fetch", neverAnswers());
    const events = await collect({ prepMs: 50 });
    expect(events).toEqual([{ type: "error", detail: prepDetail(50), status: 0 }]);
  });

  it("the caller's own abort stays silent", async () => {
    vi.stubGlobal("fetch", neverAnswers());
    const ctl = new AbortController();
    setTimeout(() => ctl.abort(), 20);
    const events = await collect({ prepMs: 60_000 }, ctl.signal);
    expect(events).toEqual([]);
  });

  it("onOpen fires once the response has begun", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(bodyOf([], 0, "close"))),
    );
    const onOpen = vi.fn();
    await collect({ onOpen });
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("the stall message tells the user what happened and what to do", () => {
    expect(stallDetail(60_000)).toMatch(/60 s/);
    expect(stallDetail(60_000)).toMatch(/Retry/);
    expect(prepDetail(600_000)).toMatch(/10 minutes/);
  });
});

describe("useChatStream says where the turn is (v1.246.0)", () => {
  it("preparing (with files) → working → cleared", async () => {
    let answer!: (r: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>((r) => (answer = r))),
    );
    const { result } = renderHook(() => useChatStream());
    let running!: Promise<unknown>;
    act(() => {
      running = result.current.run({ messages: [], attachments: ["C:/x/a.pdf"] });
    });
    expect(result.current.phase).toBe("preparing");
    expect(result.current.withFiles).toBe(true);
    expect(typeof result.current.startedAt).toBe("number");

    const done = `event: done\ndata: ${JSON.stringify({ reply: "ok" })}\n\n`;
    await act(async () => {
      answer(new Response(bodyOf([done], 0, "close")));
      await running;
    });
    expect(result.current.phase).toBeNull();
    expect(result.current.startedAt).toBeNull();
    expect(result.current.lastEventAt).toBeNull();
  });

  it("a turn with no attachments is not 'reading your files'", async () => {
    vi.stubGlobal("fetch", neverAnswers());
    const { result } = renderHook(() => useChatStream());
    act(() => {
      void result.current.run({ messages: [] });
    });
    expect(result.current.phase).toBe("preparing");
    expect(result.current.withFiles).toBe(false);
    act(() => result.current.abort());
  });
});

describe("TurnClock / QuietNote", () => {
  it("formats elapsed time", () => {
    expect(formatElapsed(0)).toBe("0s");
    expect(formatElapsed(12_400)).toBe("12s");
    expect(formatElapsed(65_000)).toBe("1:05");
  });

  it("a fast turn shows no clock; a slow one counts up", () => {
    vi.useFakeTimers();
    render(<TurnClock since={Date.now()} />);
    expect(screen.queryByTestId("turn-clock")).toBeNull();
    act(() => {
      vi.advanceTimersByTime(4_000);
    });
    expect(screen.getByTestId("turn-clock").textContent).toBe("4s");
  });

  it("QuietNote appears only after the turn has gone quiet", () => {
    vi.useFakeTimers();
    render(<QuietNote since={Date.now()} />);
    expect(screen.queryByTestId("turn-quiet")).toBeNull();
    act(() => {
      vi.advanceTimersByTime(9_000);
    });
    expect(screen.getByTestId("turn-quiet").textContent).toContain("Still working · 9s");
  });

  it("no turn, no clock", () => {
    render(<TurnClock since={null} />);
    render(<QuietNote since={null} />);
    expect(screen.queryByTestId("turn-clock")).toBeNull();
    expect(screen.queryByTestId("turn-quiet")).toBeNull();
  });
});

describe("the chat surfaces wire the status in (source pins)", () => {
  it("the Chat page bubble names the wait, times it, and flags a quiet turn", () => {
    const src = readSrc("app/chat/page.tsx");
    expect(src).toContain('stream.phase === "preparing" && stream.withFiles');
    expect(src).toContain('"Reading your files…"');
    expect(src).toContain("<TurnClock since={stream.startedAt ?? null} />");
    expect(src).toContain("<QuietNote since={stream.lastEventAt ?? null} />");
  });

  it("the Build pane chat shows the same clock", () => {
    const src = readSrc("components/terminal/PaneChat.tsx");
    expect(src).toContain("<TurnClock since={stream.startedAt ?? null} />");
  });

  it("the hook hands its watch to streamSSE", () => {
    const src = readSrc("lib/useChatStream.ts");
    expect(src).toMatch(/streamSSE\(\s*"\/chat\/stream",\s*body,\s*controller\.signal,\s*watch,?\s*\)/);
  });
});
