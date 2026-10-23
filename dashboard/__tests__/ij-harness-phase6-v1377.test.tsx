/**
 * v1.377.0 (IJ harness part 2, Phase 6) — the smaller touches on the screen:
 *
 *  - NEXT STEP: after a reply, up to two suggested follow-ups as buttons; a
 *    press puts the words in the box (after what is typed) and NEVER sends;
 *    a new turn retires them; nothing shows when the switch is off;
 *  - BRANCH FROM A MESSAGE: one press on a reply or a question starts a new
 *    session (the daemon copies the conversation; nothing of this one is
 *    sent or changed); a question branch hands its words and files to the
 *    new session's box; the new session says where it came from;
 *  - SEARCH in the left pane: titles and folder names, Escape clears;
 *  - KEYBOARD: Ctrl+N / Esc / Ctrl+L / Ctrl+K inside the IJ harness only —
 *    never in a dialog's Escape, a field's Escape, an open menu's Escape, and
 *    never taking the app's Ctrl+K palette outside the harness;
 *  - REWIND puts a message's PICTURES and "@" files back with its words, and
 *    the box SAYS what did not fit (it used to cut at 20 without a word);
 *  - a session opened while Compact runs says "Summarizing earlier turns…";
 *  - the SIGN-IN RULE is the same in the model menu and the header chip;
 *  - "Ask first" names the browser; the chat page's gauge never calls the
 *    app's safe size the model's limit.
 *
 * Driven through the REAL chat stream against a fetch double and the real
 * components; only the api, the daemon hook and the model list are doubles.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    body?: unknown;
    constructor(message: string, status = 0, body?: unknown) {
      super(message);
      this.status = status;
      if (body !== undefined) this.body = body;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown | ((path: string) => unknown)>,
    calls: [] as { method: string; path: string; body?: unknown }[],
    postAnswers: {} as Record<string, unknown | ((body: unknown) => unknown)>,
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : api.postAnswers[`${method} ${path}`];
    const value = typeof hit === "function" ? (hit as (b: unknown) => unknown)(method === "GET" ? path : body) : hit;
    if (value instanceof Error) return Promise.reject(value);
    if (value === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(value ?? {});
  };
  return {
    ApiError: api.ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    flattenDetail: (d: unknown) => String(d),
    get: (p: string) => answer("GET", p),
    post: (p: string, b?: unknown) => answer("POST", p, b),
    put: (p: string, b?: unknown) => answer("PUT", p, b),
    patch: (p: string, b?: unknown) => answer("PATCH", p, b),
    del: (p: string) => answer("DELETE", p),
  };
});

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    checking: false,
    health: { default_provider: "mock", default_model: "mock-1", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div data-testid="dir-tree" /> }));

import { HarnessSession } from "@/components/harness/HarnessSession";
import { HarnessSidebar } from "@/components/harness/HarnessSidebar";
import { IjHarnessView } from "@/components/harness/IjHarnessView";
import { AccountList } from "@/components/harness/HarnessAccounts";
import { RewindControl } from "@/components/harness/RewindControl";
import { leftOutWords, useHarnessAttachments, MAX_FILES } from "@/components/harness/HarnessAttachments";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { accountBlockedReason, decodeAccounts } from "@/lib/buildAccounts";
import { decodeFolders } from "@/lib/buildSessions";
import { filterFolders, withNextStep, decodeNextSteps, MAX_NEXT_STEPS } from "@/lib/buildNext";
import { harnessKeyAction } from "@/lib/harnessKeys";
import { PERMISSION_LEVELS } from "@/lib/permissionLevels";
import { fillWords, trimmedWords } from "@/lib/contextWords";
import { meterTitle } from "@/lib/buildContext";

/* ------------------------------------------------------------- fixtures */

const FOLDER = "C:\\work\\shop";
const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Add a cart",
    updated_at: "2026-10-22T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: FOLDER,
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: {},
    messages: [],
    session_id: "build:s1",
    queued: [],
    turn_id: "",
    ...over,
  };
}

const userLine = (content: string, turnId = "t0", extra: Record<string, unknown> = {}) => ({
  role: "user",
  content,
  turnId,
  at: "2026-10-22T10:00:00",
  ...extra,
});
const replyLine = (content: string, turnId = "t0") => ({
  role: "assistant",
  content,
  turnId,
  route: { provider: "mock", model: "mock-1", reason: "mock" },
});

function sse(frames: [string, unknown][]): Response {
  const text = frames.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("");
  return new Response(
    new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode(text));
        c.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

function controlledStream() {
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctl = c;
    },
  });
  const enc = new TextEncoder();
  return {
    response: new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    push: (event: string, data: unknown) => ctl.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    end: () => ctl.close(),
  };
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.gets = { "/build/sessions/s1": view(), "/build/accounts": new api.ApiError("no such route", 404) };
  api.calls = [];
  api.postAnswers = {};
  localStorage.clear();
  fetchMock = vi.fn(async () => sse([["done", { reply: "ok" }]]));
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** The session inside the IJ harness view's root (where its keys work). */
function renderSession(extra: Partial<React.ComponentProps<typeof HarnessSession>> = {}) {
  return render(
    <div>
      <button data-testid="outside-button">outside</button>
      <div data-testid="ij-harness-view">
        <HarnessSession
          threadId="s1"
          terminal={terminal}
          listRunning={false}
          onChanged={() => {}}
          onGone={() => {}}
          {...extra}
        />
      </div>
    </div>,
  );
}

const box = () => screen.getByTestId("harness-input") as HTMLTextAreaElement;
const turnPosts = () => fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/turn"));
const posts = (path: string) => api.calls.filter((c) => c.method === "POST" && c.path === path);

/** One finished turn: the daemon's transcript holds its question and reply. */
function answerTurns(reply = "Added the cart.") {
  fetchMock.mockImplementation(async (url: string, init: RequestInit) => {
    if (!String(url).endsWith("/turn")) return sse([["done", { reply }]]);
    const id = JSON.parse(String(init.body)).turn_id;
    api.gets["/build/sessions/s1"] = view({ messages: [userLine("Add a cart", id), replyLine(reply, id)] });
    return sse([["done", { reply }]]);
  });
}

/* ----------------------------------------------------------- Next step */

describe("Next step: suggested follow-ups after a reply", () => {
  it("asks once the reply ended, shows at most two, and a press fills the box without sending", async () => {
    api.postAnswers["POST /build/sessions/s1/followups"] = {
      suggestions: ["Run the tests?", "Add a remove button?", "Third one?"],
      reason: "",
    };
    answerTurns();
    renderSession();
    await screen.findByTestId("harness-empty");
    fireEvent.change(box(), { target: { value: "Add a cart" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    const strip = await screen.findByTestId("harness-next-steps");
    const buttons = within(strip).getAllByTestId("harness-next-step");
    expect(buttons.map((b) => b.textContent)).toEqual(["Run the tests?", "Add a remove button?"]);
    expect(posts("/build/sessions/s1/followups")).toHaveLength(1);
    // Typed words are kept; the step goes after them; nothing is sent.
    fireEvent.change(box(), { target: { value: "Also:" } });
    const sent = turnPosts().length;
    fireEvent.click(buttons[0]);
    expect(box().value).toBe("Also:\nRun the tests?");
    expect(turnPosts()).toHaveLength(sent);
    expect(screen.queryByTestId("harness-next-steps")).toBeNull();
  });

  it("shows nothing when the switch is off (the daemon answers 'off'), and a new turn retires old steps", async () => {
    api.postAnswers["POST /build/sessions/s1/followups"] = { suggestions: [], reason: "off" };
    answerTurns();
    renderSession();
    await screen.findByTestId("harness-empty");
    fireEvent.change(box(), { target: { value: "Add a cart" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(posts("/build/sessions/s1/followups")).toHaveLength(1));
    expect(screen.queryByTestId("harness-next-steps")).toBeNull();
  });

  it("the decoder keeps strings only, at most two, and never a duplicate", () => {
    expect(MAX_NEXT_STEPS).toBe(2);
    expect(decodeNextSteps({ suggestions: ["a", "a", 3, "b", "c"] })).toEqual(["a", "b"]);
    expect(decodeNextSteps({ nope: true })).toEqual([]);
    expect(withNextStep("", "Run it")).toBe("Run it");
    expect(withNextStep("Typed  ", "Run it")).toBe("Typed\nRun it");
  });
});

/* -------------------------------------------------------- Branch from here */

describe("Branch from a message", () => {
  it("a reply's Branch starts a new session from it and changes nothing here", async () => {
    api.gets["/build/sessions/s1"] = view({ messages: [userLine("Add a cart", "t1"), replyLine("Added.", "t1")] });
    api.postAnswers["POST /build/sessions/s1/branch"] = { ...view({ id: "s2", title: "Add a cart (branch)" }) };
    const onBranched = vi.fn();
    renderSession({ onBranched });
    const reply = await screen.findByTestId("harness-reply");
    fireEvent.click(within(reply).getByTestId("harness-branch-reply"));
    await waitFor(() => expect(onBranched).toHaveBeenCalledWith("s2", null));
    expect(posts("/build/sessions/s1/branch")).toEqual([
      { method: "POST", path: "/build/sessions/s1/branch", body: { turn_id: "t1", at: "reply" } },
    ]);
    // Nothing of THIS session was changed or sent.
    expect(api.calls.filter((c) => c.method === "PATCH" || c.method === "PUT" || c.method === "DELETE")).toEqual([]);
    expect(turnPosts()).toHaveLength(0);
  });

  it("a question's Branch hands its words and files to the new session's box", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [userLine("Look at this", "t1", { attachmentPaths: ["C:\\up\\shot.png"] }), replyLine("Ok.", "t1")],
    });
    api.postAnswers["POST /build/sessions/s1/branch"] = {
      ...view({ id: "s2" }),
      draft: { text: "Look at this", attachments: ["C:\\up\\shot.png"], picked_files: ["C:\\work\\shop\\src\\a.py"] },
    };
    const onBranched = vi.fn();
    renderSession({ onBranched });
    const bubble = await screen.findByTestId("harness-user-bubble");
    fireEvent.click(within(bubble.parentElement as HTMLElement).getByTestId("harness-branch-question"));
    await waitFor(() =>
      expect(onBranched).toHaveBeenCalledWith("s2", {
        text: "Look at this",
        attachments: ["C:\\up\\shot.png"],
        pickedFiles: ["C:\\work\\shop\\src\\a.py"],
      }),
    );
  });

  it("a session opened with a draft holds its words, pictures and files in the box, and says where it came from", async () => {
    api.gets["/build/sessions/s1"] = view({
      branched_from: { thread_id: "s0", title: "First try", turn_id: "t1", at: "question" },
    });
    const onOpenSession = vi.fn();
    renderSession({
      initialDraft: { text: "Look at this", attachments: ["C:\\up\\shot.png"], pickedFiles: ["C:\\work\\shop\\src\\a.py"] },
      onOpenSession,
    });
    await waitFor(() => expect(box().value).toBe("Look at this"));
    expect(screen.getAllByTestId("harness-attachment")).toHaveLength(1);
    expect(screen.getByTestId("harness-at-chip")).toHaveAttribute("title", "src/a.py");
    const from = screen.getByTestId("harness-branched-from");
    expect(from).toHaveTextContent("Branched from “First try”");
    fireEvent.click(from);
    expect(onOpenSession).toHaveBeenCalledWith("s0");
    expect(turnPosts()).toHaveLength(0);
  });

  it("the daemon's refusal is said in its words", async () => {
    api.gets["/build/sessions/s1"] = view({ messages: [userLine("q", "t1"), replyLine("a", "t1")] });
    api.postAnswers["POST /build/sessions/s1/branch"] = new api.ApiError(
      "This session's folder cannot be used now: it is gone",
      409,
    );
    renderSession({ onBranched: vi.fn() });
    const reply = await screen.findByTestId("harness-reply");
    fireEvent.click(within(reply).getByTestId("harness-branch-reply"));
    expect(await screen.findByText("This session's folder cannot be used now: it is gone")).toBeInTheDocument();
  });
});

/* ----------------------------------------------------------------- Search */

const FOLDERS = decodeFolders({
  folders: [
    {
      path: "C:\\work\\shop",
      name: "shop",
      sessions: [
        { id: "a", title: "Add a cart", updated_at: null },
        { id: "b", title: "Fix the header", updated_at: null },
      ],
    },
    { path: "C:\\work\\blog", name: "blog", sessions: [{ id: "c", title: "Write the about page", updated_at: null }] },
  ],
});

describe("Search sessions in the left pane", () => {
  function renderSidebar() {
    return render(
      <HarnessSidebar
        folders={FOLDERS}
        listError={null}
        selectedId={null}
        onSelect={() => {}}
        onStart={async () => null}
        onRename={async () => null}
        defaultFolder={null}
        terminals={[]}
        onOpenTerminal={() => {}}
      />,
    );
  }

  it("filters by title, a folder's name keeps its sessions, no match is said, Escape clears", () => {
    renderSidebar();
    const search = screen.getByTestId("harness-session-search");
    fireEvent.change(search, { target: { value: "header" } });
    expect(screen.queryByTestId("harness-session-row-a")).toBeNull();
    expect(screen.getByTestId("harness-session-row-b")).toBeInTheDocument();
    expect(screen.queryByTestId("harness-session-row-c")).toBeNull();
    fireEvent.change(search, { target: { value: "BLOG" } });
    expect(screen.getByTestId("harness-session-row-c")).toBeInTheDocument();
    expect(screen.queryByTestId("harness-session-row-a")).toBeNull();
    fireEvent.change(search, { target: { value: "zzz" } });
    expect(screen.getByTestId("harness-no-session-match")).toHaveTextContent("No sessions match “zzz”.");
    fireEvent.keyDown(search, { key: "Escape" });
    expect((search as HTMLInputElement).value).toBe("");
    expect(screen.getByTestId("harness-session-row-a")).toBeInTheDocument();
  });

  it("the rule itself: blank is everything; case does not matter", () => {
    expect(filterFolders(FOLDERS, "  ").map((f) => f.sessions.length)).toEqual([2, 1]);
    expect(filterFolders(FOLDERS, "ADD").map((f) => f.sessions.map((s) => s.id))).toEqual([["a"]]);
  });
});

/* ---------------------------------------------------------------- Keyboard */

describe("the keys: inside the IJ harness only", () => {
  it("the rule: no modifier mix, no IME, Esc only while a reply runs and nothing else uses it", () => {
    const at = { inside: true, busy: true, popupOpen: false, asking: false };
    expect(harnessKeyAction({ key: "k", ctrlKey: true }, at)).toBe("model");
    expect(harnessKeyAction({ key: "K", metaKey: true }, at)).toBe("model");
    expect(harnessKeyAction({ key: "l", ctrlKey: true }, at)).toBe("focus");
    expect(harnessKeyAction({ key: "n", ctrlKey: true }, at)).toBe("new");
    expect(harnessKeyAction({ key: "Escape" }, at)).toBe("stop");
    expect(harnessKeyAction({ key: "k", ctrlKey: true }, { ...at, inside: false })).toBeNull();
    expect(harnessKeyAction({ key: "k", ctrlKey: true, shiftKey: true }, at)).toBeNull();
    expect(harnessKeyAction({ key: "k", ctrlKey: true, isComposing: true }, at)).toBeNull();
    expect(harnessKeyAction({ key: "Escape" }, { ...at, busy: false })).toBeNull();
    expect(harnessKeyAction({ key: "Escape" }, { ...at, popupOpen: true })).toBeNull();
    expect(harnessKeyAction({ key: "Escape" }, { ...at, asking: true })).toBeNull();
    expect(harnessKeyAction({ key: "Escape" }, { ...at, inOtherField: true })).toBeNull();
    expect(harnessKeyAction({ key: "Escape", defaultPrevented: true }, at)).toBeNull();
  });

  it("Ctrl+K inside opens the model menu and the app's palette never sees it; outside the palette gets it", async () => {
    const palette = vi.fn();
    window.addEventListener("keydown", palette); // the palette's own listener (bubble, window)
    try {
      renderSession();
      await screen.findByTestId("harness-empty");
      box().focus();
      fireEvent.keyDown(box(), { key: "k", ctrlKey: true });
      expect(await screen.findByTestId("model-filter")).toBeInTheDocument();
      expect(palette).not.toHaveBeenCalled();
      // Outside the harness (the app's own controls): the palette's.
      const outside = screen.getByTestId("outside-button");
      outside.focus();
      fireEvent.keyDown(outside, { key: "k", ctrlKey: true });
      expect(palette).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener("keydown", palette);
    }
  });

  it("Ctrl+L puts the cursor in the box; outside the harness it does nothing", async () => {
    renderSession();
    await screen.findByTestId("harness-empty");
    const folder = screen.getByTestId("harness-title");
    folder.focus();
    fireEvent.keyDown(folder, { key: "l", ctrlKey: true });
    expect(document.activeElement).toBe(box());
    const outside = screen.getByTestId("outside-button");
    outside.focus();
    fireEvent.keyDown(outside, { key: "l", ctrlKey: true });
    expect(document.activeElement).toBe(outside);
  });

  async function runningTurn() {
    const stream = controlledStream();
    fetchMock.mockImplementation(async (url: string) => (String(url).endsWith("/turn") ? stream.response : sse([])));
    renderSession();
    await screen.findByTestId("harness-empty");
    fireEvent.change(box(), { target: { value: "Go" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await screen.findByTestId("harness-stop");
    return stream;
  }
  const stops = () => api.calls.filter((c) => c.method === "POST" && /\/chat\/turns\/.+\/stop$/.test(c.path));

  it("Esc stops a running reply from the box", async () => {
    const stream = await runningTurn();
    box().focus();
    fireEvent.keyDown(box(), { key: "Escape" });
    await waitFor(() => expect(stops()).toHaveLength(1));
    stream.end();
  });

  it("Esc does NOT stop while a dialog or a menu is open, in another field, or from outside", async () => {
    const stream = await runningTurn();
    // An open menu in the harness: the model menu.
    fireEvent.keyDown(box(), { key: "k", ctrlKey: true });
    await screen.findByTestId("model-filter");
    fireEvent.keyDown(screen.getByTestId("model-filter"), { key: "Escape" });
    // Renaming the session: the title box's Escape is its own.
    fireEvent.click(screen.getByTestId("harness-title"));
    const title = await screen.findByTestId("harness-title-input");
    fireEvent.keyDown(title, { key: "Escape" });
    // A dialog over the page (aria-modal) takes Escape.
    const dlg = document.createElement("div");
    dlg.setAttribute("aria-modal", "true");
    dlg.setAttribute("role", "dialog");
    document.body.appendChild(dlg);
    fireEvent.keyDown(box(), { key: "Escape" });
    dlg.remove();
    // Outside the harness.
    const outside = screen.getByTestId("outside-button");
    outside.focus();
    fireEvent.keyDown(outside, { key: "Escape" });
    await new Promise((r) => setTimeout(r, 50));
    expect(stops()).toHaveLength(0);
    stream.end();
  });

  it("Ctrl+N opens New session in the view; outside the harness it does not", async () => {
    api.gets["/build/sessions"] = { folders: [] };
    render(
      <div>
        <button data-testid="app-outside">outside</button>
        <IjHarnessView
          initialSession={null}
          terminals={[]}
          onOpenTerminal={() => {}}
          defaultFolder={null}
          terminal={terminal}
        />
      </div>,
    );
    const newBtn = await screen.findByTestId("harness-new-session");
    const outside = screen.getByTestId("app-outside");
    outside.focus();
    fireEvent.keyDown(outside, { key: "n", ctrlKey: true });
    expect(screen.queryByTestId("harness-new-session-form")).toBeNull();
    newBtn.focus();
    fireEvent.keyDown(newBtn, { key: "n", ctrlKey: true });
    expect(await screen.findByTestId("harness-new-session-form")).toBeInTheDocument();
  });

  it("a page with no IJ harness (the chat page) keeps every key: nothing of the harness answers", () => {
    const palette = vi.fn();
    window.addEventListener("keydown", palette);
    try {
      render(<button data-testid="chat-button">chat</button>);
      const b = screen.getByTestId("chat-button");
      b.focus();
      fireEvent.keyDown(b, { key: "k", ctrlKey: true });
      expect(palette).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener("keydown", palette);
    }
  });
});

/* ----------------------------------------------------------------- Rewind */

describe("Rewind puts a message's pictures and files back with its words", () => {
  const PREVIEW = {
    to: "t1",
    question: "Look at this",
    turns: ["t1"],
    files: [],
    problems: [],
    commands: [],
    already_undone: 0,
    can_rewind: true,
    note: "",
    detail: "",
    attachments: ["C:\\up\\shot.png"],
    picked_files: ["C:\\work\\shop\\src\\a.py"],
    skill: "",
  };

  it("the words, the picture and the '@' file go back in the box", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [userLine("Look at this", "t1", { attachmentPaths: ["C:\\up\\shot.png"] }), replyLine("Ok.", "t1")],
    });
    api.gets["/build/sessions/s1/rewind?to=t1"] = PREVIEW;
    api.postAnswers["POST /build/sessions/s1/rewind"] = () => {
      api.gets["/build/sessions/s1"] = view();
      return { rewound: true, ...PREVIEW };
    };
    renderSession();
    fireEvent.click(await screen.findByTestId("rewind-start"));
    fireEvent.click(await screen.findByTestId("rewind-confirm-button"));
    await waitFor(() => expect(box().value).toBe("Look at this"));
    expect(screen.getAllByTestId("harness-attachment")).toHaveLength(1);
    expect(screen.getByTestId("harness-at-chip")).toHaveAttribute("title", "src/a.py");
  });

  it("committed work is said before the second press and after it", async () => {
    const note =
      "Some of this work was committed: 1 commit on ij/session-ab12 (“Set x”) came after this message. Rewinding puts the files back, but the commits stay on the branch, so the files show as changes that are not committed yet.";
    api.gets["/build/sessions/s1"] = view({ messages: [userLine("Set x", "t1"), replyLine("Done.", "t1")] });
    api.gets["/build/sessions/s1/rewind?to=t1"] = { ...PREVIEW, attachments: [], picked_files: [], commits_note: note };
    api.postAnswers["POST /build/sessions/s1/rewind"] = { rewound: true, ...PREVIEW, attachments: [], picked_files: [], commits_note: note };
    renderSession();
    fireEvent.click(await screen.findByTestId("rewind-start"));
    expect(await screen.findByTestId("rewind-commits")).toHaveTextContent("the commits stay on the branch");
    fireEvent.click(screen.getByTestId("rewind-confirm-button"));
    expect(await screen.findByTestId("harness-rewind-commits")).toHaveTextContent(note);
  });

  it("past what one message carries, the box SAYS which files did not go back", async () => {
    const six = Array.from({ length: 6 }, (_, i) => `C:\\up\\p${i}.png`);
    api.gets["/build/sessions/s1"] = view({ messages: [userLine("Six", "t1"), replyLine("Ok.", "t1")] });
    api.gets["/build/sessions/s1/rewind?to=t1"] = { ...PREVIEW, question: "Six", attachments: six, picked_files: [] };
    api.postAnswers["POST /build/sessions/s1/rewind"] = { rewound: true, ...PREVIEW, question: "Six", attachments: six, picked_files: [] };
    renderSession();
    fireEvent.click(await screen.findByTestId("rewind-start"));
    fireEvent.click(await screen.findByTestId("rewind-confirm-button"));
    await waitFor(() => expect(screen.getAllByTestId("harness-attachment")).toHaveLength(4));
    expect(screen.getByTestId("harness-attach-problem")).toHaveTextContent(
      "2 files did not go back in the box (p4.png, p5.png): one message carries at most 4 files.",
    );
  });

  it("past 20 files (no read limit) the box says so instead of cutting the extras without a word", () => {
    const { result } = renderHook(() => useHarnessAttachments());
    const files = Array.from({ length: MAX_FILES + 3 }, (_, i) => ({
      name: `f${i}.png`,
      path: `C:\\up\\f${i}.png`,
      bytes: 0,
      label: `f${i}.png`,
    }));
    act(() => result.current.restore(files));
    expect(result.current.files).toHaveLength(MAX_FILES);
    expect(result.current.problem).toBe(leftOutWords(files.slice(MAX_FILES), MAX_FILES));
    expect(result.current.problem).toContain("3 files did not go back in the box");
  });
});

/* -------------------------------------------------- Compact, on return */

describe("a session opened while Compact runs", () => {
  it("says 'Summarizing earlier turns…' and holds the box until it ends", async () => {
    api.gets["/build/sessions/s1"] = view({
      compacting: true,
      messages: [userLine("q", "t1"), replyLine("a", "t1")],
    });
    renderSession();
    const meter = await screen.findByTestId("harness-context-meter");
    expect(meter).toHaveAttribute("data-state", "compacting");
    expect(meter).toHaveTextContent("Summarizing earlier turns…");
    fireEvent.change(box(), { target: { value: "next" } });
    expect(screen.getByTestId("harness-send")).toBeDisabled();
    // Compact ends: the next read says so.
    api.gets["/build/sessions/s1"] = view({ messages: [userLine("q", "t1"), replyLine("a", "t1")] });
    await waitFor(() => expect(screen.queryByText("Summarizing earlier turns…")).toBeNull(), { timeout: 8000 });
    expect(screen.getByTestId("harness-send")).not.toBeDisabled();
  });
});

/* ------------------------------------------------------- the sign-in rule */

describe("one sign-in rule for the model menu and the header chip", () => {
  const acct = (id: string, status: string) => ({
    id,
    name: id,
    vendor: "claude",
    vendor_label: "Claude Code",
    provider: "claude-cli",
    default: false,
    plan: "",
    plan_label: "",
    status,
  });

  it("signed out can be picked in the chip's list (and the menu); turned off cannot, with the same reason", () => {
    const view_ = decodeAccounts({
      claude: [acct("a-in", "ready"), acct("a-out", "needs_sign_in"), acct("a-off", "off")],
      codex: [],
      iron_proxy: { enabled: true, running: true },
    });
    expect(view_).not.toBeNull();
    const out = view_!.groups.claude.find((a) => a.id === "a-out")!;
    const off = view_!.groups.claude.find((a) => a.id === "a-off")!;
    expect(accountBlockedReason(out)).toBe("");
    expect(accountBlockedReason(off)).toBe("Turn it on in Connections to use it.");
    const onPick = vi.fn();
    render(<AccountList view={view_!} currentId="a-in" onPick={onPick} onMakeDefault={() => {}} title="Accounts" />);
    const pickOut = screen.getByTestId("account-pick-a-out") as HTMLButtonElement;
    const pickOff = screen.getByTestId("account-pick-a-off") as HTMLButtonElement;
    expect(pickOut.disabled).toBe(false);
    expect(pickOff.disabled).toBe(true);
    expect(pickOff.getAttribute("title")).toBe("Turn it on in Connections to use it.");
    fireEvent.click(pickOut);
    expect(onPick).toHaveBeenCalledTimes(1);
  });
});

/* --------------------------------------------------------------- words */

describe("the words", () => {
  it("'Ask first' names a browser's clicks and typed entries", () => {
    expect(PERMISSION_LEVELS.always_ask.description).toMatch(
      /^Asks before it edits files, uses the internet, runs commands, or clicks or types in a browser\./,
    );
  });

  it("the chat gauge and the harness meter say the same thing, never the model's limit for a safe size", () => {
    expect(fillWords("chat", 41, false)).toBe(
      "This chat fills about 41% of what Iron Jarvis sends this model at once. This model's own limit is not known, so a safe size is used.",
    );
    expect(fillWords("chat", 41, true)).toBe("This chat fills about 41% of what this model can read at once.");
    expect(trimmedWords(2, false)).not.toContain("what this model can read");
    expect(
      meterTitle({ window: 32000, used: 1, percent: 41, level: "ok", suggestAt: 70, appSized: true, compactionOff: false }),
    ).toBe(fillWords("conversation", 41, false));
    // The chat page itself (a Next page exports no helpers): its gauge and its
    // offer read the daemon's window_known through the shared words.
    const src = readFileSync(join(__dirname, "..", "app", "chat", "page.tsx"), "utf8").replace(/\r\n/g, "\n");
    const gauge = src.slice(src.indexOf("function ContextMeter("), src.indexOf("function CompactionOffer("));
    expect(gauge).toContain("trimmedWords(usage.dropped, windowKnownOf(usage))");
    expect(gauge).toContain('fillWords("chat", pct, windowKnownOf(usage))');
    expect(gauge).not.toContain("of what this model can read at once");
    const offer = src.slice(src.indexOf("function CompactionOffer("), src.indexOf("function CompactionOffer(") + 2500);
    expect(offer).toContain('title={fillWords("chat", pct, windowKnownOf(usage))}');
  });
});

/* ------------------------------------------------- the rewind control alone */

describe("the rewind control hands back what rode the message", () => {
  it("passes the pictures, the '@' files and the commit words to the session", async () => {
    api.gets["/build/sessions/s1/rewind?to=t1"] = {
      to: "t1",
      question: "Q",
      turns: ["t1"],
      files: [],
      problems: [],
      commands: [],
      can_rewind: true,
      note: "",
      detail: "",
      attachments: ["C:\\up\\x.png"],
      picked_files: ["C:\\w\\a.py"],
      commits_note: "",
    };
    api.postAnswers["POST /build/sessions/s1/rewind"] = { rewound: true, question: "Q", files: [], note: "" };
    const onRewound = vi.fn();
    render(<RewindControl threadId="s1" turnId="t1" onRewound={onRewound} />);
    fireEvent.click(screen.getByTestId("rewind-start"));
    fireEvent.click(await screen.findByTestId("rewind-confirm-button"));
    await waitFor(() =>
      expect(onRewound).toHaveBeenCalledWith("Q", {
        attachments: ["C:\\up\\x.png"],
        pickedFiles: ["C:\\w\\a.py"],
        commitsNote: "",
      }),
    );
  });
});
