/**
 * v1.329.0 (calm chat wave 6, H2): the Build page uses the calm file browser,
 * whole pixels and theme tokens everywhere.
 *
 * The final audit after wave 5 found the Build page still mixed two looks:
 *
 *   1. The same file browser in two looks. The chat drawer drew the CALM
 *      DirectoryTree / FilesPanel (wave 5, G4) while Build's Folders and Files
 *      tabs, right beside the calm pane chat, were still the card (rounded-2xl
 *      border shadow-card, a monospace path header), and a missing folder was
 *      a rose bordered ErrorNote box. Build now passes variant="calm" to both,
 *      and the error is ONE plain tone-danger line that names the folder.
 *   2. Literal hues (amber / rose / emerald / sky / violet / orange) and
 *      half-pixel sizes across the rest of the page: the pane badge and peek,
 *      the capability notes, the continue / resume / sign-in strips, the
 *      connection pills, the state chips, the account chips. Daylight does
 *      not re-ink a literal hue the way it re-inks a tone token. All of them
 *      are tone tokens and whole pixels now.
 *   3. The S3 source guard read components/chat only. This file is its
 *      sibling for components/terminal and app/terminals/page.tsx, with an
 *      allowlist for the colours that paint the terminal canvas itself (it
 *      stays dark in every theme, like xterm's own palette).
 */

import React from "react";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
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
    getErrors: {} as Record<string, { message: string; status: number }>,
    posts: [] as [string, unknown][],
    treeProps: [] as Record<string, unknown>[],
    filesProps: [] as Record<string, unknown>[],
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://daemon.test",
  ijToken: () => "",
  get: (p: string) => {
    const e = api.getErrors[p];
    if (e) return Promise.reject(new api.FakeApiError(e.message, e.status));
    const r = api.responses[p];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${p}`, 404));
    return Promise.resolve(r);
  },
  post: (p: string, body: unknown) => {
    api.posts.push([p, body]);
    return Promise.resolve({});
  },
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

/* ---- the page's heavy parts never enter jsdom ------------------------------ */

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
  OfflineHint: () => <div data-testid="offline-hint" />,
  ErrorNote: ({ children }: { children?: React.ReactNode }) => (
    <div role="alert" data-testid="error-note-card">
      {children}
    </div>
  ),
  Spinner: ({ label }: { label?: string }) => <div>{label}</div>,
  ConfirmButton: ({ label }: { label: string }) => <button type="button">{label}</button>,
  Empty: ({ children }: { children?: React.ReactNode }) => <div data-testid="empty-card">{children}</div>,
}));
// The page test records the props Build hands the two browsers; the real
// components are rendered on their own below (vi.importActual).
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: (props: Record<string, unknown>) => {
    api.treeProps.push(props);
    return <div data-testid="directory-tree" data-variant={String(props.variant)} />;
  },
}));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: (props: Record<string, unknown>) => {
    api.filesProps.push(props);
    return <div data-testid="files-panel" data-variant={String(props.variant)} />;
  },
}));

import TerminalsPage from "@/app/terminals/page";
import { calmUses, uncalm, type KeptDefault } from "./helpers/calmVariant";

type FilesPanelModule = typeof import("@/components/terminal/FilesPanel");
type DirectoryTreeModule = typeof import("@/components/terminal/DirectoryTree");
const realFiles = () => vi.importActual<FilesPanelModule>("@/components/terminal/FilesPanel");
const realTree = () => vi.importActual<DirectoryTreeModule>("@/components/terminal/DirectoryTree");

/* ---- the guard's patterns (chat-cards-whole-pixels-v1329's, word for word) -- */

const HUES =
  "slate|gray|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose";
const PROPS =
  "text|bg|border|ring|from|via|to|fill|stroke|outline|decoration|divide|placeholder|shadow|caret|accent";
const HALF_PIXEL = /text-\[\d+\.5px\]/g;
const LITERAL_HUE = new RegExp(
  `(?<![\\w-])(?:${PROPS})(?:-[trblxyse])?-(?:${HUES})-\\d{2,3}(?:\\/(?:\\d{1,3}|\\[[0-9.]+\\]))?`,
  "g",
);
const ARBITRARY_COLOUR = new RegExp(
  `(?<![\\w-])(?:${PROPS})-\\[(?:#|(?:rgb|hsl)a?\\((?!\\s*var\\())[^\\]]*\\]`,
  "g",
);

/* ---- helpers --------------------------------------------------------------- */

const DASHBOARD = path.join(__dirname, "..");
const TERMINAL_DIR = path.join(DASHBOARD, "components", "terminal");
const PAGE = "app/terminals/page.tsx";
const readRel = (rel: string) =>
  readFileSync(path.join(DASHBOARD, rel), "utf8").replace(/\r\n/g, "\n");
const tokens = (cls: string | null | undefined) => (cls ?? "").split(/\s+/).filter(Boolean);
function allClasses(root: Element): string[] {
  return [root, ...Array.from(root.querySelectorAll("*"))].flatMap((e) => tokens(e.getAttribute("class")));
}
function patternHits(src: string): string[] {
  return [HALF_PIXEL, LITERAL_HUE, ARBITRARY_COLOUR].flatMap((re) => [...src.matchAll(re)].map((m) => m[0]));
}

const FOLDER = "C:\\Sample\\Harbor";
const GONE = "C:\\Sample\\Gone";
const filesUrl = (f: string) => `/fs/files?path=${encodeURIComponent(f)}&depth=4&limit=600`;
const OPENED = "2026-10-09T12:00:00";

function seedPage() {
  api.responses = {
    "/terminals": {
      terminals: [
        {
          id: "t1",
          cwd: FOLDER,
          shell: "pwsh",
          argv: [],
          cols: 120,
          rows: 30,
          alive: true,
          exit_code: null,
          created_at: OPENED,
        },
      ],
    },
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
  api.getErrors = {};
  api.treeProps = [];
  api.filesProps = [];
  seedPage();
});
afterEach(cleanup);

/* ========================================================================== */
/*  1. Build hands the calm look to both browsers                              */
/* ========================================================================== */

describe("Build's Folders and Files tabs draw the calm browser", () => {
  it("Folders: the calm picker, still with Build's projects, its own Open terminal here and ONE collapse", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    const list = screen.getByRole("tablist", { name: "Folders and files" });
    const [folders] = within(list).getAllByRole("tab");
    fireEvent.click(folders);
    await waitFor(() => expect(screen.getByTestId("directory-tree")).toBeTruthy());
    const props = api.treeProps[api.treeProps.length - 1];
    expect(props.variant).toBe("calm");
    // Nothing lost on the way: projects first, the tab bar's collapse is the
    // only one, and the picker still opens a terminal in the picked folder.
    expect(props.showProjects).toBe(true);
    expect(props.hideHeaderCollapse).toBe(true);
    expect(typeof props.onOpenTerminal).toBe("function");
    (props.onOpenTerminal as (p: string) => void)("C:\\Sample\\Picked");
    await waitFor(() => expect(api.posts.map(([p]) => p)).toContain("/terminals"));
    const body = api.posts.find(([p]) => p === "/terminals")?.[1] as { cwd?: string };
    expect(body.cwd).toBe("C:\\Sample\\Picked");
  });

  it("Files: the calm list on the focused pane's folder, with Build's own no-folder words", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    const list = screen.getByRole("tablist", { name: "Folders and files" });
    const [, files] = within(list).getAllByRole("tab");
    fireEvent.click(files);
    await waitFor(() => expect(screen.getByTestId("files-panel")).toBeTruthy());
    const props = api.filesProps[api.filesProps.length - 1];
    expect(props.variant).toBe("calm");
    expect(props.folder).toBe(FOLDER);
    expect(typeof props.onOpenTerminal).toBe("function");
    expect(props.noFolderText).toBe(
      "Focus a terminal to see the files in its folder, or pick a folder in the Folders tab.",
    );
  });

  it("the calm picker with Build's props: no card, projects, a ghost Open terminal here, no second collapse", async () => {
    const { DirectoryTree } = await realTree();
    api.responses = {
      "/fs/drives": { drives: [{ path: "C:\\", label: "C:" }] },
      [`/fs/list?path=${encodeURIComponent("C:\\")}&dirs_only=true`]: {
        path: "C:\\",
        parent: null,
        entries: [{ name: "app", path: "C:\\app", is_dir: true, is_project: "node" }],
      },
      "/projects": { projects: [{ id: "p1", name: "Harbor Street Cafe", root: FOLDER, root_exists: true }] },
    };
    const onOpen = vi.fn();
    const { container } = render(
      <DirectoryTree
        variant="calm"
        selectedPath={FOLDER}
        onSelect={() => {}}
        onOpenTerminal={onOpen}
        onCollapse={() => {}}
        hideHeaderCollapse
        showProjects
      />,
    );
    await screen.findByText("Harbor Street Cafe");
    const root = container.firstElementChild as HTMLElement;
    for (const card of ["rounded-2xl", "shadow-card", "bg-ink-850/80", "backdrop-blur-sm"]) {
      expect(tokens(root.className)).not.toContain(card);
    }
    expect(screen.queryByTitle("Collapse panel")).toBeNull();
    expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
    const open = screen.getByRole("button", { name: /Open terminal here/ });
    expect(tokens(open.className)).not.toContain("btn-accent");
    fireEvent.click(open);
    expect(onOpen).toHaveBeenCalledWith(FOLDER);
    expect(await screen.findByText("app")).toBeTruthy();
    expect(allClasses(root)).not.toContain("font-mono");
    expect(patternHits(root.outerHTML)).toEqual([]);
  });
});

/* ========================================================================== */
/*  2. A missing folder is one plain line that names it                        */
/* ========================================================================== */

describe("the files list's error is one plain danger line naming the folder", () => {
  it("a folder that is gone: the daemon's words (which name it), no box, no ErrorNote card", async () => {
    const { FilesPanel } = await realFiles();
    api.getErrors[filesUrl(GONE)] = { message: `no such directory: ${GONE}`, status: 404 };
    const { container } = render(<FilesPanel variant="calm" folder={GONE} onOpenTerminal={() => {}} />);
    const line = await screen.findByTestId("files-panel-error");
    await waitFor(() => expect(line.textContent).toBe(`no such directory: ${GONE}`));
    expect(line.getAttribute("role")).toBe("alert");
    const cls = tokens(line.className);
    expect(cls).toContain("text-tone-danger");
    expect(cls.filter((c) => /border|bg-|rounded|shadow/.test(c))).toEqual([]);
    expect(screen.queryByTestId("error-note-card")).toBeNull();
    expect(patternHits(container.innerHTML)).toEqual([]);
  });

  it("words that do not name the folder get it added, so the line always says WHICH folder", async () => {
    const { FilesPanel } = await realFiles();
    api.getErrors[filesUrl(GONE)] = { message: "this folder is protected", status: 403 };
    render(<FilesPanel variant="calm" folder={GONE} />);
    const line = await screen.findByTestId("files-panel-error");
    await waitFor(() => expect(line.textContent).toBe(`this folder is protected (${GONE})`));
  });

  it("filesErrorLine: never names the folder twice, whatever the case of the drive letter", async () => {
    const { filesErrorLine } = await realFiles();
    expect(filesErrorLine(`no such directory: ${GONE}`, GONE)).toBe(`no such directory: ${GONE}`);
    expect(filesErrorLine(`no such directory: ${GONE.toLowerCase()}`, GONE)).toBe(
      `no such directory: ${GONE.toLowerCase()}`,
    );
    expect(filesErrorLine("denied", GONE)).toBe(`denied (${GONE})`);
    expect(filesErrorLine("denied", "")).toBe("denied");
  });

  it("the card look (no caller in the app any more) says it the same plain way", async () => {
    const { FilesPanel } = await realFiles();
    api.getErrors[filesUrl(GONE)] = { message: `no such directory: ${GONE}`, status: 404 };
    render(<FilesPanel folder={GONE} />);
    const line = await screen.findByTestId("files-panel-error");
    await waitFor(() => expect(line.textContent).toBe(`no such directory: ${GONE}`));
    expect(screen.queryByTestId("error-note-card")).toBeNull();
    expect(tokens(line.className).filter((c) => /border|bg-|rounded/.test(c))).toEqual([]);
  });

  it("no folder yet: the host's words when it gives them, the drawer's otherwise", async () => {
    const { FilesPanel } = await realFiles();
    const { unmount } = render(
      <FilesPanel variant="calm" folder={null} noFolderText="Focus a terminal to see its files." />,
    );
    expect(await screen.findByText("Focus a terminal to see its files.")).toBeTruthy();
    unmount();
    render(<FilesPanel variant="calm" folder={null} />);
    expect(await screen.findByText("Pick a folder to see its files here.")).toBeTruthy();
  });

  it("a file listing in the calm list: names in the normal font, whole pixels, tokens only", async () => {
    const { FilesPanel } = await realFiles();
    const now = Date.now() / 1000;
    api.responses[filesUrl(FOLDER)] = {
      root: FOLDER,
      files: [
        { name: "menu.md", path: `${FOLDER}\\menu.md`, rel: "menu.md", size: 2048, mtime: now - 600 },
        { name: "logo.png", path: `${FOLDER}\\logo.png`, rel: "logo.png", size: 4096, mtime: now - 900 },
        { name: "ad.mp4", path: `${FOLDER}\\ad.mp4`, rel: "ad.mp4", size: 9000, mtime: now - 1200 },
        { name: "tune.mp3", path: `${FOLDER}\\tune.mp3`, rel: "tune.mp3", size: 5000, mtime: now - 1500 },
      ],
      count: 4,
      truncated: false,
    };
    const { container } = render(<FilesPanel variant="calm" folder={FOLDER} onOpenTerminal={() => {}} />);
    expect(await screen.findByText("menu.md")).toBeTruthy();
    expect(allClasses(container)).not.toContain("font-mono");
    expect(patternHits(container.innerHTML)).toEqual([]);
    expect(container.textContent ?? "").not.toMatch(/—/);
  });
});

/* ========================================================================== */
/*  3. Source guard: components/terminal + the Build page                      */
/* ========================================================================== */

/** Exact class tokens a file may keep, each with the reason. Only colours that
 *  paint the terminal CANVAS itself belong here: the canvas stays dark in
 *  every theme, so its colour must not follow the theme. */
const ALLOW: Array<{ file: string; token: string; why: string }> = [
  {
    file: "components/terminal/TerminalPane.tsx",
    token: "bg-[#0a0c11]",
    why: "the terminal canvas behind xterm (and the dimmed layer over it while reconnecting): xterm paints the same #0a0c11 as its theme background in paneHost.ts, and the canvas stays dark in every theme on purpose",
  },
];

/** The canvas palette that is NOT a class (so no pattern sees it), named here
 *  so the exception is written down: xterm's theme + ANSI colours. */
const CANVAS_PALETTE_FILE = "components/terminal/paneHost.ts";

/** Every file the audit named, which must exist and be scanned. */
const NAMED = [
  "components/terminal/ContinueStrip.tsx",
  "components/terminal/DirectoryTree.tsx",
  "components/terminal/FilesPanel.tsx",
  "components/terminal/PaneAccountChip.tsx",
  "components/terminal/PaneRail.tsx",
  "components/terminal/PaneState.tsx",
  "components/terminal/TerminalPane.tsx",
  PAGE,
];

const scanned = [
  ...readdirSync(TERMINAL_DIR)
    .filter((f) => /\.(tsx|ts)$/.test(f))
    .sort()
    .map((f) => `components/terminal/${f}`),
  PAGE,
];

function offenders(rel: string, src: string): string[] {
  const allowed = ALLOW.filter((a) => a.file === rel).map((a) => a.token);
  const hits: string[] = [];
  for (const re of [HALF_PIXEL, LITERAL_HUE, ARBITRARY_COLOUR]) {
    for (const m of src.matchAll(re)) {
      const tok = m[0];
      if (allowed.some((a) => tok === a || tok.startsWith(`${a}/`))) continue;
      const line = src.slice(0, m.index ?? 0).split("\n").length;
      hits.push(`${rel}:${line} ${tok}`);
    }
  }
  return hits;
}

describe("Build page source: whole pixels and theme tokens only", () => {
  it("scans every terminal component and the Build page, including each file the audit named", () => {
    for (const f of NAMED) expect(scanned, f).toContain(f);
    // Anti-vacuity: the folder really holds the terminal components.
    expect(scanned.length).toBeGreaterThan(15);
  });

  it("no file has a half-pixel size, a literal hue or a colour value outside the allowlist", () => {
    const all = scanned.flatMap((f) => offenders(f, readRel(f)));
    expect(all, "use whole pixels and the tone-* tokens").toEqual([]);
  });

  it("every allowlist entry still matches, and the canvas colour is the one xterm paints", () => {
    for (const a of ALLOW) {
      expect(readRel(a.file), `${a.file}: ${a.token}`).toContain(a.token);
      expect(a.why.length).toBeGreaterThan(20);
    }
    const hex = ALLOW[0].token.match(/#[0-9a-f]{6}/i)?.[0] ?? "";
    expect(readRel(CANVAS_PALETTE_FILE)).toContain(`background: "${hex}"`);
  });

  it("what sits ON the dark canvas uses an opaque theme surface, never a see-through tone tint", () => {
    const pane = readRel("components/terminal/TerminalPane.tsx");
    const at = pane.indexOf('state === "reconnecting" || state === "closed"');
    expect(at).toBeGreaterThan(-1);
    const overlay = pane.slice(at, at + 1600);
    expect(overlay).toContain("bg-ink-850");
    expect(overlay).not.toMatch(/bg-tone-(?:warn|danger)\/10/);
    const page = readRel(PAGE);
    const p = page.indexOf("chatPeek.amber");
    expect(p).toBeGreaterThan(-1);
    const peek = page.slice(p, p + 700);
    expect(peek).toContain("bg-ink-900/85 text-tone-warn");
    expect(peek).not.toMatch(/bg-tone-warn\/\[0\.18\]/);
  });

  it("the boxes the audit named are plain lines now (capability error, launch error, the blocked jump)", () => {
    const rail = readRel("components/terminal/PaneRail.tsx");
    const caps = rail.slice(rail.indexOf("{capsError ? ("), rail.indexOf("{capsError}"));
    expect(caps).toContain("text-tone-warn");
    expect(caps).not.toMatch(/\bborder\b|rounded-lg|bg-tone/);
    const chip = readRel("components/terminal/PaneAccountChip.tsx");
    const err = chip.slice(chip.indexOf("launch-as-error-"), chip.indexOf("{error}"));
    expect(err).toContain("text-tone-danger");
    expect(err).not.toMatch(/\bborder\b|rounded|bg-tone/);
    const state = readRel("components/terminal/PaneState.tsx");
    const jump = state.slice(state.indexOf("focus-blocked-"), state.indexOf("{p.name || p.id}"));
    expect(jump).toContain("text-tone-warn");
    expect(jump).not.toMatch(/\bborder\b|font-mono/);
  });

  it("the patterns catch the real old classes and leave the tokens alone", () => {
    for (const old of [
      'className="basis-full text-[10.5px] text-amber-200/80"',
      'className="border-b border-amber-500/25 bg-amber-500/[0.07]"',
      'className="text-[12.5px]"',
      'className="border-orange-500/30 bg-orange-500/10 text-orange-300"',
      'className="hover:bg-rose-500/15 hover:text-rose-300"',
      'className="bg-amber-400"',
      'className="text-sky-300/80"',
      'className="bg-[#0a0c11]"',
    ]) {
      expect(patternHits(old), old).not.toEqual([]);
    }
    for (const ok of [
      'className="text-[10px] text-tone-warn/80"',
      'className="border-tone-warn/25 bg-tone-warn/[0.07]"',
      'className="bg-ink-850 text-tone-danger"',
      'className="hover:bg-white/[0.06] text-zinc-500"',
    ]) {
      expect(patternHits(ok), ok).toEqual([]);
    }
  });
});

/* v1.330.0 (calm chat wave 11, M4): every <Badge> and <ConfirmButton> on the
   Build page and in the terminal components is the calm variant, with ONE
   use kept in the default look on purpose (named here, with why). A new
   default one, or a spread that could hide the variant, fails. */
const BUILD_KEPT: KeptDefault[] = [
  {
    rel: PAGE,
    tag: "ConfirmButton",
    label: "Close terminal",
    why: "it sits in the 'Close this terminal?' confirm card over the pane, a deliberately boxed dialog, beside that dialog's bordered Cancel; a calm ghost there would put two looks in one row",
  },
];

describe("Build: Badge and ConfirmButton are calm, except the one kept on purpose", () => {
  const files = scanned.filter((f) => f.endsWith(".tsx"));

  it.each(files)("%s has no default Badge or ConfirmButton outside the kept list", (rel) => {
    expect(uncalm(rel, readRel(rel), BUILD_KEPT)).toEqual([]);
  });

  it("every kept use still exists, still is the default look, and says why", () => {
    for (const k of BUILD_KEPT) {
      const hit = calmUses(k.rel, readRel(k.rel)).filter((u) => u.tag === k.tag && u.label === k.label);
      expect(hit, `${k.rel} <${k.tag} ${k.label}>`).toHaveLength(1);
      expect(hit[0].calm).toBe(false);
      expect(k.why.length).toBeGreaterThan(40);
    }
    // The dialog really is a box: the kept button's parent card has a border.
    const page = readRel(PAGE);
    const at = page.indexOf("Close this terminal?");
    expect(at).toBeGreaterThan(-1);
    const card = page.slice(Math.max(0, at - 400), at);
    expect(card).toMatch(/rounded-2xl border border-white\/10 bg-ink-850/);
  });

  it("the kept list does not excuse a second default use (anti-vacuity)", () => {
    const probe = [
      'const a = <ConfirmButton onConfirm={go} label="Close terminal" />;',
      'const b = <ConfirmButton onConfirm={go} label="Remove pane" />;',
      'const c = <Badge value="idle" />;',
      'const d = <ConfirmButton {...p} label="Close terminal" />;',
    ].join("\n");
    expect(uncalm(PAGE, probe, BUILD_KEPT).map((x) => x.split(" ")[0].split(":")[1])).toEqual(["2", "3", "4"]);
  });
});
