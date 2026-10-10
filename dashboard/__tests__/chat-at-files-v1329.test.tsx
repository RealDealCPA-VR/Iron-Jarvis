/**
 * v1.329.0 (calm chat wave 4, F3) — the "@" menu: files and folders too,
 * chats you can tell apart, agents in plain words.
 *
 * 1. In a project, "@" offers the project folder's files and folders (GET
 *    /fs/files with the Files tab's own query). A FILE is attached exactly as
 *    "+ Attach" leaves an upload: the paperclip chip and the `attachments`
 *    request field. A FOLDER becomes the working folder exactly as "+ Choose
 *    a working folder" does: `workspace_dir`, and the card says so. No
 *    project = no Files section and no listing asked for.
 * 2. Saved chats that share a title read apart, by the chat lists' rule
 *    (W8 J2): one short time or day in place of the age; a title of its own
 *    shows the age. The project is on the row's tooltip.
 * 3. Agent rows read as names in the normal font ("Builder"), with "@builder"
 *    as a muted hint, under a calm "Agents" heading in sentence case.
 * The keys walk every section as one list, and a squeezed menu keeps the
 * sections below in view (lib/chatRefsMenuFit).
 *
 * Harness: the chat-at-chats-v1328 header.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  AT_FILES_ROWS_MAX,
  decodeProjectFiles,
  entryWhere,
  matchProjectEntries,
  projectFilesPath,
  projectFolders,
  sameFolderPath,
} from "@/lib/atMenuFiles";
import { atAgentName, chatClock, chatDay } from "@/lib/atMenuRows";
import { decodeChatRefRows } from "@/lib/chatRefs";
import { composerKeyHint } from "@/lib/composerChips";

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
      gets: [] as string[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      threads: [] as Record<string, unknown>[],
    },
    refs: { rows: [] as Record<string, unknown>[] },
    stream: { bodies: [] as Record<string, unknown>[] },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.api.gets.push(path);
    if (path.startsWith("/chat/threads/search-refs")) {
      const q = (new URL(`http://x${path}`).searchParams.get("q") ?? "").toLowerCase();
      return {
        threads: H.refs.rows.filter((r) => String(r.title).toLowerCase().includes(q)),
      };
    }
    const r = H.api.getResponses[path];
    if (r !== undefined) return r;
    if (path.startsWith("/chat/threads?")) return { threads: H.api.threads };
    throw new H.FakeApiError(`unmocked GET ${path}`, 404);
  },
  post: async () => ({}),
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
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
    run: async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
      H.stream.bodies.push(body);
      await new Promise<void>((r) => setTimeout(r, 0));
      onDelta("Noted.", "Noted.");
      return {
        reply: "Noted.",
        route: { requested: "", provider: "mock", model: "mock", reason: "default" },
      };
    },
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false, reason: null, engine: null, listening: false, processing: false,
    transcript: "", interim: "", error: null, start: () => {}, stop: () => {}, reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";

const ROOT = "C:\\Work\\Harbor";
const PROJECT = { id: "p1", name: "Harbor Street Cafe", root: ROOT, root_exists: true };
const OTHER = { id: "p2", name: "Pier 9", root: "C:\\Work\\Pier9", root_exists: true };
const FILES_PATH = projectFilesPath(ROOT);
/** Newest first, as GET /fs/files answers. */
const LISTING = {
  root: ROOT,
  files: [
    { name: "q3-report.pdf", path: `${ROOT}\\reports\\q3-report.pdf`, rel: "reports/q3-report.pdf", size: 2048, mtime: 4 },
    { name: "notes.md", path: `${ROOT}\\notes.md`, rel: "notes.md", size: 10, mtime: 3 },
    { name: "menu.xlsx", path: `${ROOT}\\data\\menus\\menu.xlsx`, rel: "data/menus/menu.xlsx", size: 5000, mtime: 2 },
    {
      name: "report-template.docx",
      path: `${ROOT}\\templates\\report-template.docx`,
      rel: "templates/report-template.docx",
      size: 300,
      mtime: 1,
    },
  ],
  count: 4,
  truncated: false,
};
const AGENTS = [
  { name: "builder", mention: "builder", description: "Builds things", kind: "builtin", healthy: true },
  { name: "file_manager", mention: "file_manager", description: "Moves files", kind: "builtin", healthy: true },
  { name: "custom:tax-helper", mention: "tax-helper", description: "Tax help", kind: "dynamic", healthy: true },
];

let boxes: Record<string, { top: number; bottom: number }> = {};
const realRect = Element.prototype.getBoundingClientRect;
const realInnerHeight = window.innerHeight;

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.puts.length = 0;
  H.api.threads = [];
  H.stream.bodies.length = 0;
  H.refs.rows = [];
  H.api.getResponses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [PROJECT, OTHER] },
    "/agents/mentionable": { agents: AGENTS },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
    [FILES_PATH]: LISTING,
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
  boxes = {};
  Element.prototype.getBoundingClientRect = function (this: Element) {
    const id = (this as HTMLElement).dataset?.testid;
    const b = id ? boxes[id] : undefined;
    return b
      ? ({
          top: b.top, bottom: b.bottom, left: 0, right: 600, width: 600, height: b.bottom - b.top,
          x: 0, y: b.top, toJSON: () => ({}),
        } as DOMRect)
      : realRect.call(this);
  };
  Object.defineProperty(window, "innerHeight", { configurable: true, value: 844 });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  Element.prototype.getBoundingClientRect = realRect;
  Object.defineProperty(window, "innerHeight", { configurable: true, value: realInnerHeight });
});

/** Open the page inside the project (the last-used choice). */
function inProject() {
  window.localStorage.setItem("ij_chat_project", PROJECT.id);
}

async function box(): Promise<HTMLTextAreaElement> {
  return (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
}

function type(el: HTMLTextAreaElement, value: string) {
  fireEvent.change(el, { target: { value } });
}

const fileNames = () =>
  screen
    .queryAllByTestId("at-file-option")
    .map((o) => `${o.getAttribute("data-kind")}:${o.querySelector('[data-testid="at-file-name"]')?.textContent}`);

/** Wait until the project is applied (its folder is the working folder). */
async function projectReady() {
  await screen.findByRole("button", { name: "Switch project" });
  await waitFor(() =>
    expect(
      screen.getAllByRole("button", { name: "Switch project" }).some((b) => b.textContent?.includes(PROJECT.name)),
    ).toBe(true),
  );
}

async function send(el: HTMLTextAreaElement, words: string, n: number) {
  type(el, words);
  fireEvent.keyDown(el, { key: "Enter" });
  await waitFor(() => expect(H.stream.bodies).toHaveLength(n));
  return H.stream.bodies[n - 1];
}

describe('"@" offers the project\'s files and folders (v1.329.0)', () => {
  it("no project: no Files section, and no folder listing is asked for", async () => {
    render(<ChatPage />);
    const el = await box();
    type(el, "@");
    await screen.findByTestId("at-menu");
    await screen.findByRole("listbox", { name: "Agents" });
    expect(screen.queryByTestId("at-menu-files")).toBeNull();
    expect(H.api.gets.some((p) => p.startsWith("/fs/files"))).toBe(false);
    // The key line promises only what "@" offers here.
    expect(screen.getByText(/@ for agents and chats/)).toBeTruthy();
  });

  it("in a project: lists the folder with the Files tab's query; a couple of folders, then the newest files", async () => {
    inProject();
    render(<ChatPage />);
    const el = await box();
    await projectReady();
    type(el, "@");
    await waitFor(() => expect(screen.getByTestId("at-menu-files")).toBeTruthy());
    expect(H.api.gets).toContain(FILES_PATH);
    expect(FILES_PATH).toBe(`/fs/files?path=${encodeURIComponent(ROOT)}&depth=4&limit=600`);
    await waitFor(() =>
      expect(fileNames()).toEqual([
        "folder:reports",
        "folder:data",
        "file:q3-report.pdf",
        "file:notes.md",
        "file:menu.xlsx",
        "file:report-template.docx",
      ]),
    );
    // A calm heading in sentence case with a plain hint.
    const head = screen.getByTestId("at-menu-files").textContent ?? "";
    expect(head).toContain("Files");
    expect(head).toContain("A file is attached, a folder becomes the working folder");
    // Where each sits inside the project, quietly.
    expect(screen.getAllByTestId("at-file-option")[2].textContent).toContain("reports");
    // The key line says "@" offers files here.
    expect(screen.getByText(/@ for agents, files and chats/)).toBeTruthy();
  });

  it("typing narrows by name first, then by path; the Files section sits between agents and chats", async () => {
    inProject();
    H.refs.rows = [{ id: "c-r", title: "Report ideas", updated_at: new Date().toISOString() }];
    render(<ChatPage />);
    const el = await box();
    await projectReady();
    type(el, "@rep");
    await waitFor(() =>
      expect(fileNames()).toEqual(["folder:reports", "file:report-template.docx", "file:q3-report.pdf"]),
    );
    await waitFor(() => expect(screen.getAllByTestId("chat-ref-option")).toHaveLength(1));
    const menu = screen.getByTestId("at-menu");
    const order = Array.from(menu.querySelectorAll('[role="listbox"]')).map((l) => l.getAttribute("aria-label"));
    expect(order.indexOf("Files")).toBeLessThan(order.indexOf("Chats"));
  });

  it("picking a FILE attaches it like + Attach: the paperclip chip, no words, and `attachments` on the request", async () => {
    inProject();
    render(<ChatPage />);
    const el = await box();
    await projectReady();
    type(el, "summarise @q3");
    await waitFor(() => expect(fileNames()).toEqual(["file:q3-report.pdf"]));
    fireEvent.click(screen.getByTestId("at-file-option"));
    await screen.findByRole("button", { name: "Remove q3-report.pdf" });
    expect(el.value).toBe("summarise ");
    // Not offered again while attached (the others that match still are).
    type(el, "summarise @rep");
    await waitFor(() => expect(fileNames()).toEqual(["folder:reports", "file:report-template.docx"]));
    type(el, "summarise ");
    const body = await send(el, "summarise it", 1);
    expect(body.attachments).toEqual([`${ROOT}\\reports\\q3-report.pdf`]);
    // Consumed like an upload: the next message carries none.
    await waitFor(() => expect(screen.queryByRole("button", { name: "Remove q3-report.pdf" })).toBeNull());
  });

  it("picking a FOLDER makes it the working folder like + Choose a working folder; the card says so; × goes back", async () => {
    inProject();
    render(<ChatPage />);
    const el = await box();
    await projectReady();
    // The project's own folder is the working folder: no chip.
    const first = await send(el, "hello", 1);
    expect(first.workspace_dir).toBe(ROOT);
    expect(screen.queryByTestId("working-folder-chip")).toBeNull();
    type(el, "look in @data");
    await waitFor(() => expect(fileNames()[0]).toBe("folder:data"));
    fireEvent.click(screen.getAllByTestId("at-file-option")[0]);
    const chip = await screen.findByTestId("working-folder-chip");
    expect(chip.textContent).toContain("Working in data");
    expect(el.value).toBe("look in ");
    // The same sticky default "+ Choose a working folder" writes.
    expect(window.localStorage.getItem("ij_chat_workspace")).toBe(`${ROOT}\\data`);
    const second = await send(el, "what is here?", 2);
    expect(second.workspace_dir).toBe(`${ROOT}\\data`);
    expect("attachments" in second).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Work in the project folder again" }));
    await waitFor(() => expect(screen.queryByTestId("working-folder-chip")).toBeNull());
    const third = await send(el, "and now?", 3);
    expect(third.workspace_dir).toBe(ROOT);
  });

  it("the keys walk from the agents into the files; Enter on a file attaches it and sends nothing", async () => {
    inProject();
    H.api.getResponses["/agents/mentionable"] = { agents: [AGENTS[0]] };
    render(<ChatPage />);
    const el = await box();
    await projectReady();
    type(el, "@note");
    await waitFor(() => expect(fileNames()).toEqual(["file:notes.md"]));
    // No agent matches "note": the file is the first row.
    const active = () =>
      screen.getByTestId("at-menu").querySelector('[data-active="true"]')?.getAttribute("data-testid");
    await waitFor(() => expect(active()).toBe("at-file-option"));
    fireEvent.keyDown(el, { key: "Enter" });
    await screen.findByRole("button", { name: "Remove notes.md" });
    expect(H.stream.bodies).toHaveLength(0);
    // With an agent above, ↓ moves from it into the files.
    type(el, "@");
    await waitFor(() => expect(fileNames().length).toBeGreaterThan(0));
    const menu = screen.getByTestId("at-menu");
    await waitFor(() => expect(menu.querySelector('[data-active="true"]')?.textContent).toContain("Builder"));
    fireEvent.keyDown(el, { key: "ArrowDown" });
    await waitFor(() => expect(active()).toBe("at-file-option"));
  });

  it("a folder whose project folder is missing offers no files", async () => {
    H.api.getResponses["/projects"] = { projects: [{ ...PROJECT, root_exists: false }] };
    inProject();
    render(<ChatPage />);
    const el = await box();
    await projectReady();
    type(el, "@");
    await screen.findByRole("listbox", { name: "Agents" });
    expect(screen.queryByTestId("at-menu-files")).toBeNull();
    expect(H.api.gets.some((p) => p.startsWith("/fs/files"))).toBe(false);
  });

  it("a squeezed menu: agents and files keep their first rows while chats match, each saying how many more", async () => {
    inProject();
    H.refs.rows = [{ id: "c-a", title: "A chat", updated_at: new Date().toISOString() }];
    render(<ChatPage />);
    const el = await box();
    await projectReady();
    boxes["chat-composer"] = { top: 300, bottom: 424 };
    boxes["chat-card"] = { top: 166, bottom: 601 };
    (screen.getByTestId("chat-card") as HTMLElement).style.overflowY = "auto";
    type(el, "@");
    await waitFor(() => expect(screen.getAllByTestId("chat-ref-option")).toHaveLength(1));
    // 161px of room: one row per section above the chats.
    await waitFor(() => expect(screen.getAllByTestId("at-file-option")).toHaveLength(1));
    const agentRows = screen.getByRole("listbox", { name: "Agents" }).querySelectorAll('[role="option"]');
    expect(agentRows).toHaveLength(1);
    expect(screen.getByTestId("at-menu-more-agents").textContent).toBe("2 more agents, type to narrow");
    // Two folders and four files offered; one shown.
    expect(screen.getByTestId("at-menu-more-files").textContent).toBe("5 more in this project, type to narrow");
  });
});

describe("chats that share a title read apart (v1.329.0)", () => {
  // W8 J2: the menu's chat rows follow the chat lists' same-title rule
  // (lib/sameTitleRows via lib/chatRefsRows): ONE quiet part per row. A
  // shared title shows one short time or day IN PLACE of the age; a title of
  // its own shows the age, as the lists do. The project (from the daemon or
  // the chat list) moved to the row's tooltip; the old drawn "project · time"
  // part plus an age was the double label H1 took out of both lists.
  it("a shared title: one short time or day in place of the age; a unique title: the age; the project on hover", async () => {
    inProject();
    const now = new Date();
    const at = (h: number, m: number) =>
      new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m).toISOString();
    const lastYear = new Date(now.getFullYear() - 1, 2, 4, 9, 0).toISOString();
    H.refs.rows = [
      { id: "c1", title: "Check what changed in the repo", updated_at: at(0, 5), project_id: "p2" },
      { id: "c2", title: "Check what changed in the repo", updated_at: lastYear, project_id: null },
      { id: "c3", title: "Lease options", updated_at: at(0, 10) },
      { id: "c4", title: "Lease options", updated_at: at(0, 40) },
      { id: "c5", title: "Menu prices", updated_at: new Date(Date.now() - 3 * 60_000).toISOString() },
    ];
    // The chat list knows c3 and c4 are in this project (the row does not say).
    H.api.threads = [
      { id: "c3", title: "Lease options", project_id: "p1", updated_at: at(0, 10), messages: 2 },
      { id: "c4", title: "Lease options", project_id: "p1", updated_at: at(0, 40), messages: 2 },
    ];
    render(<ChatPage />);
    const el = await box();
    await projectReady();
    await waitFor(() => expect(H.api.gets.some((p) => p.startsWith("/chat/threads?"))).toBe(true));
    type(el, "@");
    const parts = () =>
      screen.getAllByTestId("chat-ref-option").map((o) => {
        const times = o.querySelectorAll("time");
        return `${times.length}:${times[0]?.getAttribute("data-testid") ?? ""}:${times[0]?.textContent ?? ""}`;
      });
    const y = new Date(lastYear);
    await waitFor(() =>
      expect(parts()).toEqual([
        // Alone on its day, each twin says its day.
        "1:chat-ref-twin:Today",
        `1:chat-ref-twin:Mar 4, ${y.getFullYear()}`,
        // Twins on one day say the time.
        "1:chat-ref-twin:12:10 AM",
        "1:chat-ref-twin:12:40 AM",
        // A title of its own: the list's age.
        "1:chat-ref-age:3m",
      ]),
    );
    // Never a second label on the row: no project or day part beside it.
    expect(screen.queryAllByTestId("chat-ref-where")).toHaveLength(0);
    const twin = screen.getAllByTestId("chat-ref-twin")[0];
    expect(twin.className).toContain("text-zinc-500");
    // The exact time (with seconds) is on hover, as in the lists.
    expect(twin.getAttribute("title")).toBe("Today · 12:05:00 AM");
    // The project stays reachable: the row's tooltip names it.
    const opts = screen.getAllByTestId("chat-ref-option");
    expect(opts[0].getAttribute("title")).toBe("Check what changed in the repo (Pier 9)");
    expect(opts[1].getAttribute("title")).toBe("Check what changed in the repo");
    expect(opts[2].getAttribute("title")).toBe("Lease options (Harbor Street Cafe)");
    // The quiet part is never sent or saved: the chip keeps id + title.
    fireEvent.click(opts[0]);
    const body = await send(el, "compare", 1);
    expect(body.thread_refs).toEqual(["c1"]);
  });

  it("the keys still walk the twin rows and Enter picks the highlighted one", async () => {
    inProject();
    const now = new Date();
    const at = (h: number, m: number) =>
      new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m).toISOString();
    H.refs.rows = [
      { id: "c3", title: "Lease options", updated_at: at(0, 10) },
      { id: "c4", title: "Lease options", updated_at: at(0, 40) },
    ];
    render(<ChatPage />);
    const el = await box();
    await projectReady();
    type(el, "@lease");
    await waitFor(() =>
      expect(screen.getAllByTestId("chat-ref-twin").map((t) => t.textContent)).toEqual(["12:10 AM", "12:40 AM"]),
    );
    const active = () =>
      screen.getAllByTestId("chat-ref-option").map((o) => o.getAttribute("data-active") === "true");
    expect(active()).toEqual([true, false]);
    fireEvent.keyDown(el, { key: "ArrowDown" });
    await waitFor(() => expect(active()).toEqual([false, true]));
    fireEvent.keyDown(el, { key: "Enter" });
    await waitFor(() => expect(screen.getAllByTestId("chat-ref-chip")).toHaveLength(1));
    const body = await send(el, "which lease?", 1);
    expect(body.thread_refs).toEqual(["c4"]);
  });
});

describe("agents in plain words (v1.329.0)", () => {
  it("a calm 'Agents' heading; each row a name in the normal font with the @name as a muted hint", async () => {
    render(<ChatPage />);
    const el = await box();
    type(el, "@");
    const list = await screen.findByRole("listbox", { name: "Agents" });
    await waitFor(() => expect(list.querySelectorAll('[role="option"]')).toHaveLength(3));
    const text = list.textContent ?? "";
    expect(text).toContain("Agents");
    expect(text).toContain("Answer in place of Iron Jarvis");
    expect(text).not.toMatch(/they answer instead/i);
    expect(text).not.toContain("—");
    // Sentence case, never shouted.
    const head = list.firstElementChild as HTMLElement;
    expect(head.innerHTML).not.toContain("uppercase");
    const names = Array.from(list.querySelectorAll('[data-testid="at-agent-name"]'));
    expect(names.map((n) => n.textContent)).toEqual(["Builder", "File manager", "tax-helper"]);
    for (const n of names) expect(n.className).not.toContain("font-mono");
    const hints = Array.from(list.querySelectorAll('[data-testid="at-agent-mention"]'));
    expect(hints.map((n) => n.textContent)).toEqual(["@builder", "@file_manager", "@tax-helper"]);
    expect(hints[0].className).toContain("text-zinc-500");
  });

  it("picking an agent still writes its @mention", async () => {
    render(<ChatPage />);
    const el = await box();
    type(el, "@file");
    const list = await screen.findByRole("listbox", { name: "Agents" });
    await waitFor(() => expect(list.querySelectorAll('[role="option"]')).toHaveLength(1));
    fireEvent.click(list.querySelector('[role="option"]') as HTMLElement);
    await waitFor(() => expect(el.value).toBe("@file_manager "));
  });

  it("with nothing matching anywhere, one plain line", async () => {
    render(<ChatPage />);
    const el = await box();
    type(el, "@zzz");
    await screen.findByText("No agent by that name. Add one on the Agents page.");
  });
});

describe("helpers (v1.329.0)", () => {
  it("decodes the listing by whitelist", () => {
    expect(decodeProjectFiles(null)).toBeNull();
    expect(decodeProjectFiles({ root: "", files: [] })).toBeNull();
    expect(decodeProjectFiles({ root: "C:\\x", files: "no" })).toBeNull();
    const got = decodeProjectFiles({
      root: "C:\\x",
      files: [
        { name: "a.txt", path: "C:\\x\\a.txt", rel: "a.txt", size: 3 },
        { name: "b.txt", path: "C:\\x\\d\\b.txt", rel: "d\\b.txt" },
        { name: 7, path: "C:\\x\\c", rel: "c" },
        { name: "", path: "C:\\x\\e", rel: "e" },
        null,
      ],
    });
    expect(got).toEqual({
      root: "C:\\x",
      files: [
        { name: "a.txt", path: "C:\\x\\a.txt", rel: "a.txt", size: 3 },
        { name: "b.txt", path: "C:\\x\\d\\b.txt", rel: "d/b.txt", size: 0 },
      ],
    });
  });

  it("folders come from where the files live, with real paths on either kind of root", () => {
    const win = decodeProjectFiles(LISTING)!;
    expect(projectFolders(win).map((f) => [f.rel, f.path])).toEqual([
      ["reports", `${ROOT}\\reports`],
      ["data", `${ROOT}\\data`],
      ["data/menus", `${ROOT}\\data\\menus`],
      ["templates", `${ROOT}\\templates`],
    ]);
    const posix = decodeProjectFiles({
      root: "/home/me/proj/",
      files: [{ name: "x.md", path: "/home/me/proj/a/b/x.md", rel: "a/b/x.md", size: 1 }],
    })!;
    expect(projectFolders(posix).map((f) => f.path)).toEqual(["/home/me/proj/a", "/home/me/proj/a/b"]);
  });

  it("matching: nothing typed = two top folders then newest files; typed = name start, name, path; attached left out", () => {
    const l = decodeProjectFiles(LISTING)!;
    expect(matchProjectEntries(l, "").map((e) => e.name)).toEqual([
      "reports", "data", "q3-report.pdf", "notes.md", "menu.xlsx", "report-template.docx",
    ]);
    expect(matchProjectEntries(l, "").length).toBeLessThanOrEqual(AT_FILES_ROWS_MAX);
    expect(matchProjectEntries(l, "rep").map((e) => `${e.kind}:${e.name}`)).toEqual([
      "folder:reports", "file:report-template.docx", "file:q3-report.pdf",
    ]);
    // A path match ranks last: "menus" names the folder, and the file only by path.
    expect(matchProjectEntries(l, "menus").map((e) => `${e.kind}:${e.name}`)).toEqual([
      "folder:menus", "file:menu.xlsx",
    ]);
    // Rank beats recency: a newer file found only by its path comes after an
    // older one whose name starts with the words, then one that holds them.
    const ranked = decodeProjectFiles({
      root: "C:\\p",
      files: [
        { name: "a.txt", path: "C:\\p\\plans\\a.txt", rel: "plans/a.txt", size: 1 },
        { name: "old-plans.txt", path: "C:\\p\\old-plans.txt", rel: "old-plans.txt", size: 1 },
        { name: "plans-2026.md", path: "C:\\p\\plans-2026.md", rel: "plans-2026.md", size: 1 },
      ],
    })!;
    expect(matchProjectEntries(ranked, "plans").map((e) => `${e.kind}:${e.name}`)).toEqual([
      "folder:plans", "file:plans-2026.md", "file:old-plans.txt", "file:a.txt",
    ]);
    expect(
      matchProjectEntries(l, "rep", [`${ROOT}\\templates\\report-template.docx`]).map((e) => e.name),
    ).toEqual(["reports", "q3-report.pdf"]);
    expect(matchProjectEntries(null, "x")).toEqual([]);
    expect(entryWhere({ kind: "file", name: "menu.xlsx", path: "p", rel: "data/menus/menu.xlsx" })).toBe("data/menus");
    expect(entryWhere({ kind: "file", name: "notes.md", path: "p", rel: "notes.md" })).toBe("");
  });

  it("same folder: slashes either way, trailing separator, case", () => {
    expect(sameFolderPath("C:\\Work\\Harbor\\", "c:/work/harbor")).toBe(true);
    expect(sameFolderPath("C:\\Work\\Harbor\\data", "C:\\Work\\Harbor")).toBe(false);
  });

  // W8 J2: the rows' one quiet part is lib/chatRefsRows (pinned in
  // chat-at-same-title-v1329); the day and time words stay here.
  it("a chat's day and time in words", () => {
    const now = new Date(2026, 9, 9, 15, 0).getTime();
    const iso = (d: Date) => d.toISOString();
    expect(chatDay(iso(new Date(2026, 9, 8, 9, 0)), now)).toBe("Yesterday");
    expect(chatDay(iso(new Date(2026, 8, 1, 9, 0)), now)).toBe("Sep 1");
    expect(chatDay(null, now)).toBe("");
    expect(chatDay(iso(new Date(2026, 9, 9, 1, 0)), now)).toBe("Today");
    expect(chatDay(iso(new Date(2025, 0, 2, 1, 0)), now)).toBe("Jan 2, 2025");
    expect(chatClock(iso(new Date(2026, 9, 9, 0, 7)))).toBe("12:07 AM");
    expect(chatClock(iso(new Date(2026, 9, 9, 12, 0)))).toBe("12:00 PM");
    expect(chatClock("not a time")).toBe("");
  });

  it("decodes project_id onto a chat row only when the daemon sends one", () => {
    expect(
      decodeChatRefRows({
        threads: [
          { id: "a", title: "A", project_id: "p1" },
          { id: "b", title: "B", project_id: null },
          { id: "c", title: "C", project_id: 7 },
          { id: "d", title: "D" },
        ],
      }),
    ).toEqual([
      { id: "a", title: "A", projectId: "p1" },
      { id: "b", title: "B", projectId: null },
      { id: "c", title: "C" },
      { id: "d", title: "D" },
    ]);
  });

  it("the key line names files only when there are files to offer", () => {
    expect(composerKeyHint(false, true)).toContain("@ for agents and chats");
    expect(composerKeyHint(false, true, true)).toContain("@ for agents, files and chats");
    expect(composerKeyHint(true, true, true)).not.toContain("@");
  });

  it("an agent's name in words", () => {
    expect(atAgentName({ mention: "builder", kind: "builtin" })).toBe("Builder");
    expect(atAgentName({ mention: "file_manager", kind: "builtin" })).toBe("File manager");
    expect(atAgentName({ mention: "tax-helper", kind: "dynamic" })).toBe("tax-helper");
    expect(atAgentName({ mention: "hermes", kind: "remote" })).toBe("hermes");
  });
});
