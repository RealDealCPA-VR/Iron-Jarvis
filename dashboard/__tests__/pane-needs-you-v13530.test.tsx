/**
 * v1.353.0 lane 3D (G52, G54's bell row) — a Build pane that stops is not a
 * success, and a pane that needs you reaches you.
 *
 *  G52  A pane that went quiet while unseen used to wear a green "finished"
 *       check — but the classifier has no error state, so a CLI that died on
 *       an error looked exactly like one that succeeded. It now reads
 *       "stopped — take a look" in the neutral look (no green, no check).
 *       A pane whose CLI is waiting on a question ("Allow this edit?") was
 *       visible only on Build; the bell now lists it — "Claude Code in Build
 *       is waiting for you", opening that pane — everywhere except Build.
 *  G54  The bell's agent-browser row says what it is: "A website action is
 *       waiting for your OK" (it said "1 computer-use approval").
 *
 * Driven through the REAL NotificationBell, PaneState components and
 * lib/paneAsks; only the network (lib/api), the event socket, the desktop
 * notification hook and the router's pathname are stubbed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const { getMock, notifyMock, nav, H } = vi.hoisted(() => ({
  getMock: vi.fn(),
  notifyMock: vi.fn(),
  nav: { pathname: "/chat" as string | null },
  H: {
    panes: [] as unknown[],
    pendingWebsite: 0,
  },
}));

vi.mock("@/lib/api", () => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: MockApiError,
    get: getMock,
    post: vi.fn(async () => ({})),
    put: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
    API_BASE: "",
    ijToken: () => "",
  };
});

vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ push: () => {}, replace: () => {} }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: true }),
}));

vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: notifyMock,
  }),
}));

import { NotificationBell } from "@/components/NotificationBell";
import {
  DONE_WORDS,
  PaneDot,
  PaneStateChip,
  PaneStateSummary,
  stateWord,
} from "@/components/terminal/PaneState";
import {
  PANE_CLI_NAMES,
  blockedPanes,
  onBuildPage,
  paneWaitingHref,
  paneWaitingSubject,
  paneWaitingTitle,
} from "@/lib/paneAsks";
import { __resetApiCache } from "@/lib/apiCache";

/** A `GET /terminals/activity` row, as routes/terminals.py shapes it. */
const pane = (over: Record<string, unknown> = {}) => ({
  id: "t1",
  name: null,
  agent_cli: "claude",
  state: "blocked",
  state_line: "Edit src/app.py? 1. Yes 2. No",
  alive: true,
  ...over,
});

beforeEach(() => {
  __resetApiCache();
  getMock.mockReset();
  notifyMock.mockReset();
  nav.pathname = "/chat";
  H.panes = [];
  H.pendingWebsite = 0;
  window.localStorage.clear();
  document.title = "Iron Jarvis";
  getMock.mockImplementation(async (path: unknown) => {
    const p = String(path);
    if (p === "/terminals/activity") return { panes: H.panes };
    if (p === "/computeruse") return { pending_approvals: H.pendingWebsite };
    return {};
  });
});

afterEach(() => cleanup());

async function openBell() {
  fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
}

/* ================================================== G52: the pane's words */

describe("a quiet pane reads 'stopped — take a look', never a green check (G52)", () => {
  it("the chip says it in words, in the neutral look, with no check icon", () => {
    render(<PaneStateChip state="done" cli="claude" />);
    const chip = screen.getByTestId("pane-state-done");
    expect(chip.textContent).toBe("stopped — take a look");
    expect(DONE_WORDS).toBe("stopped — take a look");
    expect(chip.className).not.toMatch(/success|emerald|green/);
    expect(chip.className).toContain("text-zinc-300");
    // The idle look's frame — the same quiet pill as "ready", brighter words.
    expect(chip.className).toContain("border-white/10");
    expect(chip.querySelector("svg")).toBeNull();
    // Its title never claims an outcome.
    const title = chip.getAttribute("title") ?? "";
    expect(title).toMatch(/finished or stopped on an error/);
    expect(title).not.toMatch(/^claude: finished/);
  });

  it("the rail's word and dot agree: neutral, never the success tone", () => {
    expect(stateWord("done")).toBe("stopped — take a look");
    render(<PaneDot state="done" />);
    const dot = screen.getByTestId("pane-dot-done");
    expect(dot.className).not.toMatch(/success|emerald|green/);
  });

  it("the canvas strip counts them as stopped, in zinc, never 'finished'", () => {
    render(
      <PaneStateSummary
        panes={[
          { id: "a", state: "done", name: "builder" },
          { id: "b", state: "done" },
        ]}
        onFocus={vi.fn()}
      />,
    );
    const stopped = screen.getByTestId("pane-summary-stopped");
    expect(stopped.textContent).toBe("2 stopped — take a look");
    expect(stopped.className).not.toMatch(/success/);
    expect(screen.getByTestId("pane-summary").textContent).not.toMatch(/finished/);
  });

  it("anti-vacuity: 'ready' (seen) and 'needs you' (blocked) are unchanged", () => {
    render(<PaneStateChip state="idle" />);
    expect(screen.getByTestId("pane-state-idle").textContent).toBe("ready");
    cleanup();
    render(<PaneStateChip state="blocked" />);
    expect(screen.getByTestId("pane-state-blocked").textContent).toBe("needs you");
  });
});

/* ============================================ G52: lib/paneAsks (pure) */

describe("lib/paneAsks: which panes wait, and what they are called", () => {
  it("only blocked, live panes count — once each, in the daemon's order", () => {
    const rows = blockedPanes({
      panes: [
        pane({ id: "t1" }),
        pane({ id: "t2", state: "working" }),
        pane({ id: "t3", state: "idle" }),
        pane({ id: "t4", alive: false }),
        pane({ id: "t5", agent_cli: "codex", name: " tests " }),
        pane({ id: "t1" }),
        { id: "", state: "blocked" },
        "junk",
      ],
    });
    expect(rows).toEqual([
      { id: "t1", name: "", cli: "claude" },
      { id: "t5", name: "tests", cli: "codex" },
    ]);
  });

  it("any other shape is no rows, never a crash", () => {
    for (const bad of [undefined, null, {}, { panes: "x" }, [], "panes"]) {
      expect(blockedPanes(bad)).toEqual([]);
    }
  });

  it("names the CLI as the Launch menu does; an unknown id as itself; none as a Build pane", () => {
    expect(paneWaitingTitle({ cli: "claude" })).toBe("Claude Code in Build is waiting for you");
    expect(paneWaitingTitle({ cli: "codex" })).toBe("Codex in Build is waiting for you");
    expect(paneWaitingTitle({ cli: "my-cli" })).toBe("my-cli in Build is waiting for you");
    expect(paneWaitingTitle({ cli: "" })).toBe("A Build terminal is waiting for you");
    expect(paneWaitingSubject("grok")).toBe("Grok CLI");
    expect(Object.keys(PANE_CLI_NAMES)).toContain("cursor-agent");
  });

  it("opens that pane on Build; Build itself is told apart", () => {
    expect(paneWaitingHref({ id: "t 1" })).toBe("/terminals?focus=t%201");
    expect(onBuildPage("/terminals")).toBe(true);
    expect(onBuildPage("/terminals/")).toBe(true);
    expect(onBuildPage("/chat")).toBe(false);
    expect(onBuildPage("/terminalsx")).toBe(false);
    expect(onBuildPage(null)).toBe(false);
  });
});

/* =========================================== G52: the bell, where you are */

describe("a pane waiting on its CLI's question reaches the bell (G52)", () => {
  it("in chat: 'Claude Code in Build is waiting for you', opening that pane, on the badge and pinged", async () => {
    H.panes = [pane({ id: "t1" })];
    render(<NotificationBell />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "1 notifications" })).toBeInTheDocument(),
    );
    await waitFor(() => expect(document.title).toBe("(1) Iron Jarvis"));
    // The desktop ping names the pane's CLI, like the row.
    await waitFor(() => expect(notifyMock).toHaveBeenCalled());
    const [title, body] = notifyMock.mock.calls[notifyMock.mock.calls.length - 1];
    expect(title).toBe("Iron Jarvis: 1 pending");
    expect(body).toContain("Claude Code in Build is waiting for you");

    await openBell();
    const row = await screen.findByTestId("bell-pane-waiting");
    expect(row).toHaveTextContent("Claude Code in Build is waiting for you");
    expect(row).toHaveTextContent("Open the terminal");
    expect(within(row).getByRole("link")).toHaveAttribute("href", "/terminals?focus=t1");
    // Only panes waiting: the footer goes to Build, not the Agents page.
    const footer = screen.getByRole("link", { name: /Open Build/ });
    expect(footer).toHaveAttribute("href", "/terminals?focus=t1");
  });

  it("a named pane is opened by its name; one row per waiting pane", async () => {
    H.panes = [pane({ id: "t1", name: "builder" }), pane({ id: "t2", agent_cli: "codex" }), pane({ id: "t3", state: "working" })];
    render(<NotificationBell />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "2 notifications" })).toBeInTheDocument(),
    );
    await openBell();
    const rows = await screen.findAllByTestId("bell-pane-waiting");
    expect(rows.map((r) => r.getAttribute("data-pane"))).toEqual(["t1", "t2"]);
    expect(rows[0]).toHaveTextContent("Open builder");
    expect(rows[1]).toHaveTextContent("Codex in Build is waiting for you");
  });

  it("on Build itself the bell stays quiet (the pane already wears 'needs you')", async () => {
    H.panes = [pane({ id: "t1" })];
    const { rerender } = render(<NotificationBell />);
    // In chat it rings (the positive control: the poll's answer has landed)…
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "1 notifications" })).toBeInTheDocument(),
    );
    // …and once the user is on Build, the same answer says nothing.
    nav.pathname = "/terminals";
    rerender(<NotificationBell />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Notifications" })).toBeInTheDocument(),
    );
    await openBell();
    expect(screen.queryByTestId("bell-pane-waiting")).not.toBeInTheDocument();
  });

  it("a pane that is only working, or that ended, says nothing (keyed on blocked only)", async () => {
    H.panes = [
      pane({ id: "t1", state: "working" }),
      pane({ id: "t2", alive: false }),
      pane({ id: "t3", state: "idle" }),
      pane({ id: "t4" }),
    ];
    render(<NotificationBell />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "1 notifications" })).toBeInTheDocument(),
    );
    await openBell();
    const rows = await screen.findAllByTestId("bell-pane-waiting");
    expect(rows.map((r) => r.getAttribute("data-pane"))).toEqual(["t4"]);
  });
});

/* ================================= G54: the website action, in words */

describe("the bell's agent-browser row says what it is (G54)", () => {
  it("'A website action is waiting for your OK', and what to do after approving", async () => {
    H.pendingWebsite = 1;
    render(<NotificationBell />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "1 notifications" })).toBeInTheDocument(),
    );
    await waitFor(() => expect(notifyMock).toHaveBeenCalled());
    expect(notifyMock.mock.calls[notifyMock.mock.calls.length - 1][1]).toContain(
      "A website action is waiting for your OK",
    );
    await openBell();
    const words = await screen.findByTestId("bell-website-action");
    expect(words.textContent).toBe("A website action is waiting for your OK");
    const row = words.closest("li") as HTMLElement;
    expect(row).toHaveTextContent("Approve it, then ask the agent to do that step again.");
    expect(within(row).getByRole("link")).toHaveAttribute("href", "/computeruse");
    expect(document.body.textContent ?? "").not.toMatch(/computer-use approval/i);
  });

  it("several read as a count", async () => {
    H.pendingWebsite = 3;
    render(<NotificationBell />);
    await openBell();
    expect((await screen.findByTestId("bell-website-action")).textContent).toBe(
      "3 website actions are waiting for your OK",
    );
  });
});
