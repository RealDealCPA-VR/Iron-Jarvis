import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

/**
 * CommandPalette — the front door.
 *
 * lib/palette.test.ts already pins the RANKING. What is pinned here is the part
 * that ranking can't see and that fails silently in the running app:
 *
 *  - the open contract. "ij:open-palette" must OPEN, never toggle: the visible
 *    Search button in the TitleBar dispatches it, and a toggle makes clicking
 *    "Search" while the search is open the thing that closes the search.
 *  - the deep links. A `?focus=` row is only worth having if Enter really emits
 *    that query string — and the memory row must emit BOTH params, because
 *    /memory?focus=add-base alone lands on the default (working) scope where no
 *    such control exists, and the user concludes the search lied.
 *  - the ask row. Search is never a dead end: a query that matches nothing
 *    still offers to answer it.
 *  - the selection invariant. aria-activedescendant must always name a row that
 *    is actually on screen; a stranded index means Enter goes nowhere.
 *  - the fetch gate. One flaky endpoint must not re-request the two that
 *    already succeeded, and must not duplicate a request still in flight.
 */

// ── Mocks ────────────────────────────────────────────────────────────────────

// useRouter has no App Router provider in a bare render; a hoisted box lets the
// assertions read the exact href every Enter/click produced.
const routerMock = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }));

/** The fake daemon. `calls` is the whole point of the fetch-gate tests: it is
 *  the record of how many times each endpoint was actually asked. */
const apiState = vi.hoisted(() => ({
  calls: [] as string[],
  /** Paths that reject (a stopped daemon). */
  fail: new Set<string>(),
  /** Paths whose promise never settles (a request still in flight). */
  hang: new Set<string>(),
  data: {} as Record<string, unknown>,
}));

vi.mock("@/lib/api", () => ({
  get: (path: string) => {
    apiState.calls.push(path);
    if (apiState.hang.has(path)) return new Promise(() => {});
    if (apiState.fail.has(path)) return Promise.reject(new Error("daemon unreachable"));
    return Promise.resolve(apiState.data[path] ?? {});
  },
}));

// framer-motion's exit animation keeps a closed dialog mounted for ~150ms,
// which would make "Esc closes it" a race against a timer. Strip the animation
// and keep the DOM: presence, not tweening, is what these tests are about.
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial",
    "animate",
    "exit",
    "transition",
    "variants",
    "layout",
    "layoutId",
    "whileHover",
    "whileTap",
    "whileFocus",
    "drag",
  ]);
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

// ── Fixtures + helpers ───────────────────────────────────────────────────────

const SKILLS = {
  skills: [
    {
      name: "pii-redaction",
      description: "Scrub names, SSNs and account numbers out of a document.",
    },
  ],
};
const THREADS = {
  threads: [
    { id: "t1", title: "Q2 depreciation schedule", updated_at: "2026-07-20T10:00:00Z" },
    { id: "t2", title: "Client onboarding checklist", updated_at: "2026-07-19T10:00:00Z" },
    { id: "t3", title: "", updated_at: "2026-07-18T10:00:00Z" },
  ],
};
const PROJECTS = { projects: [{ id: "acme", name: "Acme 1120S", brief: "Corp return, FY2025." }] };

beforeEach(() => {
  apiState.calls = [];
  apiState.fail = new Set();
  apiState.hang = new Set();
  apiState.data = {
    "/skills": SKILLS,
    "/chat/threads": THREADS,
    "/projects": PROJECTS,
  };
  routerMock.push.mockReset();
});

afterEach(() => {
  cleanup();
});

/** Dispatch the TitleBar's event and let the three lazy fetches settle. */
async function openViaEvent() {
  await act(async () => {
    window.dispatchEvent(new Event("ij:open-palette"));
  });
}

async function pressCtrlK() {
  await act(async () => {
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
  });
}

const box = () => screen.getByRole("combobox") as HTMLInputElement;
const options = () => screen.queryAllByRole("option");
const labels = () => options().map((o) => o.textContent || "");
const isOpen = () => screen.queryByRole("combobox") !== null;

async function type(value: string) {
  await act(async () => {
    fireEvent.change(box(), { target: { value } });
  });
}

async function press(key: string, times = 1) {
  for (let i = 0; i < times; i++) {
    await act(async () => {
      fireEvent.keyDown(box(), { key });
    });
  }
}

/** The single row the keyboard would act on, proven from the DOM rather than
 *  from component state: exactly one option is selected, and the combobox's
 *  aria-activedescendant names that very element. */
function activeOption(): HTMLElement {
  const selected = options().filter((o) => o.getAttribute("aria-selected") === "true");
  expect(selected).toHaveLength(1);
  const descendant = box().getAttribute("aria-activedescendant");
  expect(descendant).toBeTruthy();
  expect(document.getElementById(descendant as string)).toBe(selected[0]);
  return selected[0];
}

// ── The open contract ────────────────────────────────────────────────────────

describe("CommandPalette — how it opens and closes", () => {
  it("starts closed and opens on ij:open-palette", async () => {
    render(<CommandPalette />);
    expect(isOpen()).toBe(false);
    await openViaEvent();
    expect(isOpen()).toBe(true);
  });

  it("ij:open-palette while already open does NOT close it (open, never toggle)", async () => {
    render(<CommandPalette />);
    await openViaEvent();
    await type("redact");

    await openViaEvent();
    expect(isOpen()).toBe(true);
    // And it is the SAME session: a second click on Search must not wipe what
    // the user already typed.
    expect(box().value).toBe("redact");
  });

  it("Ctrl+K still toggles both ways", async () => {
    render(<CommandPalette />);
    await pressCtrlK();
    expect(isOpen()).toBe(true);
    await pressCtrlK();
    expect(isOpen()).toBe(false);
  });

  it("Esc closes and hands focus back to whatever had it", async () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    render(<CommandPalette />);

    await openViaEvent();
    expect(isOpen()).toBe(true);

    await act(async () => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    expect(isOpen()).toBe(false);
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it("unmounting removes BOTH window listeners", async () => {
    // Asserted on the actual removeEventListener traffic: a leaked listener is
    // invisible from the DOM (a detached component just no-ops), so "nothing
    // rendered" would pass whether or not the cleanup ran.
    const original = window.removeEventListener.bind(window);
    const removed: string[] = [];
    const spy = vi
      .spyOn(window, "removeEventListener")
      .mockImplementation(((type: string, cb: EventListener, opts?: unknown) => {
        removed.push(type);
        original(type, cb, opts as never);
      }) as unknown as typeof window.removeEventListener);

    const { unmount } = render(<CommandPalette />);
    unmount();
    spy.mockRestore();

    expect(removed).toContain("keydown");
    expect(removed).toContain("ij:open-palette");

    // ...and the palette stays gone when either one is poked afterwards.
    await act(async () => {
      window.dispatchEvent(new Event("ij:open-palette"));
      fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    });
    expect(isOpen()).toBe(false);
  });
});

// ── Deep links ───────────────────────────────────────────────────────────────

describe("CommandPalette — deep links reach mid-page capabilities", () => {
  it("typing 'redact' puts the Documents deep link first and Enter navigates to ?focus=redact", async () => {
    render(<CommandPalette />);
    await openViaEvent();
    await type("redact");

    expect(activeOption().textContent).toContain("Documents → Redact PII");
    await press("Enter");
    expect(routerMock.push).toHaveBeenCalledWith("/documents?focus=redact");
  });

  it("the memory deep link carries BOTH params — scope picks the tab, focus picks the control", async () => {
    render(<CommandPalette />);
    await openViaEvent();
    await type("memory base");

    expect(activeOption().textContent).toContain("Add a memory base");
    await press("Enter");
    const href = routerMock.push.mock.calls[0][0] as string;
    expect(href).toContain("scope=longterm");
    expect(href).toContain("focus=add-base");
    expect(href).toBe("/memory?scope=longterm&focus=add-base");
  });

  it("clicking a row navigates exactly like Enter does", async () => {
    render(<CommandPalette />);
    await openViaEvent();
    await type("redact");

    await act(async () => {
      fireEvent.click(options()[0]);
    });
    expect(routerMock.push).toHaveBeenCalledWith("/documents?focus=redact");
    expect(isOpen()).toBe(false);
  });

  it("an action row with a handler fires its event instead of navigating", async () => {
    render(<CommandPalette />);
    await openViaEvent();
    await type("switch model");

    const seen = vi.fn();
    window.addEventListener("ij:open-switcher", seen);
    await press("Enter");
    window.removeEventListener("ij:open-switcher", seen);

    expect(seen).toHaveBeenCalledTimes(1);
    expect(routerMock.push).not.toHaveBeenCalled();
    expect(isOpen()).toBe(false);
  });
});

// ── Live rows ────────────────────────────────────────────────────────────────

describe("CommandPalette — the live catalogue", () => {
  it("surfaces a skill by the string you would type in chat", async () => {
    render(<CommandPalette />);
    await openViaEvent();
    await type("redaction");

    expect(labels()[0]).toContain("/pii-redaction");
    await press("Enter");
    expect(routerMock.push).toHaveBeenCalledWith("/chat?skill=pii-redaction");
  });

  it("the empty screen teaches the verbs, then offers recent chats", async () => {
    render(<CommandPalette />);
    await openViaEvent();

    expect(screen.getByText("Do something")).toBeInTheDocument();
    expect(screen.getByText("Recent chats")).toBeInTheDocument();
    // Five actions + the three fixture threads, and a blank title degrades to a
    // readable placeholder rather than an empty row.
    expect(options()).toHaveLength(8);
    expect(labels()[0]).toContain("New session");
    expect(labels()[5]).toContain("Q2 depreciation schedule");
    expect(labels()[7]).toContain("(untitled)");
  });
});

// ── The ask row ──────────────────────────────────────────────────────────────

describe("CommandPalette — search is never a dead end", () => {
  it("a query that matches nothing renders exactly the ask row, and Enter opens a chat", async () => {
    render(<CommandPalette />);
    await openViaEvent();
    await type("zzz qqq?");

    expect(options()).toHaveLength(1);
    expect(labels()[0]).toContain("Ask Iron Jarvis");
    await press("Enter");
    // The query rides along url-encoded, not raw.
    expect(routerMock.push).toHaveBeenCalledWith("/chat?ask=zzz%20qqq%3F");
  });

  it("the ask row is a real option, always last, even when there are matches", async () => {
    render(<CommandPalette />);
    await openViaEvent();
    await type("redact");

    const rows = labels();
    expect(rows.length).toBeGreaterThan(1);
    expect(rows[rows.length - 1]).toContain("Ask Iron Jarvis");
    // ...and reachable by keyboard like any other row.
    await press("ArrowDown", rows.length - 1);
    expect(activeOption().textContent).toContain("Ask Iron Jarvis");
  });
});

// ── Selection ────────────────────────────────────────────────────────────────

describe("CommandPalette — the selection can never strand", () => {
  it("arrowing deep into a long list then narrowing the query leaves a live selection", async () => {
    render(<CommandPalette />);
    await openViaEvent();

    await type("e");
    expect(options().length).toBeGreaterThanOrEqual(8);
    await press("ArrowDown", 7);
    expect(options().indexOf(activeOption())).toBe(7);

    // Now only a handful of rows survive. Two mechanisms have to agree: the
    // keystroke resets the highlight to the top result (that is what the user
    // asked for by retyping), and activeIdx's clamp guarantees that even a
    // shrink NO keystroke caused can't leave the index past the end. What is
    // asserted is the union of both — a live, visible, single selection.
    await type("endpoints");
    expect(options().length).toBeLessThan(8);
    expect(options().indexOf(activeOption())).toBe(0);
    expect(activeOption().textContent).toContain("Your endpoints");
  });

  it("clearing the query swaps in the empty screen without losing the highlight", async () => {
    render(<CommandPalette />);
    await openViaEvent();
    await type("e");
    await press("ArrowDown", 6);

    await type("");
    expect(options()).toHaveLength(8);
    expect(options().indexOf(activeOption())).toBe(0);
  });

  it("ArrowDown stops at the last row and ArrowUp stops at the first", async () => {
    render(<CommandPalette />);
    await openViaEvent();
    await type("endpoints");
    const total = options().length;

    await press("ArrowDown", total + 5);
    expect(options().indexOf(activeOption())).toBe(total - 1);
    await press("ArrowUp", total + 5);
    expect(options().indexOf(activeOption())).toBe(0);
  });

  it("every rendered row has a unique id and the listbox is wired to the input", async () => {
    render(<CommandPalette />);
    await openViaEvent();
    await type("redact");

    const ids = options().map((o) => o.id);
    expect(ids.every(Boolean)).toBe(true);
    expect(new Set(ids).size).toBe(ids.length);
    expect(box().getAttribute("aria-controls")).toBe("ij-palette-list");
    expect(screen.getByRole("listbox").id).toBe("ij-palette-list");
  });
});

// ── Navigation side effects ──────────────────────────────────────────────────

describe("CommandPalette — what a reopened palette shows", () => {
  it("navigating closes it AND clears the query, so the next open is a clean box", async () => {
    render(<CommandPalette />);
    await openViaEvent();
    await type("usage");
    await press("Enter");
    expect(routerMock.push).toHaveBeenCalledWith("/usage");
    expect(isOpen()).toBe(false);

    await openViaEvent();
    expect(box().value).toBe("");
    // The empty screen, not last search's results with a stale highlight.
    expect(options()).toHaveLength(8);
    expect(options().indexOf(activeOption())).toBe(0);
  });

  it("closing with Esc also leaves a clean box behind", async () => {
    render(<CommandPalette />);
    await openViaEvent();
    await type("redact");
    await act(async () => {
      fireEvent.keyDown(window, { key: "Escape" });
    });

    await openViaEvent();
    expect(box().value).toBe("");
  });
});

// ── The lazy fetch gate ──────────────────────────────────────────────────────

describe("CommandPalette — the live catalogue is fetched once, per source", () => {
  const countOf = (path: string) => apiState.calls.filter((p) => p === path).length;

  it("fetches nothing until the first open", async () => {
    render(<CommandPalette />);
    await act(async () => {});
    expect(apiState.calls).toEqual([]);

    await openViaEvent();
    expect(countOf("/skills")).toBe(1);
    expect(countOf("/chat/threads")).toBe(1);
    expect(countOf("/projects")).toBe(1);
  });

  it("a source that succeeded — even with an EMPTY list — is never re-asked", async () => {
    apiState.data["/skills"] = { skills: [] };
    apiState.data["/projects"] = { projects: [] };
    render(<CommandPalette />);

    await openViaEvent();
    await pressCtrlK();
    await openViaEvent();

    expect(countOf("/skills")).toBe(1);
    expect(countOf("/projects")).toBe(1);
    expect(countOf("/chat/threads")).toBe(1);
  });

  it("only the source that FAILED is retried on the next open", async () => {
    apiState.fail.add("/projects");
    render(<CommandPalette />);

    await openViaEvent();
    await pressCtrlK();
    await openViaEvent();

    expect(countOf("/projects")).toBe(2);
    // The regression: a shared gate reopened by any failure re-requested these
    // two catalogues that had already answered.
    expect(countOf("/skills")).toBe(1);
    expect(countOf("/chat/threads")).toBe(1);
  });

  it("a request still IN FLIGHT is not duplicated by a reopen, even when a sibling failed", async () => {
    apiState.hang.add("/skills");
    apiState.fail.add("/projects");
    render(<CommandPalette />);

    await openViaEvent();
    await pressCtrlK();
    await openViaEvent();

    // /skills never settled, so it is still gated — one request, not two.
    expect(countOf("/skills")).toBe(1);
    expect(countOf("/projects")).toBe(2);
  });

  it("a dead daemon degrades to silence: static pages, actions and deep links still search", async () => {
    apiState.fail.add("/skills");
    apiState.fail.add("/chat/threads");
    apiState.fail.add("/projects");
    render(<CommandPalette />);

    await openViaEvent();
    // No "Recent chats" section to show, but the teaching actions remain.
    expect(options()).toHaveLength(5);
    expect(screen.queryByText("Recent chats")).not.toBeInTheDocument();

    await type("redact");
    expect(activeOption().textContent).toContain("Documents → Redact PII");
    await press("Enter");
    expect(routerMock.push).toHaveBeenCalledWith("/documents?focus=redact");
  });
});
