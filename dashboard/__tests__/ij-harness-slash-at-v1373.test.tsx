/**
 * v1.373.0 (IJ harness part 2, Phase 2) — "/" and "@" in a Build session's
 * box. The mocks and fixtures are ij-harness-pictures-v1371's (copied, not
 * imported: a test file is not a module other tests import).
 *
 *  - "/" lists the session's commands (/plan, /rewind, /model — and since
 *    v1.374.0 /compact) and the skills (the folder's own and the user's), filters as you
 *    type; Enter (or Tab) picks; a skill is a chip and rides the NEXT turn's
 *    body as `skill` (the user's) or `folder_skill` (the folder's), then the
 *    chip is gone;
 *  - /plan switches the session to Plan first through the box's own call,
 *    /rewind opens the list of messages to go back to, /model the model menu;
 *  - "@" lists the files of the SESSION's folder (GET /fs/files on it) as you
 *    type, loosely matched; a pick is a chip and the turn body carries its
 *    path in `files`;
 *  - Escape closes either list without sending and keeps the words;
 *  - a message queued with Ctrl+Enter keeps its own skill and files, and a
 *    message that never started gets them back in the box;
 *  - the lists are drawn above the box (lib/chatRefsMenuFit, preferAbove).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown | ((path: string) => unknown)>,
    calls: [] as { method: string; path: string; body?: unknown }[],
    postAnswers: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : api.postAnswers[`${method} ${path}`];
    const value = typeof hit === "function" ? (hit as (p: string) => unknown)(path) : hit;
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

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { fitAtMenu } from "@/lib/chatRefsMenuFit";
import {
  READ_LIMIT_WORDS,
  atFileRows,
  decodeQueuedItem,
  queuedItemBody,
  sessionCommands,
  slashRows,
} from "@/lib/buildComposer";
import { decodeRewindPoints } from "@/lib/buildRewind";
import { matchProjectEntries } from "@/lib/atMenuFiles";

/* ------------------------------------------------------------- fixtures */

const FOLDER = "C:\\work\\shop";
const NOTES = "C:\\work\\shop\\docs\\notes.md";
const CART = "C:\\work\\shop\\src\\cart.ts";
const FILES_PATH = `/fs/files?path=${encodeURIComponent(FOLDER)}&depth=4&limit=600`;
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

/** A controllable SSE body: `push` frames, `end` it. */
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

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.gets = {
    "/build/sessions/s1": view(),
    "/build/accounts": new api.ApiError("no such route", 404),
    "/skills": {
      skills: [
        { name: "bulleteer", description: "answer as bullets", source: "user" },
        { name: "pdf-tools", description: "read PDFs", source: "builtin" },
      ],
    },
    "/build/sessions/s1/skills": {
      skills: [{ name: "release-notes", description: "write release notes", rel: ".ironjarvis/harness/skills/release-notes/SKILL.md" }],
      note: "",
    },
    [FILES_PATH]: {
      root: FOLDER,
      files: [
        { name: "cart.ts", path: CART, rel: "src/cart.ts", size: 40 },
        { name: "notes.md", path: NOTES, rel: "docs/notes.md", size: 12 },
      ],
    },
  };
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

function renderSession() {
  return render(
    <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
  );
}

const box = () => screen.getByTestId("harness-input") as HTMLTextAreaElement;
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
const key = (k: string, extra: Record<string, unknown> = {}) => fireEvent.keyDown(box(), { key: k, ...extra });
const turnBody = (i = 0) => JSON.parse(String((fetchMock.mock.calls[i] as unknown as [string, RequestInit])[1].body));
/** A wait on a list the (mocked) daemon answers: generous, never the whole
 *  test's budget (CLAUDE.md: a wait must be able to report what it waited for). */
const NET = { timeout: 5000 };
const rowNames = (testId: string) => screen.queryAllByTestId(testId).map((el) => el.getAttribute("data-name"));

async function opened() {
  renderSession();
  await screen.findByTestId("harness-empty");
}

/* ---------------------------------------------------------------- tests */

describe("“/” lists the session's commands and the skills", () => {
  it("shows /plan, /compact, /rewind and /model, then the folder's skills and yours, and filters as you type", async () => {
    await opened();
    type("/");
    const menu = await screen.findByTestId("harness-slash-menu");
    // v1.374.0 (moved on purpose): /compact exists now — it presses the
    // meter's Compact (ij-harness-work-v1374).
    expect(rowNames("harness-slash-command")).toEqual(["plan", "compact", "rewind", "model"]);
    expect(within(menu).getByText("/compact")).toBeTruthy();
    await waitFor(() => expect(rowNames("harness-slash-skill")).toEqual(["release-notes", "bulleteer", "pdf-tools"]), NET);
    expect(screen.getAllByTestId("harness-slash-skill")[0].getAttribute("data-source")).toBe("folder");
    // The two lists were read: yours (the chat's), and the folder's own.
    expect(api.calls.some((c) => c.method === "GET" && c.path === "/skills")).toBe(true);
    expect(api.calls.some((c) => c.method === "GET" && c.path === "/build/sessions/s1/skills")).toBe(true);
    type("/bul");
    await waitFor(() => expect(rowNames("harness-slash-skill")).toEqual(["bulleteer"]), NET);
    expect(rowNames("harness-slash-command")).toEqual([]);
    // A description counts too: "re" is in /plan's "it reads" — listed
    // AFTER /rewind, whose NAME holds it (v1.373.0: name matches first).
    type("/re");
    await waitFor(() => expect(rowNames("harness-slash-command")).toEqual(["rewind", "plan"]));
    expect(rowNames("harness-slash-skill")).toEqual(["release-notes", "pdf-tools"]);
    type("/rew");
    await waitFor(() => expect(rowNames("harness-slash-command")).toEqual(["rewind"]));
    expect(rowNames("harness-slash-skill")).toEqual([]);
    // A "/" inside a word never opens it (a path, "and/or").
    type("see src/cart");
    await waitFor(() => expect(screen.queryByTestId("harness-slash-menu")).toBeNull());
  });

  it("Enter picks a skill: a chip, the words around it kept, and the next turn carries `skill`", async () => {
    const s = controlledStream();
    fetchMock.mockResolvedValueOnce(s.response);
    await opened();
    type("fix the cart /bul");
    await waitFor(() => expect(rowNames("harness-slash-skill")).toEqual(["bulleteer"]), NET);
    key("Enter");
    const chip = await screen.findByTestId("harness-skill-chip");
    expect(chip).toHaveTextContent("bulleteer");
    expect(chip.getAttribute("data-source")).toBe("yours");
    expect(screen.queryByTestId("harness-slash-menu")).toBeNull();
    // Only the "/word" was taken out: nothing was sent, the words stay.
    expect(box().value).toBe("fix the cart ");
    expect(fetchMock).not.toHaveBeenCalled();
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = turnBody();
    expect(body.text).toBe("fix the cart");
    expect(body.skill).toBe("bulleteer");
    expect(body).not.toHaveProperty("folder_skill");
    // While it works, the question shows which skill rode it; the chip is
    // gone from the box.
    expect(await screen.findByTestId("harness-sent-skill")).toHaveTextContent("bulleteer");
    expect(screen.queryByTestId("harness-skill-chip")).toBeNull();
    await act(async () => {
      s.push("done", { reply: "ok" });
      s.end();
    });
  });

  it("a folder skill picked with ↓ and Tab rides the turn as `folder_skill`", async () => {
    await opened();
    type("/");
    await waitFor(() => expect(rowNames("harness-slash-skill")).toEqual(["release-notes", "bulleteer", "pdf-tools"]), NET);
    // plan, compact, rewind, model, release-notes: four steps down (v1.374.0
    // moved on purpose: /compact joined the commands).
    key("ArrowDown");
    key("ArrowDown");
    key("ArrowDown");
    key("ArrowDown");
    const rows = screen.getAllByRole("option");
    expect(rows[4].getAttribute("aria-selected")).toBe("true");
    key("Tab");
    const chip = await screen.findByTestId("harness-skill-chip");
    expect(chip.getAttribute("data-source")).toBe("folder");
    type("write the notes");
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(turnBody().folder_skill).toBe("release-notes");
    expect(turnBody()).not.toHaveProperty("skill");
  });

  it("a click on a row picks it too, and × takes the chip away", async () => {
    await opened();
    type("/pdf");
    const row = await screen.findByText("pdf-tools", {}, NET);
    fireEvent.click(row.closest("button") as HTMLButtonElement);
    const chip = await screen.findByTestId("harness-skill-chip");
    fireEvent.click(within(chip).getByRole("button", { name: "Remove the skill pdf-tools" }));
    expect(screen.queryByTestId("harness-skill-chip")).toBeNull();
    type("hello");
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(turnBody()).not.toHaveProperty("skill");
  });
});

describe("the session's commands call what the box already has", () => {
  it("/plan switches the session to Plan first (the mode switch's own call)", async () => {
    await opened();
    type("/plan");
    await waitFor(() => expect(rowNames("harness-slash-command")).toEqual(["plan"]));
    key("Enter");
    await waitFor(() =>
      expect(api.calls.find((c) => c.method === "PATCH" && c.path === "/build/sessions/s1")?.body).toEqual({ mode: "plan" }),
    );
    expect(box().value).toBe("");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("in a planning session /plan says it lets it work, and switches back", async () => {
    api.gets["/build/sessions/s1"] = view({ mode: "plan" });
    await opened();
    type("/pla");
    const row = await screen.findByTestId("harness-slash-command");
    expect(row).toHaveTextContent("Stop planning");
    key("Enter");
    await waitFor(() =>
      expect(api.calls.find((c) => c.method === "PATCH" && c.path === "/build/sessions/s1")?.body).toEqual({ mode: "act" }),
    );
  });

  it("/rewind opens the list of your messages, newest first, each with Rewind to here", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [
        { role: "user", content: "Add a cart", turnId: "t0", at: "2026-10-22T10:00:00" },
        { role: "assistant", content: "Added.", turnId: "t0" },
        { role: "user", content: "Make it blue", turnId: "t1", at: "2026-10-22T10:05:00" },
        { role: "assistant", content: "Blue now.", turnId: "t1" },
      ],
    });
    api.gets["/build/sessions/s1/rewind"] = {
      checkpoints: [
        { turn_id: "t0", question: "Add a cart", at: "2026-10-22T10:00:00", continuation: false, files: 2, commands: 1 },
        { turn_id: "c1", question: "carry on", at: "2026-10-22T10:02:00", continuation: true, files: 0, commands: 0 },
        { turn_id: "t1", question: "Make it blue", at: "2026-10-22T10:05:00", continuation: false, files: 0, commands: 0 },
      ],
    };
    api.gets["/build/sessions/s1/rewind?to=t0"] = {
      to: "t0", question: "Add a cart", turns: ["t0", "t1"], files: [{ path: "C:\\work\\shop\\cart.ts", rel: "cart.ts", change: "put back" }],
      problems: [], commands: [], can_rewind: true, note: "", detail: "",
    };
    renderSession();
    await screen.findAllByTestId("harness-reply");
    type("/rew");
    await waitFor(() => expect(rowNames("harness-slash-command")).toEqual(["rewind"]));
    key("Enter");
    const list = await screen.findByTestId("harness-rewind-list");
    await waitFor(() => expect(within(list).getAllByTestId("harness-rewind-point")).toHaveLength(2), NET);
    const points = within(list).getAllByTestId("harness-rewind-point");
    // Newest first; the daemon's own "carry on" question is not listed.
    expect(points[0]).toHaveTextContent("Make it blue");
    expect(points[1]).toHaveTextContent("Add a cart");
    // (v1.373.0 review: "file changes" — one per write or edit — counted
    // from this message to the newest.)
    expect(points[1]).toHaveTextContent("2 file changes, 1 command from here on");
    // The existing two-press rewind: the first press only says what goes back.
    fireEvent.click(within(points[1]).getByTestId("rewind-start"));
    expect(await within(list).findByTestId("rewind-confirm", {}, NET)).toHaveTextContent(
      "cart.ts goes back to how it was",
    );
    // Escape in the box closes the list, nothing sent.
    key("Escape");
    await waitFor(() => expect(screen.queryByTestId("harness-rewind-list")).toBeNull());
    expect(fetchMock).not.toHaveBeenCalled();
    expect(api.calls.some((c) => c.method === "POST" && c.path.endsWith("/rewind"))).toBe(false);
  });

  it("/model opens the model menu", async () => {
    await opened();
    expect(screen.queryByTestId("model-menu")).toBeNull();
    type("/mod");
    await waitFor(() => expect(rowNames("harness-slash-command")).toEqual(["model"]));
    key("Enter");
    expect(await screen.findByTestId("model-menu")).toBeInTheDocument();
    expect(box().value).toBe("");
  });
});

describe("“@” lists the session folder's files", () => {
  it("lists them as you type (loosely matched), and the picked file's path rides the turn", async () => {
    const s = controlledStream();
    fetchMock.mockResolvedValueOnce(s.response);
    await opened();
    type("@");
    const menu = await screen.findByTestId("harness-at-menu");
    await waitFor(() => expect(within(menu).getAllByTestId("harness-at-file")).toHaveLength(2), NET);
    // The session's folder — the Files tab's own query, never another folder.
    const listed = api.calls.filter((c) => c.method === "GET" && c.path.startsWith("/fs/files"));
    expect(listed.map((c) => c.path)).toEqual([FILES_PATH]);
    type("@crt");
    await waitFor(() =>
      expect(screen.getAllByTestId("harness-at-file").map((e) => e.getAttribute("data-path"))).toEqual([CART]),

      NET,
    );
    type("what does @not");
    await waitFor(() =>
      expect(screen.getAllByTestId("harness-at-file").map((e) => e.getAttribute("data-path"))).toEqual([NOTES]),

      NET,
    );
    key("Enter");
    const chip = await screen.findByTestId("harness-at-chip");
    expect(chip).toHaveTextContent("notes.md");
    expect(box().value).toBe("what does ");
    expect(fetchMock).not.toHaveBeenCalled();
    type("what does it say?");
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(turnBody().files).toEqual([NOTES]);
    expect(turnBody()).not.toHaveProperty("attachments");
    // While it works, the question shows the file it carried; the chip is
    // gone from the box.
    expect(await screen.findByTestId("harness-sent-file")).toHaveTextContent("notes.md");
    expect(screen.queryByTestId("harness-at-chip")).toBeNull();
    await act(async () => {
      s.push("done", { reply: "ok" });
      s.end();
    });
  });

  it("a picked file is not offered again, and a file alone is a message", async () => {
    await opened();
    type("@cart");
    await waitFor(() => expect(screen.getAllByTestId("harness-at-file")).toHaveLength(1), NET);
    key("Enter");
    await screen.findByTestId("harness-at-chip");
    type("@");
    await waitFor(() =>
      expect(screen.getAllByTestId("harness-at-file").map((e) => e.getAttribute("data-path"))).toEqual([NOTES]),

      NET,
    );
    key("Escape");
    type("");
    const sendBtn = screen.getByTestId("harness-send");
    await waitFor(() => expect(sendBtn).not.toBeDisabled());
    fireEvent.click(sendBtn);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(turnBody().text).toBe("");
    expect(turnBody().files).toEqual([CART]);
  });

  it("a pick past what one message reads is refused in words (uploads count too)", async () => {
    const many = Array.from({ length: 6 }, (_, i) => ({
      name: `part${i}.md`,
      path: `C:\\work\\shop\\part${i}.md`,
      rel: `part${i}.md`,
      size: 1,
    }));
    api.gets[FILES_PATH] = { root: FOLDER, files: many };
    api.postAnswers["POST /documents/upload"] = { path: "C:/ij/uploads/pic.png", name: "pic.png", bytes: 4 };
    await opened();
    // One upload, then "@" picks: the fourth file in all is the last read.
    fireEvent.change(screen.getByTestId("harness-attach-input"), {
      target: { files: [new File([new Uint8Array([1])], "pic.png", { type: "image/png" })] },
    });
    await screen.findByTestId("harness-attachment");
    for (const name of ["part0.md", "part1.md", "part2.md"]) {
      type(`@${name.slice(0, 5)}`);
      await waitFor(() => expect(screen.getAllByTestId("harness-at-file")[0]).toHaveTextContent(name), NET);
      key("Enter");
      await waitFor(() => expect(screen.getAllByTestId("harness-at-chip").map((c) => c.textContent)).toContain(name));
    }
    type("@part3");
    await waitFor(() => expect(screen.getAllByTestId("harness-at-file")[0]).toHaveTextContent("part3.md"), NET);
    key("Enter");
    expect(await screen.findByText(READ_LIMIT_WORDS)).toBeInTheDocument();
    expect(screen.getAllByTestId("harness-at-chip")).toHaveLength(3);
  });

  it("an older transcript line with no picks renders as before; a saved one shows them", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [
        { role: "user", content: "old question", turnId: "t0" },
        { role: "assistant", content: "old answer", turnId: "t0" },
        { role: "user", content: "read this", turnId: "t1", filePaths: [NOTES], skill: "bulleteer" },
        { role: "assistant", content: "read it", turnId: "t1" },
      ],
    });
    renderSession();
    await waitFor(() => expect(screen.getAllByTestId("harness-user-bubble")).toHaveLength(2));
    expect(screen.getAllByTestId("harness-sent-picks")).toHaveLength(1);
    expect(screen.getByTestId("harness-sent-file")).toHaveTextContent("notes.md");
    expect(screen.getByTestId("harness-sent-skill")).toHaveTextContent("bulleteer");
  });
});

describe("Escape closes a list without sending and keeps the words", () => {
  it("for “/” and for “@”, and typing brings the list back", async () => {
    await opened();
    type("/bul");
    await screen.findByTestId("harness-slash-menu");
    key("Escape");
    await waitFor(() => expect(screen.queryByTestId("harness-slash-menu")).toBeNull());
    expect(box().value).toBe("/bul");
    type("/bull");
    expect(await screen.findByTestId("harness-slash-menu")).toBeInTheDocument();
    type("look at @no");
    await screen.findByTestId("harness-at-menu");
    key("Escape");
    await waitFor(() => expect(screen.queryByTestId("harness-at-menu")).toBeNull());
    expect(box().value).toBe("look at @no");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("harness-skill-chip")).toBeNull();
    expect(screen.queryByTestId("harness-at-chip")).toBeNull();
  });
});

describe("a waiting message keeps its own skill and files", () => {
  it("Ctrl+Enter while it works takes the chips with the words, and sends them after the reply", async () => {
    const s = controlledStream();
    fetchMock.mockResolvedValueOnce(s.response);
    await opened();
    type("first");
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await screen.findByTestId("harness-stop");
    // Pick a skill and a file while it works.
    type("/bul");
    await waitFor(() => expect(rowNames("harness-slash-skill")).toEqual(["bulleteer"]), NET);
    key("Enter");
    await screen.findByTestId("harness-skill-chip");
    type("@notes");
    await waitFor(() => expect(screen.getAllByTestId("harness-at-file")).toHaveLength(1), NET);
    key("Enter");
    await screen.findByTestId("harness-at-chip");
    type("then summarise");
    key("Enter", { ctrlKey: true });
    const waiting = await screen.findByTestId("harness-queued");
    expect(waiting).toHaveTextContent("Waiting to send: then summarise · 1 file · skill bulleteer");
    // The box is empty again: the next message starts with no chips.
    expect(box().value).toBe("");
    expect(screen.queryByTestId("harness-skill-chip")).toBeNull();
    expect(screen.queryByTestId("harness-at-chip")).toBeNull();
    // Kept across a reload with its chips.
    const saved = api.calls.filter((c) => c.method === "PUT" && c.path === "/chat/threads/s1/queue").at(-1);
    expect((saved?.body as { items: unknown[] }).items).toEqual([
      {
        text: "then summarise",
        skill: { name: "bulleteer", source: "yours" },
        files: [{ path: NOTES, rel: "docs/notes.md", name: "notes.md" }],
      },
    ]);
    await act(async () => {
      s.push("token", { text: "done" });
      s.push("done", { reply: "done" });
      s.end();
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const second = turnBody(1);
    expect(second.text).toBe("then summarise");
    expect(second.skill).toBe("bulleteer");
    expect(second.files).toEqual([NOTES]);
  });

  it("a waiting message read back after a reload keeps its chips", () => {
    const back = decodeQueuedItem({
      text: "",
      skill: { name: "release-notes", source: "folder" },
      files: [{ path: NOTES, rel: "docs/notes.md", name: "notes.md" }],
      attachments: [{ path: "C:/ij/uploads/a.png", name: "a.png", bytes: 4, label: "Pasted picture" }],
    });
    expect(back).toEqual({
      text: "",
      skill: { name: "release-notes", source: "folder" },
      files: [{ path: NOTES, rel: "docs/notes.md", name: "notes.md" }],
      attachments: [{ path: "C:/ij/uploads/a.png", name: "a.png", bytes: 4, label: "Pasted picture" }],
    });
    expect(queuedItemBody(back!)).toEqual({
      text: "",
      skill: { name: "release-notes", source: "folder" },
      files: [{ path: NOTES, rel: "docs/notes.md", name: "notes.md" }],
      attachments: [{ name: "a.png", path: "C:/ij/uploads/a.png", bytes: 4, label: "Pasted picture" }],
    });
    // An older Iron Jarvis kept words only; nothing at all is not a message.
    expect(decodeQueuedItem({ text: "hi" })).toEqual({ text: "hi" });
    expect(decodeQueuedItem({ text: "  " })).toBeNull();
  });

  it("a message that never started gets its words and chips back in the box", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "no such skill: bulleteer" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await opened();
    type("/bul");
    await waitFor(() => expect(rowNames("harness-slash-skill")).toEqual(["bulleteer"]), NET);
    key("Enter");
    type("@notes");
    await waitFor(() => expect(screen.getAllByTestId("harness-at-file")).toHaveLength(1), NET);
    key("Enter");
    type("go");
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(box().value).toBe("go"));
    expect(await screen.findByTestId("harness-skill-chip")).toHaveTextContent("bulleteer");
    expect(screen.getByTestId("harness-at-chip")).toHaveTextContent("notes.md");
    expect(screen.getByText(/no such skill/)).toBeInTheDocument();
  });
});

describe("the lists are drawn above the box", () => {
  it("inside the composer card, anchored to its top edge", async () => {
    await opened();
    type("/");
    const menu = await screen.findByTestId("harness-slash-menu");
    expect(menu.parentElement).toBe(screen.getByTestId("harness-composer"));
    expect(menu.className).toContain("bottom-full");
    expect(menu.className).toContain("left-0");
    expect(menu.className).toContain("right-0");
    expect(menu.getAttribute("data-side")).toBe("above");
    type("@");
    const at = await screen.findByTestId("harness-at-menu");
    expect(at.parentElement).toBe(screen.getByTestId("harness-composer"));
    expect(at.className).toContain("bottom-full");
  });

  it("preferAbove keeps a menu above the box and inside the window while a row fits there", () => {
    // A phone (390 x 844): the box near the bottom — the menu takes the cap.
    const roomy = fitAtMenu({ cardTop: 640, cardBottom: 760, clipTop: 0, clipBottom: 844, viewportHeight: 844, preferAbove: true });
    expect(roomy).toEqual({ side: "above", maxHeight: 448 });
    expect(640 - 8 - roomy!.maxHeight).toBeGreaterThanOrEqual(0);
    // Room for a few rows above but more below: still above (the chat's
    // menu would go to the bigger side), never past the window's top edge.
    const some = fitAtMenu({ cardTop: 200, cardBottom: 320, clipTop: 0, clipBottom: 844, viewportHeight: 844, preferAbove: true });
    expect(some!.side).toBe("above");
    expect(200 - 8 - some!.maxHeight).toBeGreaterThanOrEqual(0);
    expect(fitAtMenu({ cardTop: 200, cardBottom: 320, clipTop: 0, clipBottom: 844, viewportHeight: 844 })!.side).toBe("below");
    // (v1.373.0 review: with no room above for even a row, it opens below
    // the card — ij-harness-slash-at-review-v1373.)
  });
});

describe("the rules on their own", () => {
  it("slashRows: commands first, then the folder's skills, then yours; a picked skill is not offered again", () => {
    const cmds = sessionCommands("act");
    const folder = [{ name: "release-notes", description: "notes", source: "folder" as const }];
    const yours = [{ name: "bulleteer", description: "bullets", source: "yours" as const }];
    expect(slashRows("", cmds, folder, yours).map((r) => r.key)).toEqual([
      "cmd:plan", "cmd:compact", "cmd:rewind", "cmd:model", "skill:folder:release-notes", "skill:yours:bulleteer",
    ]);
    expect(slashRows("", cmds, folder, yours, { name: "bulleteer", source: "yours" }).map((r) => r.key)).not.toContain(
      "skill:yours:bulleteer",
    );
    expect(slashRows("bullets", cmds, folder, yours).map((r) => r.key)).toEqual(["skill:yours:bulleteer"]);
    // v1.374.0 (moved on purpose): Compact exists, so "/compact" does too.
    expect(cmds.map((c) => c.name)).toContain("compact");
  });

  it("slashRows: a NAME match ranks above a row that matches only by its description", () => {
    // v1.373.0 review item b: "/b" + Enter used to switch Plan first on
    // (its description holds a "b") while a skill NAMED with a "b" sat below.
    const cmds = sessionCommands("act");
    const yours = [{ name: "bulleteer", description: "bullets", source: "yours" as const }];
    const rows = slashRows("b", cmds, [], yours).map((r) => r.key);
    expect(rows[0]).toBe("skill:yours:bulleteer");
    // Description-only matches are still offered, after the name matches.
    expect(rows.slice(1).every((k) => k.startsWith("cmd:"))).toBe(true);
    expect(rows.length).toBeGreaterThan(1);
    // A name match keeps its place among names: "/plan" leads with the command.
    expect(slashRows("plan", cmds, [], yours)[0].key).toBe("cmd:plan");
  });

  it("atFileRows: files only, best match first, loose matches after exact ones, picked ones left out", () => {
    const listing = {
      root: FOLDER,
      files: [
        { name: "cart.ts", path: CART, rel: "src/cart.ts", size: 1 },
        { name: "carton.md", path: "C:\\work\\shop\\carton.md", rel: "carton.md", size: 1 },
        { name: "checkout.ts", path: "C:\\work\\shop\\src\\checkout.ts", rel: "src/checkout.ts", size: 1 },
      ],
    };
    expect(atFileRows(listing, "cart", []).rows.map((r) => r.name)).toEqual(["cart.ts", "carton.md"]);
    // "ckt" holds in order in "checkout.ts" only.
    expect(atFileRows(listing, "ckt", []).rows.map((r) => r.name)).toEqual(["checkout.ts"]);
    // A NAME holding the letters in order ranks before a PATH holding them
    // (the newer file first otherwise).
    const loose = {
      root: FOLDER,
      files: [
        { name: "readme.md", path: "C:\\work\\shop\\c\\k\\t\\readme.md", rel: "c/k/t/readme.md", size: 1 },
        { name: "checkout.ts", path: "C:\\work\\shop\\src\\checkout.ts", rel: "src/checkout.ts", size: 1 },
      ],
    };
    expect(atFileRows(loose, "ckt", []).rows.map((r) => r.name)).toEqual(["checkout.ts", "readme.md"]);
    // The chat's own "@" menu keeps its exact matching (it does not opt in).
    expect(matchProjectEntries(loose, "ckt")).toEqual([]);
    expect(atFileRows(listing, "", [CART]).rows.map((r) => r.name)).toEqual(["carton.md", "checkout.ts"]);
    expect(atFileRows(listing, "", [], 1)).toEqual({ rows: [{ path: CART, rel: "src/cart.ts", name: "cart.ts" }], more: 2 });
  });

  it("decodeRewindPoints: newest first, no continuation questions", () => {
    expect(
      decodeRewindPoints({
        checkpoints: [
          { turn_id: "a", question: "one", at: "x", files: 1, commands: 0 },
          { turn_id: "b", question: "carry", continuation: true },
          { turn_id: "c", question: "two", at: "y", files: 0, commands: 3 },
        ],
      }).map((p) => [p.turnId, p.files, p.commands]),
    ).toEqual([["c", 0, 3], ["a", 1, 3]]); // (v1.373.0 review: summed from each point to the newest)
    expect(decodeRewindPoints({})).toEqual([]);
  });
});
