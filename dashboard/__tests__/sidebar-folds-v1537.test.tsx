/**
 * v1.537.0 — the owner, on two screenshots of the sidebar: Chat › Code's
 * "Terminals" should be collapsed by default; in Chat, "Other tools" should
 * sit right by "Projects", and both should be collapsed by default.
 *
 *  - with no saved choice, each of the three parts starts FOLDED to its
 *    heading, and the heading still says what it holds (nothing hidden
 *    silently);
 *  - one press opens it, and the choice is kept across a reload;
 *  - "Other tools" under a chat list renders just above "Projects" (in the
 *    sidebar, not inside the chat list);
 *  - Chat › Code's own "Other tools" keeps its old start (open).
 */
import React from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const nav = vi.hoisted(() => ({ pathname: "/usage", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ push: nav.push, replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const AT = "2026-11-17T10:00:00+00:00";
const CHAT_TOOLS = {
  tools: [
    {
      tool: "claude-chat",
      label: "Claude Chat",
      openable: false,
      folders: [
        {
          folder: "",
          name: "Chats",
          chats: [{ id: "c1", title: "Plan the week", folder: "", started: AT, ended: AT, working: false, quiet_s: 7200, doing: "" }],
        },
      ],
    },
  ],
  apps: [],
};
const answers = vi.hoisted(() => ({ otherTools: {} as unknown }));
vi.mock("@/lib/api", () => ({
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    if (path === "/chat/threads") return { threads: [{ id: "t1", title: "Quarterly numbers", updated_at: new Date().toISOString() }] };
    if (path === "/projects") return { projects: [{ id: "p1", name: "Q3 Bookkeeping" }] };
    if (path === "/work-record/other-tools") return answers.otherTools;
    return {};
  },
  post: async () => ({}),
  put: async () => ({}),
  patch: async () => ({}),
  del: async () => ({}),
}));

import { AppSidebar } from "@/components/AppSidebar";
import OtherToolChats from "@/components/chat/OtherToolChats";
import { HarnessSidebar, terminalsNote } from "@/components/harness/HarnessSidebar";
import type { RailPane } from "@/components/terminal/PaneRail";
import { FIRST_READ_MS, FOLDED_KEY, readFolded } from "@/lib/otherTools";
import { setChatSlot } from "@/lib/sidebarSlot";
import { resetSidebarHidden } from "@/lib/sidebarState";
import { SIDEBAR_FOLDS_KEY, readSidebarFold } from "@/lib/sidebarFolds";

function setWide(wide: boolean) {
  window.matchMedia = ((q: string) => ({
    matches: q.includes("min-width") ? wide : false,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

async function afterFirstRead() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(FIRST_READ_MS + 10);
  });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  window.localStorage.clear();
  window.sessionStorage.clear();
  resetSidebarHidden();
  nav.pathname = "/usage";
  nav.push.mockReset();
  setWide(true);
  setChatSlot(null);
  answers.otherTools = CHAT_TOOLS;
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("Other tools in the chat list starts folded", () => {
  it("with no saved choice the chat list's part is folded, Chat › Code's is open", () => {
    expect(readFolded("chat")).toBe(true);
    expect(readFolded("code")).toBe(false);
    // A choice the owner made wins over the start.
    window.localStorage.setItem(FOLDED_KEY, JSON.stringify({ chat: false, code: true }));
    expect(readFolded("chat")).toBe(false);
    expect(readFolded("code")).toBe(true);
  });

  it("renders folded, says what is new, and one press opens it", async () => {
    render(<OtherToolChats go={() => {}} />);
    await afterFirstRead();
    const fold = await screen.findByTestId("other-tools-fold");
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("other-tool-claude-chat")).toBeNull();

    fireEvent.click(fold);
    expect(fold.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("other-tool-claude-chat")).toBeTruthy();
    expect(JSON.parse(window.localStorage.getItem(FOLDED_KEY) || "{}")).toEqual({ chat: false });
  });
});

describe("Other tools sits just above Projects", () => {
  it("on the chat page (Chat view) it is in the sidebar, right before Projects", async () => {
    nav.pathname = "/chat";
    render(<AppSidebar />);
    await afterFirstRead();
    const projects = await screen.findByTestId("sidebar-projects");
    const other = await screen.findByTestId("other-tools");
    expect(other.nextElementSibling).toBe(projects);
    expect(other.parentElement).toBe(screen.getByTestId("sidebar-lists"));
  });

  it("on other pages too: under the chat list, not inside it", async () => {
    render(<AppSidebar />);
    await afterFirstRead();
    const projects = await screen.findByTestId("sidebar-projects");
    const other = await screen.findByTestId("other-tools");
    expect(other.nextElementSibling).toBe(projects);
    expect(within(await screen.findByTestId("sidebar-recent-chats")).queryByTestId("other-tools")).toBeNull();
  });
});

describe("Projects starts folded", () => {
  it("with no saved choice it is folded, says how many, and one press opens it for good", async () => {
    expect(readSidebarFold("projects")).toBe(true);
    render(<AppSidebar />);
    const projects = await screen.findByTestId("sidebar-projects");
    const fold = within(projects).getByTestId("sidebar-projects-fold");
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    expect(within(projects).getByTestId("sidebar-projects-count").textContent).toBe("1");
    expect(within(projects).queryByRole("link", { name: "Q3 Bookkeeping" })).toBeNull();

    fireEvent.click(fold);
    expect(fold.getAttribute("aria-expanded")).toBe("true");
    expect(within(projects).getByRole("link", { name: "Q3 Bookkeeping" }).getAttribute("href")).toBe("/projects/p1");
    expect(JSON.parse(window.localStorage.getItem(SIDEBAR_FOLDS_KEY) || "{}")).toEqual({ projects: false });

    cleanup();
    render(<AppSidebar />);
    const again = await screen.findByTestId("sidebar-projects");
    expect(within(again).getByTestId("sidebar-projects-fold").getAttribute("aria-expanded")).toBe("true");
    expect(within(again).getByRole("link", { name: "Q3 Bookkeeping" })).toBeTruthy();
  });
});

describe("Terminals in Chat › Code starts folded", () => {
  const panes: RailPane[] = [
    { id: "t1", label: "Sign in: VR2", state: "shell", cwd: "C:\\work" },
    { id: "t2", label: "Sign in: TSI", state: "working", cwd: "C:\\work" },
  ];

  function sidebar(terminals: RailPane[] = panes) {
    const onOpenTerminal = vi.fn();
    answers.otherTools = {};
    render(
      <HarnessSidebar
        folders={[]}
        listError={null}
        selectedId={null}
        onSelect={vi.fn()}
        onStart={vi.fn(async () => null)}
        onRename={vi.fn(async () => null)}
        terminals={terminals}
        onOpenTerminal={onOpenTerminal}
      />,
    );
    return onOpenTerminal;
  }

  it("with no saved choice the rows are folded away, the heading says what works, one press opens them", () => {
    expect(readSidebarFold("terminals")).toBe(true);
    const onOpen = sidebar();
    const fold = screen.getByTestId("harness-terminals-fold");
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("harness-terminal-row-t1")).toBeNull();
    expect(screen.getByTestId("harness-terminals-note").textContent).toBe("1 working");

    fireEvent.click(fold);
    expect(fold.getAttribute("aria-expanded")).toBe("true");
    expect(screen.queryByTestId("harness-terminals-note")).toBeNull();
    fireEvent.click(screen.getByTestId("harness-terminal-row-t1"));
    expect(onOpen).toHaveBeenCalledWith("t1");
    expect(JSON.parse(window.localStorage.getItem(SIDEBAR_FOLDS_KEY) || "{}")).toEqual({ terminals: false });
  });

  it("folded, it names what needs the owner first, then what works, then how many are open", () => {
    expect(terminalsNote([{ state: "blocked" }, { state: "working" }])).toBe("1 needs you");
    expect(terminalsNote([{ state: "working" }, { state: "working" }, { state: "shell" }])).toBe("2 working");
    expect(terminalsNote([{ state: "shell" }, { state: "unclear" }])).toBe("2 open");
    expect(terminalsNote([])).toBe("");
  });
});
