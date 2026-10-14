/**
 * v1.337.2 — "New terminal" opens where the user was working.
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
      return <div data-testid={`terminal-pane-${info.id}`}>{info.shell}</div>;
    }
    return DynamicStub;
  },
}));
vi.mock("react-rnd", () => ({
  Rnd: ({ children }: { children?: React.ReactNode }) => <div data-testid="rnd">{children}</div>,
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

const KEY = "ij.build.folder";
const QB = "C:\\Users\\VR\\Projects\\Open Source QB";

function seed(terminals: unknown[] = []) {
  api.responses = {
    "/terminals": { terminals },
    "/terminals/shells": { shells: [{ name: "pwsh" }] },
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
    "/terminals/activity": { panes: [] },
  };
}

const restored = {
  id: "term_restored",
  cwd: QB,
  shell: "pwsh",
  argv: [],
  cols: 120,
  rows: 30,
  alive: true,
  exit_code: null,
  created_at: "2026-10-14T00:00:00Z",
};

const terminalPosts = () => api.posts.filter((p) => p.path === "/terminals");

async function pressNewTerminal() {
  const before = terminalPosts().length;
  fireEvent.click(await screen.findByTestId("header-new-terminal"));
  await waitFor(() => expect(terminalPosts().length).toBe(before + 1));
  return terminalPosts()[terminalPosts().length - 1].body?.cwd;
}

beforeEach(() => {
  localStorage.clear();
  api.posts = [];
  seed();
});
afterEach(cleanup);

describe("New terminal opens where the user was working", () => {
  it("after a reload, a new terminal opens in the last terminal's folder", async () => {
    localStorage.setItem(KEY, QB);
    render(<TerminalsPage />);
    expect(await pressNewTerminal()).toBe(QB);
  });

  it("a new terminal's folder is remembered for the next visit", async () => {
    render(<TerminalsPage />);
    fireEvent.click(await screen.findByTestId("pick-other"));
    expect(await pressNewTerminal()).toBe("D:\\Other");
    await waitFor(() => expect(localStorage.getItem(KEY)).toBe("D:\\Other"));
    cleanup();
    render(<TerminalsPage />);
    expect(await pressNewTerminal()).toBe("D:\\Other");
  });

  it("a folder picked but not yet used is remembered too", async () => {
    render(<TerminalsPage />);
    fireEvent.click(await screen.findByTestId("pick-other"));
    await waitFor(() => expect(localStorage.getItem(KEY)).toBe("D:\\Other"));
    cleanup();
    render(<TerminalsPage />);
    expect(await pressNewTerminal()).toBe("D:\\Other");
  });

  it("with a restored pane in front, New terminal opens in its folder", async () => {
    seed([restored]);
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-term_restored");
    expect(await pressNewTerminal()).toBe(QB);
  });

  it("closing the restored pane still leaves its folder for the next terminal", async () => {
    seed([restored]);
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-term_restored");
    await waitFor(() => expect(localStorage.getItem(KEY)).toBe(QB));
    cleanup();
    seed([]); // the user closed it
    render(<TerminalsPage />);
    expect(await pressNewTerminal()).toBe(QB);
  });

  it("an explicit pick in the folder tree still wins", async () => {
    seed([restored]);
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-term_restored");
    fireEvent.click(document.getElementById("build-tab-folders") as HTMLElement);
    fireEvent.click(await screen.findByTestId("pick-other"));
    expect(await pressNewTerminal()).toBe("D:\\Other");
  });

  it("the empty stage names the folder it will open in", async () => {
    localStorage.setItem(KEY, QB);
    render(<TerminalsPage />);
    const empty = await screen.findByTestId("empty-state");
    await waitFor(() => expect(empty.textContent ?? "").toContain(`A new one opens in ${QB}.`));
  });

  it("anti-vacuity: with nothing remembered and no pane, no folder is sent (the home default)", async () => {
    render(<TerminalsPage />);
    expect(await pressNewTerminal()).toBeUndefined();
  });
});
