/**
 * v1.544.0 — COMMENT on a changed block, riding the next message (Feature 3
 * of docs/BUILD-CODE-T3-PLAN-2026-11-19.md; daemon: harness/code_comments.py).
 *
 *   - the store (lib/codeComments): add, the limit of 20, the cut quote and
 *     words, remove, take and put back, kept across a reload, "changed since";
 *   - the Changes component: Comment only with the flag, ONE dialog even in
 *     the wide view, no save without words, Enter and Escape stay in the
 *     dialog, and neither the dialog nor a saved comment redraws a file;
 *   - the chips above the box, in BOTH Code views;
 *   - Build › Code: the turn's body carries `comments` and the chips clear; a
 *     refused send puts them back; a waiting message carries them; a note
 *     leaves them; a rewind returns them; an older Iron Jarvis (no
 *     `takes_comments`) shows no button and no chips and sends none;
 *   - Chat › Code: the same, through the real view and the real stream;
 *   - the sent message shows its comments;
 *   - 30 streamed words redraw the Changes rows zero times.
 *
 * Stand-ins: the network (lib/api, fetch), Build › Code's stream reader
 * (`streamSSE`; Chat › Code keeps the real hook) and the folder tree. Two
 * pass-through stubs COUNT draws: DiffView's `UnifiedRows` (drawn on every
 * draw of a file's rows) and the review's Open-in-editor press (drawn on
 * every draw of a file).
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

// A wait here is for the thing asserted (a dialog, a chip, a request). Its
// 1 s default measures the RUNNER as much as the code: run beside other
// files on a busy PC, one such wait expired with nothing wrong (twice in
// about 75 runs of this file, on two different tests, each a stall of the
// whole worker). 5 s keeps a wrong expectation failing with its own message,
// well inside the 15 s a test gets (vitest.config.ts says why that is 15).
configure({ asyncUtilTimeout: 5_000 });

const H = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    body?: unknown;
    constructor(message: string, status = 0, body?: unknown) {
      super(message);
      this.status = status;
      this.name = "ApiError";
      if (body !== undefined) this.body = body;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown>,
    sent: [] as { method: string; path: string; body: unknown }[],
    reply: {} as Record<string, unknown>,
    fail: {} as Record<string, { status: number; message: string; body?: unknown }>,
    /** Build › Code's streams, in the order opened. */
    streams: [] as { path: string; body: unknown }[],
    scripts: [] as (
      | { event: string; data: Record<string, unknown> }
      | { yield: Record<string, unknown> }
      /** What the reader yields once the test lets it (`H.late`), even when
       *  the pane that asked has moved on: an answer that was already in. */
      | { late: Record<string, unknown> }
    )[][],
    /** Resolves the wait of a `{ late }` entry. */
    late: null as Promise<void> | null,
    /** An answer of lib/api held until the test lets it go (by path). */
    gate: {} as Record<string, Promise<void> | undefined>,
    lastWatch: null as null | { onFrame?: (event: string, data: Record<string, unknown>) => void },
    hold: null as Promise<void> | null,
    release: null as (() => void) | null,
    /** How many times a file's rows / a file's own row drew. */
    draws: { rows: 0, files: 0 },
    /** The same, per press: `review-open-N` is drawn on every draw of file
     *  N's own row, `review-hunk-open-N` on every draw of its LINES. */
    byId: {} as Record<string, number>,
  };
});

vi.mock("@/lib/api", () => {
  const get = (path: string) => {
    const r = H.gets[path];
    if (r === undefined) return Promise.reject(new H.ApiError(`unmocked GET ${path}`, 404));
    if (r instanceof Error) return Promise.reject(r);
    return Promise.resolve(JSON.parse(JSON.stringify(r)));
  };
  const send = (method: string) => async (path: string, body?: unknown) => {
    H.sent.push({ method, path, body });
    const held = H.gate[path];
    if (held) await held;
    const fail = H.fail[path];
    if (fail) return Promise.reject(new H.ApiError(fail.message, fail.status, fail.body));
    const r = H.reply[path];
    return Promise.resolve(r === undefined ? {} : JSON.parse(JSON.stringify(r)));
  };
  return {
    ApiError: H.ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    flattenDetail: (d: unknown) => String(d),
    get,
    post: send("POST"),
    put: send("PUT"),
    patch: send("PATCH"),
    del: send("DELETE"),
  };
});

// Build › Code reads its stream through `streamSSE`: a stand-in. Chat › Code's
// hook (`useChatStream`) stays REAL and reads the fetch double below.
vi.mock("@/lib/useChatStream", async (orig) => {
  const real = await orig<typeof import("@/lib/useChatStream")>();
  return {
    ...real,
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
        if ("late" in f) {
          if (H.late) await H.late;
          yield f.late;
          return;
        }
        if (signal?.aborted) return;
        if ("yield" in f) {
          yield f.yield;
          return;
        }
        watch?.onFrame?.(f.event, f.data);
        await Promise.resolve();
      }
      if (H.hold) await H.hold;
    },
  };
});

const stable = vi.hoisted(() => ({
  daemon: {
    online: true,
    checking: false,
    health: { default_provider: "mock", default_model: "mock-1", providers: [] },
    refresh: () => {},
  },
  models: { models: [], usable: [], loading: false },
}));
vi.mock("@/lib/daemon", () => ({ useDaemon: () => stable.daemon }));
vi.mock("@/lib/useModels", () => ({ useModels: () => stable.models }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/chat",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <span /> }));

// The two draw counters (pass-through: the real components draw).
vi.mock("@/components/chat/DiffView", async (orig) => {
  const real = await orig<typeof import("@/components/chat/DiffView")>();
  const R = await vi.importActual<typeof import("react")>("react");
  return {
    ...real,
    UnifiedRows: (p: React.ComponentProps<typeof real.UnifiedRows>) => {
      H.draws.rows += 1;
      return R.createElement(real.UnifiedRows, p);
    },
  };
});
vi.mock("@/components/harness/OpenInEditor", async (orig) => {
  const real = await orig<typeof import("@/components/harness/OpenInEditor")>();
  const R = await vi.importActual<typeof import("react")>("react");
  return {
    ...real,
    OpenInEditorButton: (p: React.ComponentProps<typeof real.OpenInEditorButton>) => {
      const id = String(p.testId ?? "");
      if (id.startsWith("review-open-")) H.draws.files += 1;
      if (id) H.byId[id] = (H.byId[id] ?? 0) + 1;
      return R.createElement(real.OpenInEditorButton, p);
    },
  };
});

import { BuildCodeView, sameSession } from "@/components/buildCode/BuildCodeView";
import { CodeConversation } from "@/components/buildCode/CodeConversation";
import { codePanesDraws } from "@/components/buildCode/CodePanes";
import { ChangesReview } from "@/components/harness/ChangesReview";
import { CommentChips, SentComments } from "@/components/harness/CommentChips";
import { IjHarnessView } from "@/components/harness/IjHarnessView";
import { queueNotSavedWords } from "@/components/harness/HarnessSession";
import type { FileChange } from "@/components/chat/ChangedFiles";
import { DiffBody, parseUnified } from "@/components/chat/DiffView";
import { markOf } from "@/lib/changesReview";
import { decodeCodeEvent, decodeCodeSessionView, foldConversation, type CodeEvent } from "@/lib/buildApp";
import { decodeQueue, queuedLine } from "@/lib/buildAppHistory";
import { decodeBranchAnswer } from "@/lib/buildAppBranch";
import { decodeQueuedItem, queuedItemBody, queuedWords } from "@/lib/buildComposer";
import { decodeRewindPreview } from "@/lib/buildRewind";
import { retryItemFor } from "@/lib/buildRetry";
import { decodeSessionView, deleteSession } from "@/lib/buildSessions";
import { __resetLooking } from "@/lib/buildWatching";
import { __resetSplitMemory } from "@/lib/codeSplit";
import {
  COMMENT,
  COMMENTS_FULL,
  COMMENTS_KEY_PREFIX,
  COMMENT_CHANGED_SINCE,
  COMMENT_NEEDS_WORDS,
  MAX_COMMENTS,
  QUOTE_CHARS,
  WORDS_CHARS,
  __resetCommentStore,
  addComment,
  buildCodeCommentsKey,
  clearComments,
  commentChipWords,
  commentLineLabel,
  commentsBody,
  decodeComments,
  getComments,
  hunkComment,
  isTypedCommand,
  lineOfRow,
  linesComment,
  noteFileMarks,
  pressLines,
  quoteCutWords,
  quoteLines,
  quoteOfHunk,
  removeComment,
  restoreComments,
  rowsOfHunkAt,
  subscribeComments,
  takeComments,
  type CommentBody,
  type LineRef,
} from "@/lib/codeComments";

/* ---- fixtures ----------------------------------------------------------- */

const CART = "C:\\work\\shop\\cart.py";
const CART_DIFF = "--- a/cart.py\n+++ b/cart.py\n@@ -1,2 +1,2 @@\n x = 1\n-y = 2\n+y = 3";
const CART_QUOTE = " x = 1\n-y = 2\n+y = 3";

const cart = (diff = CART_DIFF): FileChange => ({
  path: CART,
  rel: "cart.py",
  name: "cart.py",
  status: "modified",
  added: 1,
  removed: 1,
  diff,
});
const notes = (): FileChange => ({
  path: "C:\\work\\shop\\notes.md",
  rel: "notes.md",
  name: "notes.md",
  status: "created",
  added: 1,
  removed: 0,
  diff: "--- /dev/null\n+++ b/notes.md\n@@ -0,0 +1,1 @@\n+hello",
});

const body = (over: Partial<CommentBody> = {}): CommentBody => ({
  path: CART,
  side: "new",
  start: 1,
  end: 2,
  quote: CART_QUOTE,
  words: "Use a constant",
  ...over,
});
/** The six fields the daemon takes, for the comment the dialog makes on cart.py. */
const SENT = body();

beforeEach(() => {
  H.gets = {};
  H.sent = [];
  H.reply = {};
  H.fail = {};
  H.streams = [];
  H.scripts = [];
  H.lastWatch = null;
  H.hold = null;
  H.release = null;
  H.draws = { rows: 0, files: 0 };
  H.byId = {};
  H.late = null;
  H.gate = {};
  __resetLooking();
  __resetSplitMemory();
  window.localStorage.clear();
  window.sessionStorage.clear();
  __resetCommentStore();
});

afterEach(() => {
  H.release?.();
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
  __resetCommentStore();
});

const pause = (ms: number) => act(() => new Promise<void>((r) => setTimeout(r, ms)));

/** Press Comment on a file's first block, type the words and save. */
async function commentThroughDialog(words: string, index = 0) {
  const press = await screen.findAllByTestId(`review-hunk-comment-${index}`, {}, { timeout: 10_000 });
  fireEvent.click(press[0]);
  const box = await screen.findByTestId("review-comment-words");
  fireEvent.change(box, { target: { value: words } });
  fireEvent.click(screen.getByTestId("review-comment-save"));
  await waitFor(() => expect(screen.queryByTestId("review-comment-dialog")).toBeNull());
}

/* ================================================================ the store */

describe("the store", () => {
  it("adds a comment, tells who listens, and cuts the quote and the words", () => {
    const heard = vi.fn();
    const off = subscribeComments("s1", heard);
    const res = addComment("s1", body({ quote: "q".repeat(QUOTE_CHARS + 50), words: `  ${"w".repeat(WORDS_CHARS + 50)}  ` }));
    expect(res.ok).toBe(true);
    expect(heard).toHaveBeenCalledTimes(1);
    const [c] = getComments("s1");
    expect(c.quote).toHaveLength(QUOTE_CHARS);
    expect(c.words).toHaveLength(WORDS_CHARS);
    expect(c.id).toBeTruthy();
    expect(c.stale).toBe(false);
    // The same list until it changes (a snapshot), and another session has none.
    expect(getComments("s1")).toBe(getComments("s1"));
    expect(getComments("s2")).toHaveLength(0);
    off();
    addComment("s1", body({ words: "again" }));
    expect(heard).toHaveBeenCalledTimes(1);
  });

  it("refuses a comment with no words, and the 21st of a message", () => {
    expect(addComment("s1", body({ words: "   " }))).toEqual({ ok: false, why: COMMENT_NEEDS_WORDS });
    expect(getComments("s1")).toHaveLength(0);
    for (let i = 0; i < MAX_COMMENTS; i += 1) expect(addComment("s1", body({ words: `c${i}` })).ok).toBe(true);
    expect(addComment("s1", body({ words: "one too many" }))).toEqual({ ok: false, why: COMMENTS_FULL });
    expect(getComments("s1")).toHaveLength(MAX_COMMENTS);
  });

  it("removes one, and clears all", () => {
    addComment("s1", body({ words: "a" }));
    addComment("s1", body({ words: "b" }));
    const [a] = getComments("s1");
    removeComment("s1", a.id);
    expect(getComments("s1").map((c) => c.words)).toEqual(["b"]);
    clearComments("s1");
    expect(getComments("s1")).toHaveLength(0);
    expect(window.localStorage.getItem(`${COMMENTS_KEY_PREFIX}s1`)).toBeNull();
  });

  it("is kept across a reload, per session", () => {
    addComment("s1", body({ words: "kept" }));
    __resetCommentStore(); // what this window had read is gone, as after a reload
    expect(getComments("s1").map((c) => c.words)).toEqual(["kept"]);
    expect(getComments("code:s1")).toHaveLength(0);
    expect(buildCodeCommentsKey("s1")).toBe("code:s1");
  });

  it("takes the message's comments out, and puts a refused message's back first, each once", () => {
    addComment("s1", body({ words: "a" }));
    addComment("s1", body({ words: "b" }));
    const taken = takeComments("s1");
    expect(taken.map((c) => c.words)).toEqual(["a", "b"]);
    expect(getComments("s1")).toHaveLength(0);
    addComment("s1", body({ words: "written since" }));
    restoreComments("s1", taken);
    restoreComments("s1", taken); // twice: still once each
    expect(getComments("s1").map((c) => c.words)).toEqual(["a", "b", "written since"]);
    // A comment the daemon hands back has no id: it gets one, and is not doubled.
    restoreComments("s1", [body({ words: "from a rewind" }), body({ words: "a" })]);
    expect(getComments("s1").map((c) => c.words)).toEqual(["from a rewind", "a", "b", "written since"]);
    expect(new Set(getComments("s1").map((c) => c.id)).size).toBe(4);
  });

  it("a message takes at most 20; the rest stay waiting", () => {
    for (let i = 0; i < MAX_COMMENTS; i += 1) addComment("s1", body({ words: `c${i}` }));
    restoreComments("s1", [body({ words: "back-1" }), body({ words: "back-2" })]);
    expect(getComments("s1")).toHaveLength(MAX_COMMENTS + 2);
    expect(takeComments("s1")).toHaveLength(MAX_COMMENTS);
    expect(getComments("s1").map((c) => c.words)).toEqual(["c18", "c19"]);
  });

  it("says a file changed since, within one view, and writes only when something changed", () => {
    addComment("s1", { ...body(), mark: "m1", view: "session" });
    const heard = vi.fn();
    subscribeComments("s1", heard);
    noteFileMarks("s1", "session", new Map([[CART, "m1"]]));
    expect(heard).not.toHaveBeenCalled(); // the same mark: nothing written
    noteFileMarks("s1", "branch", new Map([[CART, "other-diff"]]));
    expect(getComments("s1")[0].stale).toBe(false); // another view's diff of the file says nothing
    noteFileMarks("s1", "session", new Map([["C:\\work\\shop\\other.py", "x"]]));
    expect(getComments("s1")[0].stale).toBe(false); // the view no longer lists the file: left as it is
    expect(heard).not.toHaveBeenCalled();
    noteFileMarks("s1", "session", new Map([[CART, "m2"]]));
    expect(getComments("s1")[0].stale).toBe(true);
    expect(heard).toHaveBeenCalledTimes(1);
    noteFileMarks("s1", "session", new Map([[CART, "m2"]]));
    expect(heard).toHaveBeenCalledTimes(1);
    noteFileMarks("s1", "session", new Map([[CART, "m1"]]));
    expect(getComments("s1")[0].stale).toBe(false); // back as it was
  });

  it("works for this visit when the browser keeps nothing", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("full");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    expect(addComment("s1", body()).ok).toBe(true);
    expect(getComments("s1")).toHaveLength(1);
  });

  it("reads the daemon's comments by whitelist, and sends the six fields only", () => {
    const raw = [
      { path: "cart.py", side: "old", start: 3, end: 5, quote: "-x", words: "why", extra: "dropped" },
      { path: "a.py", side: "sideways", start: "7", end: 0, quote: 12, words: "w" },
      { path: "", words: "no path" },
      { path: "b.py" },
      "nonsense",
    ];
    expect(decodeComments(raw)).toEqual([
      { path: "cart.py", side: "old", start: 3, end: 5, quote: "-x", words: "why" },
      { path: "a.py", side: "new", start: 7, end: 7, quote: "", words: "w" },
    ]);
    expect(decodeComments({ path: "x" })).toEqual([]);
    expect(decodeComments(Array.from({ length: 30 }, () => body()))).toHaveLength(MAX_COMMENTS);
    addComment("s1", { ...body(), mark: "m", view: "session" });
    expect(commentsBody(getComments("s1"))).toEqual([SENT]);
    expect(Object.keys(commentsBody(getComments("s1"))[0]).sort()).toEqual(["end", "path", "quote", "side", "start", "words"]);
  });

  it("a block's comment: its lines with their marks, the NEW numbers; a deleted file's old ones", () => {
    const rows = parseUnified("@@ -10,3 +12,4 @@\n a\n-b\n+c\n+d\n e\n@@ -40,1 +42,1 @@\n-z\n+y");
    expect(hunkComment(rows, 0, false)).toEqual({
      side: "new",
      start: 12,
      end: 15,
      quote: " a\n-b\n+c\n+d\n e",
      cut: "",
    });
    expect(hunkComment(rows, 1, false)).toEqual({ side: "new", start: 42, end: 42, quote: "-z\n+y", cut: "" });
    expect(hunkComment(rows, 2, false)).toBeNull();
    expect(hunkComment(rows, -1, false)).toBeNull();
    const gone = parseUnified("@@ -1,2 +0,0 @@\n-one\n-two");
    expect(hunkComment(gone, 0, true)).toEqual({ side: "old", start: 1, end: 2, quote: "-one\n-two", cut: "" });
  });

  it("two blocks with the SAME header are two blocks (by position, never by the header's words)", () => {
    // A diff that joins two edits of one file: both blocks say "@@ -1 +1 @@".
    const rows = parseUnified("@@ -1 +1 @@\n-a\n+b\n@@ -1 +1 @@\n-c\n+d");
    expect(rowsOfHunkAt(rows, 0).map((r) => r.text)).toEqual(["a", "b"]);
    expect(rowsOfHunkAt(rows, 1).map((r) => r.text)).toEqual(["c", "d"]);
    expect(hunkComment(rows, 1, false)!.quote).toBe("-c\n+d");
    // A line too: the row's own place, not every row that carries its number.
    expect(linesComment(rows, "new", 5, 5)!.quote).toBe("+d");
    expect(linesComment(rows, "new", 2, 2)!.quote).toBe("+b");
    expect(linesComment(rows, "new", 5, 2)).toMatchObject({ start: 1, end: 1, quote: "+b\n+d" });
  });

  it("a quote is cut at a WHOLE line, and says how many lines it holds", () => {
    const long = parseUnified(`@@ -1,1 +1,900 @@\n${Array.from({ length: 900 }, (_, i) => `+line number ${i}`).join("\n")}`);
    const q = quoteLines(long.slice(1));
    expect(q.total).toBe(900);
    expect(q.partial).toBe(false);
    expect(q.quote.length).toBeLessThanOrEqual(QUOTE_CHARS);
    // The last line quoted is whole, and the next one would not have fitted.
    const kept = q.quote.split("\n");
    expect(kept).toHaveLength(q.lines);
    expect(kept.at(-1)).toBe(`+line number ${q.lines - 1}`);
    expect(q.quote.length + 1 + `+line number ${q.lines}`.length).toBeGreaterThan(QUOTE_CHARS);
    expect(quoteOfHunk(long.slice(1))).toBe(q.quote);
    expect(quoteCutWords(q)).toBe(`Only the first ${q.lines} lines are quoted.`);
    expect(hunkComment(long, 0, false)!.cut).toBe(`Only the first ${q.lines} lines are quoted.`);
    expect(linesComment(long, "new", 1, 900)!.cut).toBe(`Only the first ${q.lines} lines are quoted.`);
    // Nothing cut: nothing said.
    expect(quoteCutWords(quoteLines(parseUnified("@@ -1 +1 @@\n-a\n+b").slice(1)))).toBe("");
    expect(quoteCutWords({ lines: 1, total: 3, partial: false })).toBe("Only the first line is quoted.");
    // One line longer than a quote: its start, and said.
    const one = quoteLines(parseUnified(`@@ -1 +1 @@\n+${"x".repeat(QUOTE_CHARS + 100)}\n+next`).slice(1));
    expect(one).toMatchObject({ lines: 1, total: 2, partial: true });
    expect(one.quote).toHaveLength(QUOTE_CHARS);
    expect(quoteCutWords(one)).toBe("Only the start of the first line is quoted.");
  });

  it("a typed command of the tool's own is told from a message and from a path", () => {
    for (const t of ["/compact", "/model opus", "  /review app.py", "/plugin:cmd x", "/clear\n"]) {
      expect(isTypedCommand(t)).toBe(true);
    }
    for (const t of ["fix /compact", "/etc/hosts is wrong", "/", "/ compact", "", "a/b"]) {
      expect(isTypedCommand(t)).toBe(false);
    }
  });

  it("a chip's words: the file, the lines, the start of the words", () => {
    expect(commentChipWords(body({ start: 12, end: 18, words: "Use math.fsum here" }))).toBe("cart.py 12–18: Use math.fsum here");
    expect(commentChipWords(body({ start: 4, end: 4, words: "x" }))).toBe("cart.py 4: x");
    expect(commentChipWords(body({ start: 0, end: 0, words: "x" }))).toBe("cart.py: x");
    const cut = commentChipWords(body({ words: "w".repeat(200) }));
    expect(cut.endsWith("…")).toBe(true);
    expect(cut.length).toBeLessThan(80);
  });
});

/* ====================================================== the Changes component */

describe("the Comment button and its one dialog", () => {
  it("offers Comment only with the flag", () => {
    const { rerender } = render(<ChangesReview changes={[cart()]} sessionId="s1" />);
    expect(screen.queryByTestId("review-hunk-comment-0")).toBeNull();
    rerender(<ChangesReview changes={[cart()]} sessionId="s1" comments />);
    expect(screen.getByTestId("review-hunk-comment-0").textContent).toBe(COMMENT);
  });

  it("is ONE dialog, even in the wide view where a file is drawn three times", async () => {
    render(<ChangesReview changes={[cart(), notes()]} sessionId="s1" comments commentView="session" />);
    fireEvent.click(screen.getByTestId("review-wide-open"));
    await screen.findByTestId("review-wide");
    const presses = screen.getAllByTestId("review-hunk-comment-0");
    expect(presses.length).toBe(3); // the pane, and the wide view's two layouts
    fireEvent.click(presses[2]);
    await screen.findByTestId("review-comment-dialog");
    expect(screen.getAllByTestId("review-comment-dialog")).toHaveLength(1);
    expect(screen.getAllByTestId("review-comment-words")).toHaveLength(1);
    expect(screen.getByTestId("review-comment-where").textContent).toBe("cart.py, lines 1–2");
    expect(screen.getByTestId("review-comment-quote").textContent).toBe(CART_QUOTE);
    // The words box holds the keyboard.
    expect(document.activeElement).toBe(screen.getByTestId("review-comment-words"));
  });

  it("does not save without words; with words it saves the block and closes", async () => {
    render(<ChangesReview changes={[cart()]} sessionId="s1" comments commentView="session" />);
    fireEvent.click(screen.getByTestId("review-hunk-comment-0"));
    const box = await screen.findByTestId("review-comment-words");
    const save = screen.getByTestId("review-comment-save") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.click(save);
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
    expect(screen.getByTestId("review-comment-problem").textContent).toBe(COMMENT_NEEDS_WORDS);
    fireEvent.change(box, { target: { value: "   " } });
    expect(save.disabled).toBe(true);
    expect(getComments("s1")).toHaveLength(0);
    expect(screen.getByTestId("review-comment-dialog")).toBeTruthy();
    fireEvent.change(box, { target: { value: "Use a constant" } });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(screen.queryByTestId("review-comment-dialog")).toBeNull());
    const [c] = getComments("s1");
    expect(commentsBody([c])).toEqual([SENT]);
    expect(c.mark).toBe(markOf(cart()));
    expect(c.view).toBe("session");
  });

  it("a deleted file's block is commented on as it was (the old lines)", async () => {
    const gone: FileChange = {
      path: "C:\\work\\shop\\old.py",
      rel: "old.py",
      name: "old.py",
      status: "deleted",
      added: 0,
      removed: 2,
      diff: "--- a/old.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-one\n-two",
    };
    render(<ChangesReview changes={[gone]} sessionId="s1" comments />);
    await commentThroughDialog("Why was this removed?");
    expect(commentsBody(getComments("s1"))).toEqual([
      { path: gone.path, side: "old", start: 1, end: 2, quote: "-one\n-two", words: "Why was this removed?" },
    ]);
  });

  it("Enter and Escape stay in the dialog: Escape closes it alone, never the wide view under it", async () => {
    const seen: string[] = [];
    const spy = (e: KeyboardEvent) => seen.push(e.key);
    document.addEventListener("keydown", spy);
    try {
      render(<ChangesReview changes={[cart()]} sessionId="s1" comments />);
      fireEvent.click(screen.getByTestId("review-wide-open"));
      await screen.findByTestId("review-wide");
      fireEvent.click(screen.getAllByTestId("review-hunk-comment-0")[0]);
      const box = await screen.findByTestId("review-comment-words");
      fireEvent.change(box, { target: { value: "half a thought" } });
      fireEvent.keyDown(box, { key: "a" });
      fireEvent.keyDown(box, { key: "Enter" });
      // Enter saved nothing and closed nothing; no listener outside heard it.
      expect(seen).toEqual(["a"]);
      expect(getComments("s1")).toHaveLength(0);
      expect(screen.getByTestId("review-comment-dialog")).toBeTruthy();
      fireEvent.keyDown(box, { key: "Escape" });
      await waitFor(() => expect(screen.queryByTestId("review-comment-dialog")).toBeNull());
      expect(seen).toEqual(["a"]);
      expect(screen.getByTestId("review-wide")).toBeTruthy(); // the wide view is still open
      expect(getComments("s1")).toHaveLength(0);
    } finally {
      document.removeEventListener("keydown", spy);
    }
  });

  it("Ctrl+Enter in the words saves", async () => {
    render(<ChangesReview changes={[cart()]} sessionId="s1" comments />);
    fireEvent.click(screen.getByTestId("review-hunk-comment-0"));
    const box = await screen.findByTestId("review-comment-words");
    fireEvent.change(box, { target: { value: "Rename this" } });
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(screen.queryByTestId("review-comment-dialog")).toBeNull());
    expect(getComments("s1").map((c) => c.words)).toEqual(["Rename this"]);
  });

  it("says when the message is full, and saves nothing more", async () => {
    for (let i = 0; i < MAX_COMMENTS; i += 1) addComment("s1", body({ words: `c${i}` }));
    render(<ChangesReview changes={[cart()]} sessionId="s1" comments />);
    fireEvent.click(screen.getByTestId("review-hunk-comment-0"));
    const box = await screen.findByTestId("review-comment-words");
    expect(screen.getByTestId("review-comment-problem").textContent).toBe(COMMENTS_FULL);
    fireEvent.change(box, { target: { value: "one more" } });
    expect((screen.getByTestId("review-comment-save") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
    expect(getComments("s1")).toHaveLength(MAX_COMMENTS);
  });

  it("opening the dialog, typing and saving redraw no file", async () => {
    render(
      <>
        <ChangesReview changes={[cart(), notes()]} sessionId="s1" comments commentView="session" />
        <CommentChips storeKey="s1" />
      </>,
    );
    expect(H.draws.rows).toBeGreaterThan(0); // the rows ARE counted
    expect(H.draws.files).toBeGreaterThan(0);
    const before = { ...H.draws };
    fireEvent.click(screen.getByTestId("review-hunk-comment-0"));
    const box = await screen.findByTestId("review-comment-words");
    for (const v of ["U", "Us", "Use", "Use a constant"]) fireEvent.change(box, { target: { value: v } });
    fireEvent.click(screen.getByTestId("review-comment-save"));
    await screen.findByTestId("comment-chip");
    expect(H.draws).toEqual(before);
    // The caller drawing again with the same files and flag redraws none either.
    fireEvent.click(screen.getByTestId("comment-chip-remove"));
    expect(screen.queryByTestId("comment-chip")).toBeNull();
    expect(H.draws).toEqual(before);
  });

  it("a file that changes again marks its waiting comment 'changed since'", async () => {
    const ui = (diff?: string) => (
      <>
        <ChangesReview changes={[cart(diff)]} sessionId="s1" comments commentView="session" />
        <CommentChips storeKey="s1" />
      </>
    );
    const { rerender } = render(ui());
    await commentThroughDialog("Use a constant");
    const chip = await screen.findByTestId("comment-chip");
    expect(chip.getAttribute("data-stale")).toBe("false");
    expect(screen.queryByTestId("comment-chip-stale")).toBeNull();
    rerender(ui(`${CART_DIFF}\n+z = 4`));
    await waitFor(() => expect(screen.getByTestId("comment-chip").getAttribute("data-stale")).toBe("true"));
    expect(screen.getByTestId("comment-chip-stale").textContent).toBe(COMMENT_CHANGED_SINCE);
    rerender(ui());
    await waitFor(() => expect(screen.getByTestId("comment-chip").getAttribute("data-stale")).toBe("false"));
  });
});

/* ================================================================= the chips */

describe("the chips above the box", () => {
  it("one chip per waiting comment; × removes it; kept across a remount and a reload", () => {
    addComment("s1", body({ start: 12, end: 18, words: "Use math.fsum here" }));
    addComment("s1", body({ path: "C:\\work\\shop\\notes.md", start: 1, end: 1, words: "Say more" }));
    const first = render(<CommentChips storeKey="s1" />);
    const chips = screen.getAllByTestId("comment-chip");
    expect(chips.map((c) => c.textContent)).toEqual(["cart.py 12–18: Use math.fsum here", "notes.md 1: Say more"]);
    expect(chips[0].getAttribute("title")).toContain("Use math.fsum here");
    first.unmount();
    __resetCommentStore();
    render(<CommentChips storeKey="s1" />);
    expect(screen.getAllByTestId("comment-chip")).toHaveLength(2);
    fireEvent.click(within(screen.getAllByTestId("comment-chip")[0]).getByRole("button", { name: "Remove the comment on cart.py 12–18" }));
    expect(screen.getAllByTestId("comment-chip").map((c) => c.textContent)).toEqual(["notes.md 1: Say more"]);
    expect(getComments("s1")).toHaveLength(1);
  });

  it("no key (an older Iron Jarvis): no chips, whatever the browser kept", () => {
    addComment("s1", body());
    render(<CommentChips storeKey={null} />);
    expect(screen.queryByTestId("comment-chips")).toBeNull();
  });

  it("a sent message's comments are read-only chips", () => {
    render(<SentComments comments={[body({ start: 12, end: 18, words: "Use math.fsum here" })]} />);
    const chip = screen.getByTestId("sent-comment");
    expect(chip.textContent).toBe("cart.py 12–18: Use math.fsum here");
    expect(within(chip).queryByRole("button")).toBeNull();
    cleanup();
    render(<SentComments comments={[]} />);
    expect(screen.queryByTestId("sent-comments")).toBeNull();
  });
});

/* ============================================================= the decoders */

describe("what the daemon says", () => {
  const session = {
    id: "a1",
    harness: "claude-code",
    folder: "C:\\work\\shop",
    title: "t",
    account: "",
    model: "",
    effort: "",
    mode: "ask",
    harness_session_id: "",
    created_at: "2026-11-19T09:00:00Z",
    updated_at: "2026-11-19T09:00:00Z",
    archived_at: null,
    last_error: "",
  };

  it("Build › Code: takes_comments, a sent message's comments, a waiting message's, a rewind's", () => {
    expect(decodeCodeSessionView({ session, events: [], takes_comments: true })!.takesComments).toBe(true);
    expect(decodeCodeSessionView({ session, events: [] })!.takesComments).toBe(false); // an older daemon
    expect(decodeCodeSessionView({ session, events: [], takes_comments: "yes" })!.takesComments).toBe(false);
    const ev = decodeCodeEvent({ type: "user", at: "t", text: "fix it", files: [], comments: [{ ...SENT, junk: 1 }] })!;
    expect(ev.type === "user" && ev.comments).toEqual([SENT]);
    const plain = decodeCodeEvent({ type: "user", at: "t", text: "fix it", files: [] })!;
    expect("comments" in plain).toBe(false);
    const item = foldConversation([ev]).items[0];
    expect(item.kind === "user" && item.comments).toEqual([SENT]);
    const q = decodeQueue({ queued: [{ id: "q1", text: "", files: [], comments: [SENT] }, { id: "q2", text: "plain", files: [] }] })!;
    expect(q.queued[0].comments).toEqual([SENT]);
    expect("comments" in q.queued[1]).toBe(false);
    expect(queuedLine(q.queued[0])).toBe("1 comment");
    expect(queuedLine({ id: "x", text: "then this", files: [], comments: [SENT, SENT] })).toBe("then this · 2 comments");
    expect(queuedLine(q.queued[1])).toBe("plain");
    expect(decodeBranchAnswer({ session, text: "w", files: [], note: "", comments: [SENT] })!.comments).toEqual([SENT]);
    expect(decodeBranchAnswer({ session, text: "w", files: [], note: "" })!.comments).toEqual([]);
  });

  it("Chat › Code: takes_comments, the saved question, a waiting message, the rewind plan, Retry", () => {
    const view = (over: Record<string, unknown>) =>
      decodeSessionView({
        id: "s1",
        title: "Cart",
        updated_at: "2026-11-19T10:00:00",
        folder: "C:\\work\\shop",
        messages: [{ role: "user", content: "fix it", turnId: "t0", comments: [SENT] }],
        ...over,
      })!;
    expect(view({ takes_comments: true }).takesComments).toBe(true);
    expect(view({}).takesComments).toBe(false);
    expect(decodeComments(view({}).messages[0].comments)).toEqual([SENT]);
    const item = decodeQueuedItem({ text: "", comments: [SENT] })!;
    expect(item.comments).toEqual([SENT]);
    expect(queuedItemBody(item)).toEqual({ text: "", comments: [SENT] });
    expect(queuedWords(item)).toBe("1 comment");
    expect(decodeQueuedItem({ text: "" })).toBeNull();
    const preview = decodeRewindPreview({ to: "t0", question: "fix it", files: [], problems: [], can_rewind: true, comments: [SENT] })!;
    expect(preview.comments).toEqual([SENT]);
    // Retry sends the same message again: its comments go with it.
    const msgs = [
      { role: "user" as const, content: "fix it", turnId: "t0", comments: [SENT] },
      { role: "assistant" as const, content: "", turnId: "t0", error: "It failed." },
    ];
    expect(retryItemFor(msgs, 1, msgs, "C:\\work\\shop")!.comments).toEqual([SENT]);
  });

  it("the sent message draws its comments under its bubble", () => {
    const events = [decodeCodeEvent({ type: "user", at: "t1", text: "fix these", files: [], comments: [SENT] })].filter(
      (e): e is CodeEvent => e !== null,
    );
    render(<CodeConversation items={foldConversation(events).items} folder="C:\\work\\shop" tool="claude-code" />);
    const row = screen.getByTestId("code-user");
    expect(within(row).getByTestId("sent-comment").textContent).toBe("cart.py 1–2: Use a constant");
  });
});

/* ============================================================== Build › Code */

const AT1 = "2026-11-19T10:00:00.100000Z";
const AT2 = "2026-11-19T10:01:00.200000Z";
const CODE_EVENTS = [
  { type: "user", at: AT1, text: "first one", files: [] },
  { type: "text_done", at: "2026-11-19T10:00:01Z", item: "a", text: "reply one" },
  { type: "done", at: "2026-11-19T10:00:02Z", stop: "completed" },
];
const tool = (id: string, label: string) => ({
  id,
  label,
  installed: true,
  signed_in: true,
  modes: [{ id: "ask", label: "Ask first", dangerous: false }],
  models: [{ id: "", label: `${label}'s default` }],
  efforts: [],
  accounts: [],
  commands: [],
});
const codeSess = (id: string) => ({
  id,
  harness: "claude-code",
  folder: "C:\\work\\shop",
  title: `Session ${id}`,
  account: "",
  model: "",
  effort: "",
  mode: "ask",
  harness_session_id: `hid-${id}`,
  created_at: "2026-11-19T09:00:00Z",
  updated_at: "2026-11-19T09:00:00Z",
  archived_at: null,
  last_error: "",
  source: "app",
});
const CODE_CHANGES = {
  source: "git",
  words: "Every file in this folder that differs from where git was when the session started here.",
  since: "2026-11-19T09:00:00Z",
  changes: [cart(), notes()],
  files: 2,
  added: 2,
  removed: 1,
  truncated_files: false,
  by_tool: 0,
};
const TURN = (id: string) => `/build/app/sessions/${id}/turn`;

/** The daemon for one Build › Code session (`over`: the view's own fields;
 *  an OLDER daemon is `{ takes_comments: undefined }`). */
function openCode(id: string, over: Record<string, unknown> = {}) {
  H.gets = {
    "/build/app/harnesses": { harnesses: [tool("claude-code", "Claude Code")] },
    "/build/app/sessions": { sessions: [codeSess(id)] },
    "/editor/options": { editors: [], preferred: "" },
    [`/build/app/sessions/${id}`]: {
      session: codeSess(id),
      events: CODE_EVENTS,
      running: null,
      pending: [],
      takes_comments: true,
      ...over,
    },
    [`/build/app/sessions/${id}/changes`]: CODE_CHANGES,
  };
}

/** The same session with a reply running (its stream stays open). */
function openCodeRunning(id: string, over: Record<string, unknown> = {}) {
  const asked = { type: "user", at: AT2, text: "working on it", files: [], turn: "turn_1" };
  openCode(id, { events: [...CODE_EVENTS, asked], running: "turn_1", ...over });
  H.scripts = [[{ event: "user", data: asked }]];
  H.hold = new Promise<void>((res) => {
    H.release = res;
  });
}

function holdStreams() {
  H.hold = new Promise<void>((res) => {
    H.release = res;
  });
}

const codeView = (id: string) => render(<BuildCodeView initialSession={id} onOpenedTerminal={() => {}} />);
const turnBody = (id: string) => H.streams.find((s) => s.path === TURN(id))?.body as Record<string, unknown> | undefined;
const posted = (path: string) => H.sent.filter((s) => s.method === "POST" && s.path === path);
const type = (words: string) => fireEvent.change(screen.getByTestId("code-box"), { target: { value: words } });

describe("Build › Code", () => {
  it("a comment waits as a chip, rides the next message, and the chips clear; the sent message shows it", async () => {
    openCode("a1");
    holdStreams();
    codeView("a1");
    await commentThroughDialog("Use a constant");
    const composer = screen.getByTestId("code-composer");
    await waitFor(() => expect(within(composer).getByTestId("comment-chip").textContent).toBe("cart.py 1–2: Use a constant"));
    type("fix these");
    fireEvent.click(screen.getByTestId("code-send"));
    await waitFor(() => expect(turnBody("a1")).toBeTruthy());
    expect(turnBody("a1")).toMatchObject({ text: "fix these", comments: [SENT] });
    await waitFor(() => expect(screen.queryByTestId("comment-chip")).toBeNull());
    expect(getComments("code:a1")).toHaveLength(0);
    // The message just sent shows what rode it.
    const mine = screen.getAllByTestId("code-user").at(-1)!;
    expect(mine.textContent).toContain("fix these");
    expect(within(mine).getByTestId("sent-comment").textContent).toBe("cart.py 1–2: Use a constant");
  });

  it("a message may be comments alone", async () => {
    openCode("a1");
    holdStreams();
    codeView("a1");
    await screen.findByTestId("code-box", {}, { timeout: 10_000 });
    expect((screen.getByTestId("code-send") as HTMLButtonElement).disabled).toBe(true);
    await commentThroughDialog("Use a constant");
    await waitFor(() => expect((screen.getByTestId("code-send") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.keyDown(screen.getByTestId("code-box"), { key: "Enter" });
    await waitFor(() => expect(turnBody("a1")).toBeTruthy());
    expect(turnBody("a1")).toMatchObject({ text: "", comments: [SENT] });
    expect(turnBody("a1")).not.toHaveProperty("files");
    await waitFor(() => expect(screen.queryByTestId("comment-chip")).toBeNull());
  });

  it("a refused send puts the words and the comments back, with the daemon's sentence", async () => {
    openCode("a1");
    const why = "A comment names a file that is not in this session's folder, so nothing was sent.";
    H.scripts = [[{ yield: { type: "error", detail: why, status: 400 } }]];
    codeView("a1");
    await commentThroughDialog("Use a constant");
    await screen.findByTestId("comment-chip");
    type("fix these");
    fireEvent.click(screen.getByTestId("code-send"));
    await waitFor(() => expect(screen.getByTestId("code-note").textContent).toBe(why));
    expect(turnBody("a1")).toMatchObject({ text: "fix these", comments: [SENT] });
    expect(screen.getByTestId("comment-chip").textContent).toBe("cart.py 1–2: Use a constant");
    expect((screen.getByTestId("code-box") as HTMLTextAreaElement).value).toBe("fix these");
    expect(commentsBody(getComments("code:a1"))).toEqual([SENT]);
    // No bubble for a message that never went.
    expect(screen.getAllByTestId("code-user")).toHaveLength(1);
  });

  it("while a reply runs: Enter queues the message WITH its comments; a refused queue puts them back", async () => {
    openCodeRunning("a1");
    H.reply["/build/app/sessions/a1/queue"] = {
      ok: true,
      id: "q1",
      queued: [{ id: "q1", text: "then this", files: [], comments: [SENT] }],
      queue_held: false,
    };
    codeView("a1");
    await screen.findByTestId("code-stop", {}, { timeout: 10_000 });
    await commentThroughDialog("Use a constant");
    await screen.findByTestId("comment-chip");
    type("then this");
    fireEvent.keyDown(screen.getByTestId("code-box"), { key: "Enter" });
    await waitFor(() => expect(posted("/build/app/sessions/a1/queue")).toHaveLength(1));
    expect(posted("/build/app/sessions/a1/queue")[0].body).toEqual({ text: "then this", comments: [SENT] });
    await waitFor(() => expect(screen.queryByTestId("comment-chip")).toBeNull());
    await waitFor(() => expect(screen.getByTestId("code-queued-item").textContent).toContain("then this · 1 comment"));
    expect(turnBody("a1")).toBeUndefined();
    // A second one the daemon refuses: nothing is lost.
    H.fail["/build/app/sessions/a1/queue"] = { status: 400, message: "A comment names a file that is not in this session's folder, so nothing was sent." };
    await commentThroughDialog("And rename it", 1);
    await screen.findByTestId("comment-chip");
    type("and this");
    fireEvent.keyDown(screen.getByTestId("code-box"), { key: "Enter" });
    await waitFor(() => expect(screen.getByTestId("code-note").textContent).toContain("not in this session's folder"));
    expect(screen.getByTestId("comment-chip").textContent).toBe("notes.md 1: And rename it");
    expect((screen.getByTestId("code-box") as HTMLTextAreaElement).value).toBe("and this");
  });

  it("a queue that finds no reply running sends the message now, with its comments", async () => {
    openCodeRunning("a1");
    H.fail["/build/app/sessions/a1/queue"] = { status: 409, message: "not working", body: { code: "not_working" } };
    codeView("a1");
    await screen.findByTestId("code-stop", {}, { timeout: 10_000 });
    await commentThroughDialog("Use a constant");
    await screen.findByTestId("comment-chip");
    type("then this");
    fireEvent.keyDown(screen.getByTestId("code-box"), { key: "Enter" });
    await waitFor(() => expect(turnBody("a1")).toBeTruthy());
    expect(turnBody("a1")).toMatchObject({ text: "then this", comments: [SENT] });
    await waitFor(() => expect(screen.queryByTestId("comment-chip")).toBeNull());
  });

  it("a note leaves the comments where they are (and so does a note that goes as a message)", async () => {
    openCodeRunning("a1");
    H.reply["/build/app/sessions/a1/steer"] = { ok: true, id: "n1", state: "sent" };
    codeView("a1");
    await screen.findByTestId("code-stop", {}, { timeout: 10_000 });
    await commentThroughDialog("Use a constant");
    await screen.findByTestId("comment-chip");
    type("also this");
    fireEvent.keyDown(screen.getByTestId("code-box"), { key: "Enter", shiftKey: true });
    await waitFor(() => expect(posted("/build/app/sessions/a1/steer")).toHaveLength(1));
    expect(posted("/build/app/sessions/a1/steer")[0].body).toEqual({ text: "also this" });
    await waitFor(() => expect((screen.getByTestId("code-box") as HTMLTextAreaElement).value).toBe(""));
    expect(screen.getByTestId("comment-chip").textContent).toBe("cart.py 1–2: Use a constant");
    expect(posted("/build/app/sessions/a1/queue")).toHaveLength(0);
    await pause(20);
    // The reply had ended: the note goes as a message of its own, and the
    // comments still wait for the user's next message from the box.
    H.fail["/build/app/sessions/a1/steer"] = { status: 409, message: "not working", body: { code: "not_working" } };
    type("one more");
    fireEvent.keyDown(screen.getByTestId("code-box"), { key: "Enter", shiftKey: true });
    await waitFor(() => expect(turnBody("a1")).toBeTruthy());
    expect(turnBody("a1")).toMatchObject({ text: "one more" });
    expect(turnBody("a1")).not.toHaveProperty("comments");
    expect(screen.getByTestId("comment-chip")).toBeTruthy();
    expect(getComments("code:a1")).toHaveLength(1);
  });

  it("waiting messages taken back return their comments to the box", async () => {
    openCode("a1", { queued: [{ id: "q1", text: "then deploy", files: [], comments: [SENT] }], queue_held: true });
    H.reply["/build/app/sessions/a1/queue/take"] = {
      ok: true,
      taken: [{ id: "q1", text: "then deploy", files: [], comments: [SENT] }],
    };
    codeView("a1");
    await waitFor(() => expect((screen.getByTestId("code-box") as HTMLTextAreaElement).value).toBe("then deploy"), {
      timeout: 10_000,
    });
    expect(screen.getByTestId("comment-chip").textContent).toBe("cart.py 1–2: Use a constant");
  });

  it("a rewind puts the message's comments back above the box", async () => {
    openCode("a1", {
      events: [
        ...CODE_EVENTS,
        { type: "user", at: AT2, text: "second one", files: [], comments: [SENT] },
        { type: "text_done", at: "2026-11-19T10:01:01Z", item: "b", text: "reply two" },
        { type: "done", at: "2026-11-19T10:01:02Z", stop: "completed" },
      ],
      branch: { fork: true, fork_at: true, rewind: true, at: [AT1, AT2] },
    });
    H.reply["/build/app/sessions/a1/rewind"] = {
      session: codeSess("a1"),
      text: "second one",
      files: [],
      note: "The conversation now ends before that message.",
      comments: [SENT],
    };
    codeView("a1");
    await screen.findAllByTestId("code-user-more", {}, { timeout: 10_000 });
    // The sent message shows the comment it carried.
    expect(within(screen.getAllByTestId("code-user")[1]).getByTestId("sent-comment")).toBeTruthy();
    expect(screen.queryByTestId("comment-chip")).toBeNull();
    fireEvent.click(screen.getAllByTestId("code-user-more")[1]);
    const rewind = () => screen.getByTestId("code-user-rewind").querySelector("button")!;
    fireEvent.click(rewind());
    fireEvent.click(rewind());
    await waitFor(() => expect(posted("/build/app/sessions/a1/rewind")).toHaveLength(1));
    await screen.findByDisplayValue("second one", {}, { timeout: 10_000 });
    expect(within(screen.getByTestId("code-composer")).getByTestId("comment-chip").textContent).toBe(
      "cart.py 1–2: Use a constant",
    );
  });

  it("an OLDER Iron Jarvis (no takes_comments): no button, no chips, no `comments` sent, nothing lost", async () => {
    // The browser still holds a comment from a newer one.
    addComment("code:a1", body());
    openCode("a1", { takes_comments: undefined });
    holdStreams();
    codeView("a1");
    await screen.findByTestId("changes-review", {}, { timeout: 10_000 });
    expect(screen.queryByTestId("review-hunk-comment-0")).toBeNull();
    expect(screen.queryByTestId("comment-chips")).toBeNull();
    // Nothing but a comment: there is nothing to send.
    expect((screen.getByTestId("code-send") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(screen.getByTestId("code-box"), { key: "Enter" });
    await pause(30);
    expect(turnBody("a1")).toBeUndefined();
    type("fix these");
    fireEvent.click(screen.getByTestId("code-send"));
    await waitFor(() => expect(turnBody("a1")).toBeTruthy());
    expect(turnBody("a1")).toMatchObject({ text: "fix these" });
    expect(turnBody("a1")).not.toHaveProperty("comments");
    expect(getComments("code:a1")).toHaveLength(1); // kept for when it can be sent
  });

  it("Enter and Escape in the dialog send, queue and stop nothing", async () => {
    openCodeRunning("a1");
    codeView("a1");
    await screen.findByTestId("code-stop", {}, { timeout: 10_000 });
    type("half typed");
    fireEvent.click((await screen.findAllByTestId("review-hunk-comment-0"))[0]);
    const box = await screen.findByTestId("review-comment-words");
    fireEvent.change(box, { target: { value: "Use a constant" } });
    const before = H.sent.length;
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(box, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("review-comment-dialog")).toBeNull());
    await pause(30);
    expect(H.sent.length).toBe(before); // no queue, no note, no stop
    expect(turnBody("a1")).toBeUndefined();
    expect((screen.getByTestId("code-box") as HTMLTextAreaElement).value).toBe("half typed");
    expect(screen.getByTestId("code-stop")).toBeTruthy();
  });

  it("22-b: a comment in the Branch view of a session on its own branch waits under the box's own key", async () => {
    const WT = "C:\\work\\shop.ij-worktrees\\session-3f2a";
    const branchCart = { ...cart(), path: `${WT}\\cart.py` };
    const own = {
      ...codeSess("a1"),
      folder: WT,
      checkout: "C:\\work\\shop",
      git: {
        branch: "ij/session-3f2a",
        base: "main",
        base_commit: "abc123def456",
        worktree: WT,
        checkout: "C:\\work\\shop",
        repo: "C:\\work\\shop",
        merged: false,
        merged_into: "",
        removed: false,
        pr_url: "",
      },
    };
    openCode("a1", { session: own });
    H.gets["/build/app/sessions"] = { sessions: [own] };
    H.gets["/build/app/sessions/a1/git"] = {
      exists: true,
      ahead: 1,
      behind: 0,
      uncommitted: 0,
      remote: "origin",
      pushed: false,
      unpushed: 1,
      in_base: false,
      base_exists: true,
      base_checked_out_at: "C:\\work\\shop",
      commits: [{ sha: "1a2b3c4", subject: "Add the cart" }],
      pr_draft: { title: "Add the cart", body: "" },
      compare_url: "",
      gh: { installed: true, signed_in: true, note: "" },
    };
    H.gets["/build/app/sessions/a1/git/diff"] = {
      changes: [branchCart],
      files: 1,
      added: 1,
      removed: 1,
      truncated_files: false,
      base: "main",
      merge_base: "abc123def456",
    };
    holdStreams();
    codeView("a1");
    fireEvent.click(await screen.findByTestId("code-changes-scope-branch", {}, { timeout: 10_000 }));
    await screen.findByTestId("changes-review", {}, { timeout: 10_000 });
    await commentThroughDialog("Use a constant");
    expect(getComments("a1")).toHaveLength(0);
    const [c] = getComments("code:a1");
    expect(commentsBody([c])).toEqual([{ ...SENT, path: branchCart.path }]);
    expect(c.view).toBe("branch");
    await waitFor(() =>
      expect(within(screen.getByTestId("code-composer")).getByTestId("comment-chip").textContent).toBe(
        "cart.py 1–2: Use a constant",
      ),
    );
  });

  it("30 streamed words redraw the Changes rows zero times, with a comment waiting", async () => {
    openCodeRunning("a1");
    codeView("a1");
    await screen.findByTestId("code-stop", {}, { timeout: 10_000 });
    await commentThroughDialog("Use a constant");
    await screen.findByTestId("comment-chip");
    await pause(40);
    expect(H.draws.rows).toBeGreaterThan(0); // the rows ARE counted
    expect(H.draws.files).toBeGreaterThan(0);
    const before = { ...H.draws };
    const panesBefore = codePanesDraws.count;
    for (let i = 0; i < 30; i += 1) {
      act(() => H.lastWatch?.onFrame?.("text", { type: "text", item: "m-live", delta: `word${i} `, turn: "turn_1" }));
      await act(() => Promise.resolve());
      if (i % 5 === 4) await pause(20); // several frames, as a real stream
    }
    await waitFor(() => expect(screen.getAllByTestId("code-text").at(-1)?.textContent).toContain("word29"));
    await pause(40);
    expect(H.draws).toEqual(before);
    // Nor the panes that hold them (the flag they get is one plain value).
    expect(codePanesDraws.count).toBe(panesBefore);
    expect(screen.getByTestId("comment-chip")).toBeTruthy();
  });
});

/* =============================================================== Chat › Code */

const terminal = { find: () => null, open: async () => null, render: () => <div />, close: () => {} };

interface ChatOpts {
  running?: boolean;
  takes?: boolean;
  messages?: Record<string, unknown>[];
  /** More of the view's own fields (a session on its own branch: `git`). */
  over?: Record<string, unknown>;
  /** More reads the daemon answers (there before the view first asks). */
  gets?: Record<string, unknown>;
}

function chatView(id: string, o: ChatOpts = {}) {
  const running = o.running === true;
  return {
    id,
    title: "Cart",
    updated_at: "2026-11-19T10:00:00",
    running,
    waiting: false,
    archived: false,
    folder: "C:\\work\\shop",
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: {},
    messages:
      o.messages ??
      (running
        ? [{ role: "user", content: "the cart job", turnId: "tr1", at: "2026-11-19T10:00:00" }]
        : [
            { role: "user", content: "the cart job", turnId: "t0", at: "2026-11-19T10:00:00" },
            { role: "assistant", content: "Added the cart.", turnId: "t0", timing: { startedAt: 1, firstTokenAt: 2, endedAt: 3 } },
          ]),
    session_id: `build:${id}`,
    queued: [],
    turn_id: running ? "tr1" : "",
    ...(o.takes === false ? {} : { takes_comments: true }),
    ...(o.over ?? {}),
  };
}

/** A stream the test feeds (a reply that stays running). */
function openStream() {
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      ctl = c;
    },
  });
  const enc = new TextEncoder();
  return {
    push: (event: string, data: unknown) => ctl.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    end: () => {
      try {
        ctl.close();
      } catch {
        /* closed */
      }
    },
    response: (init?: RequestInit) => {
      init?.signal?.addEventListener("abort", () => {
        try {
          ctl.error(new DOMException("The operation was aborted.", "AbortError"));
        } catch {
          /* closed */
        }
      });
      return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
    },
  };
}

/** The turns Chat › Code posted (its real stream goes through fetch). */
const chat = { turns: [] as { url: string; body: Record<string, unknown> }[], refuse: "" };
let live: ReturnType<typeof openStream>;
let turnStream: ReturnType<typeof openStream>;

function openChat(id: string, o: ChatOpts = {}) {
  chat.turns = [];
  chat.refuse = "";
  live = openStream();
  turnStream = openStream();
  H.gets = {
    "/build/sessions": {
      folders: [{ path: "C:\\work\\shop", name: "shop", sessions: [{ id, title: "Cart", running: o.running === true }] }],
    },
    [`/build/sessions/${id}`]: chatView(id, o),
    "/build/accounts": new H.ApiError("no such route", 404),
    "/editor/options": { editors: [], preferred: "" },
    ...(o.gets ?? {}),
  };
  H.reply["/chat/changes"] = { changes: [cart(), notes()], files: 2, added: 2, removed: 1, truncated_files: false };
  window.localStorage.setItem("ij.build.pane", "changes");
  window.history.replaceState(null, "", `/chat?view=code&session=${id}`);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url);
      if (/\/chat\/turns\/tr1\/stream/.test(u)) return live.response(init);
      if (/\/build\/sessions\/[^/]+\/turn$/.test(u)) {
        chat.turns.push({ url: u, body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown> });
        if (chat.refuse) {
          return new Response(JSON.stringify({ detail: chat.refuse }), {
            status: 400,
            headers: { "Content-Type": "application/json" },
          });
        }
        return turnStream.response(init);
      }
      return new Response("{}", { status: 200 });
    }),
  );
  return render(
    <IjHarnessView initialSession={id} terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />,
  );
}

const chatBox = () => screen.getByTestId("harness-input") as HTMLTextAreaElement;

describe("Chat › Code", () => {
  afterEach(() => {
    live?.end();
    turnStream?.end();
  });

  it("Comment in its Changes view, a chip above its box, and the turn's body carries the comment", async () => {
    openChat("s1");
    expect(await screen.findByText("Added the cart.", {}, { timeout: 10_000 })).toBeInTheDocument();
    await commentThroughDialog("Use a constant");
    const composer = screen.getByTestId("harness-composer");
    await waitFor(() => expect(within(composer).getByTestId("comment-chip").textContent).toBe("cart.py 1–2: Use a constant"));
    expect(getComments("s1")).toHaveLength(1);
    fireEvent.change(chatBox(), { target: { value: "fix these" } });
    fireEvent.click(screen.getByTestId("harness-send"));
    await waitFor(() => expect(chat.turns).toHaveLength(1));
    expect(chat.turns[0].url).toContain("/build/sessions/s1/turn");
    expect(chat.turns[0].body).toMatchObject({ text: "fix these", comments: [SENT] });
    await waitFor(() => expect(screen.queryByTestId("comment-chip")).toBeNull());
    expect(getComments("s1")).toHaveLength(0);
    // The message being sent shows what rides it.
    await waitFor(() => expect(screen.getByTestId("sent-comment").textContent).toBe("cart.py 1–2: Use a constant"));
  });

  it("a message may be comments alone", async () => {
    openChat("s11"); // (a session of its own: a reply left running is kept per session)
    expect(await screen.findByText("Added the cart.", {}, { timeout: 10_000 })).toBeInTheDocument();
    expect((screen.getByTestId("harness-send") as HTMLButtonElement).disabled).toBe(true);
    await commentThroughDialog("Use a constant");
    await waitFor(() => expect((screen.getByTestId("harness-send") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByTestId("harness-send"));
    await waitFor(() => expect(chat.turns).toHaveLength(1));
    expect(chat.turns[0].body).toMatchObject({ text: "", comments: [SENT] });
  });

  it("a refused message puts its words and its comments back", async () => {
    openChat("s12");
    expect(await screen.findByText("Added the cart.", {}, { timeout: 10_000 })).toBeInTheDocument();
    await commentThroughDialog("Use a constant");
    await screen.findByTestId("comment-chip");
    chat.refuse = "A comment names a file that is not in this session's folder, so nothing was sent.";
    fireEvent.change(chatBox(), { target: { value: "fix these" } });
    fireEvent.click(screen.getByTestId("harness-send"));
    await waitFor(() => expect(chat.turns).toHaveLength(1));
    expect(chat.turns[0].body).toMatchObject({ comments: [SENT] });
    await waitFor(() => expect(screen.getByTestId("comment-chip").textContent).toBe("cart.py 1–2: Use a constant"));
    await waitFor(() => expect(chatBox().value).toBe("fix these"));
    expect(commentsBody(getComments("s12"))).toEqual([SENT]);
  });

  it("an OLDER Iron Jarvis (no takes_comments): no button, no chips, no `comments` sent, nothing lost", async () => {
    addComment("s2", body());
    openChat("s2", { takes: false });
    expect(await screen.findByText("Added the cart.", {}, { timeout: 10_000 })).toBeInTheDocument();
    await screen.findByTestId("changes-review");
    expect(screen.queryByTestId("review-hunk-comment-0")).toBeNull();
    expect(screen.queryByTestId("comment-chips")).toBeNull();
    expect((screen.getByTestId("harness-send") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(chatBox(), { target: { value: "fix these" } });
    fireEvent.click(screen.getByTestId("harness-send"));
    await waitFor(() => expect(chat.turns).toHaveLength(1));
    expect(chat.turns[0].body).toMatchObject({ text: "fix these" });
    expect(chat.turns[0].body).not.toHaveProperty("comments");
    expect(getComments("s2")).toHaveLength(1);
  });

  it("while a reply runs: Ctrl+Enter makes the comments wait with the message; a note leaves them", async () => {
    openChat("s3", { running: true });
    await waitFor(
      () => expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/chat/turns/tr1/stream"), expect.anything()),
      { timeout: 10_000 },
    );
    await act(async () => {
      live.push("resume", { resumed: false, running: true });
      live.push("caught_up", { id: "" });
      live.push("token", { text: "working " });
      await new Promise((r) => setTimeout(r, 40));
    });
    await waitFor(() => expect(screen.getByTestId("harness-live-text")).toHaveTextContent("working"));
    await commentThroughDialog("Use a constant");
    await screen.findByTestId("comment-chip");
    // A note (Enter): words only; the comment stays above the box.
    fireEvent.change(chatBox(), { target: { value: "a note" } });
    fireEvent.keyDown(chatBox(), { key: "Enter" });
    await waitFor(() => expect(posted("/chat/turns/tr1/steer")).toHaveLength(1));
    expect(posted("/chat/turns/tr1/steer")[0].body).toEqual({ text: "a note" });
    expect(screen.getByTestId("comment-chip")).toBeTruthy();
    expect(chat.turns).toHaveLength(0);
    // Ctrl+Enter: the message waits, with the comment.
    await waitFor(() => expect(chatBox().value).toBe(""));
    fireEvent.change(chatBox(), { target: { value: "after it" } });
    fireEvent.keyDown(chatBox(), { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(H.sent.filter((s) => s.method === "PUT" && s.path === "/chat/threads/s3/queue")).toHaveLength(1));
    const kept = H.sent.find((s) => s.method === "PUT" && s.path === "/chat/threads/s3/queue")!.body as { items: unknown[] };
    expect(kept.items).toEqual([{ text: "after it", comments: [SENT] }]);
    await waitFor(() => expect(screen.queryByTestId("comment-chip")).toBeNull());
    expect(screen.getByTestId("harness-queued").textContent).toContain("after it · 1 comment");
  });

  it("a rewind puts the message's comments back above the box, and the sent message showed them", async () => {
    const messages = [
      { role: "user", content: "fix these", turnId: "t0", at: "2026-11-19T10:00:00", comments: [SENT] },
      { role: "assistant", content: "Fixed them.", turnId: "t0", timing: { startedAt: 1, firstTokenAt: 2, endedAt: 3 } },
    ];
    H.reply["/build/sessions/s4/rewind"] = { question: "fix these", files: [], note: "", comments: [SENT] };
    openChat("s4", { messages });
    H.gets["/build/sessions/s4/rewind?to=t0"] = {
      to: "t0",
      question: "fix these",
      turns: ["t0"],
      files: [],
      problems: [],
      commands: [],
      can_rewind: true,
      comments: [SENT],
    };
    expect(await screen.findByText("Fixed them.", {}, { timeout: 10_000 })).toBeInTheDocument();
    expect(screen.getByTestId("sent-comment").textContent).toBe("cart.py 1–2: Use a constant");
    expect(screen.queryByTestId("comment-chip")).toBeNull();
    fireEvent.click(screen.getByTestId("rewind-start"));
    fireEvent.click(await screen.findByTestId("rewind-confirm-button"));
    await waitFor(() => expect(posted("/build/sessions/s4/rewind")).toHaveLength(1));
    await waitFor(() =>
      expect(within(screen.getByTestId("harness-composer")).getByTestId("comment-chip").textContent).toBe(
        "cart.py 1–2: Use a constant",
      ),
    );
    expect(chatBox().value).toBe("fix these");
  });
});

/* ============================================ one line, or a range (phase 3b) */

const F = "C:\\work\\shop\\f.py";
// a (10|10) · -b (old 11) · -c (old 12) · +B (11) · +C (12) · +D (13) · e (13|14)
const LINES_DIFF = "--- a/f.py\n+++ b/f.py\n@@ -10,4 +10,5 @@\n a\n-b\n-c\n+B\n+C\n+D\n e";
const lined = (diff = LINES_DIFF): FileChange => ({
  path: F,
  rel: "f.py",
  name: "f.py",
  status: "modified",
  added: 3,
  removed: 2,
  diff,
});

/** The gutter's press for a line (every drawing of it). */
const lineBtns = (n: number, removed = false) =>
  screen.getAllByRole("button", { name: removed ? `Comment on removed line ${n}` : `Comment on line ${n}` });
const lineBtn = (n: number, removed = false) => lineBtns(n, removed)[0];
async function cancelDialog() {
  fireEvent.click(screen.getByTestId("review-comment-cancel"));
  await waitFor(() => expect(screen.queryByTestId("review-comment-dialog")).toBeNull());
}
/** The drawn line rows of the one-column layout, as markup. */
const lineRowsOf = (root: Element) =>
  Array.from(root.querySelectorAll('[data-testid="diff-unified"] > [data-kind]'))
    .filter((el) => el.getAttribute("data-kind") !== "hunk")
    .map((el) => el.outerHTML);

describe("a line, or a range of lines", () => {
  it("the helpers: which line a row is, what a press picks, what the lines quote", () => {
    const rows = parseUnified(LINES_DIFF); // the header, then a · -b · -c · +B · +C · +D · e
    expect(rows.map((r, i) => lineOfRow(r, i))).toEqual([
      null,
      { side: "new", line: 10, at: 1 },
      { side: "old", line: 11, at: 2 },
      { side: "old", line: 12, at: 3 },
      { side: "new", line: 11, at: 4 },
      { side: "new", line: 12, at: 5 },
      { side: "new", line: 13, at: 6 },
      { side: "new", line: 14, at: 7 },
    ]);
    expect(commentLineLabel({ side: "new", line: 14 })).toBe("Comment on line 14");
    expect(commentLineLabel({ side: "old", line: 11 })).toBe("Comment on removed line 11");
    const at = (i: number): LineRef => lineOfRow(rows[i], i)!;
    // A plain press: that one line, whatever was pressed before.
    expect(pressLines(at(4), at(6), false)).toEqual({ side: "new", from: 6, to: 6, anchor: at(6) });
    // Shift: the rows between, in either order; the first line stays the anchor.
    expect(pressLines(at(4), at(6), true)).toEqual({ side: "new", from: 4, to: 6, anchor: at(4) });
    expect(pressLines(at(7), at(1), true)).toEqual({ side: "new", from: 1, to: 7, anchor: at(7) });
    // Shift with nothing pressed before, or across sides: a new single line.
    expect(pressLines(null, at(5), true)).toEqual({ side: "new", from: 5, to: 5, anchor: at(5) });
    expect(pressLines(at(5), at(2), true)).toEqual({ side: "old", from: 2, to: 2, anchor: at(2) });
    expect(pressLines(at(2), at(5), true)).toEqual({ side: "new", from: 5, to: 5, anchor: at(5) });
    // The quote is exactly those lines, with their marks.
    expect(linesComment(rows, "new", 5, 5)).toEqual({ side: "new", start: 12, end: 12, quote: "+C", cut: "" });
    expect(linesComment(rows, "new", 7, 1)).toEqual({
      side: "new",
      start: 10,
      end: 14,
      quote: " a\n+B\n+C\n+D\n e",
      cut: "",
    });
    expect(linesComment(rows, "old", 2, 3)).toEqual({ side: "old", start: 11, end: 12, quote: "-b\n-c", cut: "" });
    expect(linesComment(rows, "old", 1, 7)).toEqual({
      side: "old",
      start: 10,
      end: 13,
      quote: " a\n-b\n-c\n e",
      cut: "",
    });
    expect(linesComment(rows, "old", 4, 6)).toBeNull(); // added lines are no lines of the old file
    expect(linesComment(rows, "new", 40, 50)).toBeNull();
    expect(linesComment(rows, "new", 0, 0)).toBeNull(); // the header is no line
  });

  it("without the flag the gutter is exactly DiffView's own: no button, the same markup", () => {
    const own = render(<DiffBody change={lined()} />);
    const plain = lineRowsOf(own.container);
    expect(plain).toHaveLength(7);
    // The markup the diff dialog always drew for a line (two plain numbers).
    expect(plain[0]).toBe(
      '<div data-kind="same" class="flex ">' +
        '<span class="w-10 shrink-0 select-none pr-2 text-right tabular-nums text-zinc-600">10</span>' +
        '<span class="w-10 shrink-0 select-none pr-2 text-right tabular-nums text-zinc-600">10</span>' +
        '<span class="w-4 shrink-0 select-none"><span> </span></span>' +
        '<span class="min-w-0 whitespace-pre-wrap break-all pr-3 text-zinc-300">a</span></div>',
    );
    expect(own.container.querySelector("button")).toBeNull();
    expect(own.container.querySelector("[data-line-press], [role='button'], [tabindex]")).toBeNull();
    own.unmount();
    const off = render(<ChangesReview changes={[lined()]} sessionId="s1" />);
    const rows = screen.getByTestId("review-rows-0");
    expect(lineRowsOf(rows)).toEqual(plain);
    expect(rows.querySelector("[data-line-press]")).toBeNull();
    off.unmount();
    // With the flag: one press a line, and only the number differs.
    render(<ChangesReview changes={[lined()]} sessionId="s1" comments />);
    expect(screen.getByTestId("review-rows-0").querySelectorAll("button[data-line-press]")).toHaveLength(7);
    expect(lineBtns(10)).toHaveLength(1); // an unchanged line: its NEW number only
  });

  it("a press on a line's number opens the one dialog for that line", async () => {
    render(<ChangesReview changes={[lined()]} sessionId="s1" comments commentView="session" />);
    fireEvent.click(lineBtn(12));
    const box = await screen.findByTestId("review-comment-words");
    expect(screen.getAllByTestId("review-comment-dialog")).toHaveLength(1);
    expect(screen.getByTestId("review-comment-where").textContent).toBe("f.py, line 12");
    expect(screen.getByTestId("review-comment-quote").textContent).toBe("+C");
    fireEvent.change(box, { target: { value: "Name this" } });
    fireEvent.click(screen.getByTestId("review-comment-save"));
    await waitFor(() => expect(screen.queryByTestId("review-comment-dialog")).toBeNull());
    expect(commentsBody(getComments("s1"))).toEqual([
      { path: F, side: "new", start: 12, end: 12, quote: "+C", words: "Name this" },
    ]);
    expect(getComments("s1")[0].mark).toBe(markOf(lined()));
    expect(getComments("s1")[0].view).toBe("session");
  });

  it("Shift+press makes it the lines between the two, in either direction", async () => {
    render(<ChangesReview changes={[lined()]} sessionId="s1" comments />);
    fireEvent.click(lineBtn(11));
    await screen.findByTestId("review-comment-dialog");
    expect(screen.getByTestId("review-comment-where").textContent).toBe("f.py, line 11");
    await cancelDialog();
    fireEvent.click(lineBtn(13), { shiftKey: true });
    await screen.findByTestId("review-comment-dialog");
    expect(screen.getByTestId("review-comment-where").textContent).toBe("f.py, lines 11–13");
    expect(screen.getByTestId("review-comment-quote").textContent).toBe("+B\n+C\n+D");
    await cancelDialog();
    // The other way round: press the LAST line first, then Shift+press above.
    fireEvent.click(lineBtn(14));
    await screen.findByTestId("review-comment-dialog");
    await cancelDialog();
    fireEvent.click(lineBtn(10), { shiftKey: true });
    const box = await screen.findByTestId("review-comment-words");
    expect(screen.getByTestId("review-comment-where").textContent).toBe("f.py, lines 10–14");
    // The removed lines drawn between them are not lines of the new file.
    expect(screen.getByTestId("review-comment-quote").textContent).toBe(" a\n+B\n+C\n+D\n e");
    fireEvent.change(box, { target: { value: "Tidy these" } });
    fireEvent.click(screen.getByTestId("review-comment-save"));
    await waitFor(() => expect(screen.queryByTestId("review-comment-dialog")).toBeNull());
    expect(commentsBody(getComments("s1"))).toEqual([
      { path: F, side: "new", start: 10, end: 14, quote: " a\n+B\n+C\n+D\n e", words: "Tidy these" },
    ]);
  });

  it("a removed line is commented on as it was, by its old number", async () => {
    render(<ChangesReview changes={[lined()]} sessionId="s1" comments />);
    fireEvent.click(lineBtn(11, true));
    const box = await screen.findByTestId("review-comment-words");
    expect(screen.getByTestId("review-comment-where").textContent).toBe("f.py, line 11 (as it was before the change)");
    expect(screen.getByTestId("review-comment-quote").textContent).toBe("-b");
    fireEvent.change(box, { target: { value: "Why did this go?" } });
    fireEvent.click(screen.getByTestId("review-comment-save"));
    await waitFor(() => expect(screen.queryByTestId("review-comment-dialog")).toBeNull());
    expect(commentsBody(getComments("s1"))).toEqual([
      { path: F, side: "old", start: 11, end: 11, quote: "-b", words: "Why did this go?" },
    ]);
  });

  it("a Shift+press across sides starts again at that line; a range never mixes old and new", async () => {
    render(<ChangesReview changes={[lined()]} sessionId="s1" comments />);
    fireEvent.click(lineBtn(12));
    await screen.findByTestId("review-comment-dialog");
    await cancelDialog();
    fireEvent.click(lineBtn(11, true), { shiftKey: true });
    await screen.findByTestId("review-comment-dialog");
    expect(screen.getByTestId("review-comment-where").textContent).toBe("f.py, line 11 (as it was before the change)");
    expect(screen.getByTestId("review-comment-quote").textContent).toBe("-b");
    await cancelDialog();
    // From there a Shift+press on another REMOVED line is a range of old lines.
    fireEvent.click(lineBtn(12, true), { shiftKey: true });
    await screen.findByTestId("review-comment-dialog");
    expect(screen.getByTestId("review-comment-where").textContent).toBe("f.py, lines 11–12 (as it was before the change)");
    expect(screen.getByTestId("review-comment-quote").textContent).toBe("-b\n-c");
  });

  it("the line pressed first says so (aria-pressed)", async () => {
    render(<ChangesReview changes={[lined()]} sessionId="s1" comments />);
    const btn = lineBtn(13) as HTMLButtonElement;
    expect(btn.tagName).toBe("BUTTON");
    expect(btn.type).toBe("button");
    expect(btn.textContent).toBe("13");
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(btn);
    await screen.findByTestId("review-comment-dialog");
    await cancelDialog();
    expect(lineBtn(13).getAttribute("aria-pressed")).toBe("true");
    expect(lineBtn(12).getAttribute("aria-pressed")).toBe("false");
    expect(lineBtn(11, true).getAttribute("aria-pressed")).toBe("false");
  });

  it("the keyboard: ONE Tab stop per file's lines; arrows, Home and End move; Enter and Space press", async () => {
    render(<ChangesReview changes={[lined(), notes()]} sessionId="s1" comments />);
    const tabbable = (i: number) =>
      Array.from(screen.getByTestId(`review-rows-${i}`).querySelectorAll<HTMLButtonElement>("button[data-line-press]")).filter(
        (b) => b.tabIndex === 0,
      );
    const all = (i: number) => screen.getByTestId(`review-rows-${i}`).querySelectorAll("button[data-line-press]");
    // Seven line numbers in the first file, one in the second: ONE stop each
    // (the first line), not one per line.
    expect(all(0)).toHaveLength(7);
    expect(tabbable(0)).toEqual([lineBtn(10)]);
    expect(tabbable(1)).toHaveLength(1);
    expect(Array.from(all(0)).filter((b) => b.getAttribute("tabindex") === "-1")).toHaveLength(6);
    // The block's own Comment press stays an ordinary Tab stop.
    expect(screen.getAllByTestId("review-hunk-comment-0")[0].getAttribute("tabindex")).toBeNull();
    // Arrows walk the numbers in the order drawn; the focused one is the stop.
    const first = lineBtn(10);
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(document.activeElement).toBe(lineBtn(11, true));
    expect(tabbable(0)).toEqual([lineBtn(11, true)]);
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    expect(document.activeElement).toBe(lineBtn(11));
    fireEvent.keyDown(document.activeElement!, { key: "ArrowUp" });
    expect(document.activeElement).toBe(lineBtn(12, true));
    fireEvent.keyDown(document.activeElement!, { key: "End" });
    expect(document.activeElement).toBe(lineBtn(14));
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" }); // the last: stays
    expect(document.activeElement).toBe(lineBtn(14));
    fireEvent.keyDown(document.activeElement!, { key: "Home" });
    expect(document.activeElement).toBe(lineBtn(10));
    expect(tabbable(0)).toEqual([lineBtn(10)]);
    expect(tabbable(1)).toHaveLength(1); // the other file keeps its own stop
    // Enter presses the focused number...
    fireEvent.keyDown(lineBtn(11), { key: "Enter" });
    await screen.findByTestId("review-comment-dialog");
    expect(screen.getByTestId("review-comment-where").textContent).toBe("f.py, line 11");
    await cancelDialog();
    // ...Shift+Enter extends to the lines between...
    fireEvent.keyDown(lineBtn(13), { key: "Enter", shiftKey: true });
    await screen.findByTestId("review-comment-dialog");
    expect(screen.getByTestId("review-comment-where").textContent).toBe("f.py, lines 11–13");
    await cancelDialog();
    // ...and Space presses too (Shift+Space extends).
    fireEvent.keyDown(lineBtn(14), { key: " " });
    await screen.findByTestId("review-comment-dialog");
    expect(screen.getByTestId("review-comment-where").textContent).toBe("f.py, line 14");
    await cancelDialog();
    fireEvent.keyDown(lineBtn(12), { key: " ", shiftKey: true });
    await screen.findByTestId("review-comment-dialog");
    expect(screen.getByTestId("review-comment-where").textContent).toBe("f.py, lines 12–14");
    await cancelDialog();
    // Another key does nothing.
    fireEvent.keyDown(lineBtn(12), { key: "a" });
    expect(screen.queryByTestId("review-comment-dialog")).toBeNull();
  });

  it("the wide view: a line press in either drawing opens the ONE dialog", async () => {
    render(<ChangesReview changes={[lined()]} sessionId="s1" comments />);
    fireEvent.click(screen.getByTestId("review-wide-open"));
    await screen.findByTestId("review-wide");
    // The pane, and the wide view's two layouts: one press each, three in all.
    expect(lineBtns(12)).toHaveLength(3);
    expect(lineBtns(11, true)).toHaveLength(3);
    expect(lineBtns(10)).toHaveLength(3);
    fireEvent.click(lineBtns(12)[2]); // the side-by-side drawing
    await screen.findByTestId("review-comment-dialog");
    expect(screen.getAllByTestId("review-comment-dialog")).toHaveLength(1);
    expect(screen.getByTestId("review-comment-where").textContent).toBe("f.py, line 12");
    await cancelDialog();
    // The same drawing's other layout extends it.
    fireEvent.click(lineBtns(13)[1], { shiftKey: true });
    await screen.findByTestId("review-comment-dialog");
    expect(screen.getAllByTestId("review-comment-dialog")).toHaveLength(1);
    expect(screen.getByTestId("review-comment-where").textContent).toBe("f.py, lines 12–13");
    expect(screen.getByTestId("review-wide")).toBeTruthy();
  });

  it("a file that changes again forgets the line pressed before (its numbers moved)", async () => {
    const { rerender } = render(<ChangesReview changes={[lined()]} sessionId="s1" comments />);
    fireEvent.click(lineBtn(11));
    await screen.findByTestId("review-comment-dialog");
    await cancelDialog();
    rerender(<ChangesReview changes={[lined(`${LINES_DIFF}\n+f`)]} sessionId="s1" comments />);
    await waitFor(() => expect(lineBtn(11).getAttribute("aria-pressed")).toBe("false"));
    fireEvent.click(lineBtn(13), { shiftKey: true });
    await screen.findByTestId("review-comment-dialog");
    expect(screen.getByTestId("review-comment-where").textContent).toBe("f.py, line 13");
  });

  it("a line press redraws its own file's rows only; marking one file Reviewed redraws that file only", async () => {
    render(<ChangesReview changes={[lined(), notes()]} sessionId="s1" comments />);
    for (const id of ["review-open-0", "review-open-1", "review-hunk-open-0", "review-hunk-open-1"]) {
      expect(H.byId[id]).toBeGreaterThan(0); // each IS counted
    }
    const before = { ...H.byId };
    fireEvent.click(lineBtn(12));
    await screen.findByTestId("review-comment-dialog");
    await cancelDialog();
    fireEvent.click(lineBtn(13), { shiftKey: true });
    await screen.findByTestId("review-comment-dialog");
    await cancelDialog();
    // The other file: not its row, not its lines. This file: its lines only.
    expect(H.byId["review-open-1"]).toBe(before["review-open-1"]);
    expect(H.byId["review-hunk-open-1"]).toBe(before["review-hunk-open-1"]);
    expect(H.byId["review-open-0"]).toBe(before["review-open-0"]);
    expect(H.byId["review-hunk-open-0"]).toBeGreaterThan(before["review-hunk-open-0"]);
    const settled = { ...H.byId };
    fireEvent.click(screen.getByTestId("review-mark-0"));
    expect(H.byId["review-open-0"]).toBeGreaterThan(settled["review-open-0"]);
    expect(H.byId["review-open-1"]).toBe(settled["review-open-1"]);
    expect(H.byId["review-hunk-open-1"]).toBe(settled["review-hunk-open-1"]);
  });
});

/* ============================================ Chat › Code: the Branch view */

describe("Chat › Code's Branch view", () => {
  afterEach(() => {
    live?.end();
    turnStream?.end();
  });

  it("Comment there adds the comment under the session's own key, as a branch comment", async () => {
    const WT = "C:\\work\\shop.ij-worktrees\\session-3f2a";
    const branchCart = { ...cart(), path: `${WT}\\cart.py` };
    const gitStatus = {
      exists: true,
      ahead: 1,
      behind: 0,
      uncommitted: 1,
      remote: "origin",
      pushed: false,
      unpushed: 1,
      in_base: false,
      base_exists: true,
      base_checked_out_at: "C:\\work\\shop",
      commits: [{ sha: "1a2b3c4", subject: "Add the cart" }],
      pr_draft: { title: "Add the cart", body: "" },
      compare_url: "",
      gh: { installed: true, signed_in: true, note: "" },
    };
    const gitDiff = {
      changes: [branchCart],
      files: 1,
      added: 1,
      removed: 1,
      truncated_files: false,
      base: "main",
      merge_base: "abc123def456",
    };
    openChat("s5", {
      gets: { "/build/sessions/s5/git": gitStatus, "/build/sessions/s5/git/diff": gitDiff },
      over: {
        folder: WT,
        git: {
          branch: "ij/session-3f2a",
          base: "main",
          base_commit: "abc123def456",
          worktree: WT,
          checkout: "C:\\work\\shop",
          repo: "C:\\work\\shop",
          merged: false,
          merged_into: "",
          removed: false,
          pr_url: "",
        },
      },
    });
    expect(await screen.findByText("Added the cart.", {}, { timeout: 10_000 })).toBeInTheDocument();
    // The Changes tab opened on the Branch view (the session has its own branch).
    await screen.findByTestId("changes-review", {}, { timeout: 10_000 });
    expect(screen.getByTestId("harness-session").getAttribute("data-session")).toBe("s5");
    await commentThroughDialog("Use a constant");
    const [c] = getComments("s5");
    expect(commentsBody([c])).toEqual([{ ...SENT, path: branchCart.path }]);
    expect(c.view).toBe("branch");
    expect(getComments("build:s5")).toHaveLength(0);
    await waitFor(() =>
      expect(within(screen.getByTestId("harness-composer")).getByTestId("comment-chip").textContent).toBe(
        "cart.py 1–2: Use a constant",
      ),
    );
    // A line's number is a press there too.
    expect(lineBtns(2).length).toBeGreaterThan(0);
  });
});

/* ===================================================== the review's findings */

describe("review: nothing is lost, nothing is spent", () => {
  const refusal = "A comment names a file that is not in this session's folder, so nothing was sent.";

  /** Two Build › Code sessions; `a1` is the one opened first. */
  function openTwo() {
    openCode("a1");
    H.gets["/build/app/sessions"] = { sessions: [codeSess("a1"), codeSess("a2")] };
    H.gets["/build/app/sessions/a2"] = {
      session: codeSess("a2"),
      events: CODE_EVENTS,
      running: null,
      pending: [],
      takes_comments: true,
    };
    H.gets["/build/app/sessions/a2/changes"] = { ...CODE_CHANGES, changes: [] };
  }

  it("Build › Code: a refusal that lands after another session was opened still puts the comments back", async () => {
    openTwo();
    let letGo: () => void = () => {};
    H.late = new Promise<void>((r) => {
      letGo = r;
    });
    H.scripts = [[{ late: { type: "error", detail: refusal, status: 400 } }]];
    const view = render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await commentThroughDialog("Use a constant");
    await screen.findByTestId("comment-chip");
    type("fix these");
    fireEvent.click(screen.getByTestId("code-send"));
    await waitFor(() => expect(turnBody("a1")).toMatchObject({ comments: [SENT] }));
    expect(getComments("code:a1")).toHaveLength(0); // they left the box with the words
    // The user opens the other session before the answer is read...
    view.rerender(<BuildCodeView initialSession="a2" linkSeq={1} onOpenedTerminal={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("code-session").getAttribute("data-session")).toBe("a2"), {
      timeout: 10_000,
    });
    await screen.findByTestId("code-box", {}, { timeout: 10_000 });
    // ...and the refusal arrives.
    await act(async () => {
      letGo();
      await new Promise((r) => setTimeout(r, 30));
    });
    await waitFor(() => expect(commentsBody(getComments("code:a1"))).toEqual([SENT]));
    // Under ITS session: the one on screen holds none, and shows none.
    expect(getComments("code:a2")).toHaveLength(0);
    expect(screen.queryByTestId("comment-chip")).toBeNull();
    expect((screen.getByTestId("code-box") as HTMLTextAreaElement).value).toBe("");
  });

  it("Build › Code: a waiting message that goes as a message after a switch goes to ITS session, and a refusal puts its comments back there", async () => {
    openTwo();
    // The first stream is the running reply; the second (the message) is refused.
    const asked = { type: "user", at: AT2, text: "working on it", files: [], turn: "turn_1" };
    (H.gets["/build/app/sessions/a1"] as Record<string, unknown>).events = [...CODE_EVENTS, asked];
    (H.gets["/build/app/sessions/a1"] as Record<string, unknown>).running = "turn_1";
    H.scripts = [[{ event: "user", data: asked }], [{ yield: { type: "error", detail: refusal, status: 400 } }]];
    holdStreams();
    let letGo: () => void = () => {};
    H.gate["/build/app/sessions/a1/queue"] = new Promise<void>((r) => {
      letGo = r;
    });
    H.fail["/build/app/sessions/a1/queue"] = { status: 409, message: "not working", body: { code: "not_working" } };
    const view = render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-stop", {}, { timeout: 10_000 });
    await commentThroughDialog("Use a constant");
    await screen.findByTestId("comment-chip");
    type("then this");
    fireEvent.keyDown(screen.getByTestId("code-box"), { key: "Enter" });
    await waitFor(() => expect(posted("/build/app/sessions/a1/queue")).toHaveLength(1));
    view.rerender(<BuildCodeView initialSession="a2" linkSeq={1} onOpenedTerminal={() => {}} />);
    await waitFor(() => expect(screen.queryByTestId("code-stop")).toBeNull());
    H.release?.(); // (the left session's stream ends)
    await act(async () => {
      letGo();
      await new Promise((r) => setTimeout(r, 30));
    });
    // The message went to the session it was written in, never the one on screen.
    await waitFor(() => expect(turnBody("a1")).toMatchObject({ text: "then this", comments: [SENT] }));
    expect(turnBody("a2")).toBeUndefined();
    // Refused there: its comments are back under a1's key.
    await waitFor(() => expect(commentsBody(getComments("code:a1"))).toEqual([SENT]));
    expect(getComments("code:a2")).toHaveLength(0);
    // The rule itself: a handed message is for one session only.
    expect(sameSession("a1", "a1")).toBe(true);
    expect(sameSession("a2", "a1")).toBe(false);
  });

  it("Build › Code: a typed command takes no comments — they stay as chips, now and while a reply runs", async () => {
    openCode("a1");
    holdStreams();
    codeView("a1");
    await commentThroughDialog("Use a constant");
    await screen.findByTestId("comment-chip");
    type("/compact");
    fireEvent.click(screen.getByTestId("code-send"));
    await waitFor(() => expect(turnBody("a1")).toBeTruthy());
    expect(turnBody("a1")).toMatchObject({ text: "/compact" });
    expect(turnBody("a1")).not.toHaveProperty("comments");
    expect(screen.getByTestId("comment-chip").textContent).toBe("cart.py 1–2: Use a constant");
    expect(getComments("code:a1")).toHaveLength(1);
    // The command's own bubble shows no comment.
    expect(within(screen.getAllByTestId("code-user").at(-1)!).queryByTestId("sent-comment")).toBeNull();
    // While it runs, a queued command leaves them too.
    await screen.findByTestId("code-stop");
    H.reply["/build/app/sessions/a1/queue"] = { ok: true, id: "q1", queued: [{ id: "q1", text: "/model opus", files: [] }] };
    type("/model opus");
    fireEvent.keyDown(screen.getByTestId("code-box"), { key: "Enter" });
    await waitFor(() => expect(posted("/build/app/sessions/a1/queue")).toHaveLength(1));
    expect(posted("/build/app/sessions/a1/queue")[0].body).toEqual({ text: "/model opus" });
    expect(screen.getByTestId("comment-chip")).toBeTruthy();
    expect(getComments("code:a1")).toHaveLength(1);
  });

  it("Build › Code: deleting a session ends its waiting comments; a refused delete keeps them", async () => {
    addComment("code:a1", body());
    addComment("code:a2", body({ words: "another session's" }));
    openCode("a1");
    H.gets["/build/app/sessions"] = { sessions: [codeSess("a1"), codeSess("a2")] };
    H.fail["/build/app/sessions/a2"] = { status: 409, message: "This session's reply is still running. Stop it first, then delete it." };
    codeView("a1");
    const press = async (id: string) => {
      fireEvent.click(await screen.findByTestId(`code-row-more-${id}`, {}, { timeout: 10_000 }));
      const del = screen.getByTestId(`code-row-delete-${id}`).querySelector("button") as HTMLButtonElement;
      fireEvent.click(del);
      fireEvent.click(del);
    };
    await press("a2");
    await screen.findByTestId("code-row-note");
    expect(getComments("code:a2")).toHaveLength(1); // refused: nothing ended
    await press("a1");
    await waitFor(() => expect(H.sent.some((x) => x.method === "DELETE" && x.path === "/build/app/sessions/a1")).toBe(true));
    await waitFor(() => expect(getComments("code:a1")).toHaveLength(0));
    expect(window.localStorage.getItem(`${COMMENTS_KEY_PREFIX}code:a1`)).toBeNull();
    expect(getComments("code:a2")).toHaveLength(1);
  });

  it("Chat › Code: deleting a session ends its waiting comments; a refused delete keeps them", async () => {
    addComment("s8", body());
    addComment("s9", body());
    H.fail["/build/sessions/s9"] = { status: 409, message: "This session's reply is still running." };
    await expect(deleteSession("s9")).rejects.toThrow("still running");
    expect(getComments("s9")).toHaveLength(1);
    await deleteSession("s8");
    expect(getComments("s8")).toHaveLength(0);
    expect(window.localStorage.getItem(`${COMMENTS_KEY_PREFIX}s8`)).toBeNull();
    expect(getComments("s9")).toHaveLength(1);
  });

  /** A diff that joins two edits of one file: both blocks say "@@ -1 +1 @@". */
  const twice: FileChange = {
    path: F,
    rel: "f.py",
    name: "f.py",
    status: "modified",
    added: 2,
    removed: 2,
    diff: "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-a\n+b\n@@ -1 +1 @@\n-c\n+d",
  };

  it("the wide view's side-by-side drawing: its own ONE Tab stop, its arrows, and its second same-header block", async () => {
    render(<ChangesReview changes={[twice]} sessionId="s1" comments />);
    fireEvent.click(screen.getByTestId("review-wide-open"));
    const wide = await screen.findByTestId("review-wide");
    const split = within(wide).getByTestId("diff-split");
    const nums = () => Array.from(split.querySelectorAll<HTMLButtonElement>("button[data-line-press]"));
    const stops = (root: Element) =>
      Array.from(root.querySelectorAll<HTMLButtonElement>("button[data-line-press]")).filter((b) => b.tabIndex === 0);
    expect(nums()).toHaveLength(4); // -a | +b, then -c | +d
    expect(stops(split)).toEqual([nums()[0]]);
    // The one-column drawing beside it, and the pane behind, keep their own.
    expect(stops(within(wide).getByTestId("diff-unified"))).toHaveLength(1);
    expect(stops(screen.getAllByTestId("review-rows-0")[0])).toHaveLength(1);
    nums()[0].focus();
    fireEvent.keyDown(nums()[0], { key: "ArrowDown" });
    expect(document.activeElement).toBe(nums()[1]);
    expect(stops(split)).toEqual([nums()[1]]);
    fireEvent.keyDown(nums()[1], { key: "End" });
    expect(document.activeElement).toBe(nums()[3]);
    expect(stops(split)).toEqual([nums()[3]]);
    // The second block's Comment, in THIS drawing.
    fireEvent.click(within(split).getAllByTestId("review-hunk-comment-0")[1]);
    await screen.findByTestId("review-comment-dialog");
    expect(screen.getAllByTestId("review-comment-dialog")).toHaveLength(1);
    expect(screen.getByTestId("review-comment-quote").textContent).toBe("-c\n+d");
  });

  it("the Comment on the SECOND of two blocks with the same header quotes the second; a line press too", async () => {
    render(<ChangesReview changes={[twice]} sessionId="s1" comments />);
    const presses = screen.getAllByTestId("review-hunk-comment-0");
    expect(presses).toHaveLength(2);
    fireEvent.click(presses[1]);
    await screen.findByTestId("review-comment-dialog");
    expect(screen.getByTestId("review-comment-quote").textContent).toBe("-c\n+d");
    await cancelDialog();
    fireEvent.click(presses[0]);
    await screen.findByTestId("review-comment-dialog");
    expect(screen.getByTestId("review-comment-quote").textContent).toBe("-a\n+b");
    await cancelDialog();
    // Both blocks have a "line 1": each number presses its OWN row.
    expect(lineBtns(1)).toHaveLength(2);
    fireEvent.click(lineBtns(1)[1]);
    await screen.findByTestId("review-comment-dialog");
    expect(screen.getByTestId("review-comment-quote").textContent).toBe("+d");
    await cancelDialog();
    expect(lineBtns(1)[1].getAttribute("aria-pressed")).toBe("true");
    expect(lineBtns(1)[0].getAttribute("aria-pressed")).toBe("false"); // the same number, another row
    fireEvent.click(lineBtns(1)[0], { shiftKey: true });
    await screen.findByTestId("review-comment-dialog");
    expect(screen.getByTestId("review-comment-quote").textContent).toBe("+b\n+d");
  });

  it("a quote that is cut says so under it, and is stored ending on a whole line", async () => {
    const many = Array.from({ length: 900 }, (_, i) => `+line number ${i}`);
    const big: FileChange = {
      path: F,
      rel: "f.py",
      name: "f.py",
      status: "created",
      added: 900,
      removed: 0,
      diff: `--- /dev/null\n+++ b/f.py\n@@ -0,0 +1,900 @@\n${many.join("\n")}`,
    };
    const held = quoteLines(parseUnified(big.diff).slice(1)).lines;
    render(<ChangesReview changes={[big, cart()]} sessionId="s1" comments />);
    fireEvent.click(screen.getByTestId("review-hunk-comment-0"));
    const box = await screen.findByTestId("review-comment-words");
    expect(screen.getByTestId("review-comment-cut").textContent).toBe(`Only the first ${held} lines are quoted.`);
    fireEvent.change(box, { target: { value: "Too long" } });
    fireEvent.click(screen.getByTestId("review-comment-save"));
    await waitFor(() => expect(screen.queryByTestId("review-comment-dialog")).toBeNull());
    const [c] = getComments("s1");
    expect(c.quote.length).toBeLessThanOrEqual(QUOTE_CHARS);
    expect(c.quote.split("\n")).toEqual(many.slice(0, held));
    expect([c.start, c.end]).toEqual([1, 900]); // the comment is still about the whole block
    // A block that fits says nothing.
    fireEvent.click(screen.getByTestId("review-hunk-comment-1"));
    await screen.findByTestId("review-comment-dialog");
    expect(screen.queryByTestId("review-comment-cut")).toBeNull();
  });
});

describe("review: Chat › Code", () => {
  afterEach(() => {
    live?.end();
    turnStream?.end();
  });

  async function running(id: string) {
    openChat(id, { running: true });
    await waitFor(
      () => expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/chat/turns/tr1/stream"), expect.anything()),
      { timeout: 10_000 },
    );
    await act(async () => {
      live.push("resume", { resumed: false, running: true });
      live.push("caught_up", { id: "" });
      live.push("token", { text: "working " });
      await new Promise((r) => setTimeout(r, 40));
    });
    await waitFor(() => expect(screen.getByTestId("harness-live-text")).toHaveTextContent("working"));
  }

  it("a waiting message that could not be saved says so, and still waits in this window", async () => {
    await running("s6");
    H.fail["/chat/threads/s6/queue"] = { status: 413, message: "the waiting messages are too large to keep" };
    await commentThroughDialog("Use a constant");
    await screen.findByTestId("comment-chip");
    fireEvent.change(chatBox(), { target: { value: "after it" } });
    fireEvent.keyDown(chatBox(), { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(screen.getByTestId("harness-session").textContent).toContain(queueNotSavedWords(1)));
    expect(queueNotSavedWords(1)).toBe("This waiting message could not be saved, so it will be lost if the page reloads.");
    expect(queueNotSavedWords(2)).toContain("These waiting messages");
    // Nothing is lost yet: it waits, with its comment.
    expect(screen.getByTestId("harness-queued").textContent).toContain("after it · 1 comment");
  });

  it("a saved waiting message says nothing", async () => {
    await running("s61");
    await commentThroughDialog("Use a constant");
    await screen.findByTestId("comment-chip");
    fireEvent.change(chatBox(), { target: { value: "after it" } });
    fireEvent.keyDown(chatBox(), { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(screen.getByTestId("harness-queued").textContent).toContain("after it · 1 comment"));
    await pause(30);
    expect(screen.getByTestId("harness-session").textContent).not.toContain("could not be saved");
  });

  it("a typed command of Claude Code's own takes no comments: they stay as chips", async () => {
    openChat("s7", {
      gets: {
        "/build/sessions/s7/commands": {
          commands: [{ name: "review", description: "Review the changes", source: "project", kind: "command" }],
        },
      },
    });
    expect(await screen.findByText("Added the cart.", {}, { timeout: 10_000 })).toBeInTheDocument();
    await commentThroughDialog("Use a constant");
    await screen.findByTestId("comment-chip");
    fireEvent.change(chatBox(), { target: { value: "/review app.py" } });
    // (Enter runs it by name once the session's command list is read.)
    const hint = await screen.findByTestId("harness-slash-invoking");
    expect(hint.getAttribute("data-name")).toBe("review");
    fireEvent.keyDown(chatBox(), { key: "Enter" });
    await waitFor(() => expect(chat.turns).toHaveLength(1));
    expect(chat.turns[0].body).toMatchObject({ command: "review", text: "app.py" });
    expect(chat.turns[0].body).not.toHaveProperty("comments");
    expect(screen.getByTestId("comment-chip").textContent).toBe("cart.py 1–2: Use a constant");
    expect(getComments("s7")).toHaveLength(1);
  });

  it("while a reply runs, a message typed as \"/name …\" waits WITHOUT the comments (they stay as chips)", async () => {
    await running("s62");
    await commentThroughDialog("Use a constant");
    await screen.findByTestId("comment-chip");
    fireEvent.change(chatBox(), { target: { value: "/review app.py" } });
    fireEvent.keyDown(chatBox(), { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(H.sent.filter((x) => x.method === "PUT" && x.path === "/chat/threads/s62/queue")).toHaveLength(1));
    const kept = H.sent.find((x) => x.method === "PUT" && x.path === "/chat/threads/s62/queue")!.body as { items: unknown[] };
    expect(kept.items).toEqual([{ text: "/review app.py" }]);
    expect(screen.getByTestId("comment-chip")).toBeTruthy();
    expect(getComments("s62")).toHaveLength(1);
  });
});
