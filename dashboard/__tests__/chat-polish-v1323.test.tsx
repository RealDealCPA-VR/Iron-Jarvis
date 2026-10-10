/**
 * v1.323.0 — chat polish (borrow list wave B), the client half:
 *
 *  - `thinking` frames decode, ride the same ref + once-a-frame store as the
 *    live text (`useLiveThinking`), reset per turn, never join the reply, and
 *    never count as committed work;
 *  - the done frame's `truncated`, the turn's client-clock `timing` and its
 *    tool `steps` (with durations) reach the result;
 *  - the approval decoder keeps `can_always` / `args_hash` (run() always read
 *    them; the whitelist dropped them);
 *  - `useTTS().readAloud` speaks on an explicit press even with voice off,
 *    strips markdown, and toggles by key;
 *  - per-conversation drafts (LRU 50, corrupt/blocked storage never throws);
 *  - follow-ups: trimmed request, defensive decode, never throws;
 *  - ThinkingDisclosure, FollowupChips, and the receipt's durations + speed
 *    line.
 *
 * The stream tests drive the REAL hook over a fake fetch body fed frame by
 * frame, one act() per frame, with `requestAnimationFrame` a queue this file
 * flushes on purpose (the v1.311.0 reset harness).
 */

import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, API_BASE: "http://localhost", ijToken: () => "", post: H.post };
});

import {
  decodeSSE,
  sseEventFrom,
  StreamError,
  upsertTool,
  useChatStream,
  useLiveThinking,
  useLiveText,
  type ChatStreamResult,
  type SSEEvent,
  type UseChatStream,
} from "@/lib/useChatStream";
import { speakableText, useTTS } from "@/lib/useTTS";
import { clearDraft, DRAFTS_KEY, MAX_DRAFTS, readDraft, writeDraft } from "@/lib/chatDrafts";
import { decodeFollowups, fetchFollowups, followupBody } from "@/lib/followups";
import { ThinkingDisclosure } from "@/components/chat/ThinkingDisclosure";
import { FollowupChips } from "@/components/chat/FollowupChips";
import { TurnReceipt, secondsText, speedLine } from "@/components/chat/TurnReceipt";

// ------------------------------------------------------------------ transport

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
let wires: Wire[] = [];
let queued = new Map<number, () => void>();
let nextFrameId = 1;
let clock = 1_000;

function flushFrames(): void {
  const entries = [...queued.entries()];
  queued.clear();
  for (const [, cb] of entries) cb();
}

async function pump(): Promise<void> {
  for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
}

async function send(event: string, data: unknown, at?: number): Promise<void> {
  if (at !== undefined) clock = at;
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

async function finish(at?: number): Promise<void> {
  if (at !== undefined) clock = at;
  await act(async () => {
    wire.end();
    await pump();
  });
}

let hook: UseChatStream | null = null;

function LiveThinking({ stream }: { stream: UseChatStream }) {
  return (
    <>
      <div data-testid="live-thinking">{useLiveThinking(stream)}</div>
      <div data-testid="live-text">{useLiveText(stream)}</div>
    </>
  );
}
function StoreOwner() {
  const s = useChatStream({ textInState: false });
  hook = s;
  return <LiveThinking stream={s} />;
}

function start(): Promise<ChatStreamResult> {
  let p!: Promise<ChatStreamResult>;
  act(() => {
    p = hook!.run({ message: "q" });
  });
  p.catch(() => {});
  return p;
}

beforeEach(() => {
  hook = null;
  queued = new Map();
  nextFrameId = 1;
  clock = 1_000;
  wires = [];
  vi.spyOn(Date, "now").mockImplementation(() => clock);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      wire = new Wire();
      wires.push(wire);
      return wire.response();
    }),
  );
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
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  H.post.mockReset();
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

// ------------------------------------------------------------------- decoding

describe("decode — thinking, truncated, can_always", () => {
  it("a thinking frame decodes to its text; junk text is an empty delta", () => {
    expect(decodeSSE("thinking", JSON.stringify({ text: "hmm, " }))).toEqual({
      type: "thinking",
      text: "hmm, ",
    });
    expect(sseEventFrom("thinking", { text: { a: 1 } })).toEqual({ type: "thinking", text: "" });
  });

  it("done carries truncated only for a real true", () => {
    const yes = decodeSSE("done", JSON.stringify({ reply: "r", truncated: true })) as Extract<
      SSEEvent,
      { type: "done" }
    >;
    expect(yes.truncated).toBe(true);
    for (const junk of [false, "yes", 1, null]) {
      const no = decodeSSE("done", JSON.stringify({ reply: "r", truncated: junk })) as Record<
        string,
        unknown
      >;
      expect("truncated" in no).toBe(false);
    }
  });

  it("the approval decoder keeps can_always and args_hash", () => {
    const ev = sseEventFrom("approval", {
      id: "a1",
      call_id: "c1",
      tool: "shell",
      can_always: true,
      args_hash: "h123",
    }) as Extract<SSEEvent, { type: "approval" }>;
    expect(ev.can_always).toBe(true);
    expect(ev.args_hash).toBe("h123");
    const plain = sseEventFrom("approval", { id: "a", call_id: "c", tool: "t", can_always: "yes" });
    expect(plain).toEqual({ type: "approval", id: "a", call_id: "c", tool: "t" });
  });
});

describe("upsertTool stamps the client clock", () => {
  const started = { type: "tool_call", id: "t1", name: "read_file", status: "started" } as const;
  const finished = { ...started, status: "finished", ok: true } as const;

  it("startedAt on start, endedAt on finish, and a repeated start never moves startedAt", () => {
    let cards = upsertTool([], started, 100);
    expect(cards[0]).toMatchObject({ status: "running", startedAt: 100 });
    cards = upsertTool(cards, started, 250);
    expect(cards[0].startedAt).toBe(100);
    cards = upsertTool(cards, finished, 400);
    expect(cards[0]).toMatchObject({ status: "done", ok: true, startedAt: 100, endedAt: 400 });
  });
});

// ----------------------------------------------------------------- the hook

describe("useChatStream — thinking, truncated, timing, steps", () => {
  it("resolves with thinking, truncated, timing and steps; the reply is the tokens only", async () => {
    render(<StoreOwner />);
    const p = start(); // t0 = 1000
    await send("thinking", { text: "Let me " }, 1_100);
    await send("thinking", { text: "think." }, 1_300);
    await send("token", { text: "Hi" }, 1_500);
    await send("tool_call", { id: "c1", name: "read_file", status: "started" }, 1_600);
    await send("tool_call", { id: "c1", name: "read_file", status: "finished", ok: true }, 1_900);
    await send("tool_call", { id: "c2", name: "shell", status: "started" }, 1_950);
    await send("tool_call", { id: "c2", name: "shell", status: "finished", ok: false }, 2_450);
    await send("token", { text: " there" }, 2_500);
    await send("done", { reply: "Hi there", truncated: true }, 2_600);
    await finish(2_700);
    const res = await p;
    expect(res.reply).toBe("Hi there");
    expect(res.thinking).toBe("Let me think.");
    expect(res.thinkingMs).toBe(400); // first thinking 1100 → first other frame 1500
    expect(res.truncated).toBe(true);
    expect(res.timing).toEqual({ startedAt: 1_000, firstTokenAt: 1_500, endedAt: 2_700 });
    expect(res.steps).toEqual([
      { name: "read_file", ok: true, ms: 300 },
      { name: "shell", ok: false, ms: 500 },
    ]);
  });

  it("no done frame: truncated is false, timing and steps still come back", async () => {
    render(<StoreOwner />);
    const p = start();
    await send("tool_call", { id: "c1", name: "web_search", status: "finished", ok: true }, 1_200);
    await finish(1_400);
    const res = await p;
    expect(res.truncated).toBe(false);
    expect(res.thinking).toBe("");
    expect(res.thinkingMs).toBeNull();
    expect(res.timing).toEqual({ startedAt: 1_000, firstTokenAt: null, endedAt: 1_400 });
    // A finish with no start seen: the step is there, its duration unknown.
    expect(res.steps).toEqual([{ name: "web_search", ok: true, ms: null }]);
  });

  it("useLiveThinking publishes once a frame, never into the reply, and resets per turn", async () => {
    render(<StoreOwner />);
    const p = start();
    const published: string[] = [];
    const unsub = hook!.thinkingStore!.subscribe(() => published.push(hook!.thinkingStore!.get()));
    await send("thinking", { text: "ponder" });
    await send("thinking", { text: "ing" });
    // The ref is current at once; subscribers hear it on the frame — ONE
    // publish for both deltas (counted at the store, not on screen: any
    // re-render reads the ref, so the screen would measure React, not us).
    expect(hook!.thinkingStore!.get()).toBe("pondering");
    expect(published).toEqual([]);
    await frame();
    expect(published).toEqual(["pondering"]);
    unsub();
    expect(screen.getByTestId("live-thinking").textContent).toBe("pondering");
    expect(screen.getByTestId("live-text").textContent).toBe("");
    await send("token", { text: "Answer" });
    await frame();
    expect(screen.getByTestId("live-text").textContent).toBe("Answer");
    await send("done", { reply: "Answer" });
    await finish();
    await p;
    expect(screen.getByTestId("live-thinking").textContent).toBe("pondering");

    // The next turn starts with no thinking — a store reader hears the reset
    // at once, not at the turn's first flush.
    const heard: string[] = [];
    const unsub2 = hook!.thinkingStore!.subscribe(() => heard.push(hook!.thinkingStore!.get()));
    const p2 = start();
    expect(heard).toEqual([""]);
    unsub2();
    await act(async () => {
      await pump();
    });
    expect(screen.getByTestId("live-thinking").textContent).toBe("");
    await send("done", { reply: "ok" });
    await finish();
    expect((await p2).thinking).toBe("");
  });

  it("a turn that only thought and then failed is NOT committed (a retry replays nothing)", async () => {
    render(<StoreOwner />);
    const p = start();
    await send("thinking", { text: "hmm" });
    await send("error", { detail: "provider dropped" });
    const err = await p.then(
      () => null,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(StreamError);
    expect((err as StreamError).committed).toBe(false);
  });

  it("useLiveThinking falls back for a hand-built stream with no store", () => {
    const { result: a } = renderHook(() => useLiveThinking({ thinking: "plain" }));
    expect(a.current).toBe("plain");
    const { result: b } = renderHook(() => useLiveThinking({}));
    expect(b.current).toBe("");
  });
});

// ------------------------------------------------------------------ readAloud

class FakeUtterance {
  text: string;
  onstart: (() => void) | null = null;
  onend: (() => void) | null = null;
  onerror: (() => void) | null = null;
  voice: unknown = null;
  rate = 1;
  pitch = 1;
  volume = 1;
  constructor(text: string) {
    this.text = text;
  }
}

function fakeSynth() {
  const spoken: FakeUtterance[] = [];
  const s = {
    spoken,
    pending: false,
    speak: vi.fn((u: FakeUtterance) => {
      spoken.push(u);
    }),
    cancel: vi.fn(),
    getVoices: () => [],
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  };
  vi.stubGlobal("SpeechSynthesisUtterance", FakeUtterance);
  vi.stubGlobal("speechSynthesis", s);
  return s;
}

describe("useTTS.readAloud — an explicit press", () => {
  it("speakableText strips markdown line by line", () => {
    expect(speakableText("# Title\n\n**Bold** and `code`.\n- one\n- [two](http://x)")).toBe(
      "Title\nBold and code.\none\ntwo",
    );
  });

  it("speaks with voice OFF, plain words, cancelling first; readingKey is the key", () => {
    const s = fakeSynth();
    const { result } = renderHook(() => useTTS());
    expect(result.current.enabled).toBe(false);
    act(() => result.current.readAloud("**Hello** there.\n- item one", "m1"));
    expect(s.cancel).toHaveBeenCalled();
    expect(s.spoken.map((u) => u.text)).toEqual(["Hello there.", "item one"]);
    expect(result.current.readingKey).toBe("m1");
    // speak() is unchanged: still a no-op while voice is off.
    act(() => result.current.speak("Auto reply."));
    expect(s.spoken).toHaveLength(2);
  });

  it("the same key again stops it (toggle); cancel() clears the key", () => {
    const s = fakeSynth();
    const { result } = renderHook(() => useTTS());
    act(() => result.current.readAloud("One.", "m1"));
    const before = s.spoken.length;
    act(() => result.current.readAloud("One.", "m1"));
    expect(result.current.readingKey).toBeNull();
    expect(s.spoken).toHaveLength(before);
    act(() => result.current.readAloud("Two.", "m2"));
    expect(result.current.readingKey).toBe("m2");
    act(() => result.current.cancel());
    expect(result.current.readingKey).toBeNull();
  });

  it("the key clears when the last sentence ends; a replaced read's late end does not clear the new key", () => {
    const s = fakeSynth();
    const { result } = renderHook(() => useTTS());
    act(() => result.current.readAloud("First. Second.", "m1"));
    const oldLast = s.spoken[s.spoken.length - 1];
    act(() => result.current.readAloud("Other.", "m2"));
    expect(result.current.readingKey).toBe("m2");
    act(() => oldLast.onend?.()); // the retired read's callback, late
    expect(result.current.readingKey).toBe("m2");
    act(() => s.spoken[s.spoken.length - 1].onend?.());
    expect(result.current.readingKey).toBeNull();
  });

  it("nothing speakable: no cancel of what is playing, no key", () => {
    const s = fakeSynth();
    const { result } = renderHook(() => useTTS());
    s.cancel.mockClear();
    act(() => result.current.readAloud("  \n```\n```\n ", "m1"));
    expect(s.spoken).toHaveLength(0);
    expect(result.current.readingKey).toBeNull();
  });
});

// --------------------------------------------------------------------- drafts

describe("chatDrafts", () => {
  it("round-trips per key; whitespace deletes; clearDraft forgets", () => {
    writeDraft("c1", "half a thought");
    writeDraft("c2", "another");
    expect(readDraft("c1")).toBe("half a thought");
    expect(readDraft("c2")).toBe("another");
    writeDraft("c1", "   ");
    expect(readDraft("c1")).toBe("");
    clearDraft("c2");
    expect(readDraft("c2")).toBe("");
    expect(window.localStorage.getItem(DRAFTS_KEY)).toBeNull();
  });

  it("keeps the 50 most recently written drafts", () => {
    for (let i = 0; i < MAX_DRAFTS; i++) writeDraft(`k${i}`, `t${i}`);
    writeDraft("k0", "t0 again"); // k0 is now the newest; k1 the oldest
    writeDraft("k-new", "fresh");
    const stored = JSON.parse(window.localStorage.getItem(DRAFTS_KEY)!) as Record<string, unknown>;
    expect(Object.keys(stored)).toHaveLength(MAX_DRAFTS);
    expect(readDraft("k1")).toBe("");
    expect(readDraft("k0")).toBe("t0 again");
    expect(readDraft("k-new")).toBe("fresh");
    expect(readDraft("k2")).toBe("t2");
  });

  it("corrupt storage reads as no drafts and is overwritten cleanly", () => {
    window.localStorage.setItem(DRAFTS_KEY, "{not json");
    expect(readDraft("c1")).toBe("");
    window.localStorage.setItem(DRAFTS_KEY, JSON.stringify({ c1: { text: 7 }, c2: "x", c3: { text: "ok", at: 1 } }));
    expect(readDraft("c1")).toBe("");
    expect(readDraft("c3")).toBe("ok");
    window.localStorage.setItem(DRAFTS_KEY, "[1,2]");
    writeDraft("c4", "kept");
    expect(readDraft("c4")).toBe("kept");
  });

  it("blocked storage never throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceeded");
    });
    expect(() => writeDraft("c1", "x")).not.toThrow();
    expect(readDraft("c1")).toBe("");
    expect(() => clearDraft("c1")).not.toThrow();
  });
});

// ------------------------------------------------------------------ followups

describe("followups", () => {
  const msgs = Array.from({ length: 8 }, (_, i) => ({
    role: i % 2 ? "assistant" : "user",
    content: i === 7 ? "x".repeat(5_000) : `m${i}`,
  }));

  it("sends only the last six messages, each cut to 4000 chars, with provider/model", async () => {
    H.post.mockResolvedValue({ suggestions: ["a"], reason: "" });
    await fetchFollowups(msgs, "claude-cli", "fable");
    expect(H.post).toHaveBeenCalledTimes(1);
    const [path, body] = H.post.mock.calls[0] as [string, ReturnType<typeof followupBody>];
    expect(path).toBe("/chat/followups");
    expect(body.messages.map((m) => m.content.slice(0, 2))).toEqual(["m2", "m3", "m4", "m5", "m6", "xx"]);
    expect(body.messages[5].content).toHaveLength(4_000);
    expect(body.provider).toBe("claude-cli");
    expect(body.model).toBe("fable");
  });

  it("decodes defensively: strings only, trimmed, deduped, at most three", async () => {
    H.post.mockResolvedValue({ suggestions: [1, "  a ", "", "a", null, "b", "c", "d"], reason: "ok" });
    expect(await fetchFollowups(msgs)).toEqual(["a", "b", "c"]);
    expect(decodeFollowups(null)).toEqual([]);
    expect(decodeFollowups({ suggestions: "a" })).toEqual([]);
  });

  it("never throws: a rejected post, junk body or nothing to send is []", async () => {
    H.post.mockRejectedValue(new Error("404 Not Found"));
    await expect(fetchFollowups(msgs)).resolves.toEqual([]);
    H.post.mockResolvedValue("junk");
    await expect(fetchFollowups(msgs)).resolves.toEqual([]);
    H.post.mockClear();
    await expect(fetchFollowups([{ role: "user", content: "  " }])).resolves.toEqual([]);
    expect(H.post).not.toHaveBeenCalled();
    await expect(fetchFollowups(null as unknown as [])).resolves.toEqual([]);
  });
});

// ----------------------------------------------------------- the new pieces

describe("ThinkingDisclosure", () => {
  it("renders nothing for empty text", () => {
    const { container } = render(<ThinkingDisclosure text="  " live />);
    expect(container.innerHTML).toBe("");
  });

  it("collapsed by default; live says Thinking…, done says Thought for N s / Thoughts", () => {
    const { rerender } = render(<ThinkingDisclosure text="**raw** idea" live />);
    const btn = screen.getByRole("button");
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByTestId("thinking-summary").textContent).toBe("Thinking…");
    expect(screen.queryByTestId("thinking-text")).toBeNull();
    rerender(<ThinkingDisclosure text="**raw** idea" seconds={3.4} />);
    expect(screen.getByTestId("thinking-summary").textContent).toBe("Thought for 3 s");
    rerender(<ThinkingDisclosure text="**raw** idea" seconds={null} />);
    expect(screen.getByTestId("thinking-summary").textContent).toBe("Thoughts");
  });

  it("expands to the raw text — plain, not markdown", () => {
    render(<ThinkingDisclosure text="**raw** idea" seconds={2} />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("thinking-text").textContent).toBe("**raw** idea");
    expect(screen.getByTestId("thinking-text").querySelector("strong")).toBeNull();
  });
});

describe("FollowupChips", () => {
  it("nothing when empty", () => {
    const { container } = render(<FollowupChips suggestions={[]} onPick={() => {}} />);
    expect(container.innerHTML).toBe("");
  });

  it("at most three pills; a press hands the text over (suggest, never send)", () => {
    const onPick = vi.fn();
    render(<FollowupChips suggestions={["One?", "Two?", "Three?", "Four?"]} onPick={onPick} />);
    const chips = screen.getAllByTestId("followup-chip");
    expect(chips.map((c) => c.textContent)).toEqual(["One?", "Two?", "Three?"]);
    fireEvent.click(chips[1]);
    expect(onPick).toHaveBeenCalledWith("Two?");
  });

  it("disabled pills do nothing", () => {
    const onPick = vi.fn();
    render(<FollowupChips suggestions={["One?"]} onPick={onPick} disabled />);
    fireEvent.click(screen.getByTestId("followup-chip"));
    expect(onPick).not.toHaveBeenCalled();
  });
});

describe("TurnReceipt — step durations and the speed line", () => {
  const expand = () => fireEvent.click(screen.getByRole("button", { expanded: false }));

  it("secondsText / speedLine words", () => {
    expect(secondsText(300)).toBe("0.3 s");
    expect(secondsText(12_400)).toBe("12 s");
    expect(secondsText(125_000)).toBe("2 min 5 s");
    expect(secondsText(-1)).toBeNull();
    const timing = { startedAt: 1_000, firstTokenAt: 2_200, endedAt: 4_200 };
    expect(speedLine(timing, 84)).toBe("First word after 1.2 s · 42 tokens/s");
    expect(speedLine(timing)).toBe("First word after 1.2 s");
    expect(speedLine({ ...timing, firstTokenAt: null }, 84)).toBeNull();
    expect(speedLine(null, 84)).toBeNull();
  });

  it("expanded chips carry durations and mark a failed step", () => {
    render(
      <TurnReceipt
        toolsUsed={["read_file", "shell"]}
        steps={[
          { name: "read_file", ok: true, ms: 300 },
          { name: "shell", ok: false, ms: 1_500 },
        ]}
      />,
    );
    expand();
    const chips = screen.getAllByTestId("turn-step");
    // v1.329.0 (G2): the work line's words; a step with no saved target keeps
    // the tool id as a quiet hint, exactly as the work line says it.
    expect(chips.map((c) => c.textContent)).toEqual(["Read · read_file · 0.3 s", "Ran · shell · 1.5 s · failed"]);
    expect(chips[1].getAttribute("data-ok")).toBe("false");
  });

  it("without steps the chips are names only (today's view)", () => {
    render(<TurnReceipt toolsUsed={["read_file"]} />);
    expand();
    expect(screen.queryByTestId("turn-step")).toBeNull();
    expect(screen.getByText("read_file")).toBeTruthy();
  });

  it("the speed line shows with timing, tokens/s from outputTokens or usage", () => {
    const timing = { startedAt: 1_000, firstTokenAt: 2_200, endedAt: 4_200 };
    const { unmount } = render(
      <TurnReceipt toolsUsed={["x"]} timing={timing} outputTokens={84} />,
    );
    expand();
    expect(screen.getByTestId("turn-speed").textContent).toBe("First word after 1.2 s · 42 tokens/s");
    unmount();
    render(<TurnReceipt toolsUsed={["x"]} timing={timing} usage={{ output_tokens: 20 }} />);
    expand();
    expect(screen.getByTestId("turn-speed").textContent).toBe("First word after 1.2 s · 10 tokens/s");
  });

  it("no timing, no speed line; timing alone is not a receipt", () => {
    const { unmount } = render(<TurnReceipt toolsUsed={["x"]} />);
    expand();
    expect(screen.queryByTestId("turn-speed")).toBeNull();
    unmount();
    const { container } = render(
      <TurnReceipt timing={{ startedAt: 1, firstTokenAt: 2, endedAt: 3 }} outputTokens={5} />,
    );
    expect(container.innerHTML).toBe("");
  });
});
