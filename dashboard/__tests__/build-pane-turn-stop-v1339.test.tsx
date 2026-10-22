/**
 * v1.339.0 — closing a terminal stops its chat's running turn.
 *
 * A Build pane's chat turn is NAMED now (it outlives a reload and the pane
 * follows it again), so dropping the connection no longer stops it. The pane
 * chat has no Stop button; closing the terminal was the one way to end its
 * turn, so the close says so to the daemon by name.
 *
 * (Harness copied from build-folder-v1337-2.)
 *
 * WHAT THE USER SAW: after an update (or closing and reopening the app) Build
 * still showed the project they had been in, but a new terminal opened in
 * their home folder; they had to walk the folder tree back to the project
 * every time. The folder a new terminal used was `selectedPath`, a pick that
 * lives only in page state — every reload reset it to nothing.
 *
 * Now the page remembers the folder it last worked in (`ij.build.folder`: the
 * last pick, the last new terminal's folder, the pane in front's folder) and
 * "New terminal" uses: the pick, else the pane in front's folder, else that.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    FakeApiError,
    responses: {} as Record<string, unknown>,
    posts: [] as { path: string; body?: Record<string, unknown> }[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    const r = api.responses[path];
    return r === undefined
      ? Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404))
      : Promise.resolve(r);
  },
  post: (path: string, body?: Record<string, unknown>) => {
    api.posts.push({ path, body });
    if (path === "/terminals") {
      return Promise.resolve({
        id: `t-${api.posts.length}`,
        cwd: (body?.cwd as string | undefined) ?? "C:\\Users\\me",
        shell: "pwsh",
        argv: [],
        cols: 120,
        rows: 30,
        alive: true,
        exit_code: null,
        created_at: "2026-10-14T00:00:00Z",
      });
    }
    return Promise.resolve({});
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("next/dynamic", () => ({
  default: () => {
    function DynamicStub(props: Record<string, unknown>) {
      if (typeof props.paneId === "string") return <div data-testid={`pane-chat-${props.paneId}`} />;
      const info = props.info as { id: string; shell: string };
      const onClose = props.onClose as (() => void) | undefined;
      return (
        <div data-testid={`terminal-pane-${info.id}`}>
          {info.shell}
          <button type="button" data-testid={`term-close-${info.id}`} onClick={() => onClose?.()}>
            x
          </button>
        </div>
      );
    }
    return DynamicStub;
  },
}));
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
// The folder tree's one job here: a pick calls the page's onSelect.
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: ({ onSelect }: { onSelect: (p: string) => void }) => (
    <button type="button" data-testid="pick-other" onClick={() => onSelect("D:\\Other")}>
      pick
    </button>
  ),
}));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: () => <div data-testid="files-panel" />,
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import TerminalsPage from "@/app/terminals/page";
import { notePaneTurn } from "@/components/terminal/paneChatCore";


const pane = {
  id: "term_a",
  cwd: "C:\work",
  shell: "pwsh",
  argv: [],
  cols: 120,
  rows: 30,
  alive: true,
  exit_code: null,
  created_at: "2026-10-15T00:00:00Z",
};

beforeEach(() => {
  localStorage.clear();
  api.posts = [];
  api.responses = {
    "/terminals": { terminals: [pane] },
    "/terminals/shells": { shells: [{ name: "pwsh" }] },
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
    "/terminals/activity": { panes: [] },
  };
});
afterEach(() => {
  notePaneTurn("term_a", null);
  cleanup();
});

async function closeIt() {
  await screen.findByTestId("terminal-pane-term_a");
  fireEvent.click(screen.getByTestId("term-close-term_a")); // the pane's X
  fireEvent.click(await screen.findByRole("button", { name: /Close terminal/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Confirm close/ }));
}

describe("closing a terminal ends its chat's turn", () => {
  it("a running pane-chat turn is stopped by name", async () => {
    notePaneTurn("term_a", "chat-abc");
    render(<TerminalsPage />);
    await closeIt();
    await waitFor(() =>
      expect(api.posts.map((p) => p.path)).toContain("/chat/turns/chat-abc/stop"),
    );
  });

  it("no turn running: nothing is stopped", async () => {
    render(<TerminalsPage />);
    await closeIt();
    await waitFor(() => expect(screen.queryByTestId("terminal-pane-term_a")).toBeNull());
    expect(api.posts.some((p) => p.path.endsWith("/stop"))).toBe(false);
  });
});
