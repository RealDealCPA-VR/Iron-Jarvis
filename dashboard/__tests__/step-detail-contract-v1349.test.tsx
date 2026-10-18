/**
 * v1.349.0 integrate — the step-detail CONTRACT, dashboard half.
 *
 * `__tests__/fixtures/step-detail-frames-v1349.json` holds the tool_call
 * frames the REAL daemon sends (tests/test_step_detail_contract_v1349.py
 * drives the real /chat/stream lane and the real agent runtime and fails when
 * they stop matching it). Here those exact frames go through:
 *
 *   1. the REAL `useChatStream` (the stream lane's own wire text, byte for
 *      byte, cut mid-frame) into the steps the chat page saves, a JSON round
 *      trip (the thread file), and the REAL `WorkLine` of a reopened chat —
 *      whose step row opens to what the step did;
 *   2. the REAL `useRunStream` (an EventSource carrying the runtime's frames)
 *      into the REAL helper-agent row (`AgentRunLive`, lane B2), whose live
 *      tool row (lane B1h's `LiveToolRows`) opens to the same detail.
 *
 * Nothing secret, fenced or raw from the frame's old `output` may reach a
 * saved step, a visible line or a hover.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, API_BASE: "http://localhost", ijToken: () => "" };
});

import { useChatStream, type ChatStreamResult, type TurnStep, type UseChatStream } from "@/lib/useChatStream";
import { useRunStream, type UseRunStream } from "@/lib/useRunStream";
import { cutCodePoints, cleanStepDetail } from "@/lib/stepDetail";
import { WorkLine } from "@/components/chat/WorkLine";
import { AgentRunLive } from "@/components/chat/AgentRunRow";

type Frame = Record<string, unknown> & { detail: { input: string; output: string }; output: string };
interface Fixture {
  chat_stream: { started: Frame; finished: Frame; wire: string };
  agent_run: { started: Frame; finished: Frame };
}

const FIX = JSON.parse(
  readFileSync(path.join(__dirname, "fixtures", "step-detail-frames-v1349.json"), "utf8").replace(/\r\n/g, "\n"),
) as Fixture;

/* What must never be saved or shown: the frame's raw output carries all of
 * these (the Python half pins that), the detail carries none. */
const RAW = ["sk-ant-api03-CONTRACTFAKEFAKEFAKE12", "4111111111111111", "abc123", "UNTRUSTED CONTENT", "END UNTRUSTED"];

/** Every piece of text a user could see or hover in a subtree. */
function visibleAndHover(root: Element): string {
  const titles = [...root.querySelectorAll("[title]")].map((e) => e.getAttribute("title") ?? "");
  return `${root.textContent ?? ""}\n${titles.join("\n")}`;
}

/** The opened detail of a row says what the fixture's detail says. */
function expectOpenedDetail(scope: HTMLElement) {
  const detail = within(scope).getByTestId("step-detail");
  const input = within(detail).getByRole("group", { name: "Input" });
  // The redacted arguments, as `key: value` lines (the daemon sorts keys).
  expect(input.textContent).toBe("card: [card hidden]q: pier 9 hours");
  // The output: the first 12 lines, then "Show all".
  const out = within(detail).getByTestId("step-detail-output");
  expect(out.textContent!.split("\n")).toEqual([
    "Pier 9 opens at 7am",
    "key sk-ant-***",
    "Authorization: Bearer ***",
    ...Array.from({ length: 9 }, (_, i) => `row ${i + 1}`),
  ]);
  fireEvent.click(within(detail).getByTestId("step-detail-more"));
  expect(within(detail).getByTestId("step-detail-output").textContent).toBe(FIX.chat_stream.finished.detail.output);
  for (const raw of RAW) expect(visibleAndHover(scope)).not.toContain(raw);
}

/* ------------------------------------------- the fixture is the daemon's shape */

describe("the contract fixture", () => {
  it("is what the daemon sends: a finished frame whose raw output is secret and fenced, and whose detail is not", () => {
    for (const lane of [FIX.chat_stream, FIX.agent_run]) {
      expect(lane.started.status).toBe("started");
      expect(lane.finished.status).toBe("finished");
      expect(Object.keys(lane.finished.detail).sort()).toEqual(["input", "output"]);
      expect(RAW.filter((r) => lane.finished.output.includes(r)).length).toBeGreaterThanOrEqual(3);
      for (const raw of RAW) expect(JSON.stringify(lane.finished.detail)).not.toContain(raw);
      // The dashboard's own reader changes nothing in a detail the daemon masked.
      expect(cleanStepDetail(lane.finished.detail)).toEqual(lane.finished.detail);
    }
    expect(FIX.chat_stream.wire).toContain("event: tool_call\ndata: ");
  });
});

/* ------------------------------------------------ 1. the chat stream lane */

class Wire {
  private chunks: Uint8Array[] = [];
  private waiting: ((r: { value?: Uint8Array; done: boolean }) => void) | null = null;
  private ended = false;
  private enc = new TextEncoder();
  raw(text: string): void {
    this.deliver(this.enc.encode(text));
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
let clock = 1_000;
let chat: UseChatStream | null = null;

async function pump(): Promise<void> {
  for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
}
async function deliver(text: string, at: number): Promise<void> {
  clock = at;
  await act(async () => {
    wire.raw(text);
    await pump();
  });
}
function ChatOwner() {
  chat = useChatStream({ textInState: false });
  return null;
}

beforeEach(() => {
  chat = null;
  clock = 1_000;
  vi.spyOn(Date, "now").mockImplementation(() => clock);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      wire = new Wire();
      return wire.response();
    }),
  );
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    cb(0);
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("the chat stream: the daemon's frame becomes a saved step that opens after a reload", () => {
  it("the stream lane's own wire text, through the real hook, the thread file and the real work line", async () => {
    render(<ChatOwner />);
    let p!: Promise<ChatStreamResult>;
    act(() => {
      p = chat!.run({ message: "pier 9 hours" });
    });
    p.catch(() => {});
    // The daemon's bytes exactly, cut in the middle of the finished frame (a
    // network read ends anywhere).
    const w = FIX.chat_stream.wire;
    const cut = w.indexOf('"detail"') + 5;
    await deliver(w.slice(0, cut), 1_100);
    await deliver(w.slice(cut), 1_400);
    await deliver(`event: done\ndata: ${JSON.stringify({ reply: "Pier 9 opens at 7am." })}\n\n`, 1_500);
    await act(async () => {
      wire.end();
      await pump();
    });
    const res = await p;
    expect(res.steps).toEqual([
      { name: "contract_lookup_fake", ok: true, ms: 300, detail: FIX.chat_stream.finished.detail },
    ]);
    // What the page writes into the thread file (it saves `steps` verbatim)
    // and what a reopened chat reads back.
    const saved = JSON.parse(JSON.stringify(res.steps)) as TurnStep[];
    for (const raw of RAW) expect(JSON.stringify(saved)).not.toContain(raw);

    render(<WorkLine steps={saved} />);
    const line = screen.getByTestId("work-line");
    fireEvent.click(within(line).getAllByRole("button")[0]);
    const [row] = within(screen.getByTestId("work-rows")).getAllByTestId("work-row");
    expect(row.tagName).toBe("BUTTON");
    expect(row.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(row);
    expect(row.getAttribute("aria-expanded")).toBe("true");
    expect(row.getAttribute("aria-controls")).toBe(screen.getByTestId("step-detail").id);
    expectOpenedDetail(line);
  });
});

/* ------------------------------------- 2. the agent run's session stream */

class FakeEventSource {
  static last: FakeEventSource | null = null;
  readonly url: string;
  private listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
  constructor(url: string) {
    this.url = url;
    FakeEventSource.last = this;
  }
  addEventListener(type: string, cb: (e: MessageEvent) => void): void {
    (this.listeners[type] ??= []).push(cb);
  }
  close(): void {}
  emit(type: string, data: unknown): void {
    for (const cb of this.listeners[type] ?? []) cb({ data: JSON.stringify(data) } as MessageEvent);
  }
}

let run: UseRunStream | null = null;

function RunOwner() {
  run = useRunStream({ textInState: false });
  return (
    <AgentRunLive
      session="s1"
      handOff={{ escalated: "needs the agent", escalatedTo: "the researcher", awaitingSession: "s1" }}
      since={null}
      progress={[]}
      tools={run.tools}
      phaseTitle="Working the task"
    />
  );
}

describe("an agent run: the runtime's frame opens in the helper-agent row's live steps", () => {
  it("the real run stream and the real row: the finished step is a disclosure to the same masked detail", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    render(<RunOwner />);
    act(() => run!.start("s1"));
    expect(FakeEventSource.last!.url).toContain("/sessions/s1/stream");
    act(() => FakeEventSource.last!.emit("tool_call", FIX.agent_run.started));
    const rowEl = screen.getByTestId("agent-run-row");
    // Running: a plain row (nothing to open yet).
    const running = within(rowEl).getAllByTestId("work-row").find((r) => r.getAttribute("data-state") === "running")!;
    expect(running.tagName).toBe("DIV");
    act(() => FakeEventSource.last!.emit("tool_call", FIX.agent_run.finished));
    const done = within(rowEl).getAllByTestId("work-row").find((r) => r.getAttribute("data-state") === "done")!;
    expect(done.tagName).toBe("BUTTON");
    // Its one-line preview is the masked, unfenced output, and it hovers no raw text.
    expect(done.textContent).toContain("Pier 9 opens at 7am");
    expect(done.getAttribute("title")).toBeNull();
    fireEvent.click(done);
    expect(done.getAttribute("aria-expanded")).toBe("true");
    expectOpenedDetail(rowEl);
  });
});

/* ------------------------------------------------ the cut counts characters */

describe("the dashboard re-cut counts characters the daemon's way", () => {
  it("an emoji is one character, and a cut never ends on half of one", () => {
    const emoji = "\u{1F600}";
    expect(cutCodePoints(emoji.repeat(4), 3)).toBe(emoji.repeat(3));
    const out = cleanStepDetail({ input: "", output: emoji.repeat(1_600) })!.output;
    // The daemon keeps 1,500 CHARACTERS (Python counts code points).
    expect([...out]).toHaveLength(1_500);
    expect(out).toBe(emoji.repeat(1_500));
    // Mixed text: never a lone surrogate at the end.
    const mixed = cutCodePoints(`a${emoji}b${emoji}`, 2);
    expect(mixed).toBe(`a${emoji}`);
    expect(cutCodePoints("plain", 10)).toBe("plain");
  });
});
