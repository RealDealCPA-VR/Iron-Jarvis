/**
 * v1.373.0 (IJ harness part 2, Phase 2) — the review's pins for "/" and "@"
 * in a Build session's box. Each one failed on the Phase 2 commit as it was
 * handed over:
 *
 *  - the box let a message carry more files than one turn reads: four "@"
 *    picks and then a picture, or five pictures, went up, and the turn read
 *    four (the daemon now refuses such a message; the box stops the extra
 *    file first, in the same words);
 *  - Enter pressed while a list was still being read SENT the half-typed
 *    "@word" or "/word" as the message, with no file and no skill;
 *  - a waiting message that never started (its "@" file was deleted
 *    meanwhile) lost its words when the box already held the next message,
 *    and its chips were handed to that next message;
 *  - the "/rewind" list counted only the one message's own changes and said
 *    "from here on" — going back to an earlier message also undoes every
 *    later one — and it called each edit a file;
 *  - with the box near the top of the window the list above it shrank to a
 *    sliver while Enter still picked from it: it opens below the card then,
 *    never over the box;
 *  - a screen reader heard nothing of the list: the box now points at it and
 *    at the highlighted row.
 *
 * The mocks are ij-harness-slash-at-v1373's (copied, not imported).
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
    if (value instanceof Promise) return value;
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
import { AT_MENU_MIN_PX, fitAtMenu } from "@/lib/chatRefsMenuFit";
import { READ_LIMIT_WORDS } from "@/lib/buildComposer";
import { decodeRewindPoints } from "@/lib/buildRewind";

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

/** A GET the test answers by hand. */
function held<T>(value: T) {
  let release!: () => void;
  const p = new Promise<T>((resolve) => {
    release = () => resolve(value);
  });
  return { promise: p, release };
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.gets = {
    "/build/sessions/s1": view(),
    "/build/accounts": new api.ApiError("no such route", 404),
    "/skills": { skills: [{ name: "bulleteer", description: "answer as bullets", source: "user" }] },
    "/build/sessions/s1/skills": { skills: [], note: "" },
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
const NET = { timeout: 5000 };

async function opened() {
  renderSession();
  await screen.findByTestId("harness-empty");
}

const png = (name: string) => new File([new Uint8Array([137, 80, 78, 71])], name, { type: "image/png" });

function answerUploads() {
  let n = 0;
  api.postAnswers["POST /documents/upload"] = () => {
    n += 1;
    return { path: `C:/ij/uploads/up${n}.png`, name: `up${n}.png`, bytes: 4 };
  };
}
const uploads = () => api.calls.filter((c) => c.method === "POST" && c.path === "/documents/upload");

async function pickAt(word: string, name: string) {
  type(`@${word}`);
  await waitFor(() => expect(screen.getAllByTestId("harness-at-file")[0]).toHaveTextContent(name), NET);
  key("Enter");
  await waitFor(() => expect(screen.getAllByTestId("harness-at-chip").map((c) => c.textContent)).toContain(name));
}

/* ---------------------------------------------------------------- tests */

describe("one message carries no more files than one turn reads", () => {
  it("pictures past the limit are not uploaded once '@' files fill it, and the box says why", async () => {
    const many = Array.from({ length: 4 }, (_, i) => ({ name: `part${i}.md`, path: `${FOLDER}\\part${i}.md`, rel: `part${i}.md`, size: 1 }));
    api.gets[FILES_PATH] = { root: FOLDER, files: many };
    answerUploads();
    await opened();
    for (const f of many.slice(0, 3)) await pickAt(f.name.slice(0, 5), f.name);
    // Three "@" files: room for ONE more file in all.
    fireEvent.change(screen.getByTestId("harness-attach-input"), { target: { files: [png("a.png"), png("b.png")] } });
    await waitFor(() => expect(screen.getAllByTestId("harness-attachment")).toHaveLength(1));
    expect(uploads()).toHaveLength(1);
    expect(screen.getByTestId("harness-attach-problem")).toHaveTextContent(READ_LIMIT_WORDS);
    // Full: another picture is not uploaded at all.
    fireEvent.change(screen.getByTestId("harness-attach-input"), { target: { files: [png("c.png")] } });
    await waitFor(() => expect(screen.getByTestId("harness-attach-problem")).toHaveTextContent(READ_LIMIT_WORDS));
    expect(uploads()).toHaveLength(1);
    expect(screen.getAllByTestId("harness-attachment")).toHaveLength(1);
  });

  it("two picks in a row while the first is still going up: the first's files hold their place", async () => {
    answerUploads();
    await opened();
    const input = screen.getByTestId("harness-attach-input");
    fireEvent.change(input, { target: { files: [png("1.png"), png("2.png"), png("3.png")] } });
    fireEvent.change(input, { target: { files: [png("4.png"), png("5.png")] } });
    await waitFor(() => expect(screen.getAllByTestId("harness-attachment")).toHaveLength(4));
    expect(uploads()).toHaveLength(4);
    expect(screen.getByTestId("harness-attach-problem")).toHaveTextContent(READ_LIMIT_WORDS);
  });

  it("five pictures at once: four go up, the fifth is refused in words", async () => {
    answerUploads();
    await opened();
    fireEvent.change(screen.getByTestId("harness-attach-input"), {
      target: { files: ["1", "2", "3", "4", "5"].map((n) => png(`${n}.png`)) },
    });
    await waitFor(() => expect(screen.getAllByTestId("harness-attachment")).toHaveLength(4));
    expect(uploads()).toHaveLength(4);
    expect(screen.getByTestId("harness-attach-problem")).toHaveTextContent(READ_LIMIT_WORDS);
    type("look");
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(turnBody().attachments).toHaveLength(4);
  });
});

describe("Enter never sends a list's half-typed word while the list is still being read", () => {
  it("'@': waits for the folder's files, then picks", async () => {
    const files = held({
      root: FOLDER,
      files: [{ name: "notes.md", path: NOTES, rel: "docs/notes.md", size: 12 }],
    });
    api.gets[FILES_PATH] = () => files.promise;
    await opened();
    type("what is in @notes");
    await screen.findByTestId("harness-at-menu");
    key("Enter");
    // Nothing sent, the words untouched.
    expect(fetchMock).not.toHaveBeenCalled();
    expect(box().value).toBe("what is in @notes");
    await act(async () => {
      files.release();
      await files.promise;
    });
    await waitFor(() => expect(screen.getAllByTestId("harness-at-file")).toHaveLength(1), NET);
    key("Enter");
    expect(await screen.findByTestId("harness-at-chip")).toHaveTextContent("notes.md");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("'/': waits for the skills when no command matches yet", async () => {
    const skills = held({ skills: [{ name: "bulleteer", description: "answer as bullets", source: "user" }] });
    api.gets["/skills"] = () => skills.promise;
    await opened();
    type("/bul");
    await screen.findByTestId("harness-slash-menu");
    key("Enter");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(box().value).toBe("/bul");
    await act(async () => {
      skills.release();
      await skills.promise;
    });
    await waitFor(() => expect(screen.getAllByTestId("harness-slash-skill")).toHaveLength(1), NET);
    key("Enter");
    expect(await screen.findByTestId("harness-skill-chip")).toHaveTextContent("bulleteer");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("a waiting message that never started loses nothing", () => {
  it("its words join the box's, and its chips come back with them", async () => {
    const first = controlledStream();
    fetchMock.mockResolvedValueOnce(first.response);
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "notes.md is not a file in this session's folder." }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await opened();
    type("first");
    key("Enter");
    await screen.findByTestId("harness-stop");
    await pickAt("notes", "notes.md");
    type("then read the notes");
    key("Enter", { ctrlKey: true });
    await screen.findByTestId("harness-queued");
    // The NEXT message is typed while the reply runs.
    type("and fix the cart");
    await act(async () => {
      first.push("done", { reply: "done" });
      first.end();
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await screen.findByText(/notes\.md is not a file/);
    // Both messages' words are in the box: nothing was lost.
    await waitFor(() => expect(box().value).toContain("then read the notes"));
    expect(box().value).toContain("and fix the cart");
    expect(screen.getByTestId("harness-at-chip")).toHaveTextContent("notes.md");
  });

  it("its files go back beside the files the box holds now", async () => {
    const first = controlledStream();
    fetchMock.mockResolvedValueOnce(first.response);
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "notes.md is not a file in this session's folder." }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await opened();
    type("first");
    key("Enter");
    await screen.findByTestId("harness-stop");
    await pickAt("notes", "notes.md");
    type("read the notes");
    key("Enter", { ctrlKey: true });
    await screen.findByTestId("harness-queued");
    // The next message already carries a file of its own.
    await pickAt("cart", "cart.ts");
    await act(async () => {
      first.push("done", { reply: "done" });
      first.end();
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await screen.findByText(/notes\.md is not a file/);
    await waitFor(() =>
      expect(screen.getAllByTestId("harness-at-chip").map((c) => c.textContent)).toEqual(["cart.ts", "notes.md"]),
    );
  });
});

describe("the /rewind list says what going back really undoes", () => {
  it("counts from that message on (later ones too), and calls an edit a change", () => {
    const points = decodeRewindPoints({
      checkpoints: [
        { turn_id: "a", question: "one", at: "x", files: 2, commands: 1 },
        { turn_id: "b", question: "carry", continuation: true, files: 1, commands: 0 },
        { turn_id: "c", question: "two", at: "y", files: 3, commands: 0 },
      ],
    });
    // Newest first; the continuation is not listed but its changes count
    // for every message before it.
    expect(points.map((p) => [p.turnId, p.files, p.commands])).toEqual([
      ["c", 3, 0],
      ["a", 6, 1],
    ]);
  });

  it("on screen: 'file changes', never 'files changed'", async () => {
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
        { turn_id: "t0", question: "Add a cart", at: "2026-10-22T10:00:00", files: 1, commands: 1 },
        { turn_id: "t1", question: "Make it blue", at: "2026-10-22T10:05:00", files: 2, commands: 0 },
      ],
    };
    renderSession();
    await screen.findAllByTestId("harness-reply");
    type("/rew");
    await waitFor(() => expect(screen.getAllByTestId("harness-slash-command")).toHaveLength(1));
    key("Enter");
    const list = await screen.findByTestId("harness-rewind-list");
    await waitFor(() => expect(within(list).getAllByTestId("harness-rewind-point")).toHaveLength(2), NET);
    const [newest, oldest] = within(list).getAllByTestId("harness-rewind-point");
    expect(newest).toHaveTextContent("2 file changes from here on");
    expect(oldest).toHaveTextContent("3 file changes, 1 command from here on");
    expect(list.textContent).not.toMatch(/files? changed/);
  });
});

describe("a list never shrinks to a sliver above the box", () => {
  it("with no room above for a row, it opens BELOW the card — still never over the box", () => {
    // The box scrolled up to the top of a phone's window.
    const tight = fitAtMenu({ cardTop: 70, cardBottom: 190, clipTop: 0, clipBottom: 844, viewportHeight: 844, preferAbove: true });
    expect(tight!.side).toBe("below");
    expect(tight!.maxHeight).toBeGreaterThanOrEqual(AT_MENU_MIN_PX);
    expect(190 + 8 + tight!.maxHeight).toBeLessThanOrEqual(844);
    // Room above for a row: above, as tall as that room (never past the top).
    const above = fitAtMenu({ cardTop: 160, cardBottom: 280, clipTop: 0, clipBottom: 844, viewportHeight: 844, preferAbove: true });
    expect(above).toEqual({ side: "above", maxHeight: 144 });
    // The usual phone case: above, at the cap.
    expect(fitAtMenu({ cardTop: 640, cardBottom: 760, clipTop: 0, clipBottom: 844, viewportHeight: 844, preferAbove: true })).toEqual({
      side: "above",
      maxHeight: 448,
    });
    // No room either side: the bigger one, never past an edge.
    const none = fitAtMenu({ cardTop: 60, cardBottom: 300, clipTop: 0, clipBottom: 340, viewportHeight: 340, preferAbove: true });
    expect(none!.side).toBe("above");
    expect(60 - 8 - none!.maxHeight).toBeGreaterThanOrEqual(0);
  });

  it("the panel takes the side the fit chose", async () => {
    const rect = (top: number, bottom: number) =>
      ({ top, bottom, left: 0, right: 390, width: 390, height: bottom - top, x: 0, y: top, toJSON: () => ({}) }) as DOMRect;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      return this.getAttribute("data-testid") === "harness-composer" ? rect(70, 190) : rect(0, 0);
    });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 844 });
    await opened();
    type("/");
    const menu = await screen.findByTestId("harness-slash-menu");
    await waitFor(() => expect(menu.getAttribute("data-side")).toBe("below"));
    expect(menu.className).toContain("top-full");
    expect(menu.className).not.toContain("bottom-full");
  });
});

describe("a screen reader hears the list", () => {
  it("the box points at the open list and at the highlighted row", async () => {
    await opened();
    type("@");
    await waitFor(() => expect(screen.getAllByTestId("harness-at-file")).toHaveLength(2), NET);
    const listbox = screen.getByRole("listbox", { name: "Files in this folder" });
    expect(listbox.id).not.toBe("");
    expect(box().getAttribute("aria-controls")).toBe(listbox.id);
    expect(box().getAttribute("aria-autocomplete")).toBe("list");
    const rows = within(listbox).getAllByRole("option");
    expect(box().getAttribute("aria-activedescendant")).toBe(rows[0].id);
    key("ArrowDown");
    await waitFor(() => expect(box().getAttribute("aria-activedescendant")).toBe(rows[1].id));
    key("Escape");
    await waitFor(() => expect(box().getAttribute("aria-controls")).toBeNull());
    expect(box().getAttribute("aria-activedescendant")).toBeNull();
  });
});
