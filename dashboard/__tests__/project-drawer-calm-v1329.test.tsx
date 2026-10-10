/**
 * CALM CHAT W4 F6 (v1.329.0): the project drawer loses its boxes.
 *
 * Before: a bordered PROJECT card, a bordered Workspace card, the Directory
 * card with a big accent "Open terminal here" (a no-op from chat), and
 * Files / Knowledge / Tasks / Board / Media as bordered chips in half-pixel
 * type that repeated the top bar's own tabs. Now:
 *
 *  - plain SECTIONS (`project-panel-project`, `project-panel-folder`): a
 *    quiet label, a hairline under each, no rounded box or fill;
 *  - the project picker is a ghost select (no `field` box);
 *  - Files / Knowledge are a real tablist ("Project panel views"), whole-pixel
 *    text; arrow keys move focus, a press selects; Tasks / Board / Media are
 *    NOT in the drawer (the top bar holds them);
 *  - the shared folder picker sits unboxed and without its dead "Open
 *    terminal here"; a picked folder gets a ghost Terminal button that POSTs
 *    /terminals with that folder;
 *  - the notes are plain words without em-dash asides.
 *
 * Harness: chat-topbar-v1326's (ChatPage rendered for real; an unmocked GET
 * answers {}), with POSTs recorded.
 */

import { type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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

const PROJECT_KEY = "ij_chat_project";
const WORKSPACE_KEY = "ij_chat_workspace";
const PROJECT = { id: "p1", name: "Q3 Bookkeeping" };
const FOLDER = "C:\\Work\\Harbor";

function classes(el: Element | null | undefined): string[] {
  return ((el?.getAttribute("class") ?? "") as string).split(/\s+/).filter(Boolean);
}

beforeEach(() => {
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
    "/fs/list?path=C%3A%5C&dirs_only=true": { entries: [] },
    [`/fs/files?path=${encodeURIComponent(FOLDER)}&depth=4&limit=600`]: {
      root: FOLDER,
      files: [],
      count: 0,
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

describe("a project chat's drawer: plain sections, not cards", () => {
  it("the Project section is a hairline-separated section with a ghost picker and plain words", async () => {
    window.localStorage.setItem(PROJECT_KEY, PROJECT.id);
    render(<ChatPage />);
    const drawer = await openDrawer();
    const section = await within(drawer).findByTestId("project-panel-project");
    const c = classes(section);
    expect(c).toEqual(expect.arrayContaining(["border-b", "hairline"]));
    expect(c.some((x) => /^rounded/.test(x) || /^bg-/.test(x))).toBe(false);
    expect(within(section).getByRole("heading", { name: "Project" })).toBeTruthy();
    const picker = within(section).getByRole("combobox", { name: "Project" }) as HTMLSelectElement;
    expect(classes(picker)).not.toContain("field");
    expect(picker.options[0].textContent).toBe("No project (plain chat)");
    const page = within(section).getByRole("link", { name: /Project page/ });
    expect(page.getAttribute("href")).toBe("/projects/p1");
    await within(section).findByText(/Replies use this project's instructions and knowledge\./);
    expect(section.textContent ?? "").not.toMatch(/—/);
  });

  it("no rounded, filled box of the page's own in the drawer, and no half-pixel type", async () => {
    window.localStorage.setItem(PROJECT_KEY, PROJECT.id);
    render(<ChatPage />);
    const drawer = await openDrawer();
    const section = await within(drawer).findByTestId("project-panel-project");
    const tabs = within(drawer).getByRole("tablist", { name: "Project panel views" });
    // The page's own parts: whole-pixel type only. (The shared folder picker
    // below keeps Build's type scale; it is not this page's markup.)
    for (const own of [section, tabs]) {
      expect(own.outerHTML).not.toMatch(/text-\[(?:8|9|9\.5|10|10\.5|11\.5|12\.5|13\.5)px\]/);
    }
    // The old chips' half-pixel size and the old cards' fill are gone everywhere.
    expect(drawer.innerHTML).not.toContain("text-[10.5px]");
    expect(drawer.innerHTML).not.toContain("bg-ink-850/60");
  });

  it("Files / Knowledge are a real tablist in whole-pixel text; Tasks / Board / Media are not repeated", async () => {
    window.localStorage.setItem(PROJECT_KEY, PROJECT.id);
    render(<ChatPage />);
    const drawer = await openDrawer();
    const list = await within(drawer).findByRole("tablist", { name: "Project panel views" });
    const tabs = within(list).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Files", "Knowledge"]);
    for (const t of tabs) expect(classes(t)).toContain("text-[13px]");
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(within(drawer).queryByRole("button", { name: /^(tasks|board|media)$/i })).toBeNull();
    expect(within(drawer).queryByRole("tab", { name: /^(tasks|board|media)$/i })).toBeNull();
    // ANTI-VACUITY: the views are still one press away, in the top bar.
    const top = screen.getByTestId("chat-topbar");
    const views = within(top).getByRole("tablist", { name: "Project views" });
    expect(within(views).getAllByRole("tab").map((t) => (t.textContent ?? "").toLowerCase())).toEqual([
      "chat",
      "tasks",
      "board",
      "media",
    ]);
  });

  it("arrow keys move focus between the tabs; a press selects and the panel follows", async () => {
    window.localStorage.setItem(PROJECT_KEY, PROJECT.id);
    H.api.responses["/projects/p1/knowledge"] = { knowledge: [], count: 0 };
    render(<ChatPage />);
    const drawer = await openDrawer();
    const [files, knowledge] = within(await within(drawer).findByRole("tablist")).getAllByRole("tab");
    files.focus();
    fireEvent.keyDown(files, { key: "ArrowRight" });
    expect(document.activeElement).toBe(knowledge);
    expect(knowledge.getAttribute("aria-selected")).toBe("false"); // manual activation
    fireEvent.click(knowledge);
    await waitFor(() => expect(knowledge.getAttribute("aria-selected")).toBe("true"));
    const panel = within(drawer).getByRole("tabpanel");
    expect(panel.getAttribute("aria-labelledby")).toBe(knowledge.id);
    expect(knowledge.getAttribute("aria-controls")).toBe(panel.id);
    // The knowledge base draws bare under its tab (no second titled card).
    const kb = await within(drawer).findByTestId("knowledge-panel");
    expect(kb.getAttribute("data-bare")).toBe("true");
  });

  it("a missing project folder is said in plain words, in the warning tone", async () => {
    H.api.responses["/projects"] = {
      projects: [{ ...PROJECT, root: "D:\\gone", root_exists: false }],
    };
    window.localStorage.setItem(PROJECT_KEY, PROJECT.id);
    render(<ChatPage />);
    const drawer = await openDrawer();
    const note = await within(drawer).findByText(
      /The project folder is missing, so file tools stay off until it is back\. Fix it on the project page\./,
    );
    expect(classes(note)).toContain("text-tone-warn");
  });

  it("the folder picker sits unboxed and without its dead 'Open terminal here'", async () => {
    window.localStorage.setItem(PROJECT_KEY, PROJECT.id);
    render(<ChatPage />);
    const drawer = await openDrawer();
    const tree = await within(drawer).findByRole("combobox", { name: "Root directory" });
    expect(within(drawer).queryByRole("button", { name: /Open terminal here/ })).toBeNull();
    // The picker's own card is its root div; the drawer's wrapper unboxes it.
    let root: HTMLElement | null = tree;
    while (root && !classes(root).includes("rounded-2xl")) root = root.parentElement;
    expect(root).not.toBeNull();
    const wrap = classes(root!.parentElement);
    expect(wrap).toEqual(
      expect.arrayContaining(["-mx-4", "[&>div]:border-0", "[&>div]:bg-transparent", "[&>div]:shadow-none"]),
    );
  });
});

describe("a chat with a working folder: the Folder section and its ghost Terminal", () => {
  it("shows Folder with ghost Make project / Change / Terminal; the old Collapse button is gone", async () => {
    window.localStorage.setItem(WORKSPACE_KEY, FOLDER);
    render(<ChatPage />);
    const drawer = await openDrawer();
    const folder = await within(drawer).findByTestId("project-panel-folder");
    expect(within(folder).getByRole("heading", { name: "Folder" })).toBeTruthy();
    for (const name of ["Make this folder a project", "Change workspace folder", "Open a terminal in this folder"]) {
      const b = within(folder).getByRole("button", { name });
      expect(classes(b)).toContain("hover:bg-white/[0.06]");
      expect(classes(b).some((x) => /^btn-accent$|^border$/.test(x))).toBe(false);
    }
    expect(within(drawer).queryByRole("button", { name: "Collapse workspace" })).toBeNull();
    expect(folder.textContent).toContain("Files the chat's tools create land here.");
  });

  it("Terminal opens a NEW terminal in that folder (a POST, the folder as cwd)", async () => {
    window.localStorage.setItem(WORKSPACE_KEY, FOLDER);
    // Refused here so the test stays on the page: the press must still have
    // asked for exactly this folder, and the refusal is said, not swallowed.
    H.api.postResponses["/terminals"] = new H.FakeApiError("Too many terminals are open (5).", 429);
    render(<ChatPage />);
    const drawer = await openDrawer();
    fireEvent.click(await within(drawer).findByRole("button", { name: "Open a terminal in this folder" }));
    const alert = await within(drawer).findByRole("alert");
    expect(alert.textContent).toBe("No terminal opened. Too many terminals are open (5).");
    expect(H.api.posts.filter((p) => p.path === "/terminals")).toEqual([
      { path: "/terminals", body: { cwd: FOLDER } },
    ]);
  });
});
