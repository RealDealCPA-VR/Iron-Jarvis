/**
 * v1.371.0 — pictures in the IJ harness composer, and the Browser pane's
 * address with every message. The mocks and fixtures are
 * ij-harness-session-v1369's (copied, not imported: a test file is not a
 * module other tests import).
 *
 * ORIGINAL HEADER (v1.369.0, IJ harness Phase 6) — one Build SESSION on the screen
 * (components/harness/HarnessSession), driven through the REAL chat stream
 * (lib/useChatStream) against a fetch double, and the chat page's own pieces.
 *
 *  - the transcript renders with the chat's pieces (work line, receipt);
 *  - a message goes to the session's OWN turn route with a minted turn id,
 *    the screen NEVER saves the session's messages, and it adopts the lines
 *    the daemon wrote when the turn ends;
 *  - the model's own question (`ask_user`) takes the composer's place and is
 *    answered through `POST /build/sessions/{id}/answer`;
 *  - the plan door (`exit_plan_mode`, no arguments) shows the reply above as
 *    its plan, with Allow once and Deny only;
 *  - an `account_limit` frame shows the limit card; Resume when ready arms
 *    the wait (Cancel while it waits), Choose account continues on another;
 *  - a steer note the turn never read goes back in the box — watched (the
 *    done frame's list) AND unwatched (the turn ended while nobody looked);
 *  - a session already answering is rejoined; Stop posts the turn's stop;
 *  - an Iron Jarvis without the account list gets no account chip.
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
import { resetWords } from "@/lib/buildAccounts";

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

const userLine = (content: string, turnId = "t0") => ({ role: "user", content, turnId, at: "2026-10-22T10:00:00" });
const replyLine = (content: string, turnId = "t0", extra: Record<string, unknown> = {}) => ({
  role: "assistant",
  content,
  turnId,
  route: { provider: "mock", model: "mock-1", reason: "mock" },
  toolsUsed: ["write_file"],
  steps: [{ name: "write_file", ok: true, ms: 12 }],
  timing: { startedAt: 1, firstTokenAt: 2, endedAt: 3 },
  ...extra,
});

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

function renderSession(extra: Partial<React.ComponentProps<typeof HarnessSession>> = {}) {
  return render(
    <HarnessSession
      threadId="s1"
      terminal={terminal}
      listRunning={false}
      onChanged={() => {}}
      onGone={() => {}}
      {...extra}
    />,
  );
}

const box = () => screen.getByTestId("harness-input") as HTMLTextAreaElement;
async function send(text: string) {
  fireEvent.change(box(), { target: { value: text } });
  fireEvent.keyDown(box(), { key: "Enter" });
}

/* ---------------------------------------------------------------- tests */

const UP = "C:/ij/uploads/";
const uploads = () => api.calls.filter((c) => c.method === "POST" && c.path === "/documents/upload");
const turnBody = (i = 0) => JSON.parse(String((fetchMock.mock.calls[i] as unknown as [string, RequestInit])[1].body));
const png = (name = "shot.png") => new File([new Uint8Array([137, 80, 78, 71])], name, { type: "image/png" });

function answerUploads() {
  api.postAnswers["POST /documents/upload"] = () => {
    const body = api.calls.filter((c) => c.path === "/documents/upload").at(-1)?.body as { filename: string };
    return { path: UP + body.filename, name: body.filename, bytes: 4 };
  };
}

describe("a picture goes with the message", () => {
  it("the paperclip uploads a file, shows it as a thumbnail, and the turn carries its path", async () => {
    answerUploads();
    renderSession();
    await screen.findByTestId("harness-empty");
    const input = screen.getByTestId("harness-attach-input") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [png("cart.png")] } });
    const chip = await screen.findByTestId("harness-attachment");
    expect(chip).toHaveTextContent("cart.png");
    expect(within(chip).getByTestId("harness-attachment-thumb").getAttribute("src")).toContain(
      encodeURIComponent(UP + "cart.png"),
    );
    expect((uploads()[0].body as { filename: string }).filename).toBe("cart.png");
    await send("Make the button match this");
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = turnBody();
    expect(body.text).toBe("Make the button match this");
    expect(body.attachments).toEqual([UP + "cart.png"]);
    // The chips are gone once the message went.
    await waitFor(() => expect(screen.queryByTestId("harness-attachment")).toBeNull());
  });

  it("a picture alone is a message", async () => {
    answerUploads();
    renderSession();
    await screen.findByTestId("harness-empty");
    fireEvent.change(screen.getByTestId("harness-attach-input"), { target: { files: [png()] } });
    await screen.findByTestId("harness-attachment");
    const sendBtn = screen.getByTestId("harness-send");
    await waitFor(() => expect(sendBtn).not.toBeDisabled());
    fireEvent.click(sendBtn);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(turnBody().text).toBe("");
    expect(turnBody().attachments).toEqual([UP + "shot.png"]);
  });

  it("a pasted picture is attached under its own name, and pasted text is just typed", async () => {
    answerUploads();
    renderSession();
    await screen.findByTestId("harness-empty");
    fireEvent.paste(box(), { clipboardData: { files: [png("image.png"), png("image.png")] } });
    await waitFor(() => expect(screen.getAllByTestId("harness-attachment")).toHaveLength(2));
    // The chip says what it is, not the stamp it was uploaded under.
    for (const chip of screen.getAllByTestId("harness-attachment")) expect(chip).toHaveTextContent("Pasted picture");
    const names = uploads().map((c) => (c.body as { filename: string }).filename);
    expect(names).toHaveLength(2);
    // Every clipboard picture arrives as "image.png": each gets its own name,
    // so the second never replaces the first one's bytes.
    for (const n of names) expect(n).toMatch(/^pasted-\d{8}-\d{6}-\d+-[a-z0-9]+\.png$/);
    expect(new Set(names).size).toBe(2);
    // A text paste carries no files and attaches nothing.
    fireEvent.paste(box(), { clipboardData: { files: [] } });
    expect(uploads()).toHaveLength(2);
  });

  it("Enter while a picture is still going up waits for it, then sends it with the words", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    api.postAnswers["POST /documents/upload"] = () =>
      gate.then(() => ({ path: UP + "slow.png", name: "slow.png", bytes: 4 }));
    renderSession();
    await screen.findByTestId("harness-empty");
    fireEvent.change(screen.getByTestId("harness-attach-input"), { target: { files: [png("slow.png")] } });
    await screen.findByTestId("harness-attachments-uploading");
    await send("Use this picture");
    // Nothing goes while the upload is out.
    await new Promise((r) => setTimeout(r, 30));
    expect(fetchMock).not.toHaveBeenCalled();
    await act(async () => release());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(turnBody().text).toBe("Use this picture");
    expect(turnBody().attachments).toEqual([UP + "slow.png"]);
  });

  it("a file too large is refused in words and nothing is uploaded", async () => {
    renderSession();
    await screen.findByTestId("harness-empty");
    const big = new File([new Uint8Array(1)], "huge.png", { type: "image/png" });
    Object.defineProperty(big, "size", { value: 21 * 1024 * 1024 });
    fireEvent.change(screen.getByTestId("harness-attach-input"), { target: { files: [big] } });
    expect(await screen.findByTestId("harness-attach-problem")).toHaveTextContent("too large");
    expect(uploads()).toHaveLength(0);
  });

  it("a sent message shows its pictures as thumbnails", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [
        { ...userLine(""), attachmentPaths: [UP + "cart.png", UP + "notes.pdf"] },
        replyLine("Matched it."),
      ],
    });
    renderSession();
    await screen.findByText("Matched it.");
    // A picture alone has no empty bubble; the picture is shown, the PDF by name.
    expect(screen.queryByTestId("harness-user-bubble")).toBeNull();
    const sent = screen.getByTestId("harness-sent-attachments");
    expect(within(sent).getAllByTestId("harness-sent-thumb")).toHaveLength(1);
    expect(sent).toHaveTextContent("notes.pdf");
  });
});

describe("the agent knows which page the Browser pane shows", () => {
  it("sends the pane's address with the message", async () => {
    localStorage.setItem("ij.build.preview.s1", "http://localhost:5173/cart");
    renderSession();
    await screen.findByTestId("harness-empty");
    await send("How does the cart look?");
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(turnBody().preview_url).toBe("http://localhost:5173/cart");
  });

  it("never sends an address that is not on this PC or the user's network", async () => {
    localStorage.setItem("ij.build.preview.s1", "https://example.com/");
    renderSession();
    await screen.findByTestId("harness-empty");
    await send("Hello");
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect("preview_url" in turnBody()).toBe(false);
  });
});

describe("review (v1.371.0): a held send, a pasted file, a text paste", () => {
  function slowUpload() {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    api.postAnswers["POST /documents/upload"] = () =>
      gate.then(() => ({ path: UP + "slow.png", name: "slow.png", bytes: 4 }));
    return () => release();
  }

  it("leaving the session while a picture goes up drops the send it was holding", async () => {
    const release = slowUpload();
    renderSession();
    await screen.findByTestId("harness-empty");
    fireEvent.change(screen.getByTestId("harness-attach-input"), { target: { files: [png("slow.png")] } });
    await screen.findByTestId("harness-attachments-uploading");
    await send("Use this picture");
    // The user opens another session: this composer is gone.
    cleanup();
    await act(async () => {
      release();
      await new Promise((r) => setTimeout(r, 50));
    });
    // Nothing went to the session the user left (it would have gone WITHOUT
    // the picture: the unmounted composer never saw the upload land).
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("Enter pressed twice while a picture goes up sends the message once", async () => {
    const release = slowUpload();
    renderSession();
    await screen.findByTestId("harness-empty");
    fireEvent.change(screen.getByTestId("harness-attach-input"), { target: { files: [png("slow.png")] } });
    await screen.findByTestId("harness-attachments-uploading");
    fireEvent.change(box(), { target: { value: "Use this picture" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    fireEvent.keyDown(box(), { key: "Enter" });
    await act(async () => release());
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(turnBody().attachments).toEqual([UP + "slow.png"]);
  });

  it("a held send waits for EVERY picture still going up, not just the first", async () => {
    const gates: Record<string, () => void> = {};
    api.postAnswers["POST /documents/upload"] = () => {
      const body = api.calls.filter((c) => c.path === "/documents/upload").at(-1)?.body as { filename: string };
      return new Promise((r) => {
        gates[body.filename] = () => r({ path: UP + body.filename, name: body.filename, bytes: 4 });
      });
    };
    renderSession();
    await screen.findByTestId("harness-empty");
    fireEvent.change(screen.getByTestId("harness-attach-input"), { target: { files: [png("one.png")] } });
    fireEvent.change(screen.getByTestId("harness-attach-input"), { target: { files: [png("two.png")] } });
    await waitFor(() => expect(Object.keys(gates).sort()).toEqual(["one.png", "two.png"]));
    await send("Use both");
    await act(async () => gates["one.png"]());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    // The first landed, the second is still going up: nothing goes yet.
    expect(fetchMock).not.toHaveBeenCalled();
    await act(async () => gates["two.png"]());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(turnBody().attachments).toEqual([UP + "one.png", UP + "two.png"]);
  });

  it("a pasted file that is not a picture keeps its own name and says what it is", async () => {
    answerUploads();
    renderSession();
    await screen.findByTestId("harness-empty");
    const notes = new File(["# notes"], "notes.md", { type: "" });
    const tool = new File(["print(1)"], "tool.py", { type: "text/x-python" });
    fireEvent.paste(box(), { clipboardData: { files: [notes, tool] } });
    await waitFor(() => expect(screen.getAllByTestId("harness-attachment")).toHaveLength(2));
    const names = uploads().map((c) => (c.body as { filename: string }).filename);
    // A copied file's type is no guide to its extension: an untyped notes.md
    // went up as a .png the turn then failed to send as a picture.
    expect(names).toEqual(["notes.md", "tool.py"]);
    const chips = screen.getAllByTestId("harness-attachment").map((c) => c.textContent);
    expect(chips.some((t) => t?.includes("notes.md"))).toBe(true);
    expect(chips.some((t) => t?.includes("Pasted picture"))).toBe(false);
  });

  it("a text paste is left to the browser", async () => {
    renderSession();
    await screen.findByTestId("harness-empty");
    // dispatchEvent answers false when the handler called preventDefault.
    expect(fireEvent.paste(box(), { clipboardData: { files: [] } })).toBe(true);
    expect(uploads()).toHaveLength(0);
  });

  it("a refused file is said where a screen reader hears it", async () => {
    renderSession();
    await screen.findByTestId("harness-empty");
    const big = new File([new Uint8Array(1)], "huge.png", { type: "image/png" });
    Object.defineProperty(big, "size", { value: 21 * 1024 * 1024 });
    fireEvent.change(screen.getByTestId("harness-attach-input"), { target: { files: [big] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("too large");
  });
});
