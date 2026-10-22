/**
 * Build's switch: Terminals | IJ harness (v1.369.0, IJ harness plan Phase 6).
 *
 * The free-form canvas is gone; the IJ harness view took its place in the
 * switch. These pin the switch itself, through the real page:
 *
 *   - Build opens on the Terminals view by default, and the switch remembers
 *     the view it was left on (`ij.build.shape`);
 *   - a saved "canvas" (an install that last used the retired canvas) opens
 *     the IJ harness, never a blank page;
 *   - a link to a session (`?session=<id>`) opens the harness on THAT
 *     session; a link to a terminal (`?focus=<id>`) opens the Terminals view;
 *   - the harness's Terminals group opens a terminal in the Terminals view,
 *     focused on it, and drops the session from the address;
 *   - the Terminals-only controls (Shell, New terminal) are not in the
 *     harness view's header;
 *   - react-rnd left the dashboard with the canvas.
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/* ---- api ------------------------------------------------------------------- */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
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

/* ---- next/dynamic: xterm and the harness view are stand-ins ----------------- */

vi.mock("next/dynamic", () => ({
  default: () =>
    function DynamicStub(props: Record<string, unknown>) {
      if (typeof props.onOpenTerminal === "function") {
        // The IJ harness view: what the page hands it is what is asserted.
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
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {actions}
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

const saved = () => localStorage.getItem("ij.build.shape");

/* --------------------------------------------------------------------------- */

describe("the switch", () => {
  it("opens on Terminals, switches to the IJ harness and back, and remembers each", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    const terminals = screen.getByTestId("build-view-terminals");
    const harness = screen.getByTestId("build-view-harness");
    expect(terminals).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("build-view-switch")).toHaveAttribute("role", "tablist");
    expect(screen.queryByTestId("ij-harness-view")).toBeNull();

    fireEvent.click(harness);
    expect(await screen.findByTestId("ij-harness-view")).toBeInTheDocument();
    expect(harness).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByTestId("pane-rail")).toBeNull();
    expect(saved()).toBe("harness");

    fireEvent.click(screen.getByTestId("build-view-terminals"));
    expect(await screen.findByTestId("pane-rail")).toBeInTheDocument();
    expect(saved()).toBe("rail");
  });

  it("the arrow keys move between the two views", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    fireEvent.keyDown(screen.getByTestId("build-view-terminals"), { key: "ArrowRight" });
    expect(await screen.findByTestId("ij-harness-view")).toBeInTheDocument();
    expect(document.activeElement).toBe(screen.getByTestId("build-view-harness"));
  });

  it("a saved IJ harness view opens again", async () => {
    localStorage.setItem("ij.build.shape", "harness");
    render(<TerminalsPage />);
    expect(await screen.findByTestId("ij-harness-view")).toBeInTheDocument();
  });

  it("an install that last used the retired canvas opens the IJ harness", async () => {
    localStorage.setItem("ij.build.shape", "canvas");
    render(<TerminalsPage />);
    expect(await screen.findByTestId("ij-harness-view")).toBeInTheDocument();
    expect(screen.queryByTestId("shape-canvas")).toBeNull();
  });

  it("the Shell picker and New terminal belong to the Terminals view only", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    expect(screen.getByTestId("header-shell")).toBeInTheDocument();
    expect(screen.getByTestId("header-new-terminal")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("build-view-harness"));
    await screen.findByTestId("ij-harness-view");
    expect(screen.queryByTestId("header-shell")).toBeNull();
    expect(screen.queryByTestId("header-new-terminal")).toBeNull();
  });
});

describe("links into Build", () => {
  it("?session=<id> opens the IJ harness on that session, whatever view was saved", async () => {
    localStorage.setItem("ij.build.shape", "rail");
    at("/terminals?session=bs_42");
    render(<TerminalsPage />);
    const view = await screen.findByTestId("ij-harness-view");
    expect(view).toHaveAttribute("data-initial", "bs_42");
  });

  it("?focus=<id> opens the Terminals view, whatever view was saved", async () => {
    localStorage.setItem("ij.build.shape", "harness");
    at("/terminals?focus=t2");
    render(<TerminalsPage />);
    expect(await screen.findByTestId("pane-rail")).toBeInTheDocument();
    expect(screen.queryByTestId("ij-harness-view")).toBeNull();
  });
});

describe("a terminal from the IJ harness's Terminals group", () => {
  it("opens in the Terminals view, focused on it, and the session leaves the address", async () => {
    at("/terminals?session=bs_42");
    render(<TerminalsPage />);
    await screen.findByTestId("ij-harness-view");
    fireEvent.click(await screen.findByTestId("stub-open-t2"));
    expect(await screen.findByTestId("pane-rail")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("rail-pane-t2").style.visibility).toBe("visible"));
    expect(screen.getByTestId("rail-pane-t1").style.visibility).toBe("hidden");
    expect(saved()).toBe("rail");
    expect(window.location.search).not.toContain("session=");
  });

  it("back in the IJ harness, the link the page was opened with is spent (v1.369.0 review)", async () => {
    // The harness then opens the session this window had open, not the old
    // link: the user had moved to another session before going to a terminal.
    at("/terminals?session=bs_42");
    render(<TerminalsPage />);
    expect(await screen.findByTestId("ij-harness-view")).toHaveAttribute("data-initial", "bs_42");
    fireEvent.click(await screen.findByTestId("stub-open-t2"));
    await screen.findByTestId("pane-rail");
    fireEvent.click(screen.getByTestId("build-view-harness"));
    expect(await screen.findByTestId("ij-harness-view")).toHaveAttribute("data-initial", "");
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
