/**
 * v1.412.0 — Build › Code starts at the composer (components/buildCode/
 * StartComposer through the real BuildCodeView). The user: "to start a new
 * session it requires that i first select a folder and a harness choice. Id
 * prefer if it instead started automatically with a no folder chosen in the
 * chat bar with a simple choose selector for folder as well as a choose
 * selector for the harness … when the harness is chosen that contains
 * multiple logins … it should ask which account i would like to use … a popup
 * modal that is the generic windows file selector … Effort levels should also
 * be displayed if available."
 *
 * Only the network (lib/api), the stream reader and the folder tree are
 * stand-ins; the desktop's folder picker is `window.ironjarvis.dialog`.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
  postReply: {} as Record<string, unknown>,
  postFail: {} as Record<string, { status: number; message: string; body?: unknown }>,
  streams: [] as { path: string; body: unknown }[],
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    body?: unknown;
    constructor(message: string, status = 0, _c = false, body?: unknown) {
      super(message);
      this.status = status;
      this.name = "ApiError";
      if (body !== undefined) this.body = body;
    }
  }
  return {
    ApiError: FakeApiError,
    get: (path: string) => {
      const key = path.replace(/\?limit=1$/, "");
      const r = H.gets[key];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(r);
    },
    post: (path: string, body?: unknown) => {
      H.posts.push({ path, body });
      const fail = H.postFail[path];
      if (fail) return Promise.reject(new FakeApiError(fail.message, fail.status, false, fail.body));
      return Promise.resolve(H.postReply[path] ?? {});
    },
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* (path: string, body: unknown) {
    H.streams.push({ path, body });
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: ({ onSelect }: { onSelect: (p: string) => void }) => (
    <button type="button" data-testid="stub-pick-folder" onClick={() => onSelect("C:\\work\\shop")}>
      pick
    </button>
  ),
}));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { LAST_ACCOUNT_KEY, LAST_TOOL_KEY } from "@/components/buildCode/StartComposer";

const MODES = [
  { id: "ask", label: "Ask first", dangerous: false },
  { id: "full", label: "Full access", dangerous: true },
];

const TOOLS = {
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      modes: MODES,
      hidden_modes: [],
      models: [
        { id: "", label: "Claude Code's default" },
        { id: "claude-opus-5-5", label: "Opus 5.5" },
      ],
      efforts: ["low", "medium", "high", "xhigh"],
      accounts: [
        { id: "p_work", name: "Work", status: "ready" },
        { id: "p_home", name: "Home", status: "ready" },
        { id: "p_off", name: "Old", status: "needs_sign_in" },
      ],
    },
    {
      id: "codex",
      label: "Codex",
      installed: true,
      signed_in: null,
      modes: MODES,
      hidden_modes: [],
      models: [{ id: "", label: "Codex's default" }],
      efforts: [],
      accounts: [{ id: "p_one", name: "Only", status: "ready" }],
    },
    {
      id: "pi",
      label: "Pi",
      installed: true,
      signed_in: null,
      own_sign_in: true,
      modes: [{ id: "own", label: "Pi's own rules", dangerous: false }],
      hidden_modes: [],
      models: [{ id: "", label: "Pi's default" }],
      efforts: ["low", "high"],
      accounts: [],
    },
  ],
};

const made = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  harness: "claude-code",
  folder: "",
  no_folder: true,
  work_root: "C:\\Users\\VR\\Documents\\Iron Jarvis",
  title: "",
  account: "",
  model: "",
  effort: "",
  mode: "ask",
  harness_session_id: "",
  created_at: "2026-10-30T09:00:00Z",
  updated_at: "2026-10-30T09:00:00Z",
  archived_at: null,
  last_error: "",
  ...over,
});

function seed() {
  H.gets = {
    "/build/app/harnesses": TOOLS,
    "/build/app/sessions": { sessions: [], work_root: "C:\\Users\\VR\\Documents\\Iron Jarvis" },
    "/editor/options": { editors: [], preferred: "" },
  };
}

function willMake(id: string, over: Record<string, unknown> = {}) {
  H.postReply["/build/app/sessions"] = made(id, over);
  H.gets[`/build/app/sessions/${id}`] = { session: made(id, over), events: [], running: null, pending: [] };
}

const bridge = (answer: unknown) => {
  const pickFolder = vi.fn(async () => answer);
  (window as unknown as { ironjarvis?: unknown }).ironjarvis = { isDesktop: true, dialog: { pickFolder } };
  return pickFolder;
};

beforeEach(() => {
  H.posts = [];
  H.postReply = {};
  H.postFail = {};
  H.streams = [];
  seed();
  window.localStorage.clear();
  // v1.412.0 review (moved on purpose): a tool with several logins and NO
  // account remembered asks before anything starts (build-code-start-review
  // pins that); these pins start from a remembered one.
  window.localStorage.setItem(LAST_ACCOUNT_KEY + "claude-code", "p_work");
});

afterEach(() => {
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
  cleanup();
});

async function ready(tool = "Claude Code") {
  render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
  await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain(tool));
}

function pickTool(id: string) {
  fireEvent.click(screen.getByTestId("code-start-tool"));
  fireEvent.click(screen.getByTestId(`code-start-tool-${id}`));
}

function type(words: string) {
  fireEvent.change(screen.getByTestId("code-start-box"), { target: { value: words } });
}

const createBody = () => H.posts.find((p) => p.path === "/build/app/sessions")?.body as Record<string, unknown>;

describe("the composer is there at once", () => {
  it("no session open: the composer, ready to type, with its chips — no form, no folder", async () => {
    await ready();
    const box = screen.getByTestId("code-start-box");
    expect(box).toHaveFocus();
    expect(box.getAttribute("placeholder")).toBe("Describe a task or ask a question");
    expect(screen.queryByTestId("code-new-session")).toBeNull();
    // The heading names the tools truthfully (v1.413.0: OpenCode — moved on purpose).
    expect(screen.getByTestId("code-start").textContent).toContain("Claude Code, Codex, Pi or OpenCode");
    const chips = screen.getByTestId("code-start-chips");
    expect(within(chips).getByTestId("code-start-folder").textContent).toBe("No folder");
    expect(within(chips).getByTestId("code-start-folder-choose").textContent).toBe("Choose…");
    expect(within(chips).getByTestId("code-start-mode-button").textContent).toContain("Ask first");
    expect(within(chips).getByTestId("code-start-model")).toBeInTheDocument();
    // Nothing typed: Send waits.
    expect(screen.getByTestId("code-start-send")).toBeDisabled();
  });

  it("New session over an open session is the composer again", async () => {
    H.gets["/build/app/sessions"] = { sessions: [made("s1", { folder: "C:\\a", no_folder: false })] };
    H.gets["/build/app/sessions/s1"] = { session: made("s1", { folder: "C:\\a", no_folder: false }), events: [], running: null, pending: [] };
    render(<BuildCodeView initialSession="s1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session");
    fireEvent.click(screen.getByTestId("code-new"));
    expect(await screen.findByTestId("code-start-box")).toBeInTheDocument();
    expect(screen.queryByTestId("code-session")).toBeNull();
  });
});

describe("Send makes the session and sends the words, in one press", () => {
  it("with no folder: the session is made with none, then the words are its first message", async () => {
    willMake("n1");
    await ready();
    type("  fix the login page  ");
    fireEvent.click(screen.getByTestId("code-start-send"));
    await screen.findByTestId("code-session");
    expect(createBody()).toEqual({ harness: "claude-code", account: "p_work", mode: "ask" });
    expect("folder" in createBody()).toBe(false);
    await waitFor(() => expect(H.streams).toHaveLength(1));
    expect(H.streams[0].path).toBe("/build/app/sessions/n1/turn");
    expect((H.streams[0].body as { text: string }).text).toBe("fix the login page");
    // Once: the session never sends it twice.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(H.streams).toHaveLength(1);
  });

  it("Enter sends too; the session says it has no folder yet", async () => {
    willMake("n2");
    await ready();
    type("hello");
    fireEvent.keyDown(screen.getByTestId("code-start-box"), { key: "Enter" });
    await screen.findByTestId("code-session");
    expect(screen.getByTestId("code-session").textContent).toContain("no folder yet");
  });

  it("the chosen model and effort ride the make", async () => {
    willMake("n3");
    await ready();
    fireEvent.change(screen.getByTestId("code-start-model"), { target: { value: "claude-opus-5-5" } });
    fireEvent.change(screen.getByTestId("code-start-effort"), { target: { value: "high" } });
    type("go");
    fireEvent.click(screen.getByTestId("code-start-send"));
    await screen.findByTestId("code-session");
    expect(createBody()).toMatchObject({ model: "claude-opus-5-5", effort: "high" });
  });
});

describe("the tool and its account", () => {
  it("the tool is the last one used, else the first one installed", async () => {
    window.localStorage.setItem(LAST_TOOL_KEY, "codex");
    await ready("Codex");
    cleanup();
    window.localStorage.setItem(LAST_TOOL_KEY, "gone-tool");
    await ready("Claude Code");
  });

  it("picking a tool with several logins ASKS which account at once; one login never asks", async () => {
    window.localStorage.setItem(LAST_TOOL_KEY, "codex");
    await ready("Codex");
    // One login: no account chip, no question.
    expect(screen.queryByTestId("code-start-account")).toBeNull();
    pickTool("claude-code");
    const menu = await screen.findByTestId("code-start-account-menu");
    expect(menu.textContent).toContain("Which Claude Code account?");
    expect(within(menu).getByTestId("code-start-account-p_off")).toBeDisabled();
    fireEvent.click(within(menu).getByTestId("code-start-account-p_home"));
    expect(screen.queryByTestId("code-start-account-menu")).toBeNull();
    expect(screen.getByTestId("code-start-account").textContent).toContain("Home");
    // And back to the one-login tool: nothing asked.
    pickTool("codex");
    expect(screen.queryByTestId("code-start-account-menu")).toBeNull();
    expect(screen.queryByTestId("code-start-account")).toBeNull();
    // Pi signs in by itself: never asks either.
    pickTool("pi");
    expect(screen.queryByTestId("code-start-account-menu")).toBeNull();
    expect(screen.getByTestId("code-start-account-own").textContent).toBe("Pi's own sign-in");
  });

  it("the account defaults to the last one used for that tool, and is sent", async () => {
    window.localStorage.setItem(LAST_ACCOUNT_KEY + "claude-code", "p_home");
    willMake("n4", { account: "p_home" });
    await ready();
    expect(screen.getByTestId("code-start-account").textContent).toContain("Home");
    // Not asked at the start: one is remembered for it.
    expect(screen.queryByTestId("code-start-account-menu")).toBeNull();
    type("go");
    fireEvent.click(screen.getByTestId("code-start-send"));
    await screen.findByTestId("code-session");
    expect(createBody()).toMatchObject({ harness: "claude-code", account: "p_home" });
    expect(window.localStorage.getItem(LAST_TOOL_KEY)).toBe("claude-code");
    expect(window.localStorage.getItem(LAST_ACCOUNT_KEY + "claude-code")).toBe("p_home");
  });
});

describe("effort only where the tool lists levels", () => {
  it("Claude Code lists them; Codex lists none", async () => {
    await ready();
    const effort = screen.getByTestId("code-start-effort") as HTMLSelectElement;
    expect(Array.from(effort.options).map((o) => o.value)).toEqual(["", "low", "medium", "high", "xhigh"]);
    pickTool("codex");
    expect(screen.queryByTestId("code-start-effort")).toBeNull();
  });
});

describe("Choose… for the folder", () => {
  it("in the desktop app: Windows' own picker; the folder's name on the chip, its path the title, sent", async () => {
    const pick = bridge({ path: "D:\\projects\\shop" });
    willMake("n5", { folder: "D:\\projects\\shop", no_folder: false });
    await ready();
    fireEvent.click(screen.getByTestId("code-start-folder-choose"));
    await waitFor(() => expect(screen.getByTestId("code-start-folder").textContent).toBe("shop"));
    expect(pick).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("code-start-folder").getAttribute("title")).toBe("D:\\projects\\shop");
    // No in-app tree when the desktop picker answered.
    expect(screen.queryByTestId("stub-pick-folder")).toBeNull();
    type("go");
    fireEvent.click(screen.getByTestId("code-start-send"));
    await screen.findByTestId("code-session");
    expect(createBody()).toMatchObject({ folder: "D:\\projects\\shop" });
  });

  it("a cancelled picker changes nothing", async () => {
    bridge({ cancelled: true });
    await ready();
    fireEvent.click(screen.getByTestId("code-start-folder-choose"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 10));
    });
    expect(screen.getByTestId("code-start-folder").textContent).toBe("No folder");
  });

  it("in a browser (no desktop picker): the app's own folder tree", async () => {
    await ready();
    fireEvent.click(screen.getByTestId("code-start-folder-choose"));
    fireEvent.click(await screen.findByTestId("stub-pick-folder"));
    fireEvent.click(screen.getByTestId("code-start-folder-tree-use"));
    expect(screen.getByTestId("code-start-folder").textContent).toBe("shop");
    // × goes back to no folder.
    fireEvent.click(screen.getByTestId("code-start-folder-clear"));
    expect(screen.getByTestId("code-start-folder").textContent).toBe("No folder");
  });

  it("a folder the daemon refuses is said on the chip, and nothing is started", async () => {
    bridge({ path: "C:\\Windows" });
    // v1.412.0 review (moved on purpose): the daemon NAMES the chip.
    H.postFail["/build/app/sessions"] = {
      status: 400,
      message: "Iron Jarvis may not work in C:\\Windows.",
      body: { detail: "Iron Jarvis may not work in C:\\Windows.", field: "folder" },
    };
    await ready();
    fireEvent.click(screen.getByTestId("code-start-folder-choose"));
    await waitFor(() => expect(screen.getByTestId("code-start-folder").textContent).toBe("Windows"));
    type("go");
    fireEvent.click(screen.getByTestId("code-start-send"));
    const problem = await screen.findByTestId("code-start-folder-problem");
    expect(problem.textContent).toBe("Iron Jarvis may not work in C:\\Windows.");
    expect(screen.getByTestId("code-start-folder").dataset.problem).toBe("true");
    expect(screen.queryByTestId("code-session")).toBeNull();
    expect(H.streams).toHaveLength(0);
    // The words stay in the box.
    expect((screen.getByTestId("code-start-box") as HTMLTextAreaElement).value).toBe("go");
  });
});
