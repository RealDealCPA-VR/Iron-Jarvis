/**
 * v1.243.0 — Build draws its panes the moment the pane LIST is in.
 *
 * Half of "it takes a while to come back to what I was working on" was not
 * the terminals at all. The page awaited five requests together and showed
 * only "Attaching to sessions…" until the slowest answered. Measured on the
 * live install: `/terminals` in 5 ms, `/models` in 2.1–3.4 s, a cold
 * `/terminals/ai-clis` in 3.1 s. Every return to Build waited seconds for
 * catalogs that only fill the per-pane model picker, the Launch menu, the
 * skill picker and the shell selector.
 *
 * Pinned here: the panes render while every catalog is still pending, the
 * catalogs still arrive afterwards, and one catalog failing costs nothing
 * but itself.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

/* ---- api: some responses can be held open --------------------------------- */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    responses: {} as Record<string, unknown>,
    /** Paths whose answer is held until the test releases it. */
    held: {} as Record<string, { promise: Promise<unknown>; resolve: (v: unknown) => void }>,
    /** Paths that fail. */
    failing: new Set<string>(),
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  get: (path: string) => {
    const hold = api.held[path];
    if (hold) return hold.promise;
    if (api.failing.has(path)) return Promise.reject(new api.FakeApiError(`boom ${path}`, 500));
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    }
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

function hold(path: string) {
  let resolve: (v: unknown) => void = () => {};
  const promise = new Promise<unknown>((r) => {
    resolve = r;
  });
  api.held[path] = { promise, resolve };
}

/* ---- next/dynamic: xterm never enters jsdom ------------------------------- */

vi.mock("next/dynamic", () => ({
  default: () => {
    function DynamicStub(props: Record<string, unknown>) {
      if (typeof props.paneId === "string") return <div data-testid={`pane-chat-${props.paneId}`} />;
      const info = props.info as { id: string; shell: string };
      return <div data-testid={`terminal-pane-${info.id}`}>{info.shell}</div>;
    }
    return DynamicStub;
  },
}));

vi.mock("react-rnd", () => ({
  Rnd: ({ children }: { children?: React.ReactNode }) => <div data-testid="rnd">{children}</div>,
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
  OfflineHint: () => <div data-testid="offline" />,
  ErrorNote: ({ children }: { children?: React.ReactNode }) => <div role="alert">{children}</div>,
  Spinner: ({ label }: { label?: string }) => <div>{label}</div>,
  ConfirmButton: ({ label }: { label: string }) => <button type="button">{label}</button>,
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <div data-testid="directory-tree" />,
}));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: () => <div data-testid="files-panel" />,
}));

import TerminalsPage from "@/app/terminals/page";

/* ---- fixtures -------------------------------------------------------------- */

const term = (id: string) => ({
  id,
  cwd: `C:\\proj\\${id}`,
  shell: "pwsh",
  argv: [],
  cols: 120,
  rows: 30,
  alive: true,
  exit_code: null,
  created_at: "2026-09-10T00:00:00Z",
});

const CATALOGS = ["/terminals/shells", "/models", "/terminals/ai-clis", "/skills"];

beforeEach(() => {
  localStorage.clear();
  api.held = {};
  api.failing = new Set();
  api.responses = {
    "/terminals": { terminals: [term("t1"), term("t2")] },
    "/terminals/shells": { shells: [{ name: "pwsh" }, { name: "cmd" }] },
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
    "/terminals/activity": { panes: [] },
  };
});

afterEach(cleanup);

describe("Build draws its panes before the catalogs answer", () => {
  it("renders every pane while the model list, CLI catalog, skills and shells are all still loading", async () => {
    CATALOGS.forEach(hold);
    render(<TerminalsPage />);
    // The pane list answered; nothing else has. The panes are there anyway.
    expect(await screen.findByTestId("terminal-pane-t1")).toBeInTheDocument();
    expect(screen.getByTestId("terminal-pane-t2")).toBeInTheDocument();
    expect(screen.queryByText("Attaching to sessions…")).not.toBeInTheDocument();
  });

  it("the catalogs still arrive afterwards", async () => {
    hold("/terminals/shells");
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    expect(screen.queryByRole("option", { name: "cmd" })).not.toBeInTheDocument();
    api.held["/terminals/shells"].resolve({ shells: [{ name: "pwsh" }, { name: "cmd" }] });
    await waitFor(() => expect(screen.getByRole("option", { name: "cmd" })).toBeInTheDocument());
    // …and the selector lands on the first shell, as it always did.
    expect((screen.getByLabelText("Shell") as HTMLSelectElement).value).toBe("pwsh");
  });

  it("a catalog that fails costs nothing but itself", async () => {
    api.failing.add("/models");
    api.failing.add("/terminals/ai-clis");
    render(<TerminalsPage />);
    expect(await screen.findByTestId("terminal-pane-t1")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("option", { name: "cmd" })).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByTestId("offline")).not.toBeInTheDocument();
  });

  it("the pane list itself failing is still the page's error", async () => {
    api.failing.add("/terminals");
    render(<TerminalsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom /terminals");
  });
});
