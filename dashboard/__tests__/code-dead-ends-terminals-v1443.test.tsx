/**
 * v1.444.0 — Build's Terminal view names Chat › Code as a REAL link (ease-of-
 * use review, item 11). The subtitle that mentions it lives behind the title
 * as a hover popover, which cannot hold a link, so the mention is a quiet
 * line with the link, under the header, in the Terminal view only (the Code
 * view's start box has its own). Mocks copied from build-app-switch-v1409.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

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
import { resetBuildViewMemory } from "@/lib/buildApp";

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

beforeEach(() => {
  localStorage.clear();
  resetBuildViewMemory();
  api.posts = [];
  api.responses = {
    "/terminals": { terminals: [term("t1", "C:\\proj\\alpha")] },
    "/terminals/shells": { shells: [{ name: "pwsh", argv: ["pwsh"] }], default: "pwsh" },
    "/projects": { projects: [] },
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
    "/terminals/activity": {
      panes: [{ id: "t1", name: null, agent_cli: null, state: "unknown", state_line: "", alive: true }],
    },
  };
  window.history.replaceState({}, "", "/terminals");
});
afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
});

describe("Build's Terminal view links Chat › Code", () => {
  it("one quiet line, a real link to /chat?view=code; the subtitle keeps its words", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    const line = screen.getByTestId("build-to-chat-code");
    expect(line.textContent).toBe("Want Jarvis to write the code itself? Use Chat › Code.");
    expect(within(line).getByRole("link", { name: "Chat › Code" })).toHaveAttribute("href", "/chat?view=code");
    expect(screen.getByTestId("page-header-subtitle").textContent).toContain("are in Chat › Code.");
  });

  it("the Code view does not show it (its start box says it once)", async () => {
    window.history.replaceState({}, "", "/terminals?view=code");
    render(<TerminalsPage />);
    await screen.findByTestId("stub-code-view");
    expect(screen.queryByTestId("build-to-chat-code")).toBeNull();
  });
});
