/**
 * v1.500.0 — ONE sidebar, Build's half. Build's own lists live in the app
 * sidebar's list slot (lib/sidebarSlot), never in a second column inside the
 * page:
 *
 *  - Build › Terminal: the terminals list (PaneRail: open, rename, close,
 *    capabilities, New terminal, "needs you", the count) in the slot.
 *  - Build › Code: the sessions list (search, New session, a folder's "+",
 *    rows, Archived sessions) in the slot; opening a row shuts the phone
 *    drawer that holds it.
 *  - With no slot at all (a pop-out window, a phone with the drawer shut)
 *    each list stays in the page, as before.
 *
 * Mocks for the Build page copied from build-restore-failed-v14181; for
 * Build › Code from build-app-sessions-v1420.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return { responses: {} as Record<string, unknown>, FakeApiError };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  get: (path: string) => {
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* () {},
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));
vi.mock("next/dynamic", () => ({
  default: () => {
    function DynamicStub(props: Record<string, unknown>) {
      const info = props.info as { id: string } | undefined;
      if (!info) return null;
      return <div data-testid={`terminal-pane-${info.id}`} />;
    }
    return DynamicStub;
  },
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div /> }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div /> }));

import TerminalsPage from "@/app/terminals/page";
import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { setChatSlot } from "@/lib/sidebarSlot";

const term = (id: string) => ({
  id,
  cwd: "C:\\proj\\alpha",
  shell: "pwsh",
  argv: [],
  cols: 120,
  rows: 30,
  alive: true,
  exit_code: null,
  created_at: "2026-10-29T00:00:00Z",
});

const TOOLS = {
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      version: "2.1.311",
      modes: [{ id: "ask", label: "Ask first", dangerous: false }],
      hidden_modes: [],
      models: [],
      efforts: [],
      accounts: [],
      commands: [],
    },
  ],
};

const sess = (id: string, title: string) => ({
  id,
  harness: "claude-code",
  folder: "C:\\work\\shop",
  title,
  account: "",
  model: "",
  effort: "",
  mode: "ask",
  harness_session_id: "",
  created_at: "2026-10-31T09:00:00Z",
  updated_at: "2026-10-31T09:00:00Z",
  archived_at: null,
  last_error: "",
});

let slot: HTMLDivElement;
const closer = vi.fn();

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  closer.mockReset();
  api.responses = {
    "/terminals": { terminals: [term("t1")] },
    "/terminals/shells": { shells: [] },
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
    "/build/app/harnesses": TOOLS,
    "/build/app/sessions": { sessions: [sess("a1", "Fix the cart")] },
    "/editor/options": { editors: [], preferred: "" },
  };
  slot = document.createElement("div");
  slot.id = "ij-sidebar-chat-slot";
  document.body.appendChild(slot);
});

afterEach(() => {
  cleanup();
  setChatSlot(null);
  slot.remove();
  window.history.replaceState(null, "", "/");
});

describe("Build › Terminal: the terminals list is in the sidebar", () => {
  it("with the sidebar's slot, the list (and New terminal) is there, not in the page", async () => {
    setChatSlot(slot);
    render(<TerminalsPage />);
    const row = await screen.findByTestId("rail-row-t1");
    expect(slot.contains(row)).toBe(true);
    expect(slot.contains(screen.getByTestId("rail-new-pane"))).toBe(true);
    expect(slot.contains(screen.getByTestId("rail-rename-t1"))).toBe(true);
    expect(slot.contains(screen.getByTestId("rail-close-t1"))).toBe(true);
    expect(slot.contains(screen.getByTestId("rail-caps-t1"))).toBe(true);
    expect(screen.queryByTestId("build-rail-in-page")).toBeNull();
    // The stage stays in the page.
    expect(slot.contains(screen.getByTestId("build-stage"))).toBe(false);
  });

  it("opening a terminal from the phone drawer shuts the drawer", async () => {
    setChatSlot(slot, closer);
    render(<TerminalsPage />);
    const row = await screen.findByTestId("rail-row-t1");
    fireEvent.click(within(row).getAllByRole("button")[0]);
    expect(closer).toHaveBeenCalled();
  });

  it("with no slot (a pop-out window) the list stays in the page", async () => {
    render(<TerminalsPage />);
    const row = await screen.findByTestId("rail-row-t1");
    expect(slot.contains(row)).toBe(false);
    expect(screen.getByTestId("build-rail-in-page").contains(row)).toBe(true);
  });
});

describe("Build › Code: the sessions list is in the sidebar", () => {
  it("with the sidebar's slot, the list is there; the session stage is not", async () => {
    setChatSlot(slot);
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    const list = await screen.findByTestId("code-session-list");
    expect(slot.contains(list)).toBe(true);
    expect(list.getAttribute("data-in-sidebar")).toBe("true");
    expect(await within(list).findByText("Fix the cart")).toBeTruthy();
    expect(within(list).getByTestId("code-new")).toBeTruthy();
    expect(within(list).getByTestId("code-session-search")).toBeTruthy();
    expect(within(list).getByTestId("code-archived-toggle")).toBeTruthy();
    expect(slot.contains(screen.getByTestId("code-stage"))).toBe(false);
  });

  it("opening a session from the phone drawer shuts the drawer and opens it", async () => {
    setChatSlot(slot, closer);
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    const row = await screen.findByTestId("code-row-a1");
    await act(async () => {
      fireEvent.click(row);
    });
    expect(closer).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId("code-row-a1").getAttribute("aria-current")).toBe("true"));
  });

  it("with no slot (a pop-out window) the list stays in the page", async () => {
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    const list = await screen.findByTestId("code-session-list");
    expect(slot.contains(list)).toBe(false);
    expect(list.getAttribute("data-in-sidebar")).toBeNull();
  });
});
