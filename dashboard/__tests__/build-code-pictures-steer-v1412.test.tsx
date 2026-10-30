/**
 * v1.412.0 — Build › Code: pictures, the mode at any time, and a note while
 * it works. The user: "I cant copy and past images into the build modules
 * </> code … the permission blocker does not allow change to auto-- allow
 * all, should be changeable mid session and the ability to steer work."
 *
 * Driven through the REAL view (BuildCodeView, StartComposer, SessionPane,
 * CodeConversation, CodeAskCard, ModeChip) and the chat's real upload hook
 * (components/harness/HarnessAttachments); only the network (lib/api) and the
 * stream reader (lib/useChatStream's streamSSE) are stand-ins — the same
 * stand-ins as build-app-view-v1409.
 *
 *   - a pasted picture goes up (its own name), shows as a thumbnail, and its
 *     path rides the turn — in an open session AND in the start composer
 *     (the first message); a dropped file and the paperclip too; a picture
 *     alone is a message; Send waits for a picture still going up;
 *   - the mode chip is reachable while the tool waits on a card (it used to
 *     hide with the composer), Full access still takes two presses there, and
 *     every mode says what it does;
 *   - Enter while it works sends a NOTE (POST …/steer), never a second turn;
 *     the note is drawn where it was sent and says when it was read; one the
 *     tool never read comes back to the box with words; with no reply running
 *     after all it goes as a message.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
  patches: [] as { path: string; body: unknown }[],
  postReply: {} as Record<string, unknown>,
  postFail: {} as Record<string, { status: number; message: string; body?: unknown }>,
  streams: [] as { path: string; body: unknown }[],
  scripts: [] as { event: string; data: Record<string, unknown> }[][],
  lastWatch: null as null | { onFrame?: (event: string, data: Record<string, unknown>) => void },
  hold: null as Promise<void> | null,
  release: null as (() => void) | null,
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    body?: unknown;
    constructor(message: string, status = 0, body?: unknown) {
      super(message);
      this.status = status;
      this.body = body;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: FakeApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    get: (path: string) => {
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(typeof r === "function" ? (r as () => unknown)() : r);
    },
    post: (path: string, body?: unknown) => {
      H.posts.push({ path, body });
      const fail = H.postFail[path];
      if (fail) return Promise.reject(new FakeApiError(fail.message, fail.status, fail.body));
      const r = H.postReply[path];
      return typeof r === "function" ? (r as () => Promise<unknown>)() : Promise.resolve(r ?? {});
    },
    patch: (path: string, body?: unknown) => {
      H.patches.push({ path, body });
      return Promise.resolve(H.postReply[`PATCH ${path}`] ?? {});
    },
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* (
    path: string,
    body: unknown,
    signal?: AbortSignal,
    watch?: { onFrame?: (event: string, data: Record<string, unknown>) => void },
  ) {
    H.streams.push({ path, body });
    H.lastWatch = watch ?? null;
    const frames = H.scripts.shift() ?? [];
    for (const f of frames) {
      if (signal?.aborted) return;
      watch?.onFrame?.(f.event, f.data);
      await Promise.resolve();
    }
    if (H.hold) await H.hold;
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { STEER_RETURNED_WORDS, decodeCodeEvent, foldConversation, steerWords } from "@/lib/buildApp";

const CLAUDE = {
  id: "claude-code",
  label: "Claude Code",
  installed: true,
  signed_in: true,
  version: "2.1.311",
  modes: [
    { id: "ask", label: "Ask first", dangerous: false, about: "Asks before it changes a file or runs a command." },
    { id: "auto", label: "Auto", dangerous: false, about: "Claude Code's own safety check allows or refuses each action without asking you; a few risky ones still ask." },
    { id: "full", label: "Full access", dangerous: true, about: "Runs everything without asking." },
  ],
  hidden_modes: [],
  models: [{ id: "", label: "Claude Code's default" }],
  efforts: [],
  accounts: [],
  commands: [],
};

const sess = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  harness: "claude-code",
  folder: "C:\\work\\shop",
  title: `Session ${id}`,
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

const UP = "/documents/upload";
const PNG_PATH = "C:\\Users\\me\\.ironjarvis\\uploads\\pasted.png";

function seed(sessions: Record<string, unknown>[] = []) {
  H.gets = {
    "/build/app/harnesses": { harnesses: [CLAUDE] },
    "/build/app/sessions": { sessions },
    "/editor/options": { editors: [], preferred: "" },
  };
  H.postReply[UP] = { path: PNG_PATH, name: "pasted.png" };
}

function settled(id = "a1", over: Record<string, unknown> = {}) {
  seed([sess(id, over)]);
  H.gets[`/build/app/sessions/${id}`] = { session: sess(id, over), events: [], running: false, pending: [] };
}

/** A session whose reply is RUNNING (its stream stays open). */
function working(id = "a1", over: Record<string, unknown> = {}, extra: Record<string, unknown>[] = []) {
  const events = [{ type: "user", at: "x", text: "Count to thirty", turn: "turn_1" }, ...extra];
  seed([sess(id, over)]);
  H.gets[`/build/app/sessions/${id}`] = { session: sess(id, over), events, running: "turn_1", pending: [] };
  H.scripts = [events.map((e) => ({ event: String(e.type), data: e }))];
  H.hold = new Promise<void>((res) => {
    H.release = res;
  });
}

const png = (name = "image.png") => new File([new Uint8Array([137, 80, 78, 71, 1, 2, 3])], name, { type: "image/png" });
const send = (id = "a1") => H.streams.find((s) => s.path === `/build/app/sessions/${id}/turn`);
const uploads = () => H.posts.filter((p) => p.path === UP);
const frame = (data: Record<string, unknown>) =>
  act(() => {
    H.lastWatch?.onFrame?.(String(data.type), data);
    return new Promise<void>((r) => setTimeout(r, 30));
  });

beforeEach(() => {
  H.posts = [];
  H.patches = [];
  H.postReply = {};
  H.postFail = {};
  H.streams = [];
  H.scripts = [];
  H.hold = null;
  H.release = null;
  H.lastWatch = null;
  seed();
});

afterEach(() => {
  H.release?.();
  cleanup();
});

/* ===================================================== pictures */

describe("pictures in an open session", () => {
  it("a pasted picture goes up under its own name, shows as a thumbnail, and rides the message", async () => {
    settled();
    H.scripts = [[]];
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box");
    fireEvent.paste(box, { clipboardData: { files: [png()] } });
    await waitFor(() => expect(uploads()).toHaveLength(1));
    expect((uploads()[0].body as { filename: string }).filename).toMatch(/^pasted-\d{8}-\d{6}-1-[a-z0-9]+\.png$/);
    expect(await screen.findByTestId("harness-attachment-thumb")).toBeInTheDocument();
    expect(screen.getByTestId("harness-attachment").textContent).toContain("Pasted picture");
    fireEvent.change(box, { target: { value: "what is this?" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(send()).toBeTruthy());
    expect(send()?.body).toMatchObject({ text: "what is this?", files: [PNG_PATH] });
    // Sent: the box is empty again, and the message shows its picture.
    await waitFor(() => expect(screen.queryByTestId("harness-attachment")).toBeNull());
    const sentRow = screen.getAllByTestId("code-user").at(-1)!;
    expect(within(sentRow).getByTestId("harness-sent-thumb")).toBeInTheDocument();
  });

  it("a text paste is left to the browser (nothing uploaded)", async () => {
    settled();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box");
    expect(fireEvent.paste(box, { clipboardData: { files: [] } })).toBe(true);
    expect(uploads()).toHaveLength(0);
  });

  it("a picture alone is a message", async () => {
    settled();
    H.scripts = [[]];
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box");
    expect(screen.getByTestId("code-send")).toBeDisabled();
    fireEvent.paste(box, { clipboardData: { files: [png()] } });
    await screen.findByTestId("harness-attachment-thumb");
    expect(screen.getByTestId("code-send")).not.toBeDisabled();
    fireEvent.click(screen.getByTestId("code-send"));
    await waitFor(() => expect(send()?.body).toMatchObject({ text: "", files: [PNG_PATH] }));
  });

  it("a dropped file and the paperclip upload too", async () => {
    settled();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-box");
    fireEvent.drop(screen.getByTestId("code-composer"), { dataTransfer: { files: [png("drop.png")], types: ["Files"] } });
    await waitFor(() => expect(uploads()).toHaveLength(1));
    expect((uploads()[0].body as { filename: string }).filename).toBe("drop.png");
    fireEvent.change(screen.getByTestId("harness-attach-input"), { target: { files: [png("clip.png")] } });
    await waitFor(() => expect(uploads()).toHaveLength(2));
  });

  it("Enter while a picture is still going up waits for it, then sends it with the words", async () => {
    settled();
    H.scripts = [[]];
    let finish: () => void = () => {};
    H.postReply[UP] = () =>
      new Promise((res) => {
        finish = () => res({ path: PNG_PATH, name: "pasted.png" });
      });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box");
    fireEvent.paste(box, { clipboardData: { files: [png()] } });
    await screen.findByTestId("harness-attachments-uploading");
    fireEvent.change(box, { target: { value: "look" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await act(() => new Promise((r) => setTimeout(r, 30)));
    expect(send()).toBeUndefined();
    await act(async () => finish());
    await waitFor(() => expect(send()?.body).toMatchObject({ text: "look", files: [PNG_PATH] }));
  });
});

describe("pictures in the start composer (the first message)", () => {
  it("a pasted picture goes with the first message", async () => {
    H.postReply["/build/app/sessions"] = sess("n1");
    H.gets["/build/app/sessions/n1"] = { session: sess("n1"), events: [], running: false, pending: [] };
    H.scripts = [[]];
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain("Claude Code"));
    const box = screen.getByTestId("code-start-box");
    fireEvent.paste(box, { clipboardData: { files: [png()] } });
    await screen.findByTestId("harness-attachment-thumb");
    // A picture alone may start the session.
    expect(screen.getByTestId("code-start-send")).not.toBeDisabled();
    fireEvent.change(box, { target: { value: "make the page look like this" } });
    fireEvent.click(screen.getByTestId("code-start-send"));
    await screen.findByTestId("code-session");
    await waitFor(() => expect(send("n1")?.body).toMatchObject({ text: "make the page look like this", files: [PNG_PATH] }));
  });
});

/* ===================================================== the mode */

describe("the mode at any time", () => {
  function asking() {
    const ev = [
      { type: "user", at: "x", text: "run it", turn: "turn_1" },
      { type: "approval", at: "x", turn: "turn_1", request_id: "ask_1", kind: "command", title: "Run echo hi", command: "echo hi", can_session: false, once_only: true },
    ];
    seed([sess("a1")]);
    H.gets["/build/app/sessions/a1"] = { session: sess("a1"), events: ev, running: "turn_1", pending: [] };
    H.scripts = [ev.map((e) => ({ event: e.type, data: e }))];
    H.hold = new Promise<void>((res) => {
      H.release = res;
    });
  }

  it("while a card waits, the mode chip is beside it (the composer's is hidden) and Full access takes two presses", async () => {
    asking();
    H.postReply["PATCH /build/app/sessions/a1"] = { session: sess("a1", { mode: "full" }), applied: { mode: "now" } };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-ask");
    expect(screen.getByTestId("code-composer")).not.toBeVisible();
    // Reachable: shown (not just in the page) and not disabled.
    expect(screen.getByTestId("code-ask-mode-button")).toBeVisible();
    expect(screen.getByTestId("code-ask-mode-button")).not.toBeDisabled();
    fireEvent.click(screen.getByTestId("code-ask-mode-button"));
    const full = screen.getByTestId("code-ask-mode-option-full");
    fireEvent.click(full);
    expect(H.patches).toHaveLength(0);
    await act(() => new Promise((r) => setTimeout(r, 450)));
    fireEvent.click(screen.getByTestId("code-ask-mode-option-full"));
    await waitFor(() => expect(H.patches[0]?.body).toEqual({ mode: "full", confirm: true }));
    // The card stays: it was asked under the old mode.
    expect(screen.getByTestId("code-ask")).toBeInTheDocument();
  });

  it("every mode says what it does; Auto is one press", async () => {
    settled();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-box");
    fireEvent.click(screen.getByTestId("code-mode-chip-button"));
    expect(screen.getByTestId("code-mode-chip-about-auto").textContent).toContain("safety check");
    expect(screen.getByTestId("code-mode-chip-about-ask").textContent).toBe("Asks before it changes a file or runs a command.");
    fireEvent.click(screen.getByTestId("code-mode-chip-option-auto"));
    await waitFor(() => expect(H.patches[0]?.body).toEqual({ mode: "auto" }));
  });

  it("the mode chip is never disabled while a reply runs", async () => {
    working();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    expect(screen.getByTestId("code-mode-chip-button")).not.toBeDisabled();
  });
});

/* ===================================================== a note while it works */

describe("a note while it works (steer)", () => {
  it("Enter while it works posts a NOTE to the running reply — never a second turn — and the box empties", async () => {
    working();
    H.postReply["/build/app/sessions/a1/steer"] = { ok: true, id: "note-1", state: "sent", how: "step" };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    const box = screen.getByTestId("code-box");
    expect(box.getAttribute("placeholder")).toContain("type a note to steer it (Enter)");
    fireEvent.change(box, { target: { value: "also add a test" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(H.posts.find((p) => p.path === "/build/app/sessions/a1/steer")?.body).toEqual({ text: "also add a test" }));
    expect(H.streams.filter((s) => s.path.endsWith("/turn"))).toHaveLength(0);
    await waitFor(() => expect((box as HTMLTextAreaElement).value).toBe(""));
    // The daemon's events: drawn where it was sent, then read.
    await frame({ type: "steer", at: "x", turn: "turn_1", id: "note-1", state: "sent", text: "also add a test", how: "Claude Code reads it at its next step, or right after this reply." });
    const note = await screen.findByTestId("code-steer");
    expect(note.textContent).toContain("also add a test");
    expect(within(note).getByTestId("code-steer-state").textContent).toBe("Claude Code reads it at its next step, or right after this reply.");
    await frame({ type: "steer", at: "x", turn: "turn_1", id: "note-1", state: "read", text: "also add a test" });
    expect(within(screen.getByTestId("code-steer")).getByTestId("code-steer-state").textContent).toBe("Read by Claude Code.");
    expect(screen.getAllByTestId("code-steer")).toHaveLength(1);
  });

  it("a note with a picture: the paste goes up and rides the note", async () => {
    working();
    H.postReply["/build/app/sessions/a1/steer"] = { ok: true, id: "note-2", state: "sent" };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    const box = screen.getByTestId("code-box");
    fireEvent.paste(box, { clipboardData: { files: [png()] } });
    await screen.findByTestId("harness-attachment-thumb");
    fireEvent.click(screen.getByTestId("code-steer-send"));
    await waitFor(() =>
      expect(H.posts.find((p) => p.path === "/build/app/sessions/a1/steer")?.body).toEqual({ text: "", files: [PNG_PATH] }),
    );
  });

  it("a note the tool never read comes back to the box, with words", async () => {
    working();
    H.postReply["/build/app/sessions/a1/steer"] = { ok: true, id: "note-3", state: "sent" };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    const box = screen.getByTestId("code-box") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "then deploy" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(box.value).toBe(""));
    await frame({ type: "steer", at: "x", turn: "turn_1", id: "note-3", state: "sent", text: "then deploy" });
    await frame({ type: "steer", at: "x", turn: "turn_1", id: "note-3", state: "returned", text: "then deploy" });
    await waitFor(() => expect(box.value).toBe("then deploy"));
    expect(screen.getByTestId("code-info").textContent).toBe(STEER_RETURNED_WORDS("Claude Code"));
    expect(within(screen.getByTestId("code-steer")).getByTestId("code-steer-state").textContent).toBe(STEER_RETURNED_WORDS("Claude Code"));
    // Said once: the same event again changes nothing.
    fireEvent.change(box, { target: { value: "" } });
    await frame({ type: "steer", at: "x", turn: "turn_1", id: "note-3", state: "returned", text: "then deploy" });
    expect(box.value).toBe("");
  });

  it("review: a note returned BEFORE its own answer arrived still comes back to the box", async () => {
    // The daemon writes `sent` then `returned` when the reply ends right after
    // the note went out; the stream can deliver both before the POST answers.
    working();
    let answer: (v: unknown) => void = () => {};
    H.postReply["/build/app/sessions/a1/steer"] = () => new Promise((res) => { answer = res; });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    const box = screen.getByTestId("code-box") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "then deploy" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(box.value).toBe(""));
    await frame({ type: "steer", at: "x", turn: "turn_1", id: "note-9", state: "sent", text: "then deploy" });
    await frame({ type: "steer", at: "x", turn: "turn_1", id: "note-9", state: "returned", text: "then deploy" });
    await act(async () => {
      answer({ ok: true, id: "note-9", state: "sent" });
      await new Promise((r) => setTimeout(r, 30));
    });
    await waitFor(() => expect(box.value).toBe("then deploy"));
    expect(screen.getByTestId("code-info").textContent).toBe(STEER_RETURNED_WORDS("Claude Code"));
  });

  it("a returned note's picture comes back to the box too", async () => {
    working();
    H.postReply["/build/app/sessions/a1/steer"] = { ok: true, id: "note-4", state: "sent" };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    fireEvent.paste(screen.getByTestId("code-box"), { clipboardData: { files: [png()] } });
    await screen.findByTestId("harness-attachment-thumb");
    fireEvent.click(screen.getByTestId("code-steer-send"));
    await waitFor(() => expect(screen.queryByTestId("harness-attachment")).toBeNull());
    await frame({ type: "steer", at: "x", turn: "turn_1", id: "note-4", state: "returned", text: "", files: [PNG_PATH] });
    expect(await screen.findByTestId("harness-attachment-thumb")).toBeInTheDocument();
  });

  it("no reply running after all (409 not_working): the words go as a message", async () => {
    working();
    H.postFail["/build/app/sessions/a1/steer"] = {
      status: 409,
      message: "Claude Code is not working on anything right now — send it as a message.",
      body: { detail: "x", code: "not_working" },
    };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    const box = screen.getByTestId("code-box");
    fireEvent.change(box, { target: { value: "next thing" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(send()?.body).toMatchObject({ text: "next thing" }));
  });

  it("a refused note stays in the box and the reason is said", async () => {
    working();
    H.postFail["/build/app/sessions/a1/steer"] = {
      status: 409,
      message: "Codex had already finished, so your note did not reach it — send it as a message.",
    };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    const box = screen.getByTestId("code-box") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "too late?" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(screen.getByTestId("code-note").textContent).toContain("did not reach it"));
    expect(box.value).toBe("too late?");
  });
});

describe("the note in the contract", () => {
  it("decodes and folds one row per note, at its place, with its newest state", () => {
    const evs = [
      { type: "user", at: "x", text: "go" },
      { type: "text_done", at: "x", item: "m1", text: "working" },
      { type: "steer", at: "x", id: "n1", state: "sent", text: "also this", how: "Pi reads it after its current step." },
      { type: "text_done", at: "x", item: "m2", text: "done, also this" },
      { type: "steer", at: "x", id: "n1", state: "read", text: "also this" },
      { type: "steer", at: "x", id: "n1", state: "bogus", text: "x" },
    ].map((e) => decodeCodeEvent(e));
    expect(evs[5]).toBeNull();
    const conv = foldConversation(evs.filter((e): e is NonNullable<typeof e> => !!e));
    expect(conv.items.map((i) => i.kind)).toEqual(["user", "text", "steer", "text"]);
    const note = conv.items[2] as { state: string; how: string };
    expect(note.state).toBe("read");
    expect(note.how).toBe("Pi reads it after its current step.");
    expect(steerWords("sent", "", "Codex")).toBe("Sent — Codex reads it at its next step.");
  });
});
