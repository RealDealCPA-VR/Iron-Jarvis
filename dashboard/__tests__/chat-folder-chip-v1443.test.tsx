/**
 * v1.443.0 — a chat can work in a FOLDER without being a project, picked
 * exactly the way Build › Code picks one.
 *
 * The user: "i have a preference in chat module that isnt there, there is an
 * option to select a project, but what if i dont want it to be a specific
 * project - rather just work in a specific folder and that selection process
 * should be exactly as the folder is selected in build > code."
 *
 * Pinned here, on the real chat page with only the transport mocked:
 *  - a new chat shows Build › Code's folder chip beside "No project"; in the
 *    desktop app Choose… opens WINDOWS' OWN picker (the shell bridge), in a
 *    browser the app's folder tree in a dialog — one picker, never a second;
 *  - a pick becomes the chat's working folder: `workspace_dir` on the turn,
 *    the file essentials armed over an empty tool set, the sticky default;
 *  - right after a pick the daemon is asked (`POST /documents/folder-check`)
 *    and a refusal is SAID under the chip; an answer about an older pick is
 *    never shown;
 *  - a pick outside the active project takes the chat out of it (one inside
 *    keeps it), and the chip's title says so first;
 *  - in a conversation the toolbar carries a compact chip; "+ Choose a working
 *    folder" opens the same picker, and "+" also clears the folder;
 *  - × goes back to "No folder": the tools it armed go and the sticky default
 *    is forgotten;
 *  - a conversation's own folder (made for an attachment) is let go, so the
 *    next attachment goes to the picked folder;
 *  - a reopened chat shows its saved folder.
 */

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
      posts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: { bodies: [] as Record<string, unknown>[] },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    if (path.startsWith("/fs/list")) {
      const p = decodeURIComponent(new URL(path, "http://x").searchParams.get("path") ?? "");
      return { path: p, entries: p === "C:\\" ? [{ name: "Work", path: "C:\\Work", is_project: null }] : [] };
    }
    if (path === "/fs/drives") return { drives: [{ path: "C:\\", label: "C:" }] };
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    const r = H.api.postResponses[path];
    if (r instanceof Error) throw r;
    if (typeof r === "function") return (r as (b: Record<string, unknown>) => unknown)(body);
    return r ?? {};
  },
  put: async (path: string) => {
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
      onDelta("done", "done");
      return { reply: "done" };
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
import { insideFolder, pickLeavesProject, chatFolderTitle } from "@/lib/chatFolder";

const BOOKS = "C:\\Work\\Books";
const LEDGERS = "C:\\Work\\Ledgers";
const ROOT = "C:\\Work\\Harbor";
const PROJECT = { id: "p1", name: "Harbor Street Cafe", root: ROOT, root_exists: true };
const UPLOADS = "C:\\home\\uploads";
const OWN = "C:\\Users\\me\\Documents\\Iron Jarvis\\2026-11-07 notes";
const PROJECT_FILE_TOOLS = ["file_search", "read_document", "write_document", "write_file"];

type Bridge = { pickFolder: ReturnType<typeof vi.fn> };
function desktop(...answers: Array<{ path?: string; cancelled?: boolean }>): Bridge {
  const queue = [...answers];
  const pickFolder = vi.fn(async () => queue.shift() ?? { cancelled: true });
  (window as unknown as { ironjarvis?: unknown }).ironjarvis = { dialog: { pickFolder } };
  return { pickFolder };
}

beforeEach(() => {
  H.api.posts.length = 0;
  H.stream.bodies.length = 0;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [PROJECT] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  H.api.postResponses["/documents/folder-check"] = { problem: null, checked: true };
  H.api.postResponses["/documents/upload"] = (b: Record<string, unknown>) => ({
    path: `${UPLOADS}\\${String(b.filename)}`,
    name: String(b.filename),
    bytes: 3,
  });
  H.api.postResponses["/documents/workfolder"] = (b: Record<string, unknown>) => {
    const prefer = String(b.prefer || "");
    const into = String(b.into || "");
    const dest = into || prefer || OWN;
    const files = (b.files as string[]).map((src) => {
      const name = src.split("\\").pop() as string;
      return { name, path: `${dest}\\${name}`, bytes: 3, source: src };
    });
    return { path: dest, created: !into && !prefer, files, skipped: [], note: "" };
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
  vi.clearAllMocks();
});

async function box(): Promise<HTMLTextAreaElement> {
  return (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
}

async function send(text: string) {
  const el = await box();
  const n = H.stream.bodies.length;
  fireEvent.change(el, { target: { value: text } });
  fireEvent.keyDown(el, { key: "Enter" });
  await waitFor(() => expect(H.stream.bodies.length).toBe(n + 1));
  return H.stream.bodies[n] as Record<string, unknown>;
}

const checks = () => H.api.posts.filter((p) => p.path === "/documents/folder-check");

async function heroChoose() {
  const choose = await screen.findByTestId("chat-hero-folder-choose");
  fireEvent.click(choose);
}

async function inConversation() {
  await send("hello");
  return screen.findByTestId("chat-folder-chip");
}

/* ------------------------------------------------------------ the rules */

describe("the rules around a pick", () => {
  it("a folder is inside a project when it is its root or under it, case and slashes aside", () => {
    expect(insideFolder("c:/work/harbor/", ROOT)).toBe(true);
    expect(insideFolder(`${ROOT}\\data`, ROOT)).toBe(true);
    expect(insideFolder("C:\\Work\\HarborPier", ROOT)).toBe(false); // a prefix is not a parent
    expect(insideFolder("", ROOT)).toBe(false);
    expect(pickLeavesProject(BOOKS, ROOT)).toBe(true);
    expect(pickLeavesProject(`${ROOT}\\data`, ROOT)).toBe(false);
    expect(pickLeavesProject(BOOKS, null)).toBe(false);
  });

  it("the chip's title says what a pick outside the project does", () => {
    expect(chatFolderTitle(null)).toBe("Choose a folder for this chat.");
    expect(chatFolderTitle(BOOKS)).toBe(`Working in ${BOOKS}. Press to choose another folder.`);
    expect(chatFolderTitle(ROOT, PROJECT.name)).toContain(
      `Choosing a folder outside ${PROJECT.name} takes this chat out of it.`,
    );
  });
});

/* ------------------------------------------------------------ a new chat */

describe("a new chat: Build › Code's folder chip beside the project", () => {
  it("says No folder, sits beside No project, and Choose… opens WINDOWS' OWN picker in the desktop app", async () => {
    const shell = desktop({ path: BOOKS });
    render(<ChatPage />);
    const hero = await screen.findByTestId("chat-hero-project");
    const chip = await within(hero).findByTestId("chat-hero-folder");
    expect(chip.textContent).toBe("No folder");
    expect(within(hero).getByRole("button", { name: "Switch project" })).toBeTruthy();
    await heroChoose();
    await waitFor(() => expect(shell.pickFolder).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId("chat-hero-folder").getAttribute("data-folder")).toBe(BOOKS));
    expect(screen.getByTestId("chat-hero-folder").textContent).toBe("Books");
    expect(screen.getByTestId("chat-hero-folder").getAttribute("title")).toBe(BOOKS);
    // Never the app's own tree while Windows' picker is there.
    expect(screen.queryByTestId("chat-folder-tree")).toBeNull();
  });

  it("in a browser (no Windows picker) Choose… opens the app's folder tree, and Use this folder picks", async () => {
    render(<ChatPage />);
    await heroChoose();
    const dialog = await screen.findByTestId("chat-folder-tree");
    const row = await within(dialog).findByTitle("C:\\Work");
    fireEvent.click(row);
    fireEvent.click(within(dialog).getByTestId("chat-folder-tree-use"));
    await waitFor(() => expect(screen.getByTestId("chat-hero-folder").getAttribute("data-folder")).toBe("C:\\Work"));
    expect(screen.queryByTestId("chat-folder-tree")).toBeNull();
  });

  it("a pick is the chat's working folder: workspace_dir, the file essentials, the sticky default, one check", async () => {
    desktop({ path: BOOKS });
    render(<ChatPage />);
    await heroChoose();
    await waitFor(() => expect(checks()).toHaveLength(1));
    expect(checks()[0].body).toEqual({ path: BOOKS });
    expect(window.localStorage.getItem("ij_chat_workspace")).toBe(BOOKS);
    const body = await send("what is in here?");
    expect(body.workspace_dir).toBe(BOOKS);
    expect(body.tools).toEqual(PROJECT_FILE_TOOLS);
  });

  it("a cancelled picker changes nothing and asks nothing", async () => {
    desktop({ cancelled: true });
    render(<ChatPage />);
    await heroChoose();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByTestId("chat-hero-folder").textContent).toBe("No folder");
    expect(checks()).toHaveLength(0);
  });

  it("a folder the chat cannot work in is SAID under the chip; the pick stands", async () => {
    desktop({ path: "C:\\Users" });
    H.api.postResponses["/documents/folder-check"] = {
      problem: "Folder is not writable by this app: C:\\Users — pick a folder you can save files in",
      checked: true,
    };
    render(<ChatPage />);
    await heroChoose();
    const problem = await screen.findByTestId("chat-hero-folder-problem");
    expect(problem.getAttribute("role")).toBe("alert");
    expect(problem.textContent).toContain("Folder is not writable by this app");
    const body = await send("hi");
    expect(body.workspace_dir).toBe("C:\\Users");
    // In the conversation the card says it, beside the next message.
    expect((await screen.findByTestId("chat-folder-problem")).textContent).toContain("not writable");
    expect(screen.getByTestId("chat-folder-chip").getAttribute("data-problem")).toBe("true");
  });

  it("an answer about an OLDER pick is never shown for the newer one", async () => {
    desktop({ path: "C:\\Users" }, { path: BOOKS });
    let releaseFirst: (v: unknown) => void = () => {};
    H.api.postResponses["/documents/folder-check"] = (b: Record<string, unknown>) =>
      b.path === "C:\\Users"
        ? new Promise((r) => {
            releaseFirst = r;
          })
        : { problem: null, checked: true };
    render(<ChatPage />);
    await heroChoose();
    await waitFor(() => expect(checks()).toHaveLength(1));
    await heroChoose();
    await waitFor(() => expect(checks()).toHaveLength(2));
    await waitFor(() => expect(screen.getByTestId("chat-hero-folder").getAttribute("data-folder")).toBe(BOOKS));
    releaseFirst({ problem: "Folder is not writable by this app: C:\\Users", checked: true });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("chat-hero-folder-problem")).toBeNull();
  });

  it("the same folder picked again: only the NEWER answer counts (the first, late, said no)", async () => {
    desktop({ path: LEDGERS }, { path: LEDGERS });
    const late: Array<(v: unknown) => void> = [];
    let n = 0;
    H.api.postResponses["/documents/folder-check"] = () => {
      n += 1;
      if (n === 1) return new Promise((r) => late.push(r));
      return { problem: null, checked: true };
    };
    render(<ChatPage />);
    await heroChoose();
    await waitFor(() => expect(checks()).toHaveLength(1));
    await heroChoose();
    await waitFor(() => expect(checks()).toHaveLength(2));
    late[0]({ problem: "Folder is not writable by this app: C:\\Work\\Ledgers", checked: true });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("chat-hero-folder-problem")).toBeNull();
  });

  it("the daemon's words go when the folder changes some other way (choosing a project)", async () => {
    desktop({ path: "C:\\Users" });
    H.api.postResponses["/documents/folder-check"] = {
      problem: "Folder is not writable by this app: C:\\Users",
      checked: true,
    };
    render(<ChatPage />);
    await heroChoose();
    await screen.findByTestId("chat-hero-folder-problem");
    const hero = screen.getByTestId("chat-hero-project");
    fireEvent.click(within(hero).getByRole("button", { name: "Switch project" }));
    fireEvent.click(await screen.findByRole("button", { name: PROJECT.name }));
    await waitFor(() => expect(screen.getByTestId("chat-hero-folder").getAttribute("data-folder")).toBe(ROOT));
    expect(screen.queryByTestId("chat-hero-folder-problem")).toBeNull();
  });

  it("× goes back to No folder: the tools it armed go and the sticky default is forgotten", async () => {
    desktop({ path: BOOKS });
    render(<ChatPage />);
    await heroChoose();
    await waitFor(() => expect(screen.getByTestId("chat-hero-folder").textContent).toBe("Books"));
    fireEvent.click(screen.getByTestId("chat-hero-folder-clear"));
    await waitFor(() => expect(screen.getByTestId("chat-hero-folder").textContent).toBe("No folder"));
    expect(window.localStorage.getItem("ij_chat_workspace")).toBeNull();
    const body = await send("hello");
    expect(body.workspace_dir).toBeUndefined();
    expect(body.tools ?? []).toEqual([]);
  });
});

/* ------------------------------------------------------------ the project */

describe("a folder and a project", () => {
  async function inProjectReady() {
    window.localStorage.setItem("ij_chat_project", PROJECT.id);
    render(<ChatPage />);
    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: "Switch project" }).some((b) => b.textContent?.includes(PROJECT.name)),
      ).toBe(true),
    );
    await waitFor(() => expect(screen.getByTestId("chat-hero-folder").getAttribute("data-folder")).toBe(ROOT));
  }

  it("the chip says first that a pick outside the project takes the chat out of it", async () => {
    await inProjectReady();
    expect(screen.getByTestId("chat-hero-folder-choose").getAttribute("title")).toContain(
      `Choosing a folder outside ${PROJECT.name} takes this chat out of it.`,
    );
    // Inside a project there is no × (the project's folder is the folder).
    expect(screen.queryByTestId("chat-hero-folder-clear")).toBeNull();
  });

  it("a folder OUTSIDE the project takes the chat out of it", async () => {
    desktop({ path: BOOKS });
    await inProjectReady();
    await heroChoose();
    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: "Switch project" }).every((b) => !b.textContent?.includes(PROJECT.name)),
      ).toBe(true),
    );
    expect(window.localStorage.getItem("ij_chat_project")).toBeNull();
    const body = await send("hello");
    expect(body.workspace_dir).toBe(BOOKS);
    expect(body.project_id ?? null).toBeNull();
    expect(body.tools).toEqual(PROJECT_FILE_TOOLS);
  });

  it("a folder INSIDE the project keeps the chat in it (the working-folder chip says so)", async () => {
    desktop({ path: `${ROOT}\\data` });
    await inProjectReady();
    await heroChoose();
    await waitFor(() => expect(screen.getByTestId("chat-hero-folder").getAttribute("data-folder")).toBe(`${ROOT}\\data`));
    const body = await send("hello");
    expect(body.project_id).toBe(PROJECT.id);
    expect(body.workspace_dir).toBe(`${ROOT}\\data`);
    expect((await screen.findByTestId("working-folder-chip")).textContent).toContain("Working in data");
  });
});

/* ------------------------------------------------------------ in a conversation */

describe("in a conversation: the toolbar chip, the + menu, the drawer", () => {
  it("the toolbar chip opens the same picker; its name gives way with the project's fit step", async () => {
    const shell = desktop({ path: BOOKS });
    render(<ChatPage />);
    const chip = await inConversation();
    expect(screen.queryByTestId("chat-hero-folder")).toBeNull();
    expect(chip.getAttribute("title")).toBe("Choose a folder for this chat.");
    expect(chip.getAttribute("aria-label")).toBe("Choose a folder for this chat");
    fireEvent.click(chip);
    await waitFor(() => expect(shell.pickFolder).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId("chat-folder-chip").getAttribute("data-folder")).toBe(BOOKS));
    const name = within(screen.getByTestId("chat-folder-chip")).getByText("Books");
    expect(name.className).toContain("group-data-[fit-project]/fit:hidden");
    expect(screen.getByTestId("chat-folder-chip").className).toContain("group-data-[fit-project]/fit:w-[30px]");
    expect(screen.getByTestId("chat-folder-chip").getAttribute("title")).toBe(
      `Working in ${BOOKS}. Press to choose another folder.`,
    );
  });

  it("+ Choose a working folder opens Windows' picker, never the drawer's tree; + also clears", async () => {
    const shell = desktop({ path: LEDGERS });
    render(<ChatPage />);
    await inConversation();
    fireEvent.click(screen.getByRole("button", { name: "Open the chat menu" }));
    fireEvent.click(await screen.findByRole("button", { name: "Choose a working folder" }));
    await waitFor(() => expect(shell.pickFolder).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId("chat-folder-chip").getAttribute("data-folder")).toBe(LEDGERS));
    expect(document.querySelector('[data-testid="directory-tree"]')).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open the chat menu" }));
    fireEvent.click(await screen.findByTestId("chat-folder-clear-menu"));
    await waitFor(() => expect(screen.getByTestId("chat-folder-chip").getAttribute("data-folder")).toBe(""));
    expect(window.localStorage.getItem("ij_chat_workspace")).toBeNull();
  });

  it("a conversation's own folder (made for an attachment) is let go: the next file goes to the picked folder", async () => {
    desktop({ path: BOOKS });
    render(<ChatPage />);
    await box();
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["a"], "a.pdf", { type: "application/pdf" })] } });
    await screen.findByTestId("workfolder-chip");
    await heroChoose();
    await waitFor(() => expect(screen.queryByTestId("workfolder-chip")).toBeNull());
    fireEvent.change(input, { target: { files: [new File(["b"], "b.pdf", { type: "application/pdf" })] } });
    const wf = () => H.api.posts.filter((p) => p.path === "/documents/workfolder");
    await waitFor(() => expect(wf()).toHaveLength(2));
    expect(wf()[1].body.into).toBeUndefined();
    expect(wf()[1].body.prefer).toBe(BOOKS);
  });
});

/* ------------------------------------------------------------ a reopened chat */

describe("a reopened chat", () => {
  it("shows the folder it was saved with", async () => {
    H.api.getResponses["/chat/threads/t9"] = {
      id: "t9",
      title: "books",
      updated_at: "S1",
      messages: [
        { role: "user", content: "hi" },
        { role: "assistant", content: "hello" },
      ],
      setup: { workspace_dir: LEDGERS },
    };
    window.history.replaceState({}, "", "/chat?thread=t9");
    render(<ChatPage />);
    await waitFor(() => expect(screen.getByTestId("chat-folder-chip").getAttribute("data-folder")).toBe(LEDGERS));
    expect(within(screen.getByTestId("chat-folder-chip")).getByText("Ledgers")).toBeTruthy();
  });
});
