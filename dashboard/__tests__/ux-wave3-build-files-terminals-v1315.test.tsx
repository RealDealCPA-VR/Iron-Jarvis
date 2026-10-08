/**
 * v1.315.0 — UX & aesthetic wave 3, track T4 (build & files): Build.
 * LAYOUT AND WORDS ONLY — no terminal behaviour is touched (CLAUDE.md
 * paneHost/park/adopt rules stay binding).
 *
 * WHAT THE USER SAW:
 *  - fresh/desk__terminals: the Directory panel opened on "C: — C:\" listing
 *    inetpub, Program Files, Windows… — not the projects the user already
 *    made; "— pick a folder below —" was painted in accent MONOSPACE, so it
 *    read like a chosen value; a bare green "node" chip; and TWO identical
 *    collapse icons 50 px apart (the tab bar's and the Directory header's).
 *  - phone__terminals: at 390 px the first screen was the empty rail stretched
 *    to `calc(100vh - 12rem)` (min 480) — the stage and the folder picker
 *    were one to two screens below the fold.
 *
 * DirectoryTree is SHARED (chat's file picker and Memory's folder picker use
 * it). Chat passes onCollapse, and that header button is chat's ONLY collapse
 * / "back to files" control — so the Build-only changes are OPT-IN props:
 *   showProjects        -> a "Your projects" list (GET /projects → {projects};
 *                          each row a button named by the project, onSelect(
 *                          project.root); rows whose root_exists === false
 *                          are skipped); the drive select stays below it.
 *   hideHeaderCollapse  -> no collapse button in the Directory header.
 * Build passes both. The neutral "No folder picked yet" (sans, not mono) and
 * the chip's tooltip ("Looks like a code project (Node)") apply everywhere.
 *
 * Phone: the rail wrapper's fixed height moves from an inline style into
 * lg-scoped classes that subtract the demo strip (the v1.314.0 coordinator pin
 * forbids a bare `lg:h-[calc(100vh-Xrem)]` in this file):
 *   max-h-56 flex flex-col lg:max-h-none
 *   lg:h-[calc(100vh-12rem-var(--ij-strip-h,0px))] lg:min-h-[480px]
 * The stage keeps its 480 px minimum at every width.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    gets: [] as string[],
    posts: [] as { path: string; body?: unknown }[],
    treeProps: [] as Record<string, unknown>[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    api.gets.push(path);
    const r = api.responses[path];
    return r === undefined
      ? Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404))
      : Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
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
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {actions}
    </div>
  ),
}));
// The REAL tree, with its props recorded (what Build passes is pinned below).
vi.mock("@/components/terminal/DirectoryTree", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/components/terminal/DirectoryTree")>();
  const { createElement } = await import("react");
  return {
    ...real,
    DirectoryTree: (props: Record<string, unknown>) => {
      api.treeProps.push(props);
      return createElement(real.DirectoryTree as React.ComponentType<Record<string, unknown>>, props);
    },
  };
});
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

const { __resetApiCache } = await import("@/lib/apiCache");
const { DirectoryTree } = await import("@/components/terminal/DirectoryTree");
const TerminalsPage = (await import("@/app/terminals/page")).default;

const ROOT_LIST = `/fs/list?path=${encodeURIComponent("C:\\")}&dirs_only=true`;
const PROJECT_ROOT = "C:\\Work\\ClientBooks";

function seed() {
  api.responses = {
    "/terminals": { terminals: [] },
    "/terminals/shells": { shells: [{ name: "pwsh" }, { name: "cmd" }] },
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
    "/terminals/activity": { panes: [] },
    "/fs/drives": { drives: [{ path: "C:\\", label: "C:" }] },
    [ROOT_LIST]: {
      path: "C:\\",
      parent: null,
      entries: [
        { name: "app", path: "C:\\app", is_dir: true, is_project: "node" },
        { name: "Windows", path: "C:\\Windows", is_dir: true, is_project: null },
      ],
    },
    "/projects": {
      projects: [
        { id: "p1", name: "Client Books", brief: "", root: PROJECT_ROOT, status: "active", created_at: "2026-10-01T00:00:00Z", root_exists: true },
        { id: "p2", name: "Old Move", brief: "", root: "C:\\Gone\\Away", status: "active", created_at: "2026-10-01T00:00:00Z", root_exists: false },
      ],
    },
  };
}

beforeEach(() => {
  localStorage.clear();
  __resetApiCache();
  api.posts = [];
  api.gets = [];
  api.treeProps = [];
  seed();
});
afterEach(cleanup);

/* ========================================================================== */
/*  The shared tree                                                            */
/* ========================================================================== */

describe("DirectoryTree: Build opens on the user's projects (opt-in)", () => {
  it("showProjects lists 'Your projects'; a row selects the project's root; a missing folder is skipped", async () => {
    const onSelect = vi.fn();
    render(<DirectoryTree selectedPath={null} onSelect={onSelect} onOpenTerminal={() => {}} showProjects />);
    expect((await screen.findAllByText(/Your projects/i)).length).toBeGreaterThanOrEqual(1);
    const row = await screen.findByRole("button", { name: /Client Books/ });
    fireEvent.click(row);
    expect(onSelect).toHaveBeenCalledWith(PROJECT_ROOT);
    expect(screen.queryByText(/Old Move/)).toBeNull();
    // Anti-vacuity: the drive select, the pasted-path box and the tree remain.
    expect(screen.getByLabelText("Root directory")).toBeInTheDocument();
    expect(screen.getByLabelText("Go to root path")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Windows/ })).toBeInTheDocument();
  });

  it("anti-vacuity: without showProjects (chat, Memory) the picker is unchanged and never asks for projects", async () => {
    render(<DirectoryTree selectedPath={null} onSelect={() => {}} hideAction />);
    expect(await screen.findByRole("button", { name: /Windows/ })).toBeInTheDocument();
    expect(screen.queryByText(/Your projects/i)).toBeNull();
    expect(api.gets.some((g) => g.split("?")[0] === "/projects")).toBe(false);
  });

  it("hideHeaderCollapse drops the Directory header's collapse button", async () => {
    render(
      <DirectoryTree selectedPath={null} onSelect={() => {}} onCollapse={() => {}} hideHeaderCollapse />,
    );
    await screen.findByLabelText("Root directory");
    expect(screen.queryByTitle("Collapse panel")).toBeNull();
  });

  it("anti-vacuity: chat's header collapse (onCollapse, no hideHeaderCollapse) is still there and still works", async () => {
    const onCollapse = vi.fn();
    render(<DirectoryTree selectedPath={null} onSelect={() => {}} onCollapse={onCollapse} hideAction />);
    fireEvent.click(await screen.findByTitle("Collapse panel"));
    expect(onCollapse).toHaveBeenCalledTimes(1);
  });

  it("nothing picked reads as a neutral sentence, not a mono value; a picked path IS mono", async () => {
    const { rerender } = render(<DirectoryTree selectedPath={null} onSelect={() => {}} onOpenTerminal={() => {}} />);
    const empty = await screen.findByText(/No folder picked yet/);
    expect(empty.className).not.toMatch(/\bfont-mono\b/);
    expect(empty.className).not.toMatch(/\btext-accent/);
    // A JS expression, not a JSX attribute string: in an attribute "\\" is two
    // characters, so the path would never equal the "C:\\app" looked up below.
    rerender(<DirectoryTree selectedPath={"C:\\app"} onSelect={() => {}} onOpenTerminal={() => {}} />);
    const picked = screen.getAllByText("C:\\app").find((el) => el.tagName !== "BUTTON" && !el.closest("button"));
    expect(picked?.className ?? "").toMatch(/\bfont-mono\b/);
    // Anti-vacuity: Open terminal here still runs with the picked folder.
    expect(screen.getByRole("button", { name: /Open terminal here/ })).not.toBeDisabled();
  });

  it("the project chip explains itself in a tooltip", async () => {
    render(<DirectoryTree selectedPath={null} onSelect={() => {}} hideAction />);
    const chip = await screen.findByText("node");
    expect(chip.getAttribute("title") ?? "").toMatch(/code project/i);
  });
});

/* ========================================================================== */
/*  The Build page                                                             */
/* ========================================================================== */

describe("Build: one collapse control, projects first, a phone-sized rail", () => {
  it("Build passes showProjects + hideHeaderCollapse, so exactly ONE 'Collapse panel' remains — and it works", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("pane-rail");
    await screen.findByLabelText("Root directory");
    const last = api.treeProps[api.treeProps.length - 1];
    expect(last.showProjects).toBe(true);
    expect(last.hideHeaderCollapse).toBe(true);
    const collapses = screen.getAllByRole("button", { name: "Collapse panel" });
    expect(collapses).toHaveLength(1);
    fireEvent.click(collapses[0]);
    await waitFor(() => expect(screen.getByRole("button", { name: /Show panel/ })).toBeInTheDocument());
  });

  it("the rail is capped below lg and full-height (minus the demo strip) only at lg", async () => {
    render(<TerminalsPage />);
    const rail = await screen.findByTestId("pane-rail");
    const wrap = rail.parentElement as HTMLElement;
    // No inline full-height style: it applied at EVERY width.
    expect(wrap.style.height).toBe("");
    expect(wrap.style.minHeight).toBe("");
    const cls = wrap.className.split(/\s+/);
    for (const c of [
      "max-h-56",
      "flex",
      "flex-col",
      "lg:max-h-none",
      "lg:h-[calc(100vh-12rem-var(--ij-strip-h,0px))]",
      "lg:min-h-[480px]",
    ]) {
      expect(cls).toContain(c);
    }
    // Anti-vacuity: the rail's own buttons are all still there.
    expect(within(rail).getByRole("button", { name: /New terminal/ })).toBeInTheDocument();
    expect(screen.getByTestId("shape-canvas")).toBeInTheDocument();
  });

  it("anti-vacuity: the stage keeps its 480 px minimum at every width", async () => {
    render(<TerminalsPage />);
    const empty = await screen.findByTestId("empty-state");
    let stage: HTMLElement | null = empty.parentElement;
    while (stage && !/bg-ink-900\/40/.test(stage.className)) stage = stage.parentElement;
    expect(stage).not.toBeNull();
    const s = stage as HTMLElement;
    const unscopedMin = s.className.split(/\s+/).includes("min-h-[480px]");
    expect(s.style.minHeight === "480px" || unscopedMin).toBe(true);
  });
});
