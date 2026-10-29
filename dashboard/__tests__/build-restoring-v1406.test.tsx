/**
 * v1.406.0 (part 6) — a pane the daemon is still reopening after a restart.
 *
 * The daemon now waits at most a few seconds for restored pane shells at boot
 * (a shell whose `CreateProcessW` never returned held the user's boot for 90 s
 * on 5 of 11 starts). A pane still starting is listed in `GET /terminals` as
 * `restoring`. The Build page must (1) never prune that pane's saved keys and
 * (2) add the pane to the rail once its shell has started.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

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
    /** Answers of `/terminals`, in order; the last one repeats. */
    terminals: [] as unknown[],
    terminalReads: 0,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  get: (path: string) => {
    if (path === "/terminals") {
      const i = Math.min(api.terminalReads, api.terminals.length - 1);
      api.terminalReads += 1;
      return Promise.resolve(api.terminals[i]);
    }
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
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
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, actions, center }: { title: string; actions?: React.ReactNode; center?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      <div>{center}</div>
      <div>{actions}</div>
    </div>
  ),
}));
vi.mock("@/components/ui", () => ({
  Card: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  OfflineHint: () => <div />,
  ErrorNote: ({ children }: { children?: React.ReactNode }) => <div role="alert">{children}</div>,
  Spinner: ({ label }: { label?: string }) => <div>{label}</div>,
  ConfirmButton: ({ label }: { label: string }) => <button type="button">{label}</button>,
  Empty: ({ title }: { title?: string }) => <div data-testid="build-empty">{title}</div>,
}));
// Review: the hosts kept are recorded, so a pane still reopening keeps its
// xterm + socket (paneHost) as well as its saved keys.
const retained = vi.hoisted(() => ({ calls: [] as string[][] }));
vi.mock("@/components/terminal/paneHost", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/components/terminal/paneHost")>();
  return {
    ...real,
    retainPaneHosts: (ids: Iterable<string>) => {
      retained.calls.push([...ids]);
      return real.retainPaneHosts(ids);
    },
  };
});
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <div />,
}));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: () => <div />,
}));

import TerminalsPage from "@/app/terminals/page";

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

beforeEach(() => {
  localStorage.clear();
  api.terminalReads = 0;
  retained.calls = [];
  api.responses = {
    "/terminals/shells": { shells: [] },
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
  };
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  window.history.replaceState(null, "", "/");
  setHidden(false);
});

let hidden = false;
function setHidden(v: boolean) {
  hidden = v;
}
Object.defineProperty(document, "visibilityState", {
  configurable: true,
  get: () => (hidden ? "hidden" : "visible"),
});

const advance = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

describe("follow-ups: every pane reopening, a focus link, slow re-reads (v1.406.0 part 6)", () => {
  it("says 'Reopening N terminals…' — never the empty Build door — while every pane is reopening", async () => {
    vi.useFakeTimers();
    api.terminals = [
      { terminals: [], restoring: ["t1", "t2"] },
      { terminals: [term("t1"), term("t2")] },
    ];
    render(<TerminalsPage />);
    await advance(50);
    expect(screen.getByTestId("build-reopening").textContent).toBe("Reopening 2 terminals…");
    expect(screen.getByTestId("rail-reopening").textContent).toBe("Reopening 2 terminals…");
    expect(screen.queryByTestId("build-empty")).toBeNull();
    expect(screen.queryByText("No terminals yet")).toBeNull();
    await advance(3_100);
    expect(screen.getByTestId("terminal-pane-t1")).toBeTruthy();
    expect(screen.getByTestId("terminal-pane-t2")).toBeTruthy();
    expect(screen.queryByTestId("build-reopening")).toBeNull();
  });

  it("control: with nothing reopening and no pane, the empty Build door shows as before", async () => {
    vi.useFakeTimers();
    api.terminals = [{ terminals: [] }];
    render(<TerminalsPage />);
    await advance(50);
    expect(screen.getByTestId("build-empty").textContent).toBe("No terminals yet");
    expect(screen.queryByTestId("build-reopening")).toBeNull();
  });

  it("a ?focus= link to a pane still reopening waits for that pane, not the first one", async () => {
    vi.useFakeTimers();
    window.history.replaceState(null, "", "/terminals?focus=t2");
    api.terminals = [
      { terminals: [term("t1")], restoring: ["t2"] },
      { terminals: [term("t1"), term("t2")] },
    ];
    render(<TerminalsPage />);
    await advance(50);
    expect(screen.getByTestId("build-reopening").textContent).toBe("Reopening 1 terminal…");
    expect(screen.getByTestId("rail-pane-t1").style.visibility).toBe("hidden");
    await advance(3_100);
    expect(screen.queryByTestId("build-reopening")).toBeNull();
    expect(screen.getByTestId("rail-pane-t2").style.visibility).toBe("visible");
    expect(screen.getByTestId("rail-pane-t1").style.visibility).toBe("hidden");
  });

  it("keeps re-reading every 15 s after the first minute, only while visible", async () => {
    vi.useFakeTimers();
    const still = { terminals: [term("t1")], restoring: ["t2"] };
    // The first answer + 20 fast re-reads + 1 slow one say "still reopening".
    api.terminals = [...Array(22).fill(still), { terminals: [term("t1"), term("t2")] }];
    render(<TerminalsPage />);
    await advance(50);
    await advance(20 * 3_000 + 100);
    expect(api.terminalReads).toBe(21);
    await advance(10_000);
    expect(api.terminalReads).toBe(21); // slow now: nothing before 15 s
    await advance(5_100);
    expect(api.terminalReads).toBe(22);
    expect(screen.queryByTestId("terminal-pane-t2")).toBeNull();
    setHidden(true);
    await advance(15_100);
    expect(api.terminalReads).toBe(22); // hidden: no read
    setHidden(false);
    await advance(15_100);
    expect(api.terminalReads).toBe(23);
    expect(screen.getByTestId("terminal-pane-t2")).toBeTruthy();
    await advance(60_000);
    expect(api.terminalReads).toBe(23); // nothing left reopening: it stops
  });
});

describe("a pane the daemon is still reopening (v1.406.0 part 6)", () => {
  it("keeps its saved keys and joins the rail once its shell has started", async () => {
    localStorage.setItem("ij.pane.view.t2", "chat");
    localStorage.setItem("ij.pane.view.gone", "chat");
    api.terminals = [
      { terminals: [term("t1")], restoring: ["t2"] },
      { terminals: [term("t1"), term("t2")] },
    ];
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    // A pane nobody offers is pruned; the one still reopening is not.
    expect(localStorage.getItem("ij.pane.view.gone")).toBeNull();
    expect(localStorage.getItem("ij.pane.view.t2")).toBe("chat");
    expect(screen.queryByTestId("terminal-pane-t2")).toBeNull();
    expect(retained.calls[0]).toEqual(["t1", "t2"]);
    // The page re-reads the list while a pane is restoring, and adds it.
    await screen.findByTestId("terminal-pane-t2", {}, { timeout: 10_000 });
    expect(localStorage.getItem("ij.pane.view.t2")).toBe("chat");
  });

  it("reads the list once when nothing is restoring", async () => {
    api.terminals = [{ terminals: [term("t1")] }];
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    await new Promise((r) => setTimeout(r, 3500));
    expect(api.terminalReads).toBe(1);
  });
});
