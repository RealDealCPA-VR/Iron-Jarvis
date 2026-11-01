/**
 * v1.418.1 — a pane the daemon GAVE UP reopening after a restart.
 *
 * THE USER BUG: Build › Terminal said "Reopening 3 terminals…" for more than
 * four hours (the three shells never started; nothing ever gave up). The
 * daemon now gives up after RESTORE_GIVE_UP_S and `GET /terminals` carries
 * `restore_failed: {count, ids, reason}`. The page must drop "Reopening…",
 * say so in one plain line beside the normal New terminal door, keep the
 * panes' saved keys, and still add a pane whose shell starts late.
 *
 * Mocks copied from build-restoring-v1406.test.tsx.
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


const FAILED_3 =
  "3 terminals from before the restart couldn't be reopened: their shell didn't start. " +
  "Open a new terminal, or restart Iron Jarvis to try again.";

describe("the daemon gave up reopening (v1.418.1)", () => {
  it("swaps 'Reopening…' for one plain line beside the New terminal door", async () => {
    vi.useFakeTimers();
    const gaveUp = {
      terminals: [],
      restore_failed: {
        count: 3,
        ids: ["t1", "t2", "t3"],
        reason: "their shell didn't start",
      },
    };
    api.terminals = [{ terminals: [], restoring: ["t1", "t2", "t3"] }, gaveUp];
    render(<TerminalsPage />);
    await advance(50);
    expect(screen.getByTestId("build-reopening").textContent).toBe("Reopening 3 terminals…");
    expect(screen.queryByTestId("build-restore-failed")).toBeNull();
    // The next re-read carries the give-up.
    await advance(3_100);
    expect(screen.queryByTestId("build-reopening")).toBeNull();
    expect(screen.queryByTestId("rail-reopening")).toBeNull();
    expect(screen.getByTestId("build-restore-failed").textContent).toBe(FAILED_3);
    expect(screen.getByTestId("rail-restore-failed").textContent).toBe(FAILED_3);
    // The normal door is back.
    expect(screen.getByTestId("build-empty").textContent).toBe("No terminals yet");
    expect(screen.getByTestId("rail-new-pane")).toBeTruthy();
  });

  it("keeps the given-up panes' saved keys (the next start tries them again)", async () => {
    vi.useFakeTimers();
    localStorage.setItem("ij.pane.view.t2", "chat");
    localStorage.setItem("ij.pane.view.gone", "chat");
    api.terminals = [
      { terminals: [term("t1")], restore_failed: { count: 1, ids: ["t2"], reason: "its shell didn't start" } },
    ];
    render(<TerminalsPage />);
    await advance(50);
    expect(screen.getByTestId("terminal-pane-t1")).toBeTruthy();
    expect(localStorage.getItem("ij.pane.view.gone")).toBeNull();
    expect(localStorage.getItem("ij.pane.view.t2")).toBe("chat");
    expect(retained.calls[0]).toEqual(["t1", "t2"]);
    // With a terminal open, the line rides the rail only.
    expect(screen.getByTestId("rail-restore-failed").textContent).toBe(
      "1 terminal from before the restart couldn't be reopened: its shell didn't start. " +
        "Open a new terminal, or restart Iron Jarvis to try again.",
    );
    expect(screen.queryByTestId("build-restore-failed")).toBeNull();
  });

  it("a shell that starts after the give-up still joins the rail, and the line goes", async () => {
    vi.useFakeTimers();
    api.terminals = [
      { terminals: [], restore_failed: { count: 1, ids: ["t1"], reason: "its shell didn't start" } },
      { terminals: [], restore_failed: { count: 1, ids: ["t1"], reason: "its shell didn't start" } },
      { terminals: [term("t1")] },
    ];
    render(<TerminalsPage />);
    await advance(50);
    expect(screen.getByTestId("build-restore-failed")).toBeTruthy();
    await advance(3_100);
    expect(screen.getByTestId("build-restore-failed")).toBeTruthy();
    await advance(3_100);
    expect(screen.getByTestId("terminal-pane-t1")).toBeTruthy();
    expect(screen.queryByTestId("build-restore-failed")).toBeNull();
    expect(screen.queryByTestId("rail-restore-failed")).toBeNull();
    const reads = api.terminalReads;
    await advance(60_000);
    expect(api.terminalReads).toBe(reads); // nothing left to wait for: it stops
  });

  it("control: no give-up, no line", async () => {
    vi.useFakeTimers();
    api.terminals = [{ terminals: [] }];
    render(<TerminalsPage />);
    await advance(50);
    expect(screen.getByTestId("build-empty").textContent).toBe("No terminals yet");
    expect(screen.queryByTestId("build-restore-failed")).toBeNull();
    expect(screen.queryByTestId("rail-restore-failed")).toBeNull();
  });
});

describe("decodeRestoreFailed (v1.418.1)", () => {
  it("reads the daemon's note and refuses what is not one", async () => {
    const { decodeRestoreFailed, restoreFailedWords } = await import("@/lib/restoreFailed");
    expect(decodeRestoreFailed(undefined)).toBeNull();
    expect(decodeRestoreFailed({ count: 0, ids: [] })).toBeNull();
    expect(decodeRestoreFailed("nope")).toBeNull();
    const a = decodeRestoreFailed({ count: 2, ids: ["a", 3, "b"] });
    expect(a).toEqual({ count: 2, ids: ["a", "b"], reason: "their shell didn't start" });
    expect(decodeRestoreFailed({ ids: ["x"] })?.reason).toBe("its shell didn't start");
    const b = decodeRestoreFailed({ ids: ["x"], reason: "its shell didn't start." });
    expect(b).toEqual({ count: 1, ids: ["x"], reason: "its shell didn't start" });
    expect(restoreFailedWords(b!)).toBe(
      "1 terminal from before the restart couldn't be reopened: its shell didn't start. " +
        "Open a new terminal, or restart Iron Jarvis to try again.",
    );
  });
});
