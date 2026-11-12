/**
 * The "working" sign of a Build › Code session (the owner: the one plain line
 * "5 helpers are still working in the background…" was too easy to miss; they
 * asked for a small, calm visual in its place).
 *
 * A small ring just above the message box:
 *   - a reply being written: the plain ring ("Claude Code is working…");
 *   - helpers at work after the reply ended: one segment per helper, the
 *     count in the middle, "5 helpers working"; a helper seen to end leaves a
 *     finished segment and the words count it ("2 finished");
 *   - the tool's own reply: the plain ring again;
 *   - background commands alone: a STILL ring and the quiet sentence.
 *
 * What these tests hold:
 *   - when it shows and what it says, in each state;
 *   - the sentence a screen reader hears is the one the view always said
 *     (the older `code-background` tests still read it, unchanged);
 *   - its slot is always in the conversation, so nothing moves when the sign
 *     comes or goes;
 *   - it stands still when the system asks for less motion and while the
 *     window is hidden, and it moves in steps, never smoothly (a session can
 *     work for hours; a smooth turn is a new frame at every screen refresh).
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  view: null as null | (() => Record<string, unknown>),
  /** The newest registration of each interval, by its period. */
  intervals: new Map<number, { fn: () => void; enabled: boolean }>(),
}));

vi.mock("@/lib/useVisibleInterval", async () => {
  const React = await import("react");
  return {
    useVisibleInterval: (fn: () => void, ms: number, enabled = true) => {
      const last = React.useRef<number | null>(null);
      if (last.current !== null && last.current !== ms) H.intervals.delete(last.current);
      last.current = ms;
      H.intervals.set(ms, { fn, enabled: enabled && ms > 0 });
    },
  };
});

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: FakeApiError,
    get: (path: string) => {
      if (path === "/build/app/harnesses") return Promise.resolve(TOOLS);
      if (path === "/build/app/sessions") return Promise.resolve({ sessions: [sess()] });
      if (path === "/editor/options") return Promise.resolve({ editors: [], preferred: "" });
      if (path === "/build/app/sessions/a1" && H.view) return Promise.resolve(H.view());
      return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
    },
    post: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* () {},
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => null }));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { WorkingSign } from "@/components/buildCode/WorkingSign";
import { CODE_BACKGROUND_REREAD_MS, backgroundWorkWords } from "@/lib/buildApp";
import {
  BEAT_BACKGROUND_MS,
  BEAT_REPLY_MS,
  SOLO_STEPS,
  RING_DONE_WIDTH,
  RING_LIVE_WIDTH,
  RING_RADIUS,
  RING_SLOTS_MAX,
  noteHelpers,
  ringSegment,
  ringSlots,
  signWords,
  workingSignOf,
} from "@/lib/workingSign";

const TOOLS = {
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      version: "2.1.316",
      modes: [{ id: "ask", label: "Ask first", dangerous: false }],
      hidden_modes: [],
      models: [{ id: "", label: "Claude Code's default" }],
      efforts: [],
      accounts: [],
      commands: [],
    },
  ],
};

function sess(over: Record<string, unknown> = {}) {
  return {
    id: "a1",
    harness: "claude-code",
    folder: "C:\\work\\shop",
    title: "Teams",
    account: "",
    model: "",
    effort: "",
    mode: "ask",
    harness_session_id: "h1",
    created_at: "2026-10-31T09:00:00Z",
    updated_at: "2026-10-31T09:00:00Z",
    archived_at: null,
    last_error: "",
    running: false,
    process: true,
    waiting: false,
    background: 0,
    background_helpers: 0,
    self_reply: false,
    ...over,
  };
}

const MINE = [
  { type: "user", text: "build it with teams", turn: "t1" },
  { type: "text_done", item: "m1:0", text: "Five teams are on it. I'll report as teams finish.", turn: "t1" },
  { type: "done", stop: "completed", turn: "t1" },
];

function serve(over: Record<string, unknown>) {
  H.view = () => ({ session: sess(over), events: MINE, running: null, turn_id: null, pending: [] });
}

async function tick(ms: number): Promise<boolean> {
  const it = H.intervals.get(ms);
  if (!it || !it.enabled) return false;
  await act(async () => {
    it.fn();
  });
  return true;
}

/** The background facts of a session, as the sign reads them. */
const facts = (background: number, backgroundHelpers: number, selfReply = false) => ({
  background,
  backgroundHelpers,
  selfReply,
});

/** Make the system say (or not say) "reduce motion". */
function systemReducesMotion(on: boolean) {
  (window as unknown as { matchMedia: unknown }).matchMedia = (query: string) => ({
    matches: on && query.includes("prefers-reduced-motion"),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  });
}

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

beforeEach(() => {
  H.view = null;
  H.intervals = new Map();
});

afterEach(() => {
  cleanup();
  delete (window as unknown as { matchMedia?: unknown }).matchMedia;
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
});

/* ------------------------------------------------------------- the logic */

describe("counting the helpers seen to end", () => {
  it("a drop between two reads is that many finished; a rise is new helpers; none left starts again", () => {
    let seen = noteHelpers(null, 5);
    expect(seen).toEqual({ working: 5, finished: 0 });
    seen = noteHelpers(seen, 4);
    expect(seen).toEqual({ working: 4, finished: 1 });
    // One more started: nothing un-finishes.
    seen = noteHelpers(seen, 5);
    expect(seen).toEqual({ working: 5, finished: 1 });
    seen = noteHelpers(seen, 2);
    expect(seen).toEqual({ working: 2, finished: 4 });
    // The same read again changes nothing.
    expect(noteHelpers(seen, 2)).toEqual({ working: 2, finished: 4 });
    // No helper left: the stretch is over, the next one counts from nothing.
    expect(noteHelpers(seen, 0)).toBeNull();
    expect(noteHelpers(null, 3)).toEqual({ working: 3, finished: 0 });
  });

  it("reads a count that is not a whole, positive number as none", () => {
    expect(noteHelpers(null, Number.NaN)).toBeNull();
    expect(noteHelpers(null, -2)).toBeNull();
    expect(noteHelpers({ working: 3, finished: 1 }, 2.9)).toEqual({ working: 2, finished: 2 });
  });
});

describe("what the sign shows", () => {
  it("a reply being written; never while a card waits on the user", () => {
    expect(workingSignOf(facts(0, 0), true, false, 0)).toEqual({ kind: "reply" });
    expect(workingSignOf(facts(2, 2), true, false, 0)).toEqual({ kind: "reply" });
    expect(workingSignOf(facts(0, 0), true, true, 0)).toBeNull();
  });

  it("helpers first, then the tool's own reply, then commands, as the sentence does", () => {
    expect(workingSignOf(facts(3, 3), false, false, 2)).toEqual({ kind: "helpers", working: 3, finished: 2 });
    // A helper's card can wait while no reply runs: the helpers still show.
    expect(workingSignOf(facts(3, 3), false, true, 0)).toEqual({ kind: "helpers", working: 3, finished: 0 });
    expect(workingSignOf(facts(3, 3, true), false, false, 0)?.kind).toBe("helpers");
    expect(workingSignOf(facts(0, 0, true), false, false, 0)).toEqual({ kind: "own" });
    expect(workingSignOf(facts(2, 0), false, false, 0)).toEqual({ kind: "commands", count: 2 });
    expect(workingSignOf(facts(0, 0), false, false, 0)).toBeNull();
    expect(workingSignOf(null, true, false, 0)).toBeNull();
    // The tool's own reply comes before a command still running beside it.
    expect(workingSignOf(facts(2, 0, true), false, false, 0)).toEqual({ kind: "own" });
    // The sign and the sentence always speak of the same thing, and the sign
    // shows exactly when the sentence has something to say.
    const about = (sentence: string) =>
      sentence === ""
        ? null
        : sentence.includes("helper")
          ? "helpers"
          : sentence.includes("reply of its own")
            ? "own"
            : "commands";
    for (const f of [
      facts(0, 0),
      facts(1, 1),
      facts(4, 4),
      facts(6, 4),
      facts(4, 4, true),
      facts(0, 0, true),
      facts(2, 0, true),
      facts(1, 0),
      facts(3, 0),
    ]) {
      expect(workingSignOf(f, false, false, 0)?.kind ?? null).toBe(about(backgroundWorkWords(f, "Claude Code", false)));
    }
  });

  it("says it in a few words", () => {
    expect(signWords({ kind: "reply" }, "Claude Code")).toEqual({ primary: "Claude Code is working…", secondary: "" });
    expect(signWords({ kind: "helpers", working: 5, finished: 0 }, "Claude Code")).toEqual({
      primary: "5 helpers working",
      secondary: "their report will appear here",
    });
    expect(signWords({ kind: "helpers", working: 1, finished: 0 }, "Claude Code")).toEqual({
      primary: "1 helper working",
      secondary: "its report will appear here",
    });
    expect(signWords({ kind: "helpers", working: 3, finished: 2 }, "Claude Code")).toEqual({
      primary: "3 helpers working",
      secondary: "2 finished",
    });
    expect(signWords({ kind: "own" }, "Codex").primary).toBe("Codex is writing a reply of its own");
  });
});

describe("the ring", () => {
  it("one segment per helper, finished ones first; past twelve it shares twelve out", () => {
    expect(ringSlots(3, 2)).toEqual({ total: 5, done: 2, live: 3 });
    expect(ringSlots(12, 0)).toEqual({ total: 12, done: 0, live: 12 });
    expect(ringSlots(20, 10)).toEqual({ total: RING_SLOTS_MAX, done: 4, live: 8 });
    // One still working is never drawn as none; one finished is never drawn as none.
    expect(ringSlots(1, 40)).toEqual({ total: RING_SLOTS_MAX, done: RING_SLOTS_MAX - 1, live: 1 });
    expect(ringSlots(40, 1)).toEqual({ total: RING_SLOTS_MAX, done: 1, live: RING_SLOTS_MAX - 1 });
    expect(ringSlots(13, 0)).toEqual({ total: RING_SLOTS_MAX, done: 0, live: RING_SLOTS_MAX });
  });

  it("two segments never touch, whatever the count and the line", () => {
    expect(ringSegment(0, 1, RING_LIVE_WIDTH)).toBeNull();
    for (const width of [RING_LIVE_WIDTH, RING_DONE_WIDTH]) {
      const cap = (width / 2 / RING_RADIUS) * (180 / Math.PI);
      for (let total = 2; total <= RING_SLOTS_MAX; total++) {
        const slot = 360 / total;
        let endOfLast = -Infinity;
        for (let i = 0; i < total; i++) {
          const seg = ringSegment(i, total, width)!;
          expect(seg.dash).toBeGreaterThan(0);
          // What is drawn: the dash and the line's two round ends.
          const from = seg.rotate - cap;
          const to = seg.rotate + seg.dash + cap;
          expect(to - from).toBeLessThan(slot);
          expect(from).toBeGreaterThan(endOfLast);
          endOfLast = to;
        }
        // The ring starts at 12 o'clock (SVG's zero is 3 o'clock).
        expect(ringSegment(0, total, width)!.rotate).toBeGreaterThan(-90);
      }
    }
  });
});

/* --------------------------------------------------------- the component */

describe("the sign, drawn", () => {
  it("helpers: the count in the ring, a few words for the eye, the old sentence for a screen reader", () => {
    render(<WorkingSign session={facts(5, 5)} toolName="Claude Code" running={false} asking={false} />);
    const sign = screen.getByTestId("code-working-sign");
    expect(sign.getAttribute("data-sign")).toBe("helpers");
    expect(screen.getByTestId("code-sign-primary").textContent).toBe("5 helpers working");
    expect(screen.getByTestId("code-sign-secondary").textContent).toBe("their report will appear here");
    const ring = screen.getByTestId("code-sign-ring");
    expect(ring.getAttribute("data-total")).toBe("5");
    expect(ring.getAttribute("data-done")).toBe("0");
    expect(ring.textContent).toBe("5");
    // The ring is a picture; the short words are for the eye only.
    expect(ring.getAttribute("aria-hidden")).toBe("true");
    expect(screen.getByTestId("code-sign-primary").parentElement?.getAttribute("aria-hidden")).toBe("true");
    // A screen reader hears the sentence the view has always said.
    const said = screen.getByTestId("code-background");
    expect(said.getAttribute("role")).toBe("status");
    expect(said.textContent).toBe(
      "5 helpers are still working in the background. Their report will appear here when they finish.",
    );
    expect(said.className).toContain("sr-only");
    // …and a pointer resting on the sign reads the same.
    expect(sign.getAttribute("title")).toBe(said.textContent);
  });

  it("a helper seen to end is counted and leaves a finished segment; a new stretch counts from nothing", () => {
    const view = render(<WorkingSign session={facts(5, 5)} toolName="Claude Code" running={false} asking={false} />);
    view.rerender(<WorkingSign session={facts(3, 3)} toolName="Claude Code" running={false} asking={false} />);
    expect(screen.getByTestId("code-sign-primary").textContent).toBe("3 helpers working");
    expect(screen.getByTestId("code-sign-secondary").textContent).toBe("2 finished");
    const ring = screen.getByTestId("code-sign-ring");
    expect(ring.getAttribute("data-total")).toBe("5");
    expect(ring.getAttribute("data-done")).toBe("2");
    expect(ring.textContent).toBe("3");
    // The same read again counts nothing twice.
    view.rerender(<WorkingSign session={facts(3, 3)} toolName="Claude Code" running={false} asking={false} />);
    expect(screen.getByTestId("code-sign-secondary").textContent).toBe("2 finished");

    // The last ones end; the tool writes its own reply.
    view.rerender(<WorkingSign session={facts(0, 0, true)} toolName="Claude Code" running={false} asking={false} />);
    expect(screen.getByTestId("code-working-sign").getAttribute("data-sign")).toBe("own");
    expect(screen.getByTestId("code-sign-primary").textContent).toBe("Claude Code is writing a reply of its own");
    expect(screen.getByTestId("code-background").textContent).toBe(
      "Claude Code is writing a reply of its own. It appears here as it goes.",
    );

    // A later batch of helpers starts from nothing finished.
    view.rerender(<WorkingSign session={facts(2, 2)} toolName="Claude Code" running={false} asking={false} />);
    expect(screen.getByTestId("code-sign-primary").textContent).toBe("2 helpers working");
    expect(screen.getByTestId("code-sign-secondary").textContent).toBe("their report will appear here");
    expect(screen.getByTestId("code-sign-ring").getAttribute("data-done")).toBe("0");
  });

  it("a reply being written: the plain ring and the words; it steps aside for a card", () => {
    const view = render(<WorkingSign session={facts(0, 0)} toolName="Claude Code" running asking={false} />);
    const working = screen.getByTestId("code-working");
    expect(working.textContent).toBe("Claude Code is working…");
    expect(screen.getByTestId("code-working-sign").getAttribute("data-sign")).toBe("reply");
    expect(screen.queryByTestId("code-background")).toBeNull();
    // The light goes round the whole ring in steps, its tail one step behind.
    const light = screen.getByTestId("code-sign-light");
    expect(light.classList.contains("wsign-hop")).toBe(true);
    expect(light.style.getPropertyValue("--wsign-m")).toBe(String(SOLO_STEPS));
    expect(light.style.getPropertyValue("--wsign-beat")).toBe(`${BEAT_REPLY_MS}ms`);
    expect(screen.getByTestId("code-sign-tail").style.animationDelay).toBe(`-${(SOLO_STEPS - 1) * BEAT_REPLY_MS}ms`);
    view.rerender(<WorkingSign session={facts(0, 0)} toolName="Claude Code" running asking />);
    expect(screen.queryByTestId("code-working-sign")).toBeNull();
    expect(screen.queryByTestId("code-working")).toBeNull();
  });

  it("background commands alone: the quiet sentence and a ring that never moves", () => {
    render(<WorkingSign session={facts(2, 0)} toolName="Claude Code" running={false} asking={false} />);
    const said = screen.getByTestId("code-background");
    expect(said.textContent).toBe("2 background commands are still running.");
    expect(said.getAttribute("role")).toBe("status");
    expect(said.className).not.toContain("sr-only");
    expect(screen.getByTestId("code-sign-ring").textContent).toBe("2");
    expect(screen.queryByTestId("code-sign-light")).toBeNull();
    expect(screen.queryByTestId("code-sign-tail")).toBeNull();
    expect(document.querySelector(".wsign-hop, .wsign-tick")).toBeNull();
  });

  it("its slot is always there and always the same height, with or without a sign", () => {
    const view = render(<WorkingSign session={facts(0, 0)} toolName="Claude Code" running={false} asking={false} />);
    const slot = screen.getByTestId("code-sign-slot");
    const classes = slot.className;
    expect(slot.childElementCount).toBe(0);
    // Sticky, 8 px above the conversation's edge: the browser measures the
    // distance from inside the conversation's 16 px padding, hence -bottom-2
    // (a positive distance pushed the sign up onto the last line).
    expect(classes.split(/\s+/)).toEqual(expect.arrayContaining(["h-8", "sticky", "-bottom-2"]));
    // An empty slot over the text takes no clicks.
    expect(classes.split(/\s+/)).toContain("pointer-events-none");
    for (const next of [
      <WorkingSign key="r" session={facts(0, 0)} toolName="Claude Code" running asking={false} />,
      <WorkingSign key="h" session={facts(5, 5)} toolName="Claude Code" running={false} asking={false} />,
      <WorkingSign key="o" session={facts(0, 0, true)} toolName="Claude Code" running={false} asking={false} />,
      <WorkingSign key="c" session={facts(1, 0)} toolName="Claude Code" running={false} asking={false} />,
    ]) {
      view.rerender(next);
      const now = screen.getByTestId("code-sign-slot");
      expect(now.className).toBe(classes);
      // The sign itself is exactly as tall as its slot.
      expect(within(now).getByTestId("code-working-sign").className.split(/\s+/)).toContain("h-8");
    }
  });

  it("scrolled up it gets a pill behind it; beside 'Jump to latest' on a narrow window it keeps to its ring", () => {
    const view = render(<WorkingSign session={facts(4, 4)} toolName="Claude Code" running={false} asking={false} />);
    let sign = screen.getByTestId("code-working-sign");
    expect(sign.getAttribute("data-floating")).toBe("false");
    expect(sign.className).not.toMatch(/\bbg-/);
    expect(screen.getByTestId("code-sign-primary").parentElement?.className).not.toContain("max-sm:hidden");
    view.rerender(
      <WorkingSign session={facts(4, 4)} toolName="Claude Code" running={false} asking={false} floating compact />,
    );
    sign = screen.getByTestId("code-working-sign");
    expect(sign.getAttribute("data-floating")).toBe("true");
    expect(sign.className).toMatch(/\bbg-ink-850/);
    // Beside "Jump to latest": the first words only (and on a narrow window
    // the ring alone), so the two never overlap.
    expect(screen.getByTestId("code-sign-primary").parentElement?.className).toContain("max-sm:hidden");
    expect(screen.queryByTestId("code-sign-secondary")).toBeNull();
    // The sentence for a screen reader is still there.
    expect(screen.getByTestId("code-background").textContent).toContain("4 helpers are still working");
  });
});

/* ------------------------------------------------------------- the motion */

describe("motion", () => {
  it("moves when it may: the light hops among several helpers and dims and brightens on the last one", () => {
    systemReducesMotion(false);
    const view = render(<WorkingSign session={facts(3, 3)} toolName="Claude Code" running={false} asking={false} />);
    expect(screen.getByTestId("code-working-sign").getAttribute("data-motion")).toBe("on");
    const light = screen.getByTestId("code-sign-light");
    expect(light.classList.contains("wsign-hop")).toBe(true);
    // One hop per working helper, over the part of the ring they hold.
    expect(light.style.getPropertyValue("--wsign-m")).toBe("3");
    expect(light.style.getPropertyValue("--wsign-sweep")).toBe("360.00deg");
    // Its tail is the same light, on the same steps, one step behind (two
    // steps ahead of three is one behind): no frame of its own.
    const tail = screen.getByTestId("code-sign-tail");
    expect(tail.classList.contains("wsign-hop")).toBe(true);
    expect(tail.style.getPropertyValue("--wsign-m")).toBe("3");
    expect(tail.style.getPropertyValue("--wsign-beat")).toBe(light.style.getPropertyValue("--wsign-beat"));
    expect(tail.style.animationDelay).toBe(`-${2 * BEAT_BACKGROUND_MS}ms`);
    view.rerender(<WorkingSign session={facts(1, 1)} toolName="Claude Code" running={false} asking={false} />);
    const last = screen.getByTestId("code-sign-light");
    expect(last.classList.contains("wsign-tick")).toBe(true);
    expect(last.classList.contains("wsign-hop")).toBe(false);
    // One helper has nowhere to hop: no tail.
    expect(screen.queryByTestId("code-sign-tail")).toBeNull();
    // Two finished, one working: the light keeps to the working third.
    expect(screen.getByTestId("code-sign-ring").getAttribute("data-done")).toBe("2");
  });

  it("stands still when the system asks for less motion, and still says what it says", async () => {
    systemReducesMotion(true);
    render(<WorkingSign session={facts(3, 3)} toolName="Claude Code" running={false} asking={false} />);
    await waitFor(() => expect(screen.getByTestId("code-working-sign").getAttribute("data-motion")).toBe("off"));
    expect(document.querySelector(".wsign-hop, .wsign-tick")).toBeNull();
    expect(screen.queryByTestId("code-sign-tail")).toBeNull();
    // Standing still it is the same ring, count and words.
    expect(screen.getByTestId("code-sign-ring").textContent).toBe("3");
    expect(screen.getByTestId("code-sign-light")).toBeTruthy();
    expect(screen.getByTestId("code-sign-primary").textContent).toBe("3 helpers working");
  });

  it("a reply being written stands still under less motion too", async () => {
    systemReducesMotion(true);
    render(<WorkingSign session={facts(0, 0)} toolName="Claude Code" running asking={false} />);
    await waitFor(() => expect(screen.getByTestId("code-working-sign").getAttribute("data-motion")).toBe("off"));
    expect(document.querySelector(".wsign-hop, .wsign-tick")).toBeNull();
    expect(screen.queryByTestId("code-sign-tail")).toBeNull();
    expect(screen.getByTestId("code-working").textContent).toBe("Claude Code is working…");
  });

  it("stands still while the window is hidden, and moves again when it is seen", () => {
    systemReducesMotion(false);
    render(<WorkingSign session={facts(3, 3)} toolName="Claude Code" running={false} asking={false} />);
    expect(document.querySelector(".wsign-hop")).not.toBeNull();
    setHidden(true);
    expect(screen.getByTestId("code-working-sign").getAttribute("data-motion")).toBe("off");
    expect(document.querySelector(".wsign-hop, .wsign-tick")).toBeNull();
    setHidden(false);
    expect(screen.getByTestId("code-working-sign").getAttribute("data-motion")).toBe("on");
    expect(document.querySelector(".wsign-hop")).not.toBeNull();
  });

  it("the stylesheet moves it in steps only, and stops it under less motion by itself", () => {
    const css = readFileSync(join(__dirname, "..", "app", "globals.css"), "utf8").replace(/\r\n/g, "\n");
    const rule = (selector: string) => {
      const at = css.indexOf(`\n${selector} {`);
      expect(at, `no rule ${selector}`).toBeGreaterThan(-1);
      return css.slice(at, css.indexOf("}", at) + 1);
    };
    for (const selector of [".wsign-hop", ".wsign-tick"]) {
      const body = rule(selector);
      expect(body).toMatch(/steps\(/);
      // Never a smooth turn: that is a new frame at every screen refresh.
      expect(body).not.toMatch(/\b(linear|ease|ease-in|ease-out|ease-in-out|cubic-bezier)\b/);
      expect(body).toMatch(/infinite/);
    }
    // Transform and opacity only.
    const frames = (name: string) => {
      const at = css.indexOf(`@keyframes ${name} {`);
      expect(at, `no keyframes ${name}`).toBeGreaterThan(-1);
      return css.slice(at, css.indexOf("\n}", at));
    };
    const props = (text: string) => Array.from(text.matchAll(/([a-z-]+)\s*:/g), (m) => m[1]);
    expect(new Set(props(frames("wsign-hop").split("{").slice(2).join("{")))).toEqual(new Set(["transform"]));
    expect(new Set(props(frames("wsign-tick").split("{").slice(2).join("{")))).toEqual(new Set(["opacity"]));
    // Stopped by the stylesheet itself (before any script has read the setting)…
    const media = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce), (forced-colors: active) {\n  .wsign-hop"));
    expect(media.startsWith("@media")).toBe(true);
    const block = media.slice(0, media.indexOf("\n}\n") + 2);
    expect(block).toContain(".wsign-hop");
    expect(block).toContain(".wsign-tick");
    expect(block).toMatch(/animation:\s*none/);
    // …and wherever the component says so (the window is hidden).
    const off = css.slice(css.indexOf('[data-motion="off"] .wsign-hop'));
    expect(off.slice(0, off.indexOf("}"))).toMatch(/\.wsign-tick[\s\S]*animation:\s*none/);
  });
});

/* -------------------------------------------------- in the session's pane */

describe("in a Build › Code session", () => {
  it("shows the helpers, counts the ones that end between two reads, and keeps its slot when all is quiet", async () => {
    serve({ background: 5, background_helpers: 5 });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const sign = await screen.findByTestId("code-working-sign");
    expect(sign.getAttribute("data-sign")).toBe("helpers");
    expect(within(sign).getByTestId("code-sign-primary").textContent).toBe("5 helpers working");
    // It sits in the conversation, in its slot.
    const slot = screen.getByTestId("code-sign-slot");
    expect(screen.getByTestId("code-transcript").contains(slot)).toBe(true);
    expect(slot.contains(sign)).toBe(true);

    // Two helpers end before the next read.
    serve({ background: 3, background_helpers: 3 });
    expect(await tick(CODE_BACKGROUND_REREAD_MS)).toBe(true);
    await waitFor(() => expect(screen.getByTestId("code-sign-primary").textContent).toBe("3 helpers working"));
    expect(screen.getByTestId("code-sign-secondary").textContent).toBe("2 finished");
    expect(screen.getByTestId("code-sign-ring").getAttribute("data-done")).toBe("2");
    expect(screen.getByTestId("code-background").textContent).toBe(
      "3 helpers are still working in the background. Their report will appear here when they finish.",
    );

    // Everything ends: the sign goes, its slot stays (nothing moves).
    serve({});
    expect(await tick(CODE_BACKGROUND_REREAD_MS)).toBe(true);
    await waitFor(() => expect(screen.queryByTestId("code-working-sign")).toBeNull());
    expect(screen.getByTestId("code-sign-slot")).toBe(slot);
    expect(screen.queryByTestId("code-background")).toBeNull();
  });

  it("a quiet session has the slot and no sign", async () => {
    serve({});
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-conversation");
    expect(screen.getByTestId("code-sign-slot").childElementCount).toBe(0);
    expect(screen.queryByTestId("code-working-sign")).toBeNull();
  });
});
