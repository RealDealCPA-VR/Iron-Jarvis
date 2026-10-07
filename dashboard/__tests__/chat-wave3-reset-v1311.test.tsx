/**
 * Wave 3 (SPEED), v1.311.0 — W3-1: the `reset` frame clears what streamed.
 *
 * Plain words: when a reply comes out in the wrong language, the daemon now
 * STREAMS the corrected reply instead of making the user wait for it in
 * silence. It first sends `event: reset` — "discard the text so far" — and
 * then the new words. The chat must clear the bubble on that frame; without
 * it the rewrite is glued onto the end of the wrong-language text until the
 * final frame overwrites it.
 *
 * THESE PINS COUNT PUBLISHES TO THE STORE'S OWN SUBSCRIBER, one act() per
 * frame (the S-02 lesson in CLAUDE.md: an on-screen assertion inside one big
 * act() measures React's batching, not the code). The transport is a real
 * `streamSSE` over a fake fetch body the test feeds frame by frame, and
 * `requestAnimationFrame` is a queue this file flushes on purpose.
 *
 * `useChatStream` is NOT mocked here — this file drives the real hook.
 */

import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, API_BASE: "http://localhost", ijToken: () => "" };
});

import {
  decodeSSE,
  StreamError,
  useChatStream,
  useLiveText,
  type ChatStreamResult,
  type UseChatStream,
} from "@/lib/useChatStream";

// ------------------------------------------------------------------ transport

/** A response body the test writes SSE frames into, one at a time. */
class Wire {
  private chunks: Uint8Array[] = [];
  private waiting: ((r: { value?: Uint8Array; done: boolean }) => void) | null = null;
  private ended = false;
  private enc = new TextEncoder();

  frame(event: string, data: unknown): void {
    this.deliver(this.enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
  }

  end(): void {
    this.ended = true;
    if (this.waiting) {
      const w = this.waiting;
      this.waiting = null;
      w({ done: true });
    }
  }

  private deliver(c: Uint8Array): void {
    if (this.waiting) {
      const w = this.waiting;
      this.waiting = null;
      w({ value: c, done: false });
    } else this.chunks.push(c);
  }

  response(): Response {
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      body: {
        getReader: () => ({
          read: () =>
            new Promise<{ value?: Uint8Array; done: boolean }>((res) => {
              const c = this.chunks.shift();
              if (c) res({ value: c, done: false });
              else if (this.ended) res({ done: true });
              else this.waiting = res;
            }),
          cancel: async () => undefined,
        }),
      },
    } as unknown as Response;
  }
}

let wire: Wire;
let queued = new Map<number, () => void>();
let nextFrameId = 1;

function flushFrames(): void {
  const entries = [...queued.entries()];
  queued.clear();
  for (const [, cb] of entries) cb();
}

/** Let the async generator chain (fetch -> reader -> parse -> run loop) move. */
async function pump(): Promise<void> {
  for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
}

/** Deliver ONE frame in its own act(), then let the hook consume it. */
async function send(event: string, data: unknown): Promise<void> {
  await act(async () => {
    wire.frame(event, data);
    await pump();
  });
}

async function frame(): Promise<void> {
  await act(async () => {
    flushFrames();
  });
}

// ------------------------------------------------------------------- harness

let hook: UseChatStream | null = null;
let published: string[] = [];

function Live({ stream }: { stream: UseChatStream }) {
  return <div data-testid="live">{useLiveText(stream)}</div>;
}
function StoreOwner() {
  const s = useChatStream({ textInState: false });
  hook = s;
  return <Live stream={s} />;
}
function StateOwner() {
  const s = useChatStream();
  hook = s;
  return <div data-testid="state">{s.text}</div>;
}

function watch(): () => void {
  published = [];
  return hook!.textStore!.subscribe(() => {
    published.push(hook!.textStore!.get());
  });
}

function start(onToken?: (d: string, full: string) => void): Promise<ChatStreamResult> {
  let p!: Promise<ChatStreamResult>;
  act(() => {
    p = hook!.run({ message: "hola" }, onToken);
  });
  // Swallow here so an expected rejection is not "unhandled"; tests await `p`.
  p.catch(() => {});
  return p;
}

beforeEach(() => {
  hook = null;
  published = [];
  queued = new Map();
  nextFrameId = 1;
  wire = new Wire();
  vi.stubGlobal("fetch", vi.fn(async () => wire.response()));
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    const id = nextFrameId++;
    queued.set(id, () => cb(0));
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => {
    queued.delete(id);
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

// --------------------------------------------------------------------- tests

describe("decodeSSE knows the reset frame (W3-1)", () => {
  it("`event: reset` with `{}` decodes to {type: 'reset'}", () => {
    // Today: null — the default branch drops every unknown name.
    expect(decodeSSE("reset", "{}")).toEqual({ type: "reset" });
  });

  it("the daemon's existing reset shape ({reason}) decodes too", () => {
    // routes/chat.py already emits `reset` with {"reason": ...} on another
    // path; the reason is optional and never required.
    expect(decodeSSE("reset", JSON.stringify({ reason: "language" }))).toMatchObject({ type: "reset" });
  });
});

describe("the live store is cleared by reset, then fills with the new words", () => {
  it("publishes: wrong text -> '' -> the rewrite (never the two glued together)", async () => {
    render(<StoreOwner />);
    const fulls: string[] = [];
    const p = start((_d, full) => fulls.push(full));
    const unsub = watch();

    await send("token", { text: "Hola " });
    await frame();
    await send("token", { text: "amigo" });
    await frame();
    await send("reset", {});
    await frame();
    // The bubble is EMPTY between the reset and the first new token — the
    // accumulator too, which is what Stop / a dropped stream read.
    expect(hook!.textStore!.get()).toBe("");
    await send("token", { text: "Hello " });
    await send("token", { text: "friend" });
    await frame();

    // Today: ["Hola ", "Hola amigo", "Hola amigoHello friend"] — the reset is
    // decoded to null and ignored, so the rewrite is appended.
    expect(published.slice(0, 4)).toEqual(["Hola ", "Hola amigo", "", "Hello friend"]);
    expect(screen.getByTestId("live").textContent).toBe("Hello friend");
    // The TTS feed's running text restarts too (it reads `full`).
    expect(fulls.slice(-2)).toEqual(["Hello ", "Hello friend"]);

    await send("done", { reply: "Hello friend" });
    await act(async () => {
      wire.end();
      await pump();
    });
    expect((await p).reply).toBe("Hello friend");
    unsub();
  });

  it("with no done frame, the fallback reply is the rewrite, not both texts", async () => {
    render(<StoreOwner />);
    const p = start();
    await send("token", { text: "Hola" });
    await send("reset", {});
    await send("token", { text: "Hello" });
    await act(async () => {
      wire.end();
      await pump();
    });
    // Today: "HolaHello" — `acc` is never cleared.
    expect((await p).reply).toBe("Hello");
  });

  it("an error after a reset keeps only the rewrite as the partial", async () => {
    render(<StoreOwner />);
    const p = start();
    await send("token", { text: "Hola" });
    await send("reset", {});
    await send("token", { text: "Hel" });
    await send("error", { detail: "provider dropped" });
    const err = await p.then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(StreamError);
    // Today: "HolaHel".
    expect((err as StreamError).partial).toBe("Hel");
  });
});

describe("the state lane (textInState: true) is cleared by reset as well", () => {
  it("stream.text reads '' after reset, then only the new words", async () => {
    render(<StateOwner />);
    const p = start();
    await send("token", { text: "Hola" });
    await frame();
    expect(screen.getByTestId("state").textContent).toBe("Hola");
    await send("reset", {});
    await frame();
    // Today: still "Hola".
    expect(screen.getByTestId("state").textContent).toBe("");
    await send("token", { text: "Hello" });
    await frame();
    // Today: "HolaHello".
    expect(screen.getByTestId("state").textContent).toBe("Hello");
    await act(async () => {
      wire.end();
      await pump();
    });
    await p;
  });
});

describe("CONTROLS (already true; they prove the harness can see a difference)", () => {
  it("the final-answer nudge is ordinary tokens after the tool cards: they fill the empty bubble", async () => {
    render(<StoreOwner />);
    const p = start();
    const unsub = watch();
    await send("tool_call", { id: "c1", name: "write_document", status: "started" });
    await send("tool_call", { id: "c1", name: "write_document", status: "finished", ok: true });
    await send("token", { text: "Do" });
    await send("token", { text: "ne" });
    await send("token", { text: "." });
    await frame();
    expect(published).toEqual(["Done."]);
    await send("done", { reply: "Done." });
    await act(async () => {
      wire.end();
      await pump();
    });
    expect((await p).reply).toBe("Done.");
    unsub();
  });

  it("without a reset, consecutive tokens still accumulate (reset must not fire on its own)", async () => {
    render(<StoreOwner />);
    const p = start();
    const unsub = watch();
    await send("token", { text: "Hola " });
    await frame();
    await send("token", { text: "amigo" });
    await frame();
    expect(published).toEqual(["Hola ", "Hola amigo"]);
    await act(async () => {
      wire.end();
      await pump();
    });
    expect((await p).reply).toBe("Hola amigo");
    unsub();
  });

  it("tokens between frames publish ONCE per frame (the S-03 coalescing survives)", async () => {
    render(<StoreOwner />);
    const p = start();
    const unsub = watch();
    await send("token", { text: "a" });
    await send("token", { text: "b" });
    await send("token", { text: "c" });
    expect(published).toEqual([]);
    await frame();
    expect(published).toEqual(["abc"]);
    await act(async () => {
      wire.end();
      await pump();
    });
    await p;
    unsub();
  });
});

describe("run() tells its caller about a reset (review fix round)", () => {
  it("onReset fires once per reset frame, between the old tokens and the new", async () => {
    // The TTS feed rewinds on this signal: inferring a reset from a SHORTER
    // `full` misses a rewrite whose first token outgrows the discarded text.
    render(<StoreOwner />);
    const seen: string[] = [];
    let p!: Promise<ChatStreamResult>;
    act(() => {
      p = hook!.run(
        { message: "hola" },
        (_d, full) => seen.push(`token:${full}`),
        () => seen.push("reset"),
      );
    });
    p.catch(() => {});
    await send("token", { text: "Ho" });
    await send("reset", {});
    await send("token", { text: "Hello friend" });
    await act(async () => {
      wire.end();
      await pump();
    });
    await p;
    expect(seen).toEqual(["token:Ho", "reset", "token:Hello friend"]);
  });
});
