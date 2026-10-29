/**
 * v1.406.0 part 7 — TWO CODING SESSIONS SIDE BY SIDE (the user: "two
 * sessions side by side", like Claude Code desktop's split view).
 *
 * The rules (lib/codeSplit) and the view around them (IjHarnessView), with
 * the session itself a marker that records what the view hands it:
 *  - a row's "Open beside", Shift+Enter on a row, and a row dropped on the
 *    right edge each open a second column;
 *  - at most two; opening one of the two FOCUSES it; another replaces the
 *    focused column's session;
 *  - the panes belong to the focused column (one shared pane column);
 *  - × returns to one column, the other session kept;
 *  - the address carries it (`&beside=<id>`) and this window remembers it;
 *  - a narrow window shows one column and a tab per session (the other kept
 *    mounted, hidden + inert, and not "looked at").
 * The REAL sessions (two streams, keys, looking) are in
 * code-split-live-v1406.test.tsx.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return { ApiError, gets: {} as Record<string, unknown> };
});
vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) =>
    p in api.gets ? Promise.resolve(api.gets[p]) : Promise.reject(new api.ApiError(`unmocked ${p}`, 404)),
  post: (p: string, b?: unknown) =>
    p === "/build/sessions"
      ? Promise.resolve({ id: "new1", title: "New session", folder: (b as { folder: string }).folder })
      : Promise.resolve({}),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div /> }));
// The session is a marker: what the view hands it.
vi.mock("@/components/harness/HarnessSession", () => ({
  HarnessSession: ({
    threadId,
    onScreen,
    panesInto,
  }: {
    threadId: string;
    onScreen?: boolean;
    panesInto?: HTMLElement | null;
  }) => (
    <div
      data-testid={`session-stub-${threadId}`}
      data-on-screen={String(onScreen !== false)}
      data-panes={panesInto === undefined ? "own" : panesInto ? "shared" : "none"}
    />
  ),
}));

import { IjHarnessView } from "@/components/harness/IjHarnessView";
import { writeViewToAddress } from "@/lib/chatSurface";
import {
  ONE,
  SESSION_DRAG_TYPE,
  SPLIT_KEY,
  __resetSplitMemory,
  closeSide,
  dropSession,
  focusSide,
  focusedSession,
  keepSplit,
  openBeside,
  openSession,
  ownsKeys,
  rememberedSplit,
  splitFromSearch,
  type CodeSplit,
} from "@/lib/codeSplit";

const LIST = {
  folders: [
    {
      path: "C:\\work\\shop",
      name: "Shop site",
      is_project: false,
      project_id: null,
      sessions: [
        { id: "a1", title: "Add a cart", updated_at: "2026-10-22T10:00:00", running: true, waiting: false, archived: false },
        { id: "a2", title: "Fix the footer", updated_at: "2026-10-21T10:00:00", running: false, waiting: true, archived: false },
        { id: "a3", title: "Tidy styles", updated_at: "2026-10-20T10:00:00", running: false, waiting: false, archived: false },
      ],
    },
  ],
};

const terminal = { find: () => null, open: async () => null, render: () => null, close: () => {} };

let wide = true;
function stubMatchMedia() {
  const listeners = new Set<() => void>();
  vi.stubGlobal("matchMedia", (q: string) => ({
    media: q,
    get matches() {
      return wide;
    },
    addEventListener: (_: string, f: () => void) => listeners.add(f),
    removeEventListener: (_: string, f: () => void) => listeners.delete(f),
  }));
  return {
    set(next: boolean) {
      wide = next;
      for (const f of listeners) f();
    },
  };
}

beforeEach(() => {
  api.gets = { "/build/sessions": LIST };
  localStorage.clear();
  window.sessionStorage.clear();
  __resetSplitMemory();
  wide = true;
  window.history.replaceState(null, "", "/chat?view=code");
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const address = () => new URL(window.location.href).searchParams;

function renderView(initialSession: string | null = "a1") {
  return render(
    <IjHarnessView initialSession={initialSession} terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />,
  );
}

/* ------------------------------------------------------------- the rules */

describe("the split's rules (lib/codeSplit)", () => {
  const two: CodeSplit = { left: "a", right: "b", focus: "left" };

  it("open beside puts a second session on the right and focuses it; at most two", () => {
    expect(openBeside({ left: "a", right: null, focus: "left" }, "b")).toEqual({ left: "a", right: "b", focus: "right" });
    // A third REPLACES the right column (never three).
    expect(openBeside(two, "c")).toEqual({ left: "a", right: "c", focus: "right" });
    // One of the two: only focused.
    expect(openBeside(two, "a")).toEqual({ ...two, focus: "left" });
    expect(openBeside({ ...two, focus: "left" }, "b")).toEqual({ ...two, focus: "right" });
    // Beside itself: nothing to put beside.
    expect(openBeside({ left: "a", right: null, focus: "left" }, "a")).toEqual({ left: "a", right: null, focus: "left" });
    expect(openBeside(ONE, "a")).toEqual({ left: "a", right: null, focus: "left" });
  });

  it("opening a session focuses it when it is one of the two, else replaces the FOCUSED column's", () => {
    expect(openSession(two, "b")).toEqual({ ...two, focus: "right" });
    expect(openSession({ ...two, focus: "right" }, "a")).toEqual({ ...two, focus: "left" });
    expect(openSession(two, "c")).toEqual({ left: "c", right: "b", focus: "left" });
    expect(openSession({ ...two, focus: "right" }, "c")).toEqual({ left: "a", right: "c", focus: "right" });
    expect(openSession({ left: "a", right: null, focus: "left" }, "c")).toEqual({ left: "c", right: null, focus: "left" });
  });

  it("closing a column returns to one, the other kept; a gone session takes its column", () => {
    expect(closeSide(two, "left")).toEqual({ left: "b", right: null, focus: "left" });
    expect(closeSide({ ...two, focus: "right" }, "right")).toEqual({ left: "a", right: null, focus: "left" });
    expect(dropSession(two, "b")).toEqual({ left: "a", right: null, focus: "left" });
    expect(dropSession({ left: "a", right: null, focus: "left" }, "a")).toEqual(ONE);
    expect(focusedSession({ ...two, focus: "right" })).toBe("b");
    expect(focusSide({ left: "a", right: null, focus: "left" }, "right").focus).toBe("left");
    // An unchanged focus is the SAME object (no re-render on every click).
    expect(focusSide(two, "left")).toBe(two);
  });

  it("the address names the split; the same session twice is one column", () => {
    expect(splitFromSearch("?view=code&session=a&beside=b")).toEqual({ left: "a", right: "b", focus: "left" });
    expect(splitFromSearch("?view=code&session=a&beside=a")).toEqual({ left: "a", right: null, focus: "left" });
    expect(splitFromSearch("?view=code&beside=b")).toBeNull();
  });

  it("going to Chat takes the split out of the address with the session", () => {
    window.history.replaceState(null, "", "/chat?view=code&session=a&beside=b");
    writeViewToAddress("chat");
    expect(address().get("session")).toBeNull();
    expect(address().get("beside")).toBeNull();
  });

  it("the window remembers a split (module first, then sessionStorage); one column is not kept here", () => {
    keepSplit({ left: "a", right: "b", focus: "right" });
    expect(rememberedSplit()).toEqual({ left: "a", right: "b", focus: "right" });
    __resetSplitMemory(); // a reload: the module copy is gone
    expect(rememberedSplit()).toEqual({ left: "a", right: "b", focus: "right" });
    keepSplit({ left: "a", right: null, focus: "left" });
    expect(window.sessionStorage.getItem(SPLIT_KEY)).toBeNull();
    expect(rememberedSplit()).toBeNull();
  });

  it("a key belongs to its target's column, else the focused one; a hidden column owns none; no split owns all", () => {
    document.body.innerHTML = `
      <div id="l" data-split-column="left" data-focused="false"><button id="lb"></button></div>
      <div id="r" data-split-column="right" data-focused="true"><button id="rb"></button></div>
      <div id="h" data-split-column="right" data-focused="true" inert><button id="hb"></button></div>
      <div id="none"><button id="nb"></button></div>`;
    const el = (id: string) => document.getElementById(id)!;
    expect(ownsKeys(el("lb"), document.body)).toBe(false);
    expect(ownsKeys(el("rb"), document.body)).toBe(true);
    expect(ownsKeys(el("lb"), el("lb"))).toBe(true); // its own target wins over the focus mark
    expect(ownsKeys(el("rb"), el("lb"))).toBe(false);
    expect(ownsKeys(el("hb"), document.body)).toBe(false);
    expect(ownsKeys(el("nb"), document.body)).toBe(true);
    expect(ownsKeys(null, document.body)).toBe(true);
    document.body.innerHTML = "";
  });
});

/* ------------------------------------------------------------- the view */

describe("opening a second session beside the first", () => {
  it("the row's Open beside: two columns, each with a header; the shared panes are the focused one's", async () => {
    renderView("a1");
    await screen.findByTestId("session-stub-a1");
    expect(screen.getByTestId("harness-columns")).toHaveAttribute("data-split", "one");
    expect(screen.getByTestId("session-stub-a1")).toHaveAttribute("data-panes", "own");
    // Not on the session already open.
    expect(screen.queryByTestId("harness-open-beside-a1")).toBeNull();
    fireEvent.click(screen.getByTestId("harness-open-beside-a2"));
    await waitFor(() => expect(screen.getByTestId("harness-columns")).toHaveAttribute("data-split", "columns"));
    expect(screen.getByTestId("harness-column-left")).toHaveAttribute("data-session-id", "a1");
    expect(screen.getByTestId("harness-column-right")).toHaveAttribute("data-session-id", "a2");
    expect(screen.getByTestId("harness-column-name-left")).toHaveTextContent("Add a cart");
    expect(screen.getByTestId("harness-column-name-right")).toHaveTextContent("Fix the footer");
    // The new column has the focus: its panes are the shared column's.
    expect(screen.getByTestId("harness-column-right")).toHaveAttribute("data-focused", "true");
    expect(screen.getByTestId("harness-column-focused-right")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("session-stub-a2")).toHaveAttribute("data-panes", "shared"));
    expect(screen.getByTestId("session-stub-a1")).toHaveAttribute("data-panes", "none");
    expect(screen.getByTestId("harness-split-panes-for")).toHaveTextContent("Views for Fix the footer");
    // A click in the left column gives it the focus (and the panes).
    fireEvent.pointerDown(screen.getByTestId("session-stub-a1"));
    await waitFor(() => expect(screen.getByTestId("session-stub-a1")).toHaveAttribute("data-panes", "shared"));
    expect(screen.getByTestId("session-stub-a2")).toHaveAttribute("data-panes", "none");
    expect(screen.getByTestId("harness-split-panes-for")).toHaveTextContent("Views for Add a cart");
    // The list marks the focused one current and the other as beside it.
    expect(screen.getByTestId("harness-session-row-a1")).toHaveAttribute("aria-current", "true");
    expect(screen.getByTestId("harness-session-row-a2")).toHaveAttribute("data-beside", "true");
  });

  it("Shift+Enter on a row opens it beside (the keyboard path); plain Enter does not", async () => {
    renderView("a1");
    await screen.findByTestId("session-stub-a1");
    fireEvent.keyDown(screen.getByTestId("harness-session-row-a3"), { key: "Enter" });
    expect(screen.getByTestId("harness-columns")).toHaveAttribute("data-split", "one");
    fireEvent.keyDown(screen.getByTestId("harness-session-row-a3"), { key: "Enter", shiftKey: true });
    await waitFor(() => expect(screen.getByTestId("harness-column-right")).toHaveAttribute("data-session-id", "a3"));
  });

  it("a row dragged to the right edge opens beside", async () => {
    renderView("a1");
    await screen.findByTestId("session-stub-a1");
    const data = new Map<string, string>();
    const dataTransfer = {
      setData: (t: string, v: string) => data.set(t, v),
      getData: (t: string) => data.get(t) ?? "",
      get types() {
        return [...data.keys()];
      },
      effectAllowed: "",
      dropEffect: "",
    };
    expect(screen.queryByTestId("harness-drop-beside")).toBeNull();
    fireEvent.dragStart(screen.getByTestId("harness-session-row-a2"), { dataTransfer });
    expect(data.get(SESSION_DRAG_TYPE)).toBe("a2");
    const zone = await screen.findByTestId("harness-drop-beside");
    fireEvent.dragOver(zone, { dataTransfer });
    fireEvent.drop(zone, { dataTransfer });
    await waitFor(() => expect(screen.getByTestId("harness-column-right")).toHaveAttribute("data-session-id", "a2"));
    expect(screen.queryByTestId("harness-drop-beside")).toBeNull();
  });

  it("opening a session that is one of the two FOCUSES it; another replaces the focused column", async () => {
    renderView("a1");
    await screen.findByTestId("session-stub-a1");
    fireEvent.click(screen.getByTestId("harness-open-beside-a2"));
    await waitFor(() => expect(screen.getByTestId("harness-column-right")).toHaveAttribute("data-focused", "true"));
    // The list: a1 is the LEFT column — it is focused, nothing replaced.
    fireEvent.click(screen.getByTestId("harness-session-row-a1"));
    await waitFor(() => expect(screen.getByTestId("harness-column-left")).toHaveAttribute("data-focused", "true"));
    expect(screen.getByTestId("harness-column-right")).toHaveAttribute("data-session-id", "a2");
    // A link (the bell) to the right one focuses it too.
    act(() => {
      window.dispatchEvent(new CustomEvent("ij:open-build-session", { detail: { id: "a2" } }));
    });
    await waitFor(() => expect(screen.getByTestId("harness-column-right")).toHaveAttribute("data-focused", "true"));
    // A third session replaces the FOCUSED (right) column's.
    fireEvent.click(screen.getByTestId("harness-session-row-a3"));
    await waitFor(() => expect(screen.getByTestId("harness-column-right")).toHaveAttribute("data-session-id", "a3"));
    expect(screen.getByTestId("harness-column-left")).toHaveAttribute("data-session-id", "a1");
    expect(screen.queryByTestId("session-stub-a2")).toBeNull();
  });

  it("× on a column returns to one, the other session kept (and its session not reopened)", async () => {
    renderView("a1");
    await screen.findByTestId("session-stub-a1");
    fireEvent.click(screen.getByTestId("harness-open-beside-a2"));
    await screen.findByTestId("harness-column-right");
    const kept = screen.getByTestId("session-stub-a2");
    fireEvent.click(screen.getByRole("button", { name: "Close Add a cart" }));
    await waitFor(() => expect(screen.getByTestId("harness-columns")).toHaveAttribute("data-split", "one"));
    expect(screen.queryByTestId("session-stub-a1")).toBeNull();
    // The very same element: the remaining session was never remounted.
    expect(screen.getByTestId("session-stub-a2")).toBe(kept);
    expect(kept).toHaveAttribute("data-panes", "own");
    await waitFor(() => {
      expect(address().get("session")).toBe("a2");
      expect(address().get("beside")).toBeNull();
    });
  });

  it("opening beside never remounts the first session", async () => {
    renderView("a1");
    const first = await screen.findByTestId("session-stub-a1");
    fireEvent.click(screen.getByTestId("harness-open-beside-a2"));
    await screen.findByTestId("session-stub-a2");
    expect(screen.getByTestId("session-stub-a1")).toBe(first);
  });
});

describe("the split rides the address and this window's memory", () => {
  it("is written to the address (`&beside=`) and read back from it on a load", async () => {
    const v = renderView("a1");
    await screen.findByTestId("session-stub-a1");
    fireEvent.click(screen.getByTestId("harness-open-beside-a2"));
    await waitFor(() => {
      expect(address().get("session")).toBe("a1");
      expect(address().get("beside")).toBe("a2");
      expect(address().get("view")).toBe("code");
    });
    v.unmount();
    // A reload of that address (the window's memory gone too).
    __resetSplitMemory();
    window.sessionStorage.clear();
    renderView("a1");
    await waitFor(() => expect(screen.getByTestId("harness-columns")).toHaveAttribute("data-split", "columns"));
    expect(screen.getByTestId("harness-column-left")).toHaveAttribute("data-session-id", "a1");
    expect(screen.getByTestId("harness-column-right")).toHaveAttribute("data-session-id", "a2");
  });

  it("a link to ONE session (another `session`) does not drag in a remembered beside", async () => {
    window.history.replaceState(null, "", "/chat?view=code&session=a3&beside=a2");
    renderView("a1"); // the link names a1; the address's beside belongs to a3
    await screen.findByTestId("session-stub-a1");
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByTestId("harness-columns")).toHaveAttribute("data-split", "one");
  });

  it("a return to Code with no address comes back to the window's split (the memory is NOT reset here)", async () => {
    const v = renderView("a1");
    await screen.findByTestId("session-stub-a1");
    fireEvent.click(screen.getByTestId("harness-open-beside-a3"));
    await waitFor(() => expect(window.sessionStorage.getItem(SPLIT_KEY)).toContain("a3"));
    v.unmount();
    // Chat › Code again: the address has only the view.
    window.history.replaceState(null, "", "/chat?view=code");
    renderView(null);
    await waitFor(() => expect(screen.getByTestId("harness-column-right")).toHaveAttribute("data-session-id", "a3"));
    expect(screen.getByTestId("harness-column-left")).toHaveAttribute("data-session-id", "a1");
    // …the right column still has the focus it had.
    expect(screen.getByTestId("harness-column-right")).toHaveAttribute("data-focused", "true");
    await waitFor(() => expect(address().get("beside")).toBe("a3"));
  });
});

describe("a narrow window shows ONE column and a tab per session", () => {
  it("tabs switch which session shows; the other stays mounted, hidden, inert and not looked at", async () => {
    const mq = stubMatchMedia();
    wide = false;
    renderView("a1");
    await screen.findByTestId("session-stub-a1");
    fireEvent.click(screen.getByTestId("harness-open-beside-a2"));
    await waitFor(() => expect(screen.getByTestId("harness-columns")).toHaveAttribute("data-split", "tabs"));
    const tabs = screen.getByTestId("harness-split-tabs");
    expect(within(tabs).getAllByRole("tab")).toHaveLength(2);
    expect(screen.getByTestId("harness-split-tab-right")).toHaveAttribute("aria-selected", "true");
    // Each tab carries its session's dot: a2 waits on the user (words too),
    // a1 works — so a hidden tab that needs you says so.
    expect(screen.getByTestId("harness-split-tab-right").querySelector("[data-dot]")).toHaveAttribute("data-dot", "waiting");
    expect(screen.getByTestId("harness-split-tab-right")).toHaveTextContent("(needs you)");
    expect(screen.getByTestId("harness-split-tab-left").querySelector("[data-dot]")).toHaveAttribute("data-dot", "running");
    // The left is hidden but still mounted; it is not looked at, and has no panes.
    expect(screen.getByTestId("harness-column-left")).toHaveAttribute("hidden");
    expect(screen.getByTestId("harness-column-left")).toHaveAttribute("inert");
    expect(screen.getByTestId("session-stub-a1")).toHaveAttribute("data-on-screen", "false");
    expect(screen.getByTestId("session-stub-a1")).toHaveAttribute("data-panes", "none");
    // The shown one keeps its own panes (stacked under it, as one session does).
    expect(screen.getByTestId("session-stub-a2")).toHaveAttribute("data-on-screen", "true");
    expect(screen.getByTestId("session-stub-a2")).toHaveAttribute("data-panes", "own");
    expect(screen.queryByTestId("harness-split-panes")).toBeNull();
    // The other tab.
    fireEvent.click(screen.getByTestId("harness-split-tab-left"));
    await waitFor(() => expect(screen.getByTestId("harness-column-right")).toHaveAttribute("hidden"));
    expect(screen.getByTestId("harness-column-left")).not.toHaveAttribute("hidden");
    expect(screen.getByTestId("session-stub-a1")).toHaveAttribute("data-on-screen", "true");
    // Arrow keys move along the tabs.
    fireEvent.keyDown(screen.getByTestId("harness-split-tab-left"), { key: "ArrowRight" });
    await waitFor(() => expect(screen.getByTestId("harness-split-tab-right")).toHaveAttribute("aria-selected", "true"));
    // The window grows: two columns, the same two sessions, never remounted.
    const a1 = screen.getByTestId("session-stub-a1");
    act(() => mq.set(true));
    await waitFor(() => expect(screen.getByTestId("harness-columns")).toHaveAttribute("data-split", "columns"));
    expect(screen.getByTestId("session-stub-a1")).toBe(a1);
    expect(screen.queryByTestId("harness-split-tabs")).toBeNull();
    // A tab's × returns to one column.
    act(() => mq.set(false));
    await screen.findByTestId("harness-split-tabs");
    fireEvent.click(screen.getByTestId("harness-split-tab-close-right"));
    await waitFor(() => expect(screen.getByTestId("harness-columns")).toHaveAttribute("data-split", "one"));
    expect(screen.getByTestId("session-stub-a1")).toBe(a1);
  });
});
