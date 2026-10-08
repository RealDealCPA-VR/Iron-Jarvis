/**
 * v1.314.0 — UX wave 2 ("words & empty states"), track T2: Build (terminals).
 *
 * WHAT THE USER SAW (fresh__terminals / desk__terminals / tall__terminals):
 * the biggest thing on a fresh Build page was an empty stage holding only
 * grey text "No terminals yet — hit New terminal." (no button in it), and the
 * same job had three names: header "New terminal", rail "New pane" under a
 * "Panes" heading, canvas "Add". The subtitle said "hit +", and the default
 * rail shape has no "+" button.
 *
 * Words and the empty stage ONLY — no terminal behaviour changes. Anti-vacuity:
 * every existing door stays (header New terminal, Shell select, the rail's
 * new-terminal button still calls the page's handler, the Canvas switch), the
 * empty stage's button POSTs /terminals exactly like the header's, and the
 * phrase "No terminals yet" stays (the out-of-suite review test
 * __review_20260922__/dash-core/build-offline-once regex-matches it —
 * "No terminals open yet" would NOT match /No terminals yet/).
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

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
    posts: [] as { path: string; body?: unknown }[],
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
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    if (path === "/terminals") {
      return Promise.resolve({
        id: "t-new",
        cwd: "C:\\Users\\me",
        shell: "pwsh",
        argv: [],
        cols: 120,
        rows: 30,
        alive: true,
        exit_code: null,
        created_at: "2026-10-01T00:00:00Z",
      });
    }
    return Promise.resolve({});
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

// xterm never enters jsdom (build-load-v1243's stub).
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
// Unlike the older Build harnesses, this one renders the SUBTITLE too.
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, subtitle, actions }: { title: string; subtitle?: React.ReactNode; actions?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      <p data-testid="page-subtitle">{subtitle}</p>
      {actions}
    </div>
  ),
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <div data-testid="directory-tree" />,
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
// NOTE: @/components/ui is deliberately NOT mocked — the empty stage must be
// the shared <Empty> (data-testid="empty-state"), not a private div.

import TerminalsPage from "@/app/terminals/page";
import { PaneRail } from "@/components/terminal/PaneRail";

const SHAPE_KEY = "ij.build.shape";

function seedEmpty() {
  api.responses = {
    "/terminals": { terminals: [] },
    "/terminals/shells": { shells: [{ name: "pwsh" }, { name: "cmd" }] },
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
    "/terminals/activity": { panes: [] },
  };
}

const src = (rel: string): string => readFileSync(join(process.cwd(), rel), "utf8").replace(/\r\n/g, "\n");

beforeEach(() => {
  localStorage.clear();
  api.posts = [];
  seedEmpty();
});
afterEach(cleanup);

/* ========================================================================== */

describe("Build's empty stage teaches and offers a terminal (rail shape, the default)", () => {
  it("the stage is the shared Empty, keeps 'No terminals yet', and its button opens a terminal", async () => {
    render(<TerminalsPage />);
    const empty = await screen.findByTestId("empty-state");
    expect(empty.textContent ?? "").toMatch(/No terminals yet/);
    const open = within(empty).getByRole("button", { name: /terminal/i });
    fireEvent.click(open);
    await waitFor(() => expect(api.posts.filter((p) => p.path === "/terminals")).toHaveLength(1));
  });

  it("anti-vacuity: the header's New terminal and the Shell picker are still there", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("pane-rail");
    expect(screen.getByLabelText("Shell")).toBeInTheDocument();
    const header = screen
      .getAllByRole("button", { name: /New terminal/ })
      .filter((b) => !b.closest('[data-testid="pane-rail"]') && !b.closest('[data-testid="empty-state"]'));
    expect(header.length).toBeGreaterThanOrEqual(1);
    fireEvent.click(header[0]);
    await waitFor(() => expect(api.posts.filter((p) => p.path === "/terminals")).toHaveLength(1));
  });

  it("the subtitle names a button that exists (no 'hit +')", async () => {
    render(<TerminalsPage />);
    const sub = await screen.findByTestId("page-subtitle");
    expect(sub.textContent ?? "").not.toMatch(/hit \+/);
    expect(sub.textContent ?? "").toMatch(/New terminal/);
  });
});

describe("One name for a terminal: the rail says Terminals / New terminal", () => {
  function renderRail(onNew = vi.fn()) {
    render(
      <PaneRail
        panes={[]}
        focusedId={null}
        onFocus={() => {}}
        onClose={() => {}}
        onRename={() => {}}
        onNew={onNew}
        footer={<button type="button">Canvas</button>}
      />,
    );
    return onNew;
  }

  it("the heading reads Terminals and the button New terminal — never pane", () => {
    const onNew = renderRail();
    const rail = screen.getByTestId("pane-rail");
    expect(within(rail).getByText("Terminals")).toBeInTheDocument();
    expect(within(rail).queryByText("Panes")).toBeNull();
    const btn = within(rail).getByRole("button", { name: /New terminal/ });
    expect(within(rail).queryByRole("button", { name: /New pane/ })).toBeNull();
    // Anti-vacuity: the button still runs the page's own handler.
    fireEvent.click(btn);
    expect(onNew).toHaveBeenCalledTimes(1);
    // The footer (the Canvas switch) still renders.
    expect(within(rail).getByRole("button", { name: "Canvas" })).toBeInTheDocument();
  });

  it("the empty rail line speaks of terminals, not panes", () => {
    renderRail();
    const rail = screen.getByTestId("pane-rail");
    expect(rail.textContent ?? "").not.toMatch(/No panes yet/);
    expect(rail.textContent ?? "").toMatch(/terminal/i);
  });
});

describe("Canvas shape: the floating button is 'New terminal', not 'Add'", () => {
  it("no button is named just 'Add'; the floating door opens a terminal", async () => {
    localStorage.setItem(SHAPE_KEY, "canvas");
    render(<TerminalsPage />);
    await screen.findByTestId("shape-rail");
    expect(screen.queryByRole("button", { name: /^\s*Add\s*$/ })).toBeNull();
    const floating = screen.getByTitle("Open a new terminal");
    expect(floating.textContent ?? floating.getAttribute("aria-label") ?? "").toMatch(/New terminal/);
    fireEvent.click(floating);
    await waitFor(() => expect(api.posts.filter((p) => p.path === "/terminals")).toHaveLength(1));
  });
});

describe("source: PaneRail carries no 'New pane' / 'Panes' user words", () => {
  it("the rail's visible strings say terminal", () => {
    const s = src("components/terminal/PaneRail.tsx");
    expect(s).not.toMatch(/>\s*New pane\s*</);
    expect(s).not.toMatch(/>\s*Panes\s*</);
  });
});
