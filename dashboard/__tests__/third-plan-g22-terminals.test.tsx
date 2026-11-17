/**
 * Third plan TM3 + TM19 — a terminal from before a restart that could not be
 * reopened is listed ONE ROW EACH on the Build page, with the daemon's plain
 * reason, "Try again" and "Close".
 *
 * Before: one whose program was uninstalled or moved vanished with no word;
 * one the start gave up on was only a count in a sentence, could not be
 * dismissed, and was tried again at every start. `GET /terminals` now names
 * each (`restore_failed_panes: [{id, name, cwd, reason}]`); "Try again" is
 * `POST /terminals/not-reopened/{id}/retry`, "Close" (asked first, as closing
 * a terminal is) `DELETE /terminals/not-reopened/{id}`.
 *
 * Page mocks copied from build-restore-failed-v14181.test.tsx.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
    terminals: [] as unknown[],
    terminalReads: 0,
    posts: [] as string[],
    dels: [] as string[],
    postAnswer: null as null | (() => Promise<unknown>),
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
  post: (path: string) => {
    api.posts.push(path);
    return api.postAnswer ? api.postAnswer() : Promise.resolve({});
  },
  patch: () => Promise.resolve({}),
  del: (path: string) => {
    api.dels.push(path);
    return Promise.resolve({ closed: true });
  },
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
// The real ConfirmButton asks with a second press; this stand-in keeps that
// shape (first press arms, second confirms) so "Close asks first" is pinned.
vi.mock("@/components/ui", () => ({
  Card: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  OfflineHint: () => <div />,
  ErrorNote: ({ children }: { children?: React.ReactNode }) => <div role="alert">{children}</div>,
  Spinner: ({ label }: { label?: string }) => <div>{label}</div>,
  ConfirmButton: ({
    label,
    confirmLabel,
    onConfirm,
    testId,
  }: {
    label: string;
    confirmLabel?: string;
    onConfirm?: () => void | Promise<void>;
    testId?: string;
  }) => {
    const [armed, setArmed] = React.useState(false);
    return (
      <button
        type="button"
        data-testid={testId}
        onClick={() => {
          if (!armed) setArmed(true);
          else void onConfirm?.();
        }}
      >
        {armed ? confirmLabel ?? "Confirm?" : label}
      </button>
    );
  },
  Empty: ({ title }: { title?: string }) => <div data-testid="build-empty">{title}</div>,
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div /> }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div /> }));

import TerminalsPage from "@/app/terminals/page";
import { decodeNotReopened } from "@/components/terminal/NotReopenedList";

const term = (id: string) => ({
  id,
  cwd: "C:\\proj\\alpha",
  shell: "pwsh",
  argv: [],
  cols: 120,
  rows: 30,
  alive: true,
  exit_code: null,
  created_at: "2026-11-17T00:00:00Z",
});

const GONE_WHY = "Its program, pwsh, is no longer on this PC (it may have been uninstalled or moved).";

const listing = () => ({
  terminals: [term("t1")],
  restore_failed: { count: 2, ids: ["t2", "t3"], reason: "their shell didn't start" },
  restore_failed_panes: [
    { id: "t2", name: "notes", cwd: "C:\\proj\\beta", reason: GONE_WHY },
    { id: "t3", name: null, cwd: "C:\\proj\\gamma", reason: "Its shell did not start within a minute." },
  ],
});

beforeEach(() => {
  localStorage.clear();
  api.terminalReads = 0;
  api.posts = [];
  api.dels = [];
  api.postAnswer = null;
  api.responses = {
    "/terminals/shells": { shells: [] },
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
  };
});

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

describe("decodeNotReopened", () => {
  it("keeps well-formed rows and drops the rest", () => {
    expect(
      decodeNotReopened([
        { id: "a", name: " x ", cwd: "C:\\w", reason: "Why." },
        { id: "", reason: "no id" },
        "junk",
        { id: "b" },
      ]),
    ).toEqual([
      { id: "a", name: "x", cwd: "C:\\w", reason: "Why." },
      { id: "b", name: null, cwd: null, reason: "" },
    ]);
    expect(decodeNotReopened(undefined)).toEqual([]);
  });
});

describe("terminals that could not be reopened (third plan TM3, TM19)", () => {
  it("lists each with its reason, Try again and Close", async () => {
    api.terminals = [listing()];
    render(<TerminalsPage />);
    const row = await screen.findByTestId("not-reopened-t2");
    expect(row.textContent).toContain("notes");
    expect(row.textContent).toContain(GONE_WHY);
    expect(screen.getByTestId("not-reopened-t3").textContent).toContain("Terminal in gamma");
    expect(screen.getByTestId("not-reopened-retry-t2").textContent).toContain("Try again");
    expect(screen.getByTestId("not-reopened-close-t2").textContent).toBe("Close");
    // The rows replace the one-line sentence in the list of terminals.
    expect(screen.queryByTestId("rail-restore-failed")).toBeNull();
  });

  it("Try again reopens it in place and the row goes", async () => {
    api.terminals = [listing()];
    api.postAnswer = () => Promise.resolve(term("t2"));
    render(<TerminalsPage />);
    fireEvent.click(await screen.findByTestId("not-reopened-retry-t2"));
    await waitFor(() => expect(screen.queryByTestId("not-reopened-t2")).toBeNull());
    expect(api.posts).toEqual(["/terminals/not-reopened/t2/retry"]);
    expect(await screen.findByTestId("terminal-pane-t2")).toBeTruthy();
    expect(screen.getByTestId("not-reopened-t3")).toBeTruthy();
  });

  it("Try again that fails says why on the row and keeps it", async () => {
    api.terminals = [listing()];
    api.postAnswer = () =>
      Promise.reject(
        Object.assign(new api.FakeApiError(`The terminal could not be reopened. ${GONE_WHY}`, 409), {
          detail: `The terminal could not be reopened. ${GONE_WHY}`,
        }),
      );
    render(<TerminalsPage />);
    fireEvent.click(await screen.findByTestId("not-reopened-retry-t2"));
    await waitFor(() =>
      expect(screen.getByTestId("not-reopened-t2").querySelector('[role="status"]')).not.toBeNull(),
    );
    expect(screen.getByTestId("not-reopened-t2")).toBeTruthy();
  });

  it("Close asks first, then forgets it", async () => {
    api.terminals = [listing()];
    render(<TerminalsPage />);
    const close = await screen.findByTestId("not-reopened-close-t2");
    fireEvent.click(close);
    expect(api.dels).toEqual([]); // the first press only asks
    expect(close.textContent).toBe("Confirm close");
    await act(async () => {
      fireEvent.click(close);
    });
    await waitFor(() => expect(screen.queryByTestId("not-reopened-t2")).toBeNull());
    expect(api.dels).toEqual(["/terminals/not-reopened/t2"]);
    expect(screen.getByTestId("not-reopened-t3")).toBeTruthy();
  });
});
