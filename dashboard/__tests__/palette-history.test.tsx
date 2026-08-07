import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

/**
 * "In your conversations" — the palette's one asynchronous lane (v1.142.0).
 *
 * palette.test.ts pins the client-side RANKING and palette-ui.test.tsx pins the
 * front door's keyboard and deep-link contract. Neither can see this lane: it
 * is the only part of the palette that talks to the daemon per keystroke, and
 * every one of its failure modes is invisible on a happy path —
 *
 *  - the debounce. Six keystrokes must cost one request, not six.
 *  - the generation guard. Two requests in flight CAN settle out of order, and
 *    the older one landing last would paint the previous query's results under
 *    the current query's rows.
 *  - the degrade. A daemon with no index 404s; the lane must vanish in silence
 *    and stop asking, because a search box that shows an error where results
 *    should be is worse than one that shows nothing.
 *  - the snippet. It is months-old user and model text carrying [] markers
 *    from the index. It gets rendered as text nodes, never as HTML.
 *  - the ORDER. The lane sits below what matched by name and above the ask row,
 *    because that ordering is the whole editorial claim of the feature.
 */

const routerMock = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }));

/** The fake daemon. `calls` is the record every debounce assertion reads. */
const apiState = vi.hoisted(() => ({
  calls: [] as string[],
  /** Every AbortSignal the lane handed to the client, in order. */
  signals: [] as AbortSignal[],
  data: {} as Record<string, unknown>,
  /** Path prefix -> how to answer /search/history. */
  history: {
    hits: [] as unknown[],
    /** Reject with this status instead of answering (404 = older daemon).
     *  null = answer normally. 0 is a REAL status here — it is what lib/api
     *  reports for an offline daemon or an abort. */
    failStatus: null as number | null,
    /** Resolve manually, so out-of-order responses can be staged. */
    deferred: null as null | ((hits: unknown[]) => void),
  },
}));

vi.mock("@/lib/api", () => ({
  get: (path: string, opts?: { signal?: AbortSignal }) => {
    apiState.calls.push(path);
    if (opts?.signal) apiState.signals.push(opts.signal);
    if (path.startsWith("/search/history")) {
      if (apiState.history.failStatus !== null) {
        const err = Object.assign(new Error("nope"), {
          status: apiState.history.failStatus,
        });
        return Promise.reject(err);
      }
      if (apiState.history.deferred !== null) {
        return new Promise((resolve) => {
          apiState.history.deferred = (hits: unknown[]) => resolve({ hits, mode: "fts5" });
        });
      }
      return Promise.resolve({ hits: apiState.history.hits, mode: "fts5" });
    }
    return Promise.resolve(apiState.data[path] ?? {});
  },
}));

/** Every item the pure ranker was ever shown. Fact 8 of the build spec is a
 *  NEGATIVE claim — history rows must never reach scorePalette — and the only
 *  way to assert a negative about a function is to watch its arguments. */
const rankerSpy = vi.hoisted(() => ({ seen: [] as { id: string; kind: string }[] }));
vi.mock("@/lib/palette", async () => {
  const actual = await vi.importActual<typeof import("@/lib/palette")>("@/lib/palette");
  return {
    ...actual,
    scorePalette: (q: string, items: unknown[], limit?: number) => {
      rankerSpy.seen.push(...(items as { id: string; kind: string }[]));
      return actual.scorePalette(q, items as never, limit);
    },
  };
});

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  return {
    AnimatePresence: ({ children }: { children?: unknown }) =>
      createElement(Fragment, null, children as never),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => tagFor(String(tag)),
    }),
  };
});

import { CommandPalette } from "@/components/CommandPalette";

// ── Fixtures ─────────────────────────────────────────────────────────────────

const hit = (over: Record<string, unknown> = {}) => ({
  kind: "chat",
  ref: "t-scorp",
  thread_id: "t-scorp",
  title: "S-corp election timing",
  snippet: "we should file the [election] before March",
  role: "user",
  at: new Date().toISOString(),
  project_id: "",
  score: 0.9,
  seq: 4,
  ...over,
});

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  apiState.calls = [];
  apiState.signals = [];
  apiState.data = { "/skills": {}, "/chat/threads": {}, "/projects": {} };
  apiState.history = { hits: [hit()], failStatus: null, deferred: null };
  rankerSpy.seen = [];
  routerMock.push.mockReset();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

// ── Helpers ──────────────────────────────────────────────────────────────────

const box = () => screen.getByRole("combobox") as HTMLInputElement;
const options = () => screen.queryAllByRole("option");
const labels = () => options().map((o) => o.textContent || "");
const historyCalls = () => apiState.calls.filter((p) => p.startsWith("/search/history"));

async function open() {
  await act(async () => {
    window.dispatchEvent(new Event("ij:open-palette"));
  });
}

async function type(value: string) {
  await act(async () => {
    fireEvent.change(box(), { target: { value } });
  });
}

/** Let the debounce elapse and the response settle. */
async function settle(ms = 300) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
  });
  await act(async () => {});
}

async function press(key: string, times = 1) {
  for (let i = 0; i < times; i++) {
    await act(async () => {
      fireEvent.keyDown(box(), { key });
    });
  }
}

// ── The request ──────────────────────────────────────────────────────────────

describe("the conversation lane asks the daemon exactly when it should", () => {
  it("a burst of keystrokes costs ONE request, after the typing stops", async () => {
    render(<CommandPalette />);
    await open();

    for (const v of ["s", "s-", "s-c", "s-co", "s-cor", "s-corp"]) await type(v);
    expect(historyCalls()).toEqual([]); // nothing yet: still typing

    await settle();
    expect(historyCalls()).toEqual(["/search/history?q=s-corp&limit=20"]);
  });

  it("a query too short to be worth searching is never sent", async () => {
    render(<CommandPalette />);
    await open();
    await type("ab");
    await settle();
    expect(historyCalls()).toEqual([]);
  });

  it("an empty box asks nothing and shows no lane", async () => {
    render(<CommandPalette />);
    await open();
    await settle();
    expect(historyCalls()).toEqual([]);
    expect(screen.queryByText("In your conversations")).not.toBeInTheDocument();
  });

  it("the query rides url-encoded, and asks for MORE rows than it renders", async () => {
    render(<CommandPalette />);
    await open();
    await type("s-corp & 1120s");
    await settle();
    // 20 asked for, 5 shown. The index answers per MESSAGE, so a five-row
    // request can be five messages from ONE conversation — which collapses to a
    // single row and starves a lane that had plenty to show. See HISTORY_FETCH.
    expect(historyCalls()[0]).toBe("/search/history?q=s-corp%20%26%201120s&limit=20");
  });

  it("five DISTINCT conversations survive a chatty one hogging the top hits", async () => {
    // What the index really returns: four hits from one thread, then others.
    apiState.history.hits = [
      hit({ seq: 1, snippet: "one [election]" }),
      hit({ seq: 2, snippet: "two [election]" }),
      hit({ seq: 3, snippet: "three [election]" }),
      hit({ seq: 4, snippet: "four [election]" }),
      ...Array.from({ length: 8 }, (_, i) =>
        hit({ ref: `t${i}`, thread_id: `t${i}`, title: `Conversation ${i}` }),
      ),
    ];
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();
    // One row for the chatty thread + four others = the full five, not one.
    expect(labels().filter((l) => l.includes("S-corp election timing"))).toHaveLength(1);
    expect(labels().filter((l) => l.includes("Conversation "))).toHaveLength(4);
  });

  it("a request the user has typed past is aborted, not left to finish", async () => {
    apiState.history.deferred = () => {};
    render(<CommandPalette />);
    await open();
    await type("election");
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    expect(apiState.signals).toHaveLength(1);
    expect(apiState.signals[0].aborted).toBe(false);

    await type("election timing");
    expect(apiState.signals[0].aborted).toBe(true);
  });

  it("closing the palette aborts whatever it had in flight", async () => {
    apiState.history.deferred = () => {};
    render(<CommandPalette />);
    await open();
    await type("election");
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    await act(async () => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    expect(apiState.signals[0].aborted).toBe(true);
  });

  it("clearing the query back down drops the lane and stops asking", async () => {
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();
    expect(screen.getByText("In your conversations")).toBeInTheDocument();

    await type("");
    await settle();
    expect(screen.queryByText("In your conversations")).not.toBeInTheDocument();
    expect(historyCalls()).toHaveLength(1);
  });

  it("typed, cleared and retyped inside the window is still ONE request", async () => {
    render(<CommandPalette />);
    await open();
    await type("election");
    await act(async () => {
      vi.advanceTimersByTime(100); // not long enough to fire
    });
    await type("");
    await act(async () => {
      vi.advanceTimersByTime(50);
    });
    await type("election"); // the same string again, still inside the window
    await settle();
    expect(historyCalls()).toEqual(["/search/history?q=election&limit=20"]);
  });

  it("unmounting mid-debounce fires nothing and throws nothing", async () => {
    const view = render(<CommandPalette />);
    await open();
    await type("election");
    await act(async () => {
      view.unmount(); // the timer is still pending
    });
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(historyCalls()).toEqual([]);
  });
});

// ── Merge integrity ──────────────────────────────────────────────────────────

describe("the lane is merged, never ranked", () => {
  it("no history row is ever handed to the client-side scorer", async () => {
    apiState.data["/chat/threads"] = {
      threads: [{ id: "t-usage", title: "Usage notes", updated_at: null }],
    };
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();
    // The lane is on screen...
    expect(screen.getByText("In your conversations")).toBeInTheDocument();
    // ...and the ranker still never saw a single row of it. Pages, actions,
    // skills, threads and projects are its whole diet.
    expect(rankerSpy.seen.length).toBeGreaterThan(0);
    expect(rankerSpy.seen.filter((i) => i.kind === "history")).toEqual([]);
    expect(rankerSpy.seen.filter((i) => i.id.startsWith("history:"))).toEqual([]);
  });

  it("the header sits exactly on the first lane row, however many matched", async () => {
    apiState.data["/chat/threads"] = {
      threads: [{ id: "t-usage", title: "Usage notes", updated_at: null }],
    };
    apiState.history.hits = [hit({ title: "Zed conversation" })];
    render(<CommandPalette />);
    await open();
    // "memory" matches several pages/deep links; "usage" matches fewer. The
    // header index is arithmetic over matched.length, so it has to survive the
    // count changing underneath it.
    for (const q of ["memory", "usage", "redact"]) {
      await type(q);
      await settle();
      const header = screen.getByText("In your conversations");
      // The header renders inside the wrapper of the row it labels.
      const labelled = header.parentElement?.querySelector('[role="option"]');
      expect(labelled?.textContent).toContain("Zed conversation");
      // ...and the ask row is still the last thing in the box.
      expect(labels()[labels().length - 1]).toContain("Ask Iron Jarvis");
    }
  });
});

// ── Ordering ─────────────────────────────────────────────────────────────────

describe("where the lane sits", () => {
  it("below the name matches, above the ask row, under its own header", async () => {
    apiState.data["/chat/threads"] = {
      threads: [{ id: "t-usage", title: "Usage notes", updated_at: "2026-07-20T10:00:00Z" }],
    };
    apiState.history.hits = [hit({ title: "S-corp election timing" })];
    render(<CommandPalette />);
    await open();
    await type("usage");
    await settle();

    const rows = labels();
    const lane = rows.findIndex((r) => r.includes("S-corp election timing"));
    const ask = rows.findIndex((r) => r.includes("Ask Iron Jarvis"));
    expect(lane).toBeGreaterThan(0); // something matched by name above it
    expect(ask).toBe(rows.length - 1);
    expect(lane).toBeLessThan(ask);
    expect(screen.getByText("In your conversations")).toBeInTheDocument();
  });

  it("caps at five conversations however many come back", async () => {
    apiState.history.hits = Array.from({ length: 12 }, (_, i) =>
      hit({ ref: `t${i}`, thread_id: `t${i}`, title: `Conversation ${i}` }),
    );
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();
    expect(labels().filter((l) => l.includes("Conversation "))).toHaveLength(5);
  });

  it("never repeats a conversation the title match already offered", async () => {
    apiState.data["/chat/threads"] = {
      threads: [{ id: "t-scorp", title: "S-corp election timing", updated_at: null }],
    };
    apiState.history.hits = [hit()]; // same thread id, so the same deep link
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();
    expect(labels().filter((l) => l.includes("S-corp election timing"))).toHaveLength(1);
  });

  it("two hits from the SAME conversation collapse to one row", async () => {
    apiState.history.hits = [hit({ seq: 2 }), hit({ seq: 9, snippet: "another line" })];
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();
    expect(labels().filter((l) => l.includes("S-corp election timing"))).toHaveLength(1);
  });
});

// ── What a row says and where it goes ────────────────────────────────────────

describe("a conversation row", () => {
  it("shows the title, the matched snippet, a kind badge and when it happened", async () => {
    apiState.history.hits = [hit({ at: new Date(Date.now() - 3 * 3600_000).toISOString() })];
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();

    const row = options().find((o) => o.textContent?.includes("S-corp election timing"));
    expect(row).toBeTruthy();
    expect(row!.textContent).toContain("we should file the");
    expect(row!.textContent).toContain("Chat");
    expect(row!.textContent).toContain("3h ago");
  });

  it("marks the matched words instead of printing the index's brackets", async () => {
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();

    const row = options().find((o) => o.textContent?.includes("S-corp election timing"))!;
    const marks = row.querySelectorAll("mark");
    expect(marks).toHaveLength(1);
    expect(marks[0].textContent).toBe("election");
    // The markers themselves are gone — they were structure, not content.
    expect(row.textContent).not.toContain("[election]");
  });

  it("renders a snippet that contains markup as literal text, never as HTML", async () => {
    apiState.history.hits = [
      hit({ snippet: "<img src=x onerror=alert(1)> and <b>bold</b> [safe]" }),
    ];
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();

    const row = options().find((o) => o.textContent?.includes("S-corp election timing"))!;
    expect(row.querySelector("img")).toBeNull();
    expect(row.querySelector("b")).toBeNull();
    expect(row.textContent).toContain("<img src=x onerror=alert(1)>");
    expect(row.textContent).toContain("<b>bold</b>");
  });

  it("survives a snippet whose brackets are broken, empty or nested", async () => {
    for (const snippet of ["unclosed [marker", "stray ] bracket", "empty [] marker", "[a [b] c]"]) {
      apiState.history.hits = [hit({ snippet })];
      render(<CommandPalette />);
      await open();
      await type("election");
      await settle();
      expect(
        options().some((o) => o.textContent?.includes("S-corp election timing")),
      ).toBe(true);
      cleanup();
    }
  });

  it("never leaks a half-marker when a long snippet is cut short", async () => {
    // Arithmetic on purpose: 144 characters of filler put the opening "[" at
    // index 144, so a 150-character clip of the RAW string cuts between the
    // marker and its closing bracket. That left a bare "[" in the visible text
    // and threw away the highlight it opened ("…y [elec…").
    apiState.history.hits = [hit({ snippet: `${"y ".repeat(72)}[election] tail` })];
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();
    const row = options().find((o) => o.textContent?.includes("S-corp election timing"))!;
    const line = row.querySelectorAll("span")[2]?.textContent ?? "";
    expect(row.textContent).toContain("…");
    expect(line).not.toContain("[");
    expect(line).not.toContain("]");
    // The surviving half of the term is still marked, not demoted to plain text.
    expect(row.querySelector("mark")?.textContent).toContain("elec");
  });

  it("spends its length budget on words, not on the index's markers", async () => {
    // 40 marked terms: under the old order the markers ate 80 characters of a
    // 150-character budget, so the reader saw barely half a line.
    apiState.history.hits = [hit({ snippet: "[ab] ".repeat(40) })];
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();
    const row = options().find((o) => o.textContent?.includes("S-corp election timing"))!;
    const marks = row.querySelectorAll("mark");
    expect(marks.length).toBeGreaterThan(30);
    expect(row.querySelectorAll("span")[2]?.textContent).not.toContain("[");
  });

  it("survives snippets that are nothing but markers", async () => {
    const hostile = [
      "[".repeat(50) + "]".repeat(50),
      "]".repeat(50),
      "[a]".repeat(80),
      "[🎉🙂]",
      "[]".repeat(60),
      "[",
      "]",
    ];
    for (const snippet of hostile) {
      apiState.history.hits = [hit({ snippet })];
      render(<CommandPalette />);
      await open();
      await type("election");
      await settle();
      expect(
        options().some((o) => o.textContent?.includes("S-corp election timing")),
      ).toBe(true);
      cleanup();
    }
  });

  it("dates a thread the same whether or not the daemon sent a zone", async () => {
    // SQLite hands the daemon back NAIVE datetimes, so /chat/threads can send
    // "…T02:00:00" where the index sends an offset-bearing string for the very
    // same instant. Read as local time the naive one lands on the wrong day —
    // and the palette would print two different dates for one conversation.
    apiState.data["/chat/threads"] = {
      threads: [
        { id: "t-naive", title: "Zed naive", updated_at: "2026-07-21T02:00:00" },
        { id: "t-zoned", title: "Zed zoned", updated_at: "2026-07-21T02:00:00Z" },
      ],
    };
    render(<CommandPalette />);
    await open();
    await type("Zed");
    await settle();
    const dates = ["Zed naive", "Zed zoned"].map(
      (t) =>
        options()
          .find((o) => o.textContent?.includes(t))!
          .textContent!.replace(t, ""),
    );
    expect(dates[0]).toBe(dates[1]);
  });

  it("degrades an untitled or dateless hit rather than dropping it", async () => {
    apiState.history.hits = [hit({ title: "", at: null })];
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();
    expect(labels().some((l) => l.includes("(untitled)"))).toBe(true);
  });

  it("drops a hit with no kind we can render or no id to open", async () => {
    apiState.history.hits = [
      hit({ kind: "wat", title: "Unknown kind" }),
      hit({ ref: "", thread_id: "", title: "No target" }),
    ];
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();
    expect(screen.queryByText("In your conversations")).not.toBeInTheDocument();
    expect(labels().some((l) => l.includes("Unknown kind") || l.includes("No target"))).toBe(
      false,
    );
  });
});

describe("Enter opens the conversation the row is about", () => {
  const cases: [string, string, string][] = [
    ["chat", "t-scorp", "/chat?thread=t-scorp"],
    ["comm", "c-9", "/chat?thread=c-9"],
    ["round", "r-9", "/agents?thread=r-9"],
    ["session", "s-9", "/sessions/s-9"],
  ];
  for (const [kind, ref, href] of cases) {
    it(`a ${kind} hit navigates to ${href}`, async () => {
      apiState.history.hits = [hit({ kind, ref, thread_id: ref, title: "Zed conversation" })];
      render(<CommandPalette />);
      await open();
      await type("election");
      await settle();

      const idx = labels().findIndex((l) => l.includes("Zed conversation"));
      expect(idx).toBeGreaterThanOrEqual(0);
      await press("ArrowDown", idx);
      await press("Enter");
      expect(routerMock.push).toHaveBeenCalledWith(href);
    });
  }

  it("clicking does the same thing as Enter", async () => {
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();
    const row = options().find((o) => o.textContent?.includes("S-corp election timing"))!;
    await act(async () => {
      fireEvent.click(row);
    });
    expect(routerMock.push).toHaveBeenCalledWith("/chat?thread=t-scorp");
  });

  it("an id with url-hostile characters is encoded", async () => {
    apiState.history.hits = [hit({ ref: "a b/c?d", thread_id: "a b/c?d" })];
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();
    const row = options().find((o) => o.textContent?.includes("S-corp election timing"))!;
    await act(async () => {
      fireEvent.click(row);
    });
    expect(routerMock.push).toHaveBeenCalledWith("/chat?thread=a%20b%2Fc%3Fd");
  });
});

// ── Races ────────────────────────────────────────────────────────────────────

describe("a late response can never overwrite a newer one", () => {
  it("the answer to an abandoned query is dropped on arrival", async () => {
    apiState.history.deferred = () => {};
    render(<CommandPalette />);
    await open();
    await type("first query");
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    const resolveFirst = apiState.history.deferred!;

    // The user moves on before the first answer lands.
    apiState.history.deferred = null;
    apiState.history.hits = [hit({ title: "Second answer" })];
    await type("second query");
    await settle();
    expect(labels().some((l) => l.includes("Second answer"))).toBe(true);

    // ...and now the stale one finally arrives.
    await act(async () => {
      resolveFirst([hit({ ref: "t-old", thread_id: "t-old", title: "First answer" })]);
    });
    expect(labels().some((l) => l.includes("First answer"))).toBe(false);
    expect(labels().some((l) => l.includes("Second answer"))).toBe(true);
  });

  it("an answer that arrives after a close/reopen cycle is not shown", async () => {
    // The nastiest shape of the race: the palette is closed with a request in
    // flight and reopened before it lands, so the component never unmounted and
    // the promise still has a live setState to call.
    apiState.history.deferred = () => {};
    render(<CommandPalette />);
    await open();
    await type("election");
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    const resolveStale = apiState.history.deferred!;

    await act(async () => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    apiState.history.deferred = null;
    await open();
    await settle();
    // A reopened palette starts on the empty screen, so there is nothing the
    // stale answer could legitimately be an answer TO.
    expect(box().value).toBe("");
    await act(async () => {
      resolveStale([hit({ ref: "t-stale", thread_id: "t-stale", title: "STALE ANSWER" })]);
    });
    expect(labels().some((l) => l.includes("STALE ANSWER"))).toBe(false);
    expect(screen.queryByText("In your conversations")).not.toBeInTheDocument();
  });

  it("results landing while the user is arrowing leave the highlight where it was", async () => {
    apiState.history.deferred = () => {};
    render(<CommandPalette />);
    await open();
    await type("usage");
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    const resolve = apiState.history.deferred!;

    await press("ArrowDown"); // the user is already navigating the title matches
    const before = options()[1].textContent;

    await act(async () => {
      resolve([hit({ title: "Late conversation" })]);
    });

    // The lane appended BELOW the matches, so row 1 is still the same row and
    // still the selected one — no jump, no re-render of the highlight.
    expect(options()[1].textContent).toBe(before);
    expect(options()[1].getAttribute("aria-selected")).toBe("true");
    expect(labels().some((l) => l.includes("Late conversation"))).toBe(true);
  });
});

// ── Degrading ────────────────────────────────────────────────────────────────

describe("an older or unhappy daemon is silent, not broken", () => {
  it("a 404 hides the lane and is never asked again", async () => {
    apiState.history.failStatus = 404;
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();

    expect(screen.queryByText("In your conversations")).not.toBeInTheDocument();
    expect(historyCalls()).toHaveLength(1);

    await type("election timing");
    await settle();
    expect(historyCalls()).toHaveLength(1); // the lane switched itself off

    // ...and the palette itself is untouched: name matching and the ask row work.
    await type("redact");
    await settle();
    expect(labels()[0]).toContain("Documents → Redact PII");
    expect(labels()[labels().length - 1]).toContain("Ask Iron Jarvis");
  });

  it("a 405 switches the lane off too — the path is there, the verb isn't", async () => {
    // A daemon that answers 405 for GET /search/history is as incapable of
    // serving this lane as one that 404s, and it will still be 405 on the next
    // keystroke. app/agents/page.tsx already reads the same pair as "this
    // daemon doesn't have that feature".
    apiState.history.failStatus = 405;
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();
    await type("election timing");
    await settle();
    await type("election timing rules");
    await settle();
    expect(historyCalls()).toHaveLength(1);
    expect(screen.queryByText("In your conversations")).not.toBeInTheDocument();
  });

  it("an offline daemon keeps trying — it is coming back", async () => {
    // status 0 is what lib/api reports for a network failure. Latching on it
    // would cost the lane for the rest of the session over a daemon restart.
    apiState.history.failStatus = 0;
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();
    await type("election timing");
    await settle();
    expect(historyCalls()).toHaveLength(2);
  });

  it("a 500 or an offline daemon shows no lane and no error row", async () => {
    apiState.history.failStatus = 500;
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();

    expect(screen.queryByText("In your conversations")).not.toBeInTheDocument();
    expect(labels().some((l) => /error|failed|sorry/i.test(l))).toBe(false);
    // Unlike a 404, a 500 is transient — the next query still tries.
    await type("election timing");
    await settle();
    expect(historyCalls()).toHaveLength(2);
  });

  it("an honest empty result set shows no header at all", async () => {
    apiState.history.hits = [];
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();
    expect(screen.queryByText("In your conversations")).not.toBeInTheDocument();
  });

  it("a malformed body is treated as no results, not as a crash", async () => {
    // @ts-expect-error deliberately wrong shape — the daemon is not trusted
    apiState.history.hits = "not an array";
    render(<CommandPalette />);
    await open();
    await type("election");
    await settle();
    expect(screen.queryByText("In your conversations")).not.toBeInTheDocument();
  });
});
