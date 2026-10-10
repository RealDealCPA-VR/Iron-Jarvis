/**
 * v1.329.0 (calm chat wave 5, G4): the drawer's folder picker, the Tasks
 * warnings and the chat page's notices use the calm look and theme tokens.
 *
 * The re-audit after wave 4 found three things that broke the calm drawer
 * and page:
 *
 *  1. With no working folder, the project drawer's Files tab drew Build's
 *     DirectoryTree as it is: an accent "Directory" header with a collapse
 *     icon, a bordered ROOT select, a monospace "or paste a path…" input with
 *     a bordered Go, an uppercase SELECTED label, an em-dash line and a green
 *     "node" badge. FilesPanel rows were monospace too. Now the tree and the
 *     file list take `variant="calm"` (the drawer passes it): no card,
 *     sentence-case quiet labels, hairline inputs in the normal font, a ghost
 *     Go, a quiet project word, whole pixels, theme tokens only. Build (and
 *     Memory) keep the card look, unchanged.
 *  2. ProjectTasks' tool-grant box was literal amber (border-amber-500/25 …
 *     text-amber-200) and its failure line text-rose-200: now tone-warn /
 *     tone-danger, and the grant is a plain hairline section in the calm
 *     (bare) form.
 *  3. page.tsx's amber notices, emerald checks and rose Delete were literal
 *     hues Daylight does not re-ink: now tone tokens.
 *
 * Harness: project-drawer-calm-v1329's (ChatPage rendered for real; an
 * unmocked GET answers {}), plus the shared components rendered on their own.
 */

import { type ReactNode } from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      responses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
      posts: [] as { path: string; body: unknown }[],
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (path: string) => {
    const r = H.api.responses[path];
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  post: async (path: string, body: unknown) => {
    H.api.posts.push({ path, body });
    const r = H.api.postResponses[path];
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: () => Promise.resolve({ reply: "Noted.", tools_used: [] }),
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false,
    reason: null,
    engine: null,
    listening: false,
    processing: false,
    transcript: "",
    interim: "",
    error: null,
    start: () => {},
    stop: () => {},
    reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false,
    enabled: false,
    speaking: false,
    enable: () => {},
    disable: () => {},
    toggle: () => {},
    speak: () => {},
    resetStream: () => {},
    speakMore: () => {},
    cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({
    byProvider: {}, signedOutByProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {},
  }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: ReactNode; href: string } & Record<string, unknown>) => (
    <a href={href} {...(rest as Record<string, never>)}>
      {children}
    </a>
  ),
}));
vi.mock("@/components/project/ProjectSurfaces", () => ({
  ProjectSurface: ({ view }: { view: string }) => <div data-testid="surface-stub">surface:{view}</div>,
}));

import ChatPage from "@/app/chat/page";
import { DirectoryTree } from "@/components/terminal/DirectoryTree";
import { FilesPanel } from "@/components/terminal/FilesPanel";
import { ProjectTasks } from "@/components/project/ProjectTasks";
import { __resetApiCache } from "@/lib/apiCache";

const PROJECT_KEY = "ij_chat_project";
const WORKSPACE_KEY = "ij_chat_workspace";
const PROJECT = { id: "p1", name: "Q3 Bookkeeping" };
const FOLDER = "C:\\Work\\Harbor";
const ROOT_LIST = `/fs/list?path=${encodeURIComponent("C:\\")}&dirs_only=true`;
const FILES = `/fs/files?path=${encodeURIComponent(FOLDER)}&depth=4&limit=600`;
const NOW_S = Date.now() / 1000;

/* The guard's own patterns (chat-cards-whole-pixels-v1329's, word for word). */
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
function offenders(src: string): string[] {
  return [HALF_PIXEL, LITERAL_HUE, ARBITRARY_COLOUR].flatMap((re) => [...src.matchAll(re)].map((m) => m[0]));
}

function classes(el: Element | null | undefined): string[] {
  return ((el?.getAttribute("class") ?? "") as string).split(/\s+/).filter(Boolean);
}
/** Every class on `root` and below it. */
function allClasses(root: Element): string[] {
  return [root, ...Array.from(root.querySelectorAll("*"))].flatMap((e) => classes(e));
}

const DASHBOARD = path.join(__dirname, "..");
const readSrc = (rel: string) =>
  readFileSync(path.join(DASHBOARD, rel), "utf8").replace(/\r\n/g, "\n");

beforeEach(() => {
  __resetApiCache();
  H.api.posts = [];
  H.api.postResponses = {};
  H.api.responses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/chat/threads?project_id=p1": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [PROJECT] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
    "/fs/drives": { drives: [{ path: "C:\\", label: "C:" }] },
    [ROOT_LIST]: {
      path: "C:\\",
      parent: null,
      entries: [
        { name: "app", path: "C:\\app", is_dir: true, is_project: "node" },
        { name: "Windows", path: "C:\\Windows", is_dir: true, is_project: null },
      ],
    },
    [FILES]: {
      root: FOLDER,
      files: [
        { name: "menu.md", path: `${FOLDER}\\menu.md`, rel: "menu.md", size: 2048, mtime: NOW_S - 600 },
        { name: "logo.png", path: `${FOLDER}\\logo.png`, rel: "art\\logo.png", size: 4096, mtime: NOW_S - 900 },
        { name: "ad.mp4", path: `${FOLDER}\\ad.mp4`, rel: "ad.mp4", size: 900000, mtime: NOW_S - 1200 },
        { name: "jingle.mp3", path: `${FOLDER}\\jingle.mp3`, rel: "jingle.mp3", size: 50000, mtime: NOW_S - 1500 },
      ],
      count: 4,
      truncated: false,
    },
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = function scrollIntoView() {} as unknown as Element["scrollIntoView"];
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function openDrawer(): Promise<HTMLElement> {
  const top = await screen.findByTestId("chat-topbar");
  fireEvent.click(await within(top).findByRole("button", { name: /^Project/ }));
  return await screen.findByRole("dialog", { name: "Project panel" });
}

/* ========================================================================== */
/*  1a. The folder picker, on its own                                          */
/* ========================================================================== */

describe("DirectoryTree variant='calm': no card, quiet words, tokens only", () => {
  it("draws no card, no accent header, sentence-case labels, a hairline select and input, a ghost Go", async () => {
    const { container } = render(<DirectoryTree variant="calm" selectedPath={null} onSelect={() => {}} hideAction />);
    // Wait for the THING asserted: the tree's rows (they land after two reads).
    await screen.findByRole("button", { name: /Windows/ });
    const root = container.querySelector('[data-variant="calm"]') as HTMLElement;
    expect(root).not.toBeNull();
    for (const card of ["rounded-2xl", "border", "shadow-card", "backdrop-blur-sm", "bg-ink-850/80"]) {
      expect(classes(root)).not.toContain(card);
    }
    expect(screen.queryByRole("heading", { name: "Directory" })).toBeNull();
    expect(screen.getByRole("heading", { name: "Pick a folder" })).toBeTruthy();
    // Quiet sentence-case labels: no ROOT / SELECTED in uppercase.
    expect(screen.getByText("Drive")).toBeTruthy();
    expect(screen.queryByText("Root")).toBeNull();
    expect(screen.queryByText("Selected")).toBeNull();
    const all = allClasses(root);
    expect(all).not.toContain("uppercase");
    expect(all).not.toContain("font-mono");
    // Hairline inputs in the normal font, not the boxed `field`.
    const select = screen.getByRole("combobox", { name: "Root directory" }) as HTMLSelectElement;
    const input = screen.getByLabelText("Go to root path") as HTMLInputElement;
    for (const f of [select, input]) {
      expect(classes(f)).toEqual(expect.arrayContaining(["border", "hairline", "text-[13px]"]));
      expect(classes(f)).not.toContain("field");
    }
    expect(input.placeholder).toBe("Or paste a folder path");
    // The drive reads as its path, with no em-dash.
    expect(select.options[0].textContent).toBe("C:\\");
    // A ghost Go: no border, no btn-ghost box; a soft fill on hover.
    const go = screen.getByRole("button", { name: "Go" });
    expect(classes(go)).toContain("hover:bg-white/[0.06]");
    expect(classes(go).some((c) => c === "border" || c === "btn-ghost")).toBe(false);
    // Plain copy, no em-dash anywhere in the picker.
    expect(root.textContent).toContain("No folder picked yet. Pick one below.");
    expect(root.textContent ?? "").not.toMatch(/—/);
  });

  it("the project marker is a quiet word (no fill, no hue) that still explains itself", async () => {
    const { container } = render(<DirectoryTree variant="calm" selectedPath={null} onSelect={() => {}} hideAction />);
    const chip = await screen.findByText("node");
    expect(chip.getAttribute("title") ?? "").toMatch(/code project/i);
    expect(classes(chip)).toContain("text-zinc-500");
    expect(classes(chip).some((c) => /^(bg|border)-/.test(c))).toBe(false);
    // The whole calm picker, rows and chip included: whole pixels, tokens only.
    await screen.findByRole("button", { name: /Windows/ });
    const root = container.querySelector('[data-variant="calm"]') as HTMLElement;
    expect(offenders(root.outerHTML)).toEqual([]);
    const row = screen.getByRole("button", { name: /Windows/ });
    expect(classes(row)).toContain("text-[13px]");
  });

  it("still picks: a row selects its folder, a pasted path becomes the root, the collapse words are the caller's", async () => {
    const onSelect = vi.fn();
    const onCollapse = vi.fn();
    render(
      <DirectoryTree
        variant="calm"
        selectedPath={"C:\\Work"}
        onSelect={onSelect}
        hideAction
        onCollapse={onCollapse}
        collapseLabel="Back to files"
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Windows/ }));
    expect(onSelect).toHaveBeenCalledWith("C:\\Windows");
    expect(screen.getByText("C:\\Work")).toBeTruthy(); // "Picked: C:\Work"
    fireEvent.change(screen.getByLabelText("Go to root path"), { target: { value: "\\\\server\\share" } });
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    await waitFor(() =>
      expect((screen.getByRole("combobox", { name: "Root directory" }) as HTMLSelectElement).value).toBe(
        "\\\\server\\share",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Back to files" }));
    expect(onCollapse).toHaveBeenCalledTimes(1);
  });

  it("anti-vacuity: the card look (Build, Memory) is unchanged, and its line is plain copy too", async () => {
    const { container } = render(<DirectoryTree selectedPath={null} onSelect={() => {}} onOpenTerminal={() => {}} />);
    await screen.findByRole("button", { name: /Windows/ });
    expect(screen.getByRole("heading", { name: "Directory" })).toBeTruthy();
    expect(classes(container.firstElementChild)).toContain("rounded-2xl");
    expect(classes(await screen.findByText("node"))).toContain("bg-emerald-500/10");
    expect(screen.getByText("Selected")).toBeTruthy();
    const line = screen.getByText(/No folder picked yet/);
    expect(line.textContent).toBe("No folder picked yet. Pick a project or a folder.");
  });
});

/* ========================================================================== */
/*  1b. The file list, on its own                                              */
/* ========================================================================== */

describe("FilesPanel variant='calm': names in the normal font, tokens only", () => {
  it("draws no card; a row's name is 13 px in the normal font, its size line 11 px with tabular numbers", async () => {
    const { container } = render(<FilesPanel variant="calm" folder={FOLDER} onPreview={() => {}} />);
    const name = await screen.findByText("menu.md");
    const root = container.querySelector('[data-variant="calm"]') as HTMLElement;
    for (const card of ["rounded-2xl", "border", "shadow-card"]) expect(classes(root)).not.toContain(card);
    expect(classes(name)).toContain("text-[13px]");
    expect(classes(name)).not.toContain("font-mono");
    const meta = name.nextElementSibling as HTMLElement;
    expect(classes(meta)).toEqual(expect.arrayContaining(["text-[11px]", "tabular-nums"]));
    // The folder's path under its name is in the normal font too.
    expect(classes(screen.getByTitle(FOLDER))).not.toContain("font-mono");
    // Image, video, audio and text icons all present, all tokens.
    expect(await screen.findByText("art\\logo.png")).toBeTruthy();
    expect(allClasses(root)).not.toContain("font-mono");
    expect(offenders(root.outerHTML)).toEqual([]);
    expect(root.textContent ?? "").not.toMatch(/—/);
  });

  it("anti-vacuity: the card look (Build) keeps its monospace rows", async () => {
    render(<FilesPanel folder={FOLDER} onPreview={() => {}} />);
    expect(classes(await screen.findByText("menu.md"))).toContain("font-mono");
  });
});

/* ========================================================================== */
/*  1c. The drawer passes the calm look                                        */
/* ========================================================================== */

describe("the chat's project drawer uses the calm picker and the calm file list", () => {
  it("with no folder: the calm picker, whole pixels and tokens only, the drawer's Close is the way out", async () => {
    window.localStorage.setItem(PROJECT_KEY, PROJECT.id);
    render(<ChatPage />);
    const drawer = await openDrawer();
    await within(drawer).findByRole("button", { name: /Windows/ });
    const picker = drawer.querySelector('[data-testid="directory-tree"]') as HTMLElement;
    expect(picker.getAttribute("data-variant")).toBe("calm");
    expect(offenders(picker.outerHTML)).toEqual([]);
    expect(allClasses(picker)).not.toContain("uppercase");
    expect(within(drawer).queryByRole("button", { name: "Back to files" })).toBeNull();
    // Anti-vacuity: the drawer's own Close is there.
    expect(within(drawer).getByRole("button", { name: /Close/ })).toBeTruthy();
  });

  it("with a folder: the calm file list; Change opens the calm picker and Back to files returns", async () => {
    window.localStorage.setItem(WORKSPACE_KEY, FOLDER);
    render(<ChatPage />);
    const drawer = await openDrawer();
    const name = await within(drawer).findByText("menu.md");
    expect(classes(name)).not.toContain("font-mono");
    expect(drawer.querySelector('[data-testid="files-panel"]')?.getAttribute("data-variant")).toBe("calm");
    fireEvent.click(within(drawer).getByRole("button", { name: "Change workspace folder" }));
    const back = await within(drawer).findByRole("button", { name: "Back to files" });
    const picker = drawer.querySelector('[data-testid="directory-tree"]') as HTMLElement;
    expect(picker.getAttribute("data-variant")).toBe("calm");
    expect(picker.textContent).toContain(FOLDER); // "Picked: C:\Work\Harbor"
    fireEvent.click(back);
    await waitFor(() => expect(drawer.querySelector('[data-testid="directory-tree"]')).toBeNull());
    expect(await within(drawer).findByText("menu.md")).toBeTruthy();
  });
});

/* ========================================================================== */
/*  2. Tasks: the grant and the failure in the tone tokens                     */
/* ========================================================================== */

describe("ProjectTasks: the tool grant and the failure line use the tone tokens", () => {
  async function planned(bare: boolean) {
    H.api.postResponses["/projects/proj_1/task/plan"] = {
      tools: [{ perm_key: "write_file", name: "write_file", why: "save the report" }],
    };
    render(<ProjectTasks projectId="proj_1" hasRoot sessions={[]} bare={bare} />);
    fireEvent.change(screen.getByLabelText("Task for an agent in this project"), {
      target: { value: "summarize every PDF" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^\s*Run\s*$/ }));
    });
    await screen.findByRole("button", { name: /Allow all & run/ });
    return screen.getByTestId("project-task-grant");
  }

  it("calm (the chat's Tasks tab): a plain hairline section, the heading in the warning tone", async () => {
    const grant = await planned(true);
    expect(classes(grant)).toEqual(expect.arrayContaining(["border-t", "hairline"]));
    expect(classes(grant).some((c) => /^rounded|^bg-/.test(c))).toBe(false);
    expect(classes(within(grant).getByText(/This task will use these tools/))).toContain("text-tone-warn");
    expect(offenders(grant.outerHTML)).toEqual([]);
  });

  it("card (the project page): a tone-warn box, never literal amber", async () => {
    const grant = await planned(false);
    expect(classes(grant)).toEqual(expect.arrayContaining(["border-tone-warn/25", "bg-tone-warn/[0.06]"]));
    expect(offenders(grant.outerHTML)).toEqual([]);
  });
});

/* ========================================================================== */
/*  3. Source guard: the calm surfaces this item owns keep tokens only         */
/* ========================================================================== */

describe("source guard: no literal hue, half pixel or colour value on the calm surfaces", () => {
  const FILES_GUARDED = [
    "app/chat/page.tsx",
    "components/project/ProjectTasks.tsx",
    "components/project/KnowledgePanel.tsx",
    "components/project/ProjectSchedules.tsx",
    "components/AppSidebar.tsx",
    "components/Sidebar.tsx",
    "components/chat/ProjectPanelParts.tsx",
  ];

  it("each guarded file is clean", () => {
    for (const f of FILES_GUARDED) expect(offenders(readSrc(f)), f).toEqual([]);
  });

  it("the patterns catch what they claim (anti-vacuity on the real old classes)", () => {
    for (const old of [
      'className="text-rose-400/90"',
      'className="border border-amber-400/25 bg-amber-400/5 text-amber-200/90"',
      'className="text-emerald-400"',
      'className="text-rose-300 hover:text-rose-200"',
      'className="border-amber-500/25 bg-amber-500/[0.06] text-amber-200"',
      'className="bg-emerald-400"',
    ]) {
      expect(offenders(old), old).not.toEqual([]);
    }
    for (const ok of ['className="text-tone-warn bg-tone-warn/[0.05]"', 'className="text-tone-danger"']) {
      expect(offenders(ok), ok).toEqual([]);
    }
  });

  it("the page's notices say their words without em-dash asides", () => {
    const page = readSrc("app/chat/page.tsx");
    expect(page).toContain("keeps the thread going, and\n        the full transcript is kept either way.");
    expect(page).toContain('"The reply ran out of room and stopped."');
    expect(page).toContain('"The reply was cut off."');
    expect(page).not.toMatch(/"stopped — the reply ran out of room"|"interrupted — the reply was cut off"/);
  });
});
