/**
 * v1.409.0 — Build's header switch, **Code | Terminal** (the user, beside a
 * screenshot of Build's empty header centre: "put the switch there"; then
 * "</> Code | Terminal", the chat page's own switch).
 *
 *   - the switch sits in the header's CENTRE, Code first, Terminal second;
 *     it is the chat page's switch (components/ViewSwitch), never a copy;
 *   - Terminal is today's page: the rail, the header's Shell + New terminal,
 *     the subtitle — and it is the default;
 *   - choosing Code is remembered (`ij.build.view`) and put in the address
 *     (`?view=code`, `&code=<id>` for a session); `?view=code` opens it; a
 *     terminal link (`?focus=`) opens the terminals whatever was saved;
 *   - Open in terminal (from the Code view) shows that terminal, focused;
 *   - a terminal whose Claude Code / Codex has exited offers "Open in Code
 *     view" (POST /build/app/from-terminal/{id}); a plain one offers nothing;
 *   - a Code view link pressed while Build is open opens that session.
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  // (codeViewRenders: every draw of the Code view stand-in, a one-frame
  // flash included.)
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    responses: {} as Record<string, unknown>,
    codeViewRenders: 0,
    posts: [] as { path: string; body: unknown }[],
    postReply: {} as Record<string, unknown>,
    postFail: {} as Record<string, string>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  get: (path: string) => {
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    return Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    if (api.postFail[path]) return Promise.reject(new api.FakeApiError(api.postFail[path], 409));
    return Promise.resolve(api.postReply[path] ?? {});
  },
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("next/dynamic", () => ({
  default: () =>
    function DynamicStub(props: Record<string, unknown>) {
      if (typeof props.onOpenedTerminal === "function") {
        // The Code view: what the page hands it is what is asserted.
        api.codeViewRenders += 1;
        const opened = props.onOpenedTerminal as (id: string) => void;
        return (
          <div
            data-testid="stub-code-view"
            data-initial={String(props.initialSession ?? "")}
            data-seq={String(props.linkSeq ?? "")}
          >
            <button type="button" data-testid="stub-open-terminal" onClick={() => opened("t9")}>
              open
            </button>
          </div>
        );
      }
      if (typeof props.paneId === "string") return <div data-testid={`pane-chat-${props.paneId}`} />;
      const info = props.info as { id: string; shell: string };
      return <div data-testid={`terminal-pane-${info.id}`}>{info.shell}</div>;
    },
}));

vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, subtitle, actions, center }: { title: string; subtitle?: React.ReactNode; actions?: React.ReactNode; center?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      <p data-testid="page-header-subtitle">{subtitle}</p>
      <div data-testid="page-header-center">{center}</div>
      <div data-testid="page-header-actions">{actions}</div>
    </div>
  ),
}));
vi.mock("@/components/ui", () => ({
  Card: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  OfflineHint: () => <div />,
  ErrorNote: ({ children }: { children?: React.ReactNode }) => <div role="alert">{children}</div>,
  Spinner: ({ label }: { label?: string }) => <div>{label}</div>,
  ConfirmButton: ({ label }: { label: string }) => <button type="button">{label}</button>,
  Empty: ({ title }: { title?: React.ReactNode }) => <div>{title}</div>,
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <div data-testid="directory-tree" />,
}));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: () => <div data-testid="files-panel" />,
}));

import TerminalsPage from "@/app/terminals/page";
import {
  BUILD_VIEW_KEY,
  announceBuildCodeLink,
  buildCodeHref,
  buildViewFromSearch,
  codeSessionOfHref,
  resetBuildViewMemory,
} from "@/lib/buildApp";
import { codeViewOfferFor } from "@/lib/buildCodeOffer";

const term = (id: string, cwd: string, over: Record<string, unknown> = {}) => ({
  id,
  cwd,
  shell: "pwsh",
  argv: [],
  cols: 120,
  rows: 30,
  alive: true,
  exit_code: null,
  created_at: "2026-10-20T00:00:00Z",
  ...over,
});

function seed(activity: Record<string, unknown>[] | null = null) {
  api.responses = {
    "/terminals": { terminals: [term("t1", "C:\\proj\\alpha"), term("t2", "C:\\proj\\beta")] },
    "/terminals/shells": { shells: [{ name: "pwsh", argv: ["pwsh"] }], default: "pwsh" },
    "/projects": { projects: [] },
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
    "/terminals/activity": {
      panes: activity ?? [
        { id: "t1", name: null, agent_cli: null, state: "unknown", state_line: "", alive: true },
        { id: "t2", name: null, agent_cli: null, state: "unknown", state_line: "", alive: true },
      ],
    },
  };
}

function at(url: string) {
  window.history.replaceState({}, "", url);
}

beforeEach(() => {
  localStorage.clear();
  resetBuildViewMemory();
  api.posts = [];
  api.postReply = {};
  api.postFail = {};
  seed();
  at("/terminals");
});

afterEach(() => {
  cleanup();
  at("/");
});

const dash = process.cwd();
const read = (...p: string[]) => readFileSync(join(dash, ...p), "utf8").replace(/\r\n/g, "\n");

describe("the switch", () => {
  it("sits in the header's centre: Code first, Terminal second, Terminal chosen by default", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    const centre = screen.getByTestId("page-header-center");
    const sw = within(centre).getByTestId("build-view-switch");
    expect(sw.getAttribute("role")).toBe("tablist");
    const tabs = within(sw).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Code", "Terminal"]);
    expect(tabs.map((t) => t.querySelector("svg")?.getAttribute("class"))).toEqual([
      expect.stringContaining("lucide-code"),
      expect.stringContaining("lucide-square-terminal"),
    ]);
    expect(screen.getByTestId("build-view-terminal")).toHaveAttribute("aria-selected", "true");
  });

  it("Terminal is today's page: the rail, Shell and New terminal in the header, the subtitle", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    expect(screen.getByTestId("pane-rail")).toBeInTheDocument();
    expect(screen.getByTestId("page-header-actions")).toContainElement(screen.getByTestId("header-new-terminal"));
    expect(screen.getByTestId("header-shell")).toBeInTheDocument();
    expect(screen.getByTestId("page-header-subtitle").textContent).toContain("Live terminals on this PC.");
    expect(screen.queryByTestId("stub-code-view")).toBeNull();
    // A plain shell offers nothing new.
    expect(screen.queryByTestId("build-code-offer")).toBeNull();
  });

  it("the chat page's switch and Build's are ONE component (never a copy that can drift)", () => {
    const page = read("app", "terminals", "page.tsx");
    const chat = read("components", "chat", "ChatCodeSurface.tsx");
    expect(page).toContain("<ViewSwitch");
    expect(chat).toContain("<ViewSwitch");
    for (const src of [page, chat]) expect(src).not.toMatch(/aria-label="(Chat or code|Code or terminal)"/);
    expect(read("components", "ViewSwitch.tsx")).toContain('role="tablist"');
  });

  it("choosing Code shows the Code view, remembers it and puts it in the address; Terminal brings the page back", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    fireEvent.click(screen.getByTestId("build-view-code"));
    expect(await screen.findByTestId("stub-code-view")).toBeInTheDocument();
    expect(screen.queryByTestId("pane-rail")).toBeNull();
    expect(screen.getByTestId("build-view-code")).toHaveAttribute("aria-selected", "true");
    expect(localStorage.getItem(BUILD_VIEW_KEY)).toBe("code");
    expect(window.location.search).toBe("?view=code");
    // The terminal's own controls are not in the Code view's header.
    expect(screen.queryByTestId("header-new-terminal")).toBeNull();
    fireEvent.click(screen.getByTestId("build-view-terminal"));
    expect(await screen.findByTestId("pane-rail")).toBeInTheDocument();
    expect(localStorage.getItem(BUILD_VIEW_KEY)).toBe("terminal");
    expect(window.location.search).toBe("");
  });

  it("arrow keys move between the two views", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    fireEvent.keyDown(screen.getByTestId("build-view-terminal"), { key: "ArrowLeft" });
    expect(await screen.findByTestId("stub-code-view")).toBeInTheDocument();
    expect(document.activeElement).toBe(screen.getByTestId("build-view-code"));
  });

  it("?view=code&code=<id> opens the Code view on that session", async () => {
    at("/terminals?view=code&code=a7");
    render(<TerminalsPage />);
    const view = await screen.findByTestId("stub-code-view");
    await waitFor(() => expect(view.dataset.initial).toBe("a7"));
  });

  it("a saved Code view opens on the next visit (and says so in the address)", async () => {
    localStorage.setItem(BUILD_VIEW_KEY, "code");
    render(<TerminalsPage />);
    expect(await screen.findByTestId("stub-code-view")).toBeInTheDocument();
    expect(window.location.search).toBe("?view=code");
  });

  it("a terminal link (?focus=) opens the terminals whatever was saved", async () => {
    localStorage.setItem(BUILD_VIEW_KEY, "code");
    at("/terminals?focus=t2");
    render(<TerminalsPage />);
    expect(await screen.findByTestId("pane-rail")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("rail-pane-t2").style.visibility).toBe("visible"));
    expect(screen.queryByTestId("stub-code-view")).toBeNull();
  });
});

describe("Open in terminal (from the Code view)", () => {
  it("switches to Terminal and shows that terminal, focused", async () => {
    localStorage.setItem(BUILD_VIEW_KEY, "code");
    render(<TerminalsPage />);
    await screen.findByTestId("stub-code-view");
    api.responses["/terminals"] = {
      terminals: [term("t1", "C:\\proj\\alpha"), term("t2", "C:\\proj\\beta"), term("t9", "C:\\work\\shop")],
    };
    fireEvent.click(screen.getByTestId("stub-open-terminal"));
    expect(await screen.findByTestId("pane-rail")).toBeInTheDocument();
    expect(await screen.findByTestId("terminal-pane-t9")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("rail-pane-t9").style.visibility).toBe("visible"));
    expect(screen.getByTestId("build-view-terminal")).toHaveAttribute("aria-selected", "true");
  });
});

describe("Open in Code view (from a terminal)", () => {
  it("a terminal whose Claude Code has exited offers it; the press opens that conversation in the Code view", async () => {
    seed([
      { id: "t1", name: null, agent_cli: "claude", state: "idle", state_line: "", alive: true, can_open_in_code: true },
      { id: "t2", name: null, agent_cli: null, state: "unknown", state_line: "", alive: true },
    ]);
    api.postReply["/build/app/from-terminal/t1"] = { session: { id: "a42", harness: "claude-code", folder: "C:\\proj\\alpha" } };
    render(<TerminalsPage />);
    const offer = await screen.findByTestId("build-code-offer");
    expect(offer.textContent).toContain("Claude Code has finished in this terminal.");
    const press = within(offer).getByTestId("build-open-in-code");
    expect(press.textContent).toBe("Open in Code view");
    fireEvent.click(press);
    const view = await screen.findByTestId("stub-code-view");
    await waitFor(() => expect(view.dataset.initial).toBe("a42"));
    expect(api.posts.map((p) => p.path)).toContain("/build/app/from-terminal/t1");
    expect(window.location.search).toBe("?view=code&code=a42");
  });

  it("a refusal (the tool still running) is said in its words, and the terminals stay", async () => {
    seed([
      { id: "t1", name: null, agent_cli: "codex", state: "idle", state_line: "", alive: true, can_open_in_code: true },
      { id: "t2", name: null, agent_cli: null, state: "unknown", state_line: "", alive: true },
    ]);
    api.postFail["/build/app/from-terminal/t1"] = "Codex is still running in this terminal. Exit it first.";
    render(<TerminalsPage />);
    fireEvent.click(await screen.findByTestId("build-open-in-code"));
    expect((await screen.findByTestId("build-code-offer-note")).textContent).toBe(
      "Codex is still running in this terminal. Exit it first.",
    );
    expect(screen.getByTestId("pane-rail")).toBeInTheDocument();
  });

  it("the offer's rule: the daemon's word first; else a reopened terminal whose tool died; never while it works", () => {
    const t = { id: "t1", alive: true };
    expect(codeViewOfferFor(t, { agent_cli: "claude", state: "idle" })).toBeNull();
    expect(codeViewOfferFor(t, { agent_cli: "claude", state: "idle", can_open_in_code: true } as never)).toEqual({
      paneId: "t1",
      toolName: "Claude Code",
    });
    expect(codeViewOfferFor(t, { agent_cli: "claude", state: "working", can_open_in_code: true } as never)).toBeNull();
    expect(codeViewOfferFor({ ...t, resume_cli: "codex" }, { state: "unknown" })).toEqual({ paneId: "t1", toolName: "Codex" });
    expect(codeViewOfferFor({ ...t, resume_cli: "codex" }, { state: "unknown", can_open_in_code: false } as never)).toBeNull();
    // Only the daemon's exact name counts (never an invented one).
    expect(codeViewOfferFor(t, { agent_cli: "claude", state: "idle", harness_exited: true } as never)).toBeNull();
    expect(codeViewOfferFor({ ...t, resume_cli: "aider" }, { state: "unknown" })).toBeNull();
    expect(codeViewOfferFor({ ...t, alive: false, resume_cli: "claude" }, null)).toBeNull();
  });
});

describe("a Code view link pressed while Build is open", () => {
  it("opens that session in the Code view", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    act(() => {
      expect(announceBuildCodeLink(buildCodeHref("a5"))).toBe(true);
    });
    const view = await screen.findByTestId("stub-code-view");
    await waitFor(() => expect(view.dataset.initial).toBe("a5"));
  });

  it("CONTROL: off Build the link is not announced; the address helpers agree", () => {
    at("/chat");
    expect(announceBuildCodeLink(buildCodeHref("a5"))).toBe(false);
    expect(buildCodeHref("a 5")).toBe("/terminals?view=code&code=a%205");
    expect(codeSessionOfHref("/terminals?view=code&code=a5")).toBe("a5");
    expect(codeSessionOfHref("/chat?code=a5")).toBeNull();
    expect(buildViewFromSearch("?view=code")).toBe("code");
    expect(buildViewFromSearch("?focus=t1")).toBe("terminal");
    expect(buildViewFromSearch("")).toBeNull();
  });
});

// The v1.388.1 lesson: window-scoped module state must be tested WITHOUT the
// reset the other cases use. The window last showed Code; a terminal link
// must open Terminal from the very first frame (never a flash of Code).
describe("review: a terminal link with the window's memory on Code", () => {
  it("?focus= opens Terminal on the FIRST frame, with no reset of the memory", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    fireEvent.click(screen.getByTestId("build-view-code"));
    await screen.findByTestId("stub-code-view");
    cleanup();
    // NO resetBuildViewMemory() here: the module still says "code".
    api.codeViewRenders = 0;
    at("/terminals?focus=t2");
    render(<TerminalsPage />);
    expect(await screen.findByTestId("pane-rail")).toBeInTheDocument();
    expect(api.codeViewRenders).toBe(0);
    expect(screen.getByTestId("build-view-terminal")).toHaveAttribute("aria-selected", "true");
  });
});
