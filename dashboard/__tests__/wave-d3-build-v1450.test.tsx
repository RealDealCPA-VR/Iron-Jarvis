/**
 * v1.450.0 — Wave D, lane D3, item 40: Build's stuck states end.
 *
 *  - the Launch menu's "Detecting…" turns into "Couldn't check which coding
 *    tools are installed. Try again" when its read fails or passes
 *    DETECT_BOUND_MS (it was "Detecting…" for ever: the page swallowed the
 *    failure), and Try again asks again;
 *  - a failed read of the terminal list says "Your terminals couldn't load.
 *    Try again" (lib/loadFailed) beside the empty door, never the raw error,
 *    and Try again re-reads it;
 *  - a failed New terminal keeps a 4xx's own sentence and words a 5xx.
 *
 * jsdom cannot draw a TerminalPane, so the pane is the page's own stub and
 * its props are recorded (the Launch menu's words are pinned from the
 * source + the lib). Mocks copied from build-restore-failed-v14181.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    /** Per path, a function answering each GET (else `responses`). */
    answer: {} as Record<string, () => Promise<unknown>>,
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    postAnswer: null as null | (() => Promise<unknown>),
    FakeApiError,
  };
});
const panes = vi.hoisted(() => ({ props: [] as Record<string, unknown>[] }));

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  get: (path: string) => {
    api.gets.push(path);
    const fn = api.answer[path];
    if (fn) return fn();
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    return Promise.resolve(r);
  },
  post: (path: string, body: unknown) => {
    api.posts.push({ path, body });
    return api.postAnswer ? api.postAnswer() : Promise.resolve({});
  },
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("next/dynamic", () => ({
  default: () => {
    function DynamicStub(props: Record<string, unknown>) {
      const info = props.info as { id: string } | undefined;
      if (!info) return null;
      panes.props.push(props);
      return <div data-testid={`terminal-pane-${info.id}`} data-ai-clis={String(props.aiClisState)} />;
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
  OfflineHint: () => <div data-testid="offline" />,
  ErrorNote: ({ children }: { children?: React.ReactNode }) => <div role="alert">{children}</div>,
  Spinner: ({ label }: { label?: string }) => <div>{label}</div>,
  ConfirmButton: ({ label }: { label: string }) => <button type="button">{label}</button>,
  Empty: ({ title, action }: { title?: string; action?: { label: string; onClick: () => void } }) => (
    <div data-testid="build-empty">
      {title}
      {action ? (
        <button type="button" onClick={action.onClick}>
          {action.label}
        </button>
      ) : null}
    </div>
  ),
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div /> }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div /> }));

import TerminalsPage from "@/app/terminals/page";
import { DETECT_BOUND_MS, DETECT_FAILED_WORDS, NONE_FOUND_WORDS } from "@/lib/buildStuck";

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

const read = (rel: string) => readFileSync(join(process.cwd(), rel), "utf8").replace(/\r\n/g, "\n");
const lastState = () => panes.props[panes.props.length - 1]?.aiClisState;
const advance = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

beforeEach(() => {
  localStorage.clear();
  panes.props = [];
  api.gets = [];
  api.posts = [];
  api.answer = {};
  api.postAnswer = null;
  api.responses = {
    "/terminals": { terminals: [term("t1")] },
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
});

describe("40: the Launch menu's Detecting… has an end", () => {
  it("a read that fails says it couldn't check; Try again asks again and an answer ends it", async () => {
    let fail = true;
    api.answer["/terminals/ai-clis"] = () =>
      fail ? Promise.reject(new api.FakeApiError("boom", 500)) : Promise.resolve({ clis: [] });
    render(<TerminalsPage />);
    await waitFor(() => expect(lastState()).toBe("failed"));
    fail = false;
    const retry = panes.props[panes.props.length - 1].onRetryAiClis as () => void;
    act(() => retry());
    await waitFor(() => expect(lastState()).toBe("ready"));
    expect(api.gets.filter((p) => p === "/terminals/ai-clis")).toHaveLength(2);
  });

  it("a read that never answers is given up on after DETECT_BOUND_MS", async () => {
    vi.useFakeTimers();
    api.answer["/terminals/ai-clis"] = () => new Promise(() => {});
    render(<TerminalsPage />);
    await advance(50);
    expect(lastState()).toBe("detecting");
    await advance(DETECT_BOUND_MS - 100);
    expect(lastState()).toBe("detecting");
    await advance(200);
    expect(lastState()).toBe("failed");
  });

  it("source: the menu draws the three states in words with Try again", () => {
    const src = read("components/terminal/TerminalPane.tsx");
    const at = src.indexOf('data-testid="launch-detect"');
    const block = src.slice(at, at + 1400);
    expect(block).toContain('aiClisState === "failed"');
    expect(block).toContain("DETECT_FAILED_WORDS");
    expect(block).toContain('data-testid="launch-detect-retry"');
    expect(block).toContain("onRetryAiClis()");
    expect(block).toContain("NONE_FOUND_WORDS");
    expect(DETECT_FAILED_WORDS).toBe("Couldn't check which coding tools are installed.");
    expect(NONE_FOUND_WORDS).toBe("No coding tools found on this PC.");
  });
});

describe("40: a list of terminals that could not be read", () => {
  it("says so in the one load-failed line with Try again, never the raw error", async () => {
    let fail = true;
    api.answer["/terminals"] = () =>
      fail ? Promise.reject(new api.FakeApiError("Traceback (most recent call last)", 500)) : Promise.resolve({ terminals: [term("t9")] });
    render(<TerminalsPage />);
    const line = await screen.findByTestId("build-load-failed");
    expect(line.textContent).toBe("Your terminals couldn't load. Try again.");
    expect(document.body.textContent).not.toContain("Traceback");
    expect(screen.queryByTestId("build-error")).toBeNull();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("terminal-pane-t9")).toBeInTheDocument();
    expect(screen.queryByTestId("build-load-failed")).toBeNull();
  });

  it("a New terminal Iron Jarvis refused keeps its own sentence; a 5xx is worded", async () => {
    api.responses["/terminals"] = { terminals: [] };
    api.postAnswer = () => Promise.reject(new api.FakeApiError("That folder is not writable: C:\\x", 400));
    render(<TerminalsPage />);
    fireEvent.click(within(await screen.findByTestId("build-empty")).getByRole("button", { name: "New terminal" }));
    expect((await screen.findByTestId("build-error")).textContent).toBe("That folder is not writable: C:\\x");
    api.postAnswer = () => Promise.reject(new api.FakeApiError("Internal Server Error", 500));
    fireEvent.click(within(screen.getByTestId("build-empty")).getByRole("button", { name: "New terminal" }));
    await waitFor(() =>
      expect(screen.getByTestId("build-error").textContent).toBe("Couldn't open a terminal. Try again."),
    );
  });
});
