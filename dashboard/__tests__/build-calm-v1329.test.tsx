/**
 * The Build page loses the old boxy look (v1.329.0, calm chat wave 5, S2).
 *
 * The re-audit after wave 4 found the page around the calm side chat still in
 * the old look: an accent-bordered, glowing pane frame, accent-bordered cards
 * for the terminal list and the New terminal button, a boxed Folders / Files
 * switch, and a red bordered card for a refused folder. These tests pin the
 * calm shapes AND that nothing was lost on the way:
 *
 *   - the terminal list is ROWS (status dot + name + quiet age, state word on
 *     a second line), no border, no accent box; its rename / capabilities /
 *     close are ghosts that show on hover or focus, always show on touch, and
 *     stay real buttons in the tab order;
 *   - "New terminal" (rail footer, header, canvas) is a quiet ghost;
 *   - the pane frame is a hairline with no glow, ring or shadow card, the
 *     same for the terminal layer and the chat layer;
 *   - Folders / Files are plain text tabs (role=tab, arrow keys move);
 *   - a refused create is ONE plain danger-tone line carrying the path.
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/* ---- api ------------------------------------------------------------------- */

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
    posts: [] as [string, unknown][],
    postError: null as null | { message: string; status: number },
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  get: (path: string) => {
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    }
    return Promise.resolve(r);
  },
  post: (path: string, body: unknown) => {
    api.posts.push([path, body]);
    if (api.postError) {
      return Promise.reject(new api.FakeApiError(api.postError.message, api.postError.status));
    }
    return Promise.resolve({});
  },
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

/* ---- next/dynamic: xterm never enters jsdom -------------------------------- */

vi.mock("next/dynamic", () => ({
  default: () =>
    function DynamicStub(props: Record<string, unknown>) {
      if (typeof props.paneId === "string") return <div data-testid={`pane-chat-${props.paneId}`} />;
      const info = props.info as { id: string; shell: string };
      return <div data-testid={`terminal-pane-${info.id}`}>{info.shell}</div>;
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
      <div data-testid="header-actions">{actions}</div>
    </div>
  ),
}));
vi.mock("@/components/ui", () => ({
  Card: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  OfflineHint: () => <div />,
  ErrorNote: ({ children }: { children?: React.ReactNode }) => (
    <div role="alert" data-testid="error-note-card">
      {children}
    </div>
  ),
  Spinner: ({ label }: { label?: string }) => <div>{label}</div>,
  ConfirmButton: ({ label }: { label: string }) => <button type="button">{label}</button>,
  Empty: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <div data-testid="directory-tree" />,
}));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => (
    <div data-testid="files-panel">{folder ?? "no-folder"}</div>
  ),
}));

import TerminalsPage from "@/app/terminals/page";
import { PaneRail, RAIL_FOOTER_BUTTON, type RailPane } from "@/components/terminal/PaneRail";
import { PaneDot } from "@/components/terminal/PaneState";
import { paneFrameClass } from "@/components/terminal/paneFrame";

/* ---- helpers --------------------------------------------------------------- */

const ROOT = join(__dirname, "..");
const read = (...p: string[]) => readFileSync(join(ROOT, ...p), "utf8").replace(/\r\n/g, "\n");
const tokens = (cls: string) => cls.split(/\s+/).filter(Boolean);

const OPENED = "2026-10-09T12:00:00";
const NOW = Date.parse(OPENED + "Z") + 4 * 60_000;

const pane = (over: Partial<RailPane> = {}): RailPane => ({
  id: "t1",
  label: "Harbor reports",
  state: "working",
  cli: "Claude Code",
  cwd: "C:\\Sample\\Harbor",
  since: OPENED,
  ...over,
});

function renderRail(panes: RailPane[], extra: Partial<React.ComponentProps<typeof PaneRail>> = {}) {
  const props = {
    panes,
    focusedId: panes[0]?.id ?? null,
    onFocus: vi.fn(),
    onClose: vi.fn(),
    onRename: vi.fn(),
    onNew: vi.fn(),
    now: NOW,
    ...extra,
  };
  render(<PaneRail {...props} />);
  return props;
}

const term = (id: string, cwd: string) => ({
  id,
  cwd,
  shell: "pwsh",
  argv: [],
  cols: 120,
  rows: 30,
  alive: true,
  exit_code: null,
  created_at: OPENED,
});

function seedApi() {
  api.responses = {
    "/terminals": { terminals: [term("t1", "C:\\proj\\alpha")] },
    "/terminals/shells": { shells: [] },
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
    "/terminals/activity": { panes: [] },
  };
}

beforeEach(() => {
  localStorage.clear();
  api.posts = [];
  api.postError = null;
  seedApi();
});
afterEach(cleanup);

/* ------------------------------------------------------------ 1. the list */

describe("the terminal list is rows, not cards", () => {
  it("a row is a status dot, the name and a quiet age, with the state word under it", () => {
    renderRail([pane()]);
    const row = screen.getByTestId("rail-row-t1");
    expect(within(row).getByTestId("pane-dot-working")).toBeTruthy();
    expect(row.textContent).toContain("Harbor reports");
    const age = screen.getByTestId("rail-age-t1");
    expect(age.textContent).toBe("4m");
    expect(age.getAttribute("dateTime")).toBe(OPENED);
    expect(age.className).toContain("text-zinc-500");
    // The state WORD stays (never colour alone), and the CLI keeps its testid.
    expect(row.textContent).toContain("working");
    expect(screen.getByTestId("rail-cli-t1").textContent).toBe("Claude Code");
  });

  it("no row is a bordered or accent box; the open one only has a subtle fill", () => {
    renderRail([pane(), pane({ id: "t2", label: "Supplier sync", state: "blocked" })]);
    for (const id of ["t1", "t2"]) {
      const cls = tokens(screen.getByTestId(`rail-row-${id}`).className);
      expect(cls, id).not.toContain("border");
      expect(cls.filter((c) => /accent|amber|shadow|ring/.test(c)), id).toEqual([]);
    }
    expect(tokens(screen.getByTestId("rail-row-t1").className)).toContain("bg-white/[0.07]");
    expect(screen.getByTestId("rail-row-t1").getAttribute("data-active")).toBe("true");
    expect(tokens(screen.getByTestId("rail-row-t2").className)).toContain("hover:bg-white/[0.05]");
  });

  it("a row with no readable time draws no age (never a made-up one)", () => {
    renderRail([pane({ since: null })]);
    expect(screen.queryByTestId("rail-age-t1")).toBeNull();
  });

  it("the ghost actions hide only on a hover screen, show on hover and focus, and stay in the tab order", () => {
    renderRail([pane()]);
    const actions = [
      screen.getByTestId("rail-caps-t1"),
      screen.getByTestId("rail-rename-t1"),
      screen.getByTestId("rail-close-t1"),
    ];
    for (const b of actions) {
      const cls = tokens(b.className);
      expect(b.tagName).toBe("BUTTON");
      expect(b.getAttribute("tabindex")).not.toBe("-1");
      expect(b.getAttribute("aria-label")).toBeTruthy();
      // A bare opacity-0 would hide it on a touch screen, which has no hover.
      expect(cls).not.toContain("opacity-0");
      expect(cls).toContain("[@media(hover:hover)]:opacity-0");
      expect(cls).toContain("[@media(hover:hover)]:group-hover/row:opacity-100");
      expect(cls).toContain("[@media(hover:hover)]:group-focus-within/row:opacity-100");
      expect(cls).toContain("focus-visible:opacity-100");
      // Big enough to press with a finger, not the old 16 px.
      expect(cls).toContain("h-6");
      expect(cls).toContain("w-6");
    }
  });

  it("every action still works: rename opens the box, close and capabilities answer", () => {
    const props = renderRail([pane()]);
    fireEvent.click(screen.getByTestId("rail-close-t1"));
    expect(props.onClose).toHaveBeenCalledWith("t1");
    fireEvent.click(screen.getByTestId("rail-rename-t1"));
    expect(screen.getByTestId("rail-rename-input")).toBeTruthy();
  });

  it("the capabilities popover keeps the actions visible while it is open", async () => {
    api.responses["/browser/status"] = { access: "off" };
    renderRail([pane()]);
    fireEvent.click(screen.getByTestId("rail-caps-t1"));
    await waitFor(() => expect(screen.getByTestId("rail-caps-panel-t1")).toBeTruthy());
    expect(tokens(screen.getByTestId("rail-caps-t1").className)).not.toContain(
      "[@media(hover:hover)]:opacity-0",
    );
    expect(tokens(screen.getByTestId("rail-rename-t1").className)).not.toContain(
      "[@media(hover:hover)]:opacity-0",
    );
  });

  it("the blocked jump is a plain warn-tone word, not an amber chip", () => {
    renderRail([pane({ state: "blocked" })]);
    const jump = screen.getByTestId("rail-jump-blocked");
    expect(jump.textContent).toBe("1 needs you");
    const cls = tokens(jump.className);
    expect(cls).toContain("text-tone-warn");
    expect(cls).not.toContain("border");
    expect(cls.filter((c) => /amber/.test(c))).toEqual([]);
  });

  it("the status dots use tone tokens (deep inks on Daylight)", () => {
    render(
      <div>
        <PaneDot state="blocked" />
        <PaneDot state="done" />
      </div>,
    );
    expect(screen.getByTestId("pane-dot-blocked").className).toContain("bg-tone-warn");
    expect(screen.getByTestId("pane-dot-done").className).toContain("bg-tone-success");
    expect(screen.getByTestId("pane-dot-blocked").className).not.toMatch(/amber|emerald/);
  });
});

/* -------------------------------------------------------- 2. New terminal */

describe("New terminal is a quiet button", () => {
  it("the rail's footer press is a ghost row and still opens a terminal", () => {
    const props = renderRail([pane()]);
    const b = screen.getByTestId("rail-new-pane");
    expect(b.className).toBe(RAIL_FOOTER_BUTTON);
    // The keyboard focus ring may use the accent; nothing else may.
    const looks = tokens(RAIL_FOOTER_BUTTON).filter((c) => !c.startsWith("focus-visible:"));
    expect(looks.filter((c) => /accent|border|shadow/.test(c))).toEqual([]);
    fireEvent.click(b);
    expect(props.onNew).toHaveBeenCalledTimes(1);
  });

  it("the header's New terminal is a ghost, not a glowing accent slab, and still creates", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    const b = screen.getByTestId("header-new-terminal");
    expect(tokens(b.className)).toContain("btn-ghost");
    expect(tokens(b.className)).not.toContain("btn-accent");
    fireEvent.click(b);
    await waitFor(() => expect(api.posts.map(([p]) => p)).toContain("/terminals"));
  });

  it("the canvas's floating New terminal has no accent border", () => {
    const src = read("app", "terminals", "page.tsx");
    const at = src.indexOf('data-testid="canvas-new-terminal"');
    expect(at).toBeGreaterThan(-1);
    const cls = src.slice(at, at + 600).match(/className="([^"]*)"/)?.[1] ?? "";
    expect(cls).not.toMatch(/border-accent|text-accent|shadow-card/);
  });
});

/* ------------------------------------------------------------ 3. the frame */

describe("the pane frame is a hairline, never a glow", () => {
  it("no state draws a glow, a ring or a shadow card", () => {
    for (const focused of [false, true]) {
      for (const canvas of [false, true]) {
        for (const drag of [false, true]) {
          const cls = paneFrameClass(focused, canvas, drag);
          expect(cls).not.toMatch(/shadow|ring|glow/);
          expect(cls).toMatch(/^border-/);
        }
      }
    }
  });

  it("only the canvas marks the focused pane in the accent; the rail's lone pane does not", () => {
    expect(paneFrameClass(true, false)).not.toContain("accent");
    expect(paneFrameClass(true, true)).toContain("border-accent/30");
    expect(paneFrameClass(false, false)).not.toContain("accent");
    // A held file still says "drop here".
    expect(paneFrameClass(false, false, true)).toContain("border-accent");
  });

  it("the terminal layer and the chat layer both draw it, with no glow left behind", () => {
    const pane = read("components", "terminal", "TerminalPane.tsx");
    const at = pane.indexOf("group relative flex h-full flex-col overflow-hidden rounded-2xl border");
    expect(at).toBeGreaterThan(-1);
    const line = pane.slice(at, pane.indexOf("\n", at));
    expect(line).toContain("paneFrameClass(");
    expect(line).not.toMatch(/shadow-card|shadow-glow|ring-/);

    const page = read("app", "terminals", "page.tsx");
    const c = page.indexOf("data-testid={`chat-layer-${t.id}`}");
    expect(c).toBeGreaterThan(-1);
    const layer = page.slice(c, c + 500);
    expect(layer).toContain("paneFrameClass(");
    expect(layer).not.toMatch(/shadow-card|shadow-glow|ring-/);
  });
});

/* ------------------------------------------------- 4. tabs and the error */

describe("the side panel and the error line", () => {
  it("Folders / Files are plain text tabs that switch the panel and move with the arrow keys", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    const list = screen.getByRole("tablist", { name: "Folders and files" });
    expect(tokens(list.className).filter((c) => /border|bg-|rounded/.test(c))).toEqual([]);
    const [folders, files] = within(list).getAllByRole("tab");
    expect(folders.textContent).toBe("Folders");
    expect(files.textContent).toBe("Files");
    for (const t of [folders, files]) {
      const looks = tokens(t.className).filter((c) => !c.startsWith("focus-visible:"));
      expect(looks.filter((c) => /accent|ring|bg-/.test(c))).toEqual([]);
    }
    // Which tab is open depends on whether a pane has a folder (it does here).
    const open = folders.getAttribute("aria-selected") === "true" ? folders : files;
    const other = open === folders ? files : folders;
    expect(open.getAttribute("tabindex")).toBe("0");
    expect(other.getAttribute("tabindex")).toBe("-1");
    open.focus();
    fireEvent.keyDown(open, { key: "ArrowRight" });
    expect(document.activeElement).toBe(other);
    fireEvent.click(folders);
    await waitFor(() => expect(folders.getAttribute("aria-selected")).toBe("true"));
    expect(screen.getByTestId("directory-tree")).toBeTruthy();
    fireEvent.click(files);
    await waitFor(() => expect(files.getAttribute("aria-selected")).toBe("true"));
    expect(screen.getByTestId("files-panel")).toBeTruthy();
    expect(screen.getByRole("tabpanel").getAttribute("aria-labelledby")).toBe("build-tab-files");
  });

  it("a refused New terminal is one plain danger-tone line with the path, not a red card", async () => {
    api.postError = { message: "no such directory: C:\\Sample\\Gone", status: 404 };
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    fireEvent.click(screen.getByTestId("header-new-terminal"));
    const line = await screen.findByTestId("build-error");
    await waitFor(() => expect(line.textContent).toBe("no such directory: C:\\Sample\\Gone"));
    expect(line.getAttribute("role")).toBe("alert");
    const cls = tokens(line.className);
    expect(cls).toContain("text-tone-danger");
    expect(cls.filter((c) => /border|bg-|rounded|shadow/.test(c))).toEqual([]);
    expect(screen.queryByTestId("error-note-card")).toBeNull();
  });

  it("a pane with an empty name reads as its shell in the list, never a blank row", async () => {
    api.responses["/terminals/activity"] = { panes: [{ id: "t1", name: "", state: "idle", alive: true }] };
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    await waitFor(() => expect(screen.getByTestId("rail-row-t1").textContent).toContain("pwsh"));
  });
});
