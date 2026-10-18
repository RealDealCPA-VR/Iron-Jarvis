/**
 * v1.348.0 polish wave A, lane L1: the live work summary and the shimmer
 * (ideas ported from 21st.dev agent-elements, Tool Group #3 and Text
 * Shimmer #6; no code installed).
 *
 * - While a turn runs, ONE quiet line counts up in the folded line's own
 *   words ("Working for 12 s · read 2 files, searched the web"), from the
 *   SAME counter as "Worked for …" (lib/stepWords stepCounts), with a clock
 *   that ticks itself: the rows and the caller never re-render for it.
 * - Under it, only the newest few steps; older finished ones fold into a
 *   quiet "N earlier steps" toggle (a real button, aria-expanded) that opens
 *   them in place, in call order. A running step is never folded away.
 * - The running step's words and the live "Thinking…" shimmer: one CSS
 *   class built from the theme's own inks, static text under reduced motion
 *   and forced colours. No borders, no boxes, whole pixels.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
    offline = false;
    partial = "";
  }
  const stream = {
    hold: false,
    streaming: false,
    version: 0,
    live: { tools: [] as unknown[], thinking: "" },
    // v1.348.0 integration: the turn's start, as the real hook reports it.
    startedAt: undefined as number | undefined,
    listeners: new Set<() => void>(),
    settle: null as null | ((r: Record<string, unknown>) => void),
    bump() {
      stream.version += 1;
      for (const l of [...stream.listeners]) l();
    },
  };
  return { FakeApiError, FakeStreamError, stream, getResponses: {} as Record<string, unknown> };
});

// A PASS-THROUGH spy on the one helper every live row calls while it renders,
// so a test can count row renders (the clock must not cause any).
vi.mock("@/lib/workTarget", async () => {
  const real = await vi.importActual<typeof import("@/lib/workTarget")>("@/lib/workTarget");
  return { ...real, stepTarget: vi.fn(real.stepTarget) };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => "",
  get: async (path: string) => {
    const r = H.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async () => ({}),
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", async () => {
  const React = await import("react");
  const subscribe = (cb: () => void) => {
    H.stream.listeners.add(cb);
    return () => {
      H.stream.listeners.delete(cb);
    };
  };
  const run = async () => {
    H.stream.streaming = true;
    H.stream.bump();
    return new Promise<Record<string, unknown>>((ok) => {
      H.stream.settle = ok;
    });
  };
  const abort = () => {
    const s = H.stream.settle;
    H.stream.settle = null;
    H.stream.streaming = false;
    H.stream.bump();
    s?.({ reply: "" });
  };
  return {
    StreamError: H.FakeStreamError,
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    useChatStream: () => {
      React.useSyncExternalStore(subscribe, () => H.stream.version);
      return {
        streaming: H.stream.streaming,
        text: "",
        thinking: H.stream.live.thinking,
        tools: H.stream.live.tools,
        startedAt: H.stream.startedAt,
        mcpAsks: [],
        approval: null,
        phase: H.stream.streaming ? "working" : null,
        run,
        abort,
      };
    },
  };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false, reason: null, engine: null, listening: false, processing: false,
    transcript: "", interim: "", error: null, start: () => {}, stop: () => {}, reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: true, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
    readAloud: () => {}, readingKey: null,
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));

import ChatPage from "@/app/chat/page";
import {
  LIVE_RECENT,
  LiveToolRows,
  WorkRow,
  liveSteps,
  liveWindow,
  liveWorkSummary,
  workCounts,
  workSummary,
} from "@/components/chat/WorkLine";
import { ThinkingDisclosure } from "@/components/chat/ThinkingDisclosure";
import { elapsedWords } from "@/components/chat/TurnClock";
import { stepCounts } from "@/lib/stepWords";
import { stepTarget } from "@/lib/workTarget";
import type { ToolCard } from "@/lib/useChatStream";
import { Loader2 } from "lucide-react";

beforeEach(() => {
  H.stream.hold = true;
  H.stream.streaming = false;
  H.stream.settle = null;
  H.stream.live = { tools: [], thinking: "" };
  H.stream.startedAt = undefined;
  H.getResponses = {
    "/models": { models: [{ provider: "claude-cli", model: "claude-opus-5-5", label: "Opus 5.5", available: true }] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState({}, "", "/chat");
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

const classes = (el: Element | null | undefined) => (el?.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);

/** A frame or a fill: any border width, any background, any rounding, any shadow. */
function boxClasses(el: Element | null | undefined): string[] {
  return classes(el).filter(
    (c) =>
      /^(border|border-[0-9])$/.test(c) ||
      /^border-[xytlr]-?[0-9]*$/.test(c) ||
      /^bg-/.test(c) ||
      /^rounded/.test(c) ||
      /^shadow/.test(c),
  );
}

const T0 = 1_000_000;

function done(id: string, name: string, args: Record<string, unknown>, ok = true): ToolCard {
  return { id, name, status: "done", ok, args, startedAt: T0, endedAt: T0 + 100 };
}
function running(id: string, name: string, args: Record<string, unknown>): ToolCard {
  return { id, name, status: "running", args, startedAt: T0 + 200 };
}

/** Six calls: five finished, the newest still running. */
const SIX: ToolCard[] = [
  done("c1", "read_file", { path: "C:\\work\\a.xlsx" }),
  done("c2", "read_file", { path: "C:\\work\\b.xlsx" }),
  done("c3", "read_file", { path: "C:\\work\\c.xlsx" }),
  done("c4", "web_search", { query: "pier 9 hours" }),
  done("c5", "excel_query", { column: "Revenue" }),
  running("c6", "web_search", { query: "harbor st hours" }),
];

/* ------------------------------------------------------------- the words */

describe("the live line says the work in the folded line's own words", () => {
  it("live and folded come from ONE counter, so they cannot drift", () => {
    const finished = SIX.map((c) => ({ ...c, status: "done" as const }));
    const steps = liveSteps(finished);
    expect(workCounts(steps)).toEqual(stepCounts(steps));
    const live = liveWorkSummary(steps, 14_000);
    expect(live).toBe("Working for 14 s · read 3 files, searched the web 2 times, ran 1 tool");
    // When the same turn folds, the words are the same, only the tense moves.
    const folded = workSummary({ steps, timing: { startedAt: 0, firstTokenAt: null, endedAt: 14_000 } });
    expect(folded).toBe(live.replace(/^Working/, "Worked"));
  });

  it("a call still running is not counted yet; a failed one is, and says so", () => {
    expect(liveWorkSummary(liveSteps(SIX), 12_000)).toBe(
      "Working for 12 s · read 3 files, searched the web, ran 1 tool",
    );
    expect(liveWorkSummary(liveSteps([running("r", "read_file", {})]), 4_000)).toBe("Working for 4 s");
    // v1.351.0 (G07): a failed write counts only as failed, never as made.
    expect(liveWorkSummary(liveSteps([done("w", "write_file", { path: "out.md" }, false)]), 5_000)).toBe(
      "Working for 5 s · 1 failed",
    );
  });

  it("no clock known, or under a second: just 'Working'", () => {
    const one = liveSteps([done("a", "read_file", {})]);
    expect(liveWorkSummary(one, null)).toBe("Working · read 1 file");
    expect(liveWorkSummary(one, 400)).toBe("Working · read 1 file");
    expect(liveWorkSummary([], null)).toBe("Working");
  });

  it("the clock reads in whole seconds, like the folded line past ten seconds", () => {
    expect(elapsedWords(12_900)).toBe("12 s");
    expect(elapsedWords(65_000)).toBe("1 min 5 s");
    expect(elapsedWords(120_000)).toBe("2 min");
    expect(elapsedWords(999)).toBeNull();
    expect(elapsedWords(Number.NaN)).toBeNull();
    expect(elapsedWords(null)).toBeNull();
  });
});

/* ------------------------------------------------------- the ticking line */

describe("the live line ticks itself", () => {
  it("counts up every second without re-rendering the rows or the caller", () => {
    vi.useFakeTimers();
    vi.setSystemTime(T0 + 12_000);
    let callerRenders = 0;
    function Caller({ cards }: { cards: ToolCard[] }) {
      callerRenders += 1;
      return <LiveToolRows cards={cards} since={T0} />;
    }
    const cards = [done("a", "read_file", { path: "C:\\work\\a.xlsx" }), running("b", "web_search", { query: "q" })];
    render(<Caller cards={cards} />);
    const words = screen.getByTestId("live-work-words");
    expect(words.textContent).toBe("Working for 12 s · read 1 file");
    const rowRenders = vi.mocked(stepTarget).mock.calls.length;
    expect(rowRenders).toBeGreaterThan(0); // anti-vacuity: the spy sees row renders
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(screen.getByTestId("live-work-words").textContent).toBe("Working for 15 s · read 1 file");
    // Only the line moved: no row re-rendered, and the caller (the chat page,
    // in the app) did not render again.
    expect(vi.mocked(stepTarget).mock.calls.length).toBe(rowRenders);
    expect(callerRenders).toBe(1);
  });

  it("without the turn's start it counts from the first call", () => {
    vi.useFakeTimers();
    vi.setSystemTime(T0 + 7_000);
    render(<LiveToolRows cards={[running("a", "read_file", { path: "x.md" })]} />);
    // The running call started at T0 + 200.
    expect(screen.getByTestId("live-work-words").textContent).toBe("Working for 6 s");
  });

  it("with the turn's start it counts the WHOLE turn, not from the first call", () => {
    // Review pin: the page is meant to pass the stream's own start so the
    // live line and the folded "Worked for …" count the same span. The call
    // started 11.8 s ago; the turn started 17 s ago.
    vi.useFakeTimers();
    vi.setSystemTime(T0 + 12_000);
    render(<LiveToolRows cards={[running("a", "read_file", { path: "x.md" })]} since={T0 - 5_000} />);
    expect(screen.getByTestId("live-work-words").textContent).toBe("Working for 17 s");
  });

  it("is one quiet line: no box, whole pixels, not read out every second", () => {
    render(<LiveToolRows cards={SIX} since={T0} />);
    const line = screen.getByTestId("live-work-summary");
    expect(boxClasses(line)).toEqual([]);
    expect(classes(line)).toContain("text-[13px]");
    expect(line.getAttribute("aria-live")).toBe("off");
    // It is NOT a work row: the rows stay one per call.
    expect(line.getAttribute("data-testid")).not.toBe("work-row");
    expect(screen.getAllByTestId("work-row")).toHaveLength(LIVE_RECENT);
  });
});

/* ----------------------------------------------------------- the window */

describe("only the newest steps show; older ones fold into 'N earlier steps'", () => {
  it("six calls: the last three show, three fold, and the toggle opens them in place", () => {
    render(<LiveToolRows cards={SIX} since={T0} />);
    const rowsText = () => screen.getAllByTestId("work-row").map((r) => r.textContent ?? "");
    expect(rowsText()).toHaveLength(3);
    expect(rowsText()[0]).toMatch(/^Searched the web/);
    expect(rowsText()[1]).toMatch(/^Ran excel_query/);
    expect(rowsText()[2]).toBe("Searching the web for harbor st hours");
    const toggle = screen.getByRole("button", { name: "3 earlier steps" });
    expect(toggle.tagName).toBe("BUTTON");
    expect(toggle.getAttribute("type")).toBe("button");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    // It controls the list it opens.
    expect(document.getElementById(toggle.getAttribute("aria-controls") ?? "")).toBe(screen.getByTestId("live-steps"));
    expect(boxClasses(toggle)).toEqual([]);
    // Keyboard reachable: a real button takes focus (Enter/Space are native).
    toggle.focus();
    expect(document.activeElement).toBe(toggle);
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    // Opened IN PLACE, in call order, under the toggle.
    expect(rowsText()).toEqual([
      "Read a.xlsx0.1 s",
      "Read b.xlsx0.1 s",
      "Read c.xlsx0.1 s",
      expect.stringMatching(/^Searched the web/),
      expect.stringMatching(/^Ran excel_query/),
      "Searching the web for harbor st hours",
    ]);
    expect(toggle.compareDocumentPosition(screen.getAllByTestId("work-row")[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(toggle);
    expect(rowsText()).toHaveLength(3);
  });

  it("a step still running is never folded away, however old", () => {
    const parallel = [running("p1", "read_file", { path: "C:\\work\\slow.xlsx" }), ...SIX.slice(0, 5)];
    const w = liveWindow(parallel);
    expect(w.recent.map((c) => c.id)).toEqual(["p1", "c3", "c4", "c5"]);
    expect(w.earlier).toBe(2);
    render(<LiveToolRows cards={parallel} />);
    const rows = screen.getAllByTestId("work-row");
    expect(rows[0].getAttribute("data-state")).toBe("running");
    expect(rows[0].textContent).toBe("Reading slow.xlsx");
    expect(screen.getByRole("button", { name: "2 earlier steps" })).toBeTruthy();
  });

  it("folding one step would save nothing: up to four calls all show, no toggle", () => {
    expect(liveWindow(SIX.slice(0, 4)).earlier).toBe(0);
    render(<LiveToolRows cards={SIX.slice(0, 4)} />);
    expect(screen.getAllByTestId("work-row")).toHaveLength(4);
    expect(screen.queryByTestId("live-earlier")).toBeNull();
  });

  it("a running old step that leaves only ONE to fold: nothing folds, no toggle", () => {
    // Review pin: five calls, the oldest still running. The window would hide
    // just c1, and a toggle row for one step saves nothing.
    const five = [running("p1", "read_file", { path: "C:\\work\\slow.xlsx" }), ...SIX.slice(0, 4)];
    expect(liveWindow(five).earlier).toBe(0);
    render(<LiveToolRows cards={five} />);
    expect(screen.getAllByTestId("work-row")).toHaveLength(5);
    expect(screen.queryByTestId("live-earlier")).toBeNull();
  });

  it("no calls: nothing at all", () => {
    const { container } = render(<LiveToolRows cards={[]} />);
    expect(container.innerHTML).toBe("");
  });
});

/* --------------------------------------------------------------- shimmer */

describe("a running label shimmers instead of pulsing", () => {
  it("the running step's words carry the shimmer; a finished step's do not", () => {
    render(<LiveToolRows cards={SIX} />);
    const rows = screen.getAllByTestId("work-row");
    const live = within(rows[2]).getByText("Searching the web for");
    expect(classes(live)).toContain("ij-shimmer");
    expect(classes(live)).not.toContain("animate-pulse");
    // The subject stays still; only the words sweep.
    expect(classes(within(rows[2]).getByTestId("work-target"))).not.toContain("ij-shimmer");
    expect(rows[0].innerHTML).not.toContain("ij-shimmer");
  });

  it("a running row with no subject shimmers too (the page's 'Thinking…' row)", () => {
    const { rerender } = render(<WorkRow icon={Loader2} running title="Thinking…" />);
    expect(classes(screen.getByText("Thinking…"))).toContain("ij-shimmer");
    rerender(<WorkRow icon={Loader2} title="Thought it through" />);
    expect(classes(screen.getByText("Thought it through"))).not.toContain("ij-shimmer");
  });

  it("the live 'Thinking…' of the reasoning row shimmers; a settled one is still", () => {
    const { rerender } = render(<ThinkingDisclosure text="weighing both sheets" live />);
    const label = screen.getByTestId("thinking-summary");
    expect(label.textContent).toBe("Thinking…");
    expect(classes(label)).toContain("ij-shimmer");
    expect(classes(label)).not.toContain("animate-pulse");
    rerender(<ThinkingDisclosure text="weighing both sheets" seconds={3} />);
    expect(classes(screen.getByTestId("thinking-summary"))).not.toContain("ij-shimmer");
  });
});

/* ------------------------------------------------ the stylesheet's rule */

const css = readFileSync(join(process.cwd(), "app", "globals.css"), "utf8").replace(/\r\n/g, "\n");

/** Every `<marker> … { body }` in the stylesheet: its header and its body,
 *  walking braces so a nested rule cannot end the block early. */
function blocks(marker: string): { head: string; body: string }[] {
  const out: { head: string; body: string }[] = [];
  let at = css.indexOf(marker);
  while (at !== -1) {
    const open = css.indexOf("{", at);
    let depth = 0;
    let i = open;
    for (; i < css.length; i++) {
      if (css[i] === "{") depth++;
      else if (css[i] === "}" && --depth === 0) break;
    }
    out.push({ head: css.slice(at, open), body: css.slice(open + 1, i) });
    at = css.indexOf(marker, i);
  }
  return out;
}

/** The body of `.ij-shimmer { … }` inside a block. */
function shimmerRule(body: string): string {
  const m = /\.ij-shimmer\s*\{([^}]*)\}/.exec(body);
  return m ? m[1] : "";
}

/** The text inside the first `linear-gradient( … )`, parentheses matched. */
function gradientArgs(rule: string): string {
  const start = rule.indexOf("linear-gradient(");
  if (start === -1) return "";
  let depth = 0;
  for (let i = start + "linear-gradient".length; i < rule.length; i++) {
    if (rule[i] === "(") depth++;
    else if (rule[i] === ")" && --depth === 0) return rule.slice(start + "linear-gradient(".length, i);
  }
  return "";
}

describe("the shimmer is one CSS class, from the theme's own inks", () => {
  const supported = blocks("@supports").find((b) => b.body.includes(".ij-shimmer"));
  const rule = shimmerRule(supported?.body ?? "");

  it("sweeps a gradient through the words (background-clip: text), guarded by @supports", () => {
    expect(supported, "the shimmer lives behind an @supports for background-clip:text").toBeTruthy();
    expect(supported!.head).toMatch(/background-clip:\s*text/);
    expect(rule).toMatch(/(^|[^-])background-clip:\s*text/);
    expect(rule).toMatch(/-webkit-background-clip:\s*text/);
    expect(rule).toMatch(/-webkit-text-fill-color:\s*transparent/);
    expect(rule).toMatch(/animation:\s*ij-shimmer\s[^;]*infinite/);
    expect(css).toMatch(/@keyframes ij-shimmer\s*\{[^}]*background-position/);
  });

  it("has no colour of its own: every stop is the text's ink or a theme variable", () => {
    const args = gradientArgs(rule);
    expect(args, "the rule draws a linear-gradient").not.toBe("");
    const stops = args.split(",").map((s) => s.trim()).slice(1); // [0] is the angle
    expect(stops.length).toBeGreaterThanOrEqual(3);
    for (const stop of stops) {
      expect(stop, stop).toMatch(/^(currentColor|rgb\(var\(--[a-z0-9-]+\)\)) \d+%$/);
    }
    // At least one stop is the sweeping band (a theme ink, not the text's own).
    expect(stops.some((s) => s.startsWith("rgb(var(--"))).toBe(true);
    // And nothing literal anywhere in the rule.
    expect(rule).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(rule).not.toMatch(/(rgb|hsl)a?\(\s*\d/);
  });

  it("is still text under prefers-reduced-motion and in forced-colours mode", () => {
    const still = blocks("@media").filter((b) => shimmerRule(b.body));
    for (const query of ["prefers-reduced-motion: reduce", "forced-colors: active"]) {
      const b = still.find((x) => x.head.includes(query));
      expect(b, `a ${query} block covers .ij-shimmer`).toBeTruthy();
      const r = shimmerRule(b!.body);
      expect(r).toMatch(/animation:\s*none/);
      expect(r).toMatch(/background-image:\s*none/);
      expect(r).toMatch(/-webkit-text-fill-color:\s*currentColor/);
    }
    // The still rule comes AFTER the moving one, so it wins at equal weight.
    const moving = css.indexOf(".ij-shimmer {");
    const stillAt = css.indexOf(".ij-shimmer {", moving + 1);
    expect(moving).toBeGreaterThan(-1);
    expect(stillAt).toBeGreaterThan(moving);
  });
});

/* --------------------------------------------------------------- the page */

describe("on the chat page", () => {
  it("a running turn shows the live line, the newest rows, the toggle, and shimmering labels", async () => {
    // The calls started a moment ago, on the client clock (as upsertTool stamps).
    const shift = Date.now() - 2_000 - T0;
    const tools = SIX.map((c) => ({
      ...c,
      startedAt: (c.startedAt ?? T0) + shift,
      ...(c.endedAt ? { endedAt: c.endedAt + shift } : {}),
    }));
    H.stream.live = { thinking: "Comparing the sheets first.", tools };
    render(<ChatPage />);
    const el = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(el, { target: { value: "compare them" } });
    fireEvent.keyDown(el, { key: "Enter" });
    const live = await screen.findByTestId("live-work");
    // Wait for the THING asserted: the toggle the live rows draw.
    await waitFor(() => expect(within(live).getByTestId("live-earlier").textContent).toBe("3 earlier steps"));
    expect(within(live).getByTestId("live-work-words").textContent).toMatch(
      /^Working for \d+ s · read 3 files, searched the web, ran 1 tool$/,
    );
    // The three newest calls, then the page's own "Thinking…" row.
    const rows = within(live).getAllByTestId("work-row");
    expect(rows).toHaveLength(4);
    expect(rows[2].textContent).toBe("Searching the web for harbor st hours");
    expect(classes(within(rows[3]).getByText("Thinking…"))).toContain("ij-shimmer");
    expect(classes(within(live).getByTestId("thinking-summary"))).toContain("ij-shimmer");
    for (const r of rows) expect(boxClasses(r)).toEqual([]);
    fireEvent.click(within(live).getByTestId("live-earlier"));
    expect(within(live).getAllByTestId("work-row")).toHaveLength(7);
  });

  it("v1.348.0 integration: ONE clock — the live line counts the whole turn and the Thinking row's clock steps aside", async () => {
    // The turn began 12 s ago; its first call only 2 s ago. Counting from the
    // first call (the line without the page's `since`) would read "2 s", a
    // different number from the Thinking row's clock and from "Worked for".
    const now = Date.now();
    H.stream.startedAt = now - 12_000;
    H.stream.live = {
      thinking: "",
      tools: [{ ...done("c1", "read_file", { path: "C:\\work\\a.xlsx" }), startedAt: now - 2_000, endedAt: now - 1_900 }],
    };
    render(<ChatPage />);
    const el = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(el, { target: { value: "read it" } });
    fireEvent.keyDown(el, { key: "Enter" });
    const live = await screen.findByTestId("live-work");
    await waitFor(() =>
      expect(within(live).getByTestId("live-work-words").textContent).toMatch(/^Working for 1[2-9] s · read 1 file$/),
    );
    // The page's "Thinking…" row is still there, but without a clock of its own.
    expect(within(live).getByText("Thinking…")).toBeTruthy();
    expect(within(live).queryByTestId("turn-clock")).toBeNull();
  });

  it("v1.348.0 integration: with no steps yet, the Thinking row keeps its clock (no live line)", async () => {
    H.stream.startedAt = Date.now() - 12_000;
    H.stream.live = { thinking: "", tools: [] };
    render(<ChatPage />);
    const el = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(el, { target: { value: "think" } });
    fireEvent.keyDown(el, { key: "Enter" });
    const live = await screen.findByTestId("live-work");
    await waitFor(() => expect(within(live).getByTestId("turn-clock").textContent).toMatch(/^1[2-9]s$/));
    expect(within(live).queryByTestId("live-work-words")).toBeNull();
  });
});
