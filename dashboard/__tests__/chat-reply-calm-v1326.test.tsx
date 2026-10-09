/**
 * Calm chat W1-4 (v1.326.0): replies with no box; your message in a tinted
 * bubble; one quiet action row.
 *
 * - A reply is prose on the page: no border, no fill, no avatar beside it.
 * - Your own message is a soft tinted bubble on the right: no border, about
 *   20px round, at most ~72% wide (85% on a phone), no avatar.
 * - Every message holds itself to the centred reading column (the scroller's
 *   own `[&>*]` rule cannot reach rows inside QuoteSelection's `contents`).
 * - Under a reply sits ONE row of 28px ghost buttons — Copy, Try again,
 *   👍 / 👎, read aloud, save to the project, "‹ 1 / 2 ›", the time. The
 *   newest reply's row is always on screen; an older one's shows on hover, on
 *   focus and on a touch screen, and stays open while 👎 asks its question.
 * - A table or chart in a reply reads as part of the prose: hairline rules,
 *   no grid, no filled header, no frame.
 * - A reply from another participant says who spoke on a small muted line,
 *   with no pill and no bubble.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

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
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      posts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => "",
  get: async (path: string) => {
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    return {};
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    phase: null,
    run: async () => ({ reply: "done" }),
    abort: () => {},
  }),
}));
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
vi.mock("react-markdown", async () => await vi.importActual("react-markdown"));

import ChatPage from "@/app/chat/page";
import { ChartCard } from "@/components/chat/ChartCard";
import { parseChartSpec } from "@/lib/chartSpec";

const AT = "2026-10-09T08:20:00";
const TABLE = [
  "Pier 9 is closing the gap.",
  "",
  "| Location | Jul | Sep |",
  "| --- | --- | --- |",
  "| Harbor St | 41,200 | 46,100 |",
  "| Pier 9 | 18,400 | 27,600 |",
].join("\n");

const THREAD = {
  id: "t1",
  title: "Q3 revenue",
  messages: [
    { role: "user", content: "How did the two locations do?", at: AT },
    { role: "assistant", content: "An older answer.", at: AT },
    { role: "user", content: "Show me a table.", at: AT },
    {
      role: "assistant",
      content: TABLE,
      at: AT,
      branch: { tails: [[{ role: "assistant", content: "A first try." }], null] },
    },
  ],
};

const PANEL_THREAD = {
  id: "t2",
  title: "Ask the builder",
  messages: [
    { role: "user", content: "@builder can you do it?", at: AT },
    { role: "assistant", content: "I can do that tomorrow.", panelWho: "builder", at: AT },
  ],
};

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.getResponses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/chat/threads/t1": THREAD,
    "/chat/threads/t2": PANEL_THREAD,
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
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const classes = (el: Element | null | undefined) => (el?.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);

/** A class list carries a frame or a fill: any border width, any background,
 *  any rounding. (`border-white/…` alone sets only a colour.) */
function boxClasses(el: Element | null | undefined): string[] {
  return classes(el).filter(
    (c) =>
      /^(border|border-[xytblr]|border-[0-9])$/.test(c) ||
      /^border-[xytblr]-?[0-9]*$/.test(c) ||
      /^bg-/.test(c) ||
      /^rounded/.test(c),
  );
}

async function openThread(id: string, text: string) {
  window.history.replaceState({}, "", `/chat?thread=${id}`);
  render(<ChatPage />);
  return screen.findByText(text);
}

describe("a reply is prose; your message is a tinted bubble (calm chat W1-4)", () => {
  it("a reply has no border, no fill and no avatar beside it", async () => {
    const text = await openThread("t1", "An older answer.");
    const prose = text.closest('[data-testid="reply-prose"]');
    expect(prose).not.toBeNull();
    expect(boxClasses(prose)).toEqual([]);
    // Nothing sits beside the prose: it is the first thing in its reply.
    expect(prose!.previousElementSibling).toBeNull();
    expect(prose!.parentElement?.getAttribute("data-testid")).toBe("reply");
    expect(prose!.querySelector("svg")).toBeNull();
  });

  it("your message is a tinted bubble on the right: no border, ~20px round, ~72% wide, no avatar", async () => {
    const text = await openThread("t1", "How did the two locations do?");
    const bubble = text.closest('[data-testid="user-bubble"]')!;
    expect(bubble).not.toBeNull();
    const c = classes(bubble);
    expect(c).toContain("bg-accent/[0.1]");
    expect(c).toContain("rounded-[20px]");
    expect(c).toContain("sm:max-w-[72%]");
    expect(c.filter((x) => /^border/.test(x))).toEqual([]);
    expect(classes(bubble.parentElement)).toContain("justify-end");
    // Alone on its line: no avatar tile next to it.
    expect(bubble.parentElement!.children).toHaveLength(1);
  });

  it("every message holds itself to the centred reading column", async () => {
    await openThread("t1", "An older answer.");
    const rows = document.querySelectorAll("[data-msg-index]");
    expect(rows.length).toBe(4);
    for (const row of Array.from(rows)) {
      expect(classes(row)).toEqual(expect.arrayContaining(["mx-auto", "w-full", "max-w-[760px]"]));
    }
  });
});

describe("one quiet action row under a reply (calm chat W1-4)", () => {
  it("the newest reply's row is always on screen; an older one's shows on hover, focus and touch", async () => {
    await openThread("t1", "An older answer.");
    const rows = screen.getAllByTestId("reply-actions");
    expect(rows).toHaveLength(2);
    const [older, newest] = rows;
    expect(classes(newest)).not.toContain("opacity-0");
    expect(classes(older)).toEqual(
      expect.arrayContaining([
        "opacity-0",
        "group-hover/msg:opacity-100",
        "focus-within:opacity-100",
        "[@media(hover:none)]:opacity-100",
        "has-[form]:opacity-100",
      ]),
    );
  });

  it("copy, try again, 👍 / 👎, the versions and the time share ONE row of 28px ghost buttons", async () => {
    await openThread("t1", "Pier 9 is closing the gap.");
    const row = screen.getAllByTestId("reply-actions").at(-1)!;
    const r = within(row);
    r.getByRole("button", { name: "Copy message" });
    r.getByRole("button", { name: "Regenerate reply" });
    r.getByRole("button", { name: "Good reply" });
    r.getByRole("button", { name: "Not quite right" });
    r.getByTestId("read-aloud");
    r.getByTestId("message-time");
    const picker = r.getByTestId("branch-picker");
    expect(picker).toHaveTextContent("2 / 2");
    // The versions moved INTO the row: no second picker under the reply.
    expect(screen.getAllByTestId("branch-picker")).toHaveLength(1);
    for (const btn of within(row).getAllByRole("button")) {
      if (btn.getAttribute("data-testid") === "regen-more") continue; // the narrow chevron
      const c = classes(btn);
      expect(c, btn.getAttribute("aria-label") ?? "").toEqual(expect.arrayContaining(["h-7", "w-7"]));
      expect(boxClasses(btn).filter((x) => !/^rounded/.test(x))).toEqual([]);
    }
    // The question is still asked — to a screen reader.
    expect(within(row).getByTestId("reply-rating").textContent).toMatch(/Was this helpful\?/);
  });

  it("👎 on an older reply asks its question inside that reply's row", async () => {
    await openThread("t1", "An older answer.");
    const older = screen.getAllByTestId("reply-actions")[0];
    fireEvent.click(within(older).getByRole("button", { name: "Not quite right" }));
    expect(within(older).getByTestId("reply-rating-ask")).toBeTruthy();
    expect(classes(within(older).getByTestId("reply-rating-ask"))).toContain("basis-full");
  });
});

describe("tables and charts read as part of the prose (calm chat W1-4)", () => {
  it("a markdown table in a reply is ruled with hairlines: no grid, no filled header", async () => {
    const text = await openThread("t1", "Pier 9 is closing the gap.");
    const prose = text.closest('[data-testid="reply-prose"]')!;
    expect(prose.querySelector("table")).not.toBeNull();
    expect(classes(prose)).toEqual(
      expect.arrayContaining([
        "[&_th]:border-x-0",
        "[&_th]:border-t-0",
        "[&_th]:bg-transparent",
        "[&_td]:border-x-0",
        "[&_td]:border-t-0",
      ]),
    );
  });

  it("a chart has no frame, no filled header, and its table view is ruled with hairlines", () => {
    const spec = parseChartSpec(
      JSON.stringify({
        type: "bar",
        title: "Monthly revenue",
        labels: ["Jul", "Aug"],
        series: [{ name: "Harbor St", values: [41200, 43800] }],
      }),
    );
    expect(spec).not.toBeNull();
    render(<ChartCard spec={spec!} />);
    const fig = screen.getByTestId("chart-card");
    expect(boxClasses(fig)).toEqual([]);
    const header = fig.firstElementChild!;
    expect(boxClasses(header)).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: /Show as table/ }));
    const table = screen.getByTestId("chart-table");
    for (const cell of Array.from(table.querySelectorAll("th, td"))) {
      const c = classes(cell);
      expect(c).toContain("border-b");
      expect(c).not.toContain("border");
      expect(c.filter((x) => /^bg-/.test(x))).toEqual([]);
    }
  });
});

describe("another participant's reply says who spoke, quietly (calm chat W1-4)", () => {
  it("a small muted name line, no pill, and the reply is prose with no bubble", async () => {
    const text = await openThread("t2", "I can do that tomorrow.");
    const who = screen.getByTestId("panel-who");
    expect(who).toHaveTextContent("builder");
    expect(boxClasses(who)).toEqual([]);
    expect(who.querySelector("svg")).toBeNull();
    const prose = text.closest('[data-testid="reply-prose"]');
    expect(prose).not.toBeNull();
    expect(boxClasses(prose)).toEqual([]);
  });
});
