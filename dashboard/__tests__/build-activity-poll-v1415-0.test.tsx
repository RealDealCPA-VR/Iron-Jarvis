/**
 * v1.415.0 (idle cost): Build's terminal activity poll (`GET
 * /terminals/activity`, every 2.5 s) runs only while the TERMINAL view is
 * shown. Its answer draws the rail's state dots, names, accounts and the
 * "Open in Code view" offer, none of which exists in the Code view, so in the
 * Code view it was 24 requests a minute (the daemon ~1.6 ms of CPU each, plus
 * the window's fetch and JSON) for nothing on screen. Coming back to Terminal
 * asks at once (fresher than before, when the held answer could be up to
 * 2.5 s old) and polls again.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

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
    gets: [] as string[],
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
    api.gets.push(path);
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
import { BUILD_VIEW_KEY, resetBuildViewMemory } from "@/lib/buildApp";

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

const ACTIVITY = "/terminals/activity";
const activityGets = () => api.gets.filter((p) => p === ACTIVITY).length;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  localStorage.clear();
  resetBuildViewMemory();
  api.gets = [];
  api.posts = [];
  api.postReply = {};
  api.postFail = {};
  api.responses = {
    "/terminals": { terminals: [term("t1", "C:\\proj\\alpha")] },
    "/terminals/shells": { shells: [{ name: "pwsh", argv: ["pwsh"] }], default: "pwsh" },
    "/projects": { projects: [] },
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
    [ACTIVITY]: { panes: [{ id: "t1", name: null, agent_cli: null, state: "unknown", state_line: "", alive: true }] },
  };
  window.history.replaceState({}, "", "/terminals");
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  window.history.replaceState({}, "", "/");
});

/** Let the interval clock run `ms`, one 2.5 s tick at a time, letting each
 *  tick's request settle (a poll waits for its own request still on the wire). */
async function pass(ms: number) {
  for (let t = 0; t < ms; t += 2500) {
    await act(async () => {
      vi.advanceTimersByTime(2500);
      for (let i = 0; i < 5; i++) await Promise.resolve();
    });
  }
}

describe("Build's terminal activity poll runs only while the terminals are shown (v1.415.0)", () => {
  it("the Terminal view polls every 2.5 s (the control)", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    const before = activityGets();
    await pass(25_000);
    expect(activityGets() - before).toBe(10);
  });

  it("the Code view asks nothing about terminals while it is shown", async () => {
    localStorage.setItem(BUILD_VIEW_KEY, "code");
    render(<TerminalsPage />);
    await screen.findByTestId("stub-code-view");
    const before = activityGets();
    await pass(25_000);
    expect(activityGets() - before).toBe(0);
  });

  it("from Terminal to Code it stops; back to Terminal it asks at once, then every 2.5 s again", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    fireEvent.click(screen.getByTestId("build-view-code"));
    await screen.findByTestId("stub-code-view");
    let before = activityGets();
    await pass(25_000);
    expect(activityGets() - before).toBe(0);

    before = activityGets();
    fireEvent.click(screen.getByTestId("build-view-terminal"));
    await screen.findByTestId("terminal-pane-t1");
    // At once: no interval tick has run since the click.
    expect(activityGets() - before).toBe(1);
    await pass(25_000);
    expect(activityGets() - before).toBe(11);
  });
});
