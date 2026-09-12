/**
 * S-02 (v1.257.0): the AGENT lane stops re-rendering its caller per token.
 *
 * Plain words: while an agent is typing an answer, the app used to redraw the
 * whole chat page for every single word. Now it redraws only the bubble the
 * words appear in, so the rest of the app stays smooth.
 *
 * THESE PINS MEASURE THE STORE, NOT REACT — learned the hard way. A first
 * version of this file emitted every token inside ONE `act()`, so React batched
 * the notifications into a single render and the assertion held whether or not
 * the hook coalesced anything. Worse, `stop()` sets state, which re-renders the
 * caller, and `useSyncExternalStore` re-reads `get()` on ANY re-render — so an
 * on-screen assertion about the final token passed even with the synchronous
 * flush deleted. Every mutation stayed green.
 *
 * So: each token is emitted in its OWN `act()`, and what is counted is
 * PUBLISHES to a subscriber of `textStore` — the exact thing the change
 * controls. The transport is a fake `EventSource` emitting the real frame shape
 * (`{ text }`, what `sseEventFrom` decodes) and `requestAnimationFrame` is a
 * queue this file flushes deliberately, so "once per frame" is asserted rather
 * than raced.
 */

import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Only `sseUrl` is stubbed; ApiError and friends stay real because
// useChatStream's StreamError extends ApiError at module load.
vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, sseUrl: (p: string) => `http://localhost/stub${p}` };
});

import { useLiveText } from "@/lib/useChatStream";
import { useRunStream, type UseRunStream } from "@/lib/useRunStream";

/** A stand-in for the browser's EventSource that lets a test push frames. */
class FakeEventSource {
  static last: FakeEventSource | null = null;
  private listeners = new Map<string, Set<(e: unknown) => void>>();
  closed = false;

  constructor(public url: string) {
    FakeEventSource.last = this;
  }

  addEventListener(name: string, cb: (e: unknown) => void): void {
    if (!this.listeners.has(name)) this.listeners.set(name, new Set());
    this.listeners.get(name)!.add(cb);
  }

  removeEventListener(name: string, cb: (e: unknown) => void): void {
    this.listeners.get(name)?.delete(cb);
  }

  close(): void {
    this.closed = true;
  }

  /** Deliver one frame in the daemon's own wire shape. */
  emit(name: string, data: unknown): void {
    for (const cb of [...(this.listeners.get(name) ?? [])]) {
      cb({ data: JSON.stringify(data) } as MessageEvent);
    }
  }
}

let queued = new Map<number, () => void>();
let nextFrameId = 1;

function flushFrames(): void {
  const entries = [...queued.entries()];
  queued.clear();
  for (const [, cb] of entries) cb();
}

let parentRenders = 0;
let hook: UseRunStream | null = null;

/** Stands in for <AgentLiveText>: the one component that SHOWS the text. */
function Child({ stream }: { stream: UseRunStream }) {
  const text = useLiveText(stream);
  return <div data-testid="live">{text}</div>;
}

/** Stands in for the chat page: it OWNS the stream but must not redraw per token. */
function Parent() {
  parentRenders += 1;
  const runStream = useRunStream({ textInState: false });
  hook = runStream;
  return <Child stream={runStream} />;
}

/** Publishes seen by a subscriber, and the value carried by the last one. */
let publishes = 0;
let lastPublished = "";

function watchStore(stream: UseRunStream): () => void {
  publishes = 0;
  lastPublished = "";
  return stream.textStore!.subscribe(() => {
    publishes += 1;
    lastPublished = stream.textStore!.get();
  });
}

/** Emit each token in its OWN act(), so React cannot batch several publishes
 *  into one render and hide the difference this change makes. */
function emitTokens(es: FakeEventSource, words: string[]): void {
  for (const word of words) {
    act(() => {
      es.emit("token", { text: word });
    });
  }
}

describe("agent tokens do not redraw the page", () => {
  beforeEach(() => {
    parentRenders = 0;
    hook = null;
    publishes = 0;
    lastPublished = "";
    queued = new Map();
    nextFrameId = 1;
    FakeEventSource.last = null;
    vi.stubGlobal("EventSource", FakeEventSource);
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

  it("five tokens publish once, not five times", () => {
    render(<Parent />);
    act(() => {
      hook!.start("s1");
    });
    const unsub = watchStore(hook!);
    const es = FakeEventSource.last!;

    emitTokens(es, ["a", "b", "c", "d", "e"]);

    // Nothing published yet — five tokens queued exactly one frame.
    expect(publishes).toBe(0);
    // The accumulator is still current between frames, which is what lets the
    // chat page's Stop button read a complete partial reply synchronously.
    expect(hook!.textStore!.get()).toBe("abcde");

    act(() => {
      flushFrames();
    });

    expect(publishes).toBe(1);
    expect(lastPublished).toBe("abcde");
    expect(screen.getByTestId("live").textContent).toBe("abcde");
    unsub();
  });

  it("a token does not re-render the component that owns the stream", () => {
    render(<Parent />);
    act(() => {
      hook!.start("s2");
    });
    const es = FakeEventSource.last!;
    const before = parentRenders;

    emitTokens(es, ["one", "two", "three"]);
    act(() => {
      flushFrames();
    });

    // THE POINT OF S-02: on the real page this component is 8,177 lines, and it
    // used to re-render once per token — with a synchronous scrollIntoView each
    // time, because the text was one of its scroll effect's dependencies.
    expect(parentRenders).toBe(before);
    expect(screen.getByTestId("live").textContent).toBe("onetwothree");
  });

  it("the final token still reaches subscribers when the run ends", () => {
    render(<Parent />);
    act(() => {
      hook!.start("s3");
    });
    const unsub = watchStore(hook!);
    const es = FakeEventSource.last!;

    act(() => {
      es.emit("token", { text: "final word" });
      es.emit("done", {});
    });

    // No flushFrames() on purpose: without a synchronous flush on `done` the
    // queued frame is cancelled by teardown and this publish never happens, so
    // the reply stops one word short of what the agent actually said.
    expect(publishes).toBeGreaterThan(0);
    expect(lastPublished).toBe("final word");
    unsub();
  });

  it("a dropped transport still publishes what arrived", () => {
    render(<Parent />);
    act(() => {
      hook!.start("s4");
    });
    const unsub = watchStore(hook!);
    const es = FakeEventSource.last!;

    act(() => {
      es.emit("token", { text: "half an answer" });
      es.emit("error", {});
    });

    expect(publishes).toBeGreaterThan(0);
    expect(lastPublished).toBe("half an answer");
    unsub();
  });

  it("a new run tells subscribers it reset", () => {
    render(<Parent />);
    act(() => {
      hook!.start("s5");
    });
    const es = FakeEventSource.last!;
    emitTokens(es, ["old run"]);
    act(() => {
      flushFrames();
    });
    expect(screen.getByTestId("live").textContent).toBe("old run");

    const unsub = watchStore(hook!);
    act(() => {
      hook!.start("s6");
    });

    // Without this notification a new run opens still showing the PREVIOUS
    // answer, until its first token happens to arrive.
    expect(publishes).toBeGreaterThan(0);
    expect(hook!.textStore!.get()).toBe("");
    expect(screen.getByTestId("live").textContent).toBe("");
    unsub();
  });
});
