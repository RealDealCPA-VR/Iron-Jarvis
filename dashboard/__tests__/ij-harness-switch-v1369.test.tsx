/**
 * Build: the terminals only (v1.406.0 part 1; it was Build's switch,
 * Terminals | IJ harness, from v1.369.0 — these cases moved ON PURPOSE when
 * the coding sessions moved to the chat page's Chat | Code switch).
 *
 *   - Build has no view switch and never draws the coding sessions, whatever
 *     `ij.build.shape` saved ("harness", or the retired "canvas");
 *   - an old link to a session or to Last night (`/terminals?session=<id>`,
 *     `?night=<date>`) is sent on to Chat › Code; a link to a terminal
 *     (`?focus=<id>`) stays on Build, on that terminal;
 *   - a terminal link pressed while Build is open adds and shows it;
 *   - react-rnd left the dashboard with the canvas.
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/* ---- api ------------------------------------------------------------------- */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return { responses: {} as Record<string, unknown>, FakeApiError, harnessRenders: 0 };
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

/* ---- next/dynamic: xterm and the harness view are stand-ins ----------------- */

vi.mock("next/dynamic", () => ({
  default: () =>
    function DynamicStub(props: Record<string, unknown>) {
      if (typeof props.onOpenTerminal === "function") {
        // The IJ harness view: what the page hands it is what is asserted.
        api.harnessRenders += 1; // v1.388.1: counts even a one-frame flash
        const open = props.onOpenTerminal as (id: string) => void;
        const terms = (props.terminals as { id: string }[]) ?? [];
        return (
          <div data-testid="ij-harness-view" data-initial={String(props.initialSession ?? "")}>
            {terms.map((t) => (
              <button key={t.id} type="button" data-testid={`stub-open-${t.id}`} onClick={() => open(t.id)}>
                {t.id}
              </button>
            ))}
          </div>
        );
      }
      if (typeof props.paneId === "string") return <div data-testid={`pane-chat-${props.paneId}`} />;
      const info = props.info as { id: string; shell: string };
      return <div data-testid={`terminal-pane-${info.id}`}>{info.shell}</div>;
    },
}));

/* ---- page chrome ----------------------------------------------------------- */

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
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <div data-testid="directory-tree" />,
}));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder ?? "no-folder"}</div>,
}));

import TerminalsPage from "@/app/terminals/page";
import { addressMove, announceBuildTerminalLink, terminalOfHref } from "@/lib/buildLinks";

/* ---- fixtures -------------------------------------------------------------- */

const term = (id: string, cwd: string) => ({
  id,
  cwd,
  shell: "pwsh",
  argv: [],
  cols: 120,
  rows: 30,
  alive: true,
  exit_code: null,
  created_at: "2026-10-20T00:00:00Z",
});

function seedApi() {
  api.responses = {
    "/terminals": { terminals: [term("t1", "C:\\proj\\alpha"), term("t2", "C:\\proj\\beta")] },
    "/terminals/shells": { shells: [{ name: "pwsh", argv: ["pwsh"] }], default: "pwsh" },
    "/projects": { projects: [] },
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
    "/terminals/activity": {
      panes: [
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
  seedApi();
  at("/terminals");
});

afterEach(() => {
  cleanup();
  at("/");
});


/* --------------------------------------------------------------------------- */

// v1.406.0 (part 1, the user's decision 2026-10-29): Build is the TERMINALS
// only — the coding sessions moved to the chat page's Chat | Code switch
// (components/chat/ChatCodeSurface; pinned in chat-code-switch-v1406). These
// cases replaced the v1.369.0 switch's, ON PURPOSE.
describe("Build is the terminals only (v1.406.0)", () => {
  // v1.409.0 (moved ON PURPOSE): Build's header centre holds its own
  // Code | Terminal switch now (the real Claude Code / Codex in a window;
  // pinned in build-app-switch-v1409). It still never draws Chat › Code's
  // coding sessions, and Terminal is the default.
  it("never draws the coding sessions; its centre holds only the Code | Terminal switch", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    expect(screen.queryByTestId("build-view-harness")).toBeNull();
    expect(screen.queryByTestId("ij-harness-view")).toBeNull();
    expect(screen.getByTestId("pane-rail")).toBeInTheDocument();
    const centre = screen.getByTestId("page-header-center");
    expect(centre.children).toHaveLength(1);
    expect(centre.firstElementChild?.getAttribute("data-testid")).toBe("build-view-switch");
    expect(screen.getByTestId("build-view-terminal")).toHaveAttribute("aria-selected", "true");
    // Its controls stay in the header's right column.
    expect(screen.getByTestId("page-header-actions")).toContainElement(screen.getByTestId("header-new-terminal"));
    expect(screen.getByTestId("header-shell")).toBeInTheDocument();
    expect(api.harnessRenders).toBe(0);
  });

  it("a saved IJ harness (or the retired canvas) view just opens the terminals", async () => {
    for (const shape of ["harness", "canvas"]) {
      localStorage.setItem("ij.build.shape", shape);
      api.harnessRenders = 0;
      render(<TerminalsPage />);
      expect(await screen.findByTestId("pane-rail")).toBeInTheDocument();
      expect(screen.queryByTestId("ij-harness-view")).toBeNull();
      expect(api.harnessRenders).toBe(0);
      cleanup();
    }
  });

  it("says where the coding sessions are now", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    expect(document.body.textContent).toContain("Coding sessions, where Jarvis writes the code, are in Chat › Code.");
  });
});

describe("old links into Build (v1.406.0)", () => {
  let moved: string[] = [];
  const realReplace = addressMove.replace;
  beforeEach(() => {
    moved = [];
    addressMove.replace = (href: string) => {
      moved.push(href);
    };
  });
  afterEach(() => {
    addressMove.replace = realReplace;
  });

  it("?session=<id> is sent on to Chat › Code on that session", async () => {
    at("/terminals?session=bs_42");
    render(<TerminalsPage />);
    expect(await screen.findByTestId("build-moved")).toHaveTextContent("Opening the coding session in Chat › Code");
    expect(moved).toEqual(["/chat?view=code&session=bs_42"]);
    expect(screen.queryByTestId("pane-rail")).toBeNull();
  });

  it("?night=<date> is sent on to Last night in Chat › Code", async () => {
    at("/terminals?night=2026-10-26");
    render(<TerminalsPage />);
    await screen.findByTestId("build-moved");
    expect(moved).toEqual(["/chat?view=code&night=2026-10-26"]);
  });

  it("?focus=<id> stays on Build, on that terminal", async () => {
    at("/terminals?focus=t2");
    render(<TerminalsPage />);
    expect(await screen.findByTestId("pane-rail")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("rail-pane-t2").style.visibility).toBe("visible"));
    expect(moved).toEqual([]);
  });

  it("a session link announced while Build is open is not Build's any more", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    window.dispatchEvent(new CustomEvent("ij:open-build-session", { detail: { id: "bs_7" } }));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("ij-harness-view")).toBeNull();
    expect(api.harnessRenders).toBe(0);
  });
});

describe("the canvas is gone", () => {
  const dash = process.cwd();
  const read = (...p: string[]) => readFileSync(join(dash, ...p), "utf8").replace(/\r\n/g, "\n");

  it("react-rnd is no longer a dependency, and the page draws no Rnd", () => {
    const pkg = JSON.parse(read("package.json")) as {
      dependencies?: Record<string, string>;
      devDependencies?: Record<string, string>;
    };
    expect(pkg.dependencies?.["react-rnd"]).toBeUndefined();
    expect(pkg.devDependencies?.["react-rnd"]).toBeUndefined();
    const page = read("app", "terminals", "page.tsx");
    expect(page).not.toMatch(/from "react-rnd"|<Rnd\b/);
  });
});

// Harness gaps (fix 2): the Sign in press now stands under a coding session's
// failed reply — ON Build — and its "Finish signing in there" link
// (/terminals?focus=<id>) changed only the address: Next keeps the page on a
// query-only change, Build's terminal list is read once, and the coding
// sessions view stayed. The link is announced (lib/buildLinks
// BUILD_TERMINAL_EVENT): Build reads its list again, adds the new terminal
// and shows it in the Terminals view — not remembered as the saved view.
describe("a terminal link pressed while Build is open (harness gaps, fix 2)", () => {
  it("adds the terminal started elsewhere and shows it in the Terminals view", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    // The Sign in press opened t9 meanwhile (the list was read before it).
    api.responses["/terminals"] = {
      terminals: [term("t1", "C:\\proj\\alpha"), term("t2", "C:\\proj\\beta"), term("t9", "C:\\Users\\me")],
    };
    act(() => {
      expect(announceBuildTerminalLink("/terminals?focus=t9")).toBe(true);
    });
    expect(await screen.findByTestId("terminal-pane-t9")).toBeInTheDocument();
    expect(await screen.findByTestId("pane-rail")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("rail-pane-t9").style.visibility).toBe("visible"));
  });

  it("CONTROL: off Build the link is not announced (the navigation opens it)", () => {
    at("/chat");
    expect(announceBuildTerminalLink("/terminals?focus=t9")).toBe(false);
    expect(terminalOfHref("/terminals?focus=t9")).toBe("t9");
    expect(terminalOfHref("/terminals?session=s1")).toBeNull();
    expect(terminalOfHref("/sessions?focus=t9")).toBeNull();
  });
});
