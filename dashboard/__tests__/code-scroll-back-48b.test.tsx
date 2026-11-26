/**
 * 48-b (master plan R2.14; idea 48 "Scroll back forever") — the CODING
 * SESSION view scrolls back too (the chat page's own pieces, 48-a).
 *
 * A coding session keeps its newest 200 lines live and, from this release,
 * MOVES the rest to its older lines instead of cutting them
 * (`harness/sessions._with_lines`); `GET /build/sessions/{id}` says how many
 * (`older`). The Code view (components/harness/HarnessSession):
 *  - opening a session with 300 older lines makes ONE session read and NO
 *    older read, and a quiet "Earlier messages" line sits at the top; so
 *    does coming back to one whose lines moved while it was closed;
 *  - once the reader has scrolled up, reaching the line loads the 50 before
 *    (`/chat/threads/{id}/older?before=`), and the line the reader was on
 *    stays where it was on screen;
 *  - the earlier lines are read-only: no Rewind, Branch or Try again on them
 *    (the live ones keep theirs);
 *  - the top says "Start of this session" (a coding session names itself;
 *    a chat's says "Start of this chat"), or "Earlier messages were not kept
 *    before this version" when the daemon says the first kept line may not
 *    be the session's first (`start_kept`);
 *  - a failed read says so and offers Try again, never an empty top;
 *  - a later read of the session whose count grew fetches just the lines in
 *    between, so nothing the reader saw disappears and nothing shows twice.
 *
 * Harness: the real HarnessSession, only the transport mocked (as
 * build-reply-not-saved-v1510).
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
    got: [] as string[],
    /** Answers every `/chat/threads/<sid>/older?...` read. */
    older: null as null | ((before: number, limit: number) => unknown),
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string) => {
    if (method === "GET") api.got.push(path);
    const older = /^\/chat\/threads\/[^/]+\/older\?before=(-?\d+)&limit=(\d+)$/.exec(path);
    if (method === "GET" && older && api.older) {
      const r = api.older(Number(older[1]), Number(older[2]));
      return r instanceof Error ? Promise.reject(r) : Promise.resolve(r);
    }
    const hit = method === "GET" ? api.gets[path] : undefined;
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
    post: (p: string) => answer("POST", p),
    put: (p: string) => answer("PUT", p),
    patch: (p: string) => answer("PATCH", p),
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
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import { HarnessSession } from "@/components/harness/HarnessSession";
import { HarnessMessageRow } from "@/components/harness/HarnessMessageRow";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import type { BuildMessage } from "@/lib/buildSessions";
import { EMPTY_ROSTER } from "@/lib/sessionTeam";

const terminal: SessionTerminalApi = { find: () => null, open: async () => null, render: () => <div />, close: () => {} };

type Line = Record<string, unknown>;

/** Line `i` of the session: a question and its reply, one turn each. */
function line(i: number): Line {
  const turn = `t${Math.floor(i / 2)}`;
  const day = i < 280 ? "2026-10-05" : "2026-10-06";
  return i % 2 === 0
    ? { role: "user", content: `question ${i}`, turnId: turn, at: `${day}T10:00:00+00:00` }
    : { role: "assistant", content: `reply ${i}`, turnId: turn, at: `${day}T10:01:00+00:00` };
}

function view(id: string, older: number | undefined, live: Line[]) {
  return {
    id,
    title: "A long job",
    updated_at: "2026-11-20T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "C:\\work\\shop",
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: {},
    messages: live,
    session_id: `build:${id}`,
    queued: [],
    turn_id: "",
    ...(older === undefined ? {} : { older }),
  };
}

/** The daemon's older lines: `line(i)` for 0..total-1, paged like the route
 *  (the `limit` just before `before`). */
function serveOlder(sid: string, total: number, startKept = true) {
  api.older = (before, limit) => {
    const end = Math.max(0, Math.min(before, total));
    const start = Math.max(0, end - limit);
    const messages: Line[] = [];
    for (let i = start; i < end; i++) messages.push(line(i));
    return { id: sid, total, offset: 0, first_seq: start, start_kept: startKept, messages };
  };
}

// IntersectionObserver: jsdom has none. Each observer is kept; `reachTop`
// tells the ones watching the top line that it came into view.
const observers: FakeIO[] = [];
/** The observers watching the "Earlier messages" line now on screen. */
function topWatchers(): { o: FakeIO; el: Element }[] {
  const out: { o: FakeIO; el: Element }[] = [];
  for (const o of observers) {
    for (const el of o.targets) {
      if (el.isConnected && el.closest('[data-testid="older-top"]')) out.push({ o, el });
    }
  }
  return out;
}
class FakeIO {
  cb: IntersectionObserverCallback;
  targets = new Set<Element>();
  constructor(cb: IntersectionObserverCallback) {
    this.cb = cb;
    observers.push(this);
  }
  observe(el: Element) {
    this.targets.add(el);
  }
  unobserve(el: Element) {
    this.targets.delete(el);
  }
  disconnect() {
    this.targets.clear();
  }
  takeRecords() {
    return [];
  }
}
/** The line came into view. OlderMessages attaches its observer in an
 *  effect, which can run after the text a test waited for is on screen (a
 *  busy PC): wait for one to watch the line now on screen, THEN tell it, so
 *  a reach is never lost (and a "reads nothing" check checks something). */
async function reachTop() {
  await waitFor(() => expect(topWatchers().length).toBeGreaterThan(0), LONG);
  act(() => {
    for (const { o, el } of topWatchers()) {
      o.cb([{ target: el, isIntersecting: true } as unknown as IntersectionObserverEntry], o as unknown as IntersectionObserver);
    }
  });
}

// Layout: jsdom lays nothing out. Every row is 100px tall, in page order —
// an earlier line, and every box straight inside the transcript but the
// earlier block itself — so the scroller's height and each row's place on
// screen follow the DOM.
/** Many rows render slowly on a busy PC: a generous wait. */
const LONG = { timeout: 10_000 };
/** The live lines a test session holds (the daemon keeps 200 live; fewer
 *  here keeps each test light: the paging does not depend on how many). */
const LIVE = 20;

const ROW = 100;
let scrollTop = 0;
function rowsIn(box: Element): Element[] {
  return Array.from(box.querySelectorAll("[data-older-seq], :scope > :not([data-older-block])"));
}
function layOut(box: HTMLElement) {
  Object.defineProperty(box, "scrollTop", { configurable: true, get: () => scrollTop, set: (v: number) => { scrollTop = v; } });
  Object.defineProperty(box, "scrollHeight", { configurable: true, get: () => rowsIn(box).length * ROW });
  Object.defineProperty(box, "clientHeight", { configurable: true, get: () => 600 });
}
const transcript = () => screen.getByTestId("harness-transcript");
/** The reader scrolls to the top of what is on screen. */
function scrollToTop() {
  const box = transcript();
  layOut(box);
  scrollTop = 0;
  fireEvent.scroll(box);
}
const olderReads = () => api.got.filter((p) => p.includes("/older"));
const sessionReads = (sid: string) => api.got.filter((p) => p === `/build/sessions/${sid}`);

let sid = "";
let n = 0;
const realRect = Element.prototype.getBoundingClientRect;
beforeEach(() => {
  localStorage.clear();
  n += 1;
  sid = `osb${n}`;
  api.got.length = 0;
  api.older = null;
  api.gets = { "/build/accounts": new api.ApiError("no such route", 404) };
  observers.length = 0;
  scrollTop = 0;
  vi.stubGlobal("IntersectionObserver", FakeIO);
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
  Element.prototype.getBoundingClientRect = function (this: Element) {
    const box = this.closest('[data-testid="harness-transcript"]');
    const all = box ? rowsIn(box) : [];
    const at = all.indexOf(this);
    if (at < 0) return realRect.call(this);
    const top = at * ROW - scrollTop;
    return { top, bottom: top + ROW, height: ROW, left: 0, right: 0, width: 0, x: 0, y: top, toJSON: () => ({}) } as DOMRect;
  };
});
afterEach(() => {
  cleanup();
  Element.prototype.getBoundingClientRect = realRect;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const session = (id: string) => (
  <HarnessSession key={id} threadId={id} terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />
);

/** A session with `older` lines kept beyond its live ones. */
async function openLongSession(older = 300, startKept = true) {
  const live = Array.from({ length: LIVE }, (_, i) => line(older + i));
  api.gets[`/build/sessions/${sid}`] = view(sid, older, live);
  serveOlder(sid, older, startKept);
  const out = render(session(sid));
  await screen.findByText(`reply ${older + LIVE - 1}`, undefined, LONG);
  return out;
}

describe("scroll back in a coding session (48-b)", () => {
  it("opens a session with 300 older lines with one session read and no older read", async () => {
    await openLongSession();
    expect(screen.getByTestId("older-more")).toHaveTextContent("Earlier messages");
    expect(sessionReads(sid).length).toBeGreaterThanOrEqual(1);
    // Following the newest line: the line in view reads nothing.
    await reachTop();
    await new Promise((r) => setTimeout(r, 30));
    expect(olderReads()).toEqual([]);
  });

  it("coming back to a session whose lines moved while away reads nothing earlier as it opens", async () => {
    // The Code view remounts a session each time it opens (keyed by its
    // id): it starts from the view drawn last time (the turn cache), whose
    // count is old. The daemon's view now says 20 more lines moved (a night
    // of turns): opening still reads nothing earlier and draws none of them.
    const { unmount } = await openLongSession(300);
    unmount();
    const live = Array.from({ length: LIVE }, (_, i) => line(320 + i));
    api.gets[`/build/sessions/${sid}`] = view(sid, 320, live);
    serveOlder(sid, 320);
    api.got.length = 0;
    render(session(sid));
    await screen.findByText(`reply ${320 + LIVE - 1}`, undefined, LONG);
    await new Promise((r) => setTimeout(r, 30));
    expect(sessionReads(sid)).toHaveLength(1);
    expect(olderReads()).toEqual([]);
    expect(screen.getByTestId("harness-older").querySelectorAll("[data-older-seq]")).toHaveLength(0);
    expect(screen.getByTestId("older-more")).toHaveTextContent("Earlier messages");
    // Scrolling back starts where the live lines start NOW.
    scrollToTop();
    await reachTop();
    await screen.findByText("question 270", undefined, LONG);
    expect(olderReads()).toEqual([`/chat/threads/${sid}/older?before=320&limit=50`]);
  });

  it("a session that keeps nothing older draws no earlier block at all", async () => {
    api.gets[`/build/sessions/${sid}`] = view(sid, 0, [line(0), line(1)]);
    render(session(sid));
    await screen.findByText("reply 1", undefined, LONG);
    expect(screen.queryByTestId("harness-older")).toBeNull();
    expect(screen.queryByTestId("older-more")).toBeNull();
  });

  it("reaching the top loads the 50 before and keeps the reader's place", async () => {
    await openLongSession();
    scrollToTop();
    // The first live line is the reader's line, at the top of the screen.
    const first = screen.getByText("question 300").closest('[data-testid="harness-transcript"] > *') as Element;
    const was = first.getBoundingClientRect().top;
    await reachTop();
    await screen.findByText("question 250", undefined, LONG);
    expect(olderReads()).toEqual([`/chat/threads/${sid}/older?before=300&limit=50`]);
    // The 50 went in above it, and the scroll moved with them: still there.
    expect(first.isConnected).toBe(true);
    expect(first.getBoundingClientRect().top).toBe(was);
    expect(scrollTop).toBe(50 * ROW);
    // In order, each once, earlier lines above the live ones.
    const words = within(transcript())
      .getAllByTestId("harness-user-bubble")
      .map((b) => b.textContent);
    expect(words.slice(0, 3)).toEqual(["question 250", "question 252", "question 254"]);
    expect(new Set(words).size).toBe(words.length);
    expect(words.length).toBe(25 + LIVE / 2);
  });

  it("an earlier line offers no Rewind, Branch or Try again; the live ones keep theirs", async () => {
    const older = 4;
    const live = Array.from({ length: LIVE }, (_, i) => line(older + i));
    api.gets[`/build/sessions/${sid}`] = view(sid, older, live);
    api.older = (before, limit) => {
      const start = Math.max(0, before - limit);
      const messages = [line(0), { ...line(1), error: "The model stopped answering." }, line(2), line(3)].slice(start, before);
      return { id: sid, total: older, offset: 0, first_seq: start, start_kept: true, messages };
    };
    render(session(sid));
    await screen.findByText(`reply ${older + LIVE - 1}`, undefined, LONG);
    fireEvent.click(screen.getByTestId("older-more"));
    await screen.findByText("question 0", undefined, LONG);
    const block = screen.getByTestId("harness-older");
    expect(within(block).getAllByTestId("harness-user-bubble")).toHaveLength(2);
    expect(within(block).queryByTestId("rewind-control")).toBeNull();
    expect(within(block).queryByTestId("harness-branch-question")).toBeNull();
    expect(within(block).queryByTestId("harness-branch-reply")).toBeNull();
    expect(within(block).queryByTestId("harness-retry")).toBeNull();
    // The live lines below still rewind and branch.
    expect(screen.getAllByTestId("rewind-control").length).toBeGreaterThan(0);
    expect(screen.getAllByTestId("harness-branch-question").length).toBeGreaterThan(0);
  });

  it("an earlier line asks nothing: no client question and no preference suggestion on it", async () => {
    const older = 2;
    const ask = { id: "client_ab12cd34ef56", name: "Hartleys", folders: 2, work: 9 };
    const offer = { id: "s1", text: "Always use the firm letterhead", count: 2, quotes: [], since: "" };
    const live = Array.from({ length: LIVE }, (_, i) => line(older + i));
    // The newest live reply carries a client question: the live lines keep it.
    live[LIVE - 1] = { ...live[LIVE - 1], clientQuestion: ask };
    api.gets[`/build/sessions/${sid}`] = view(sid, older, live);
    api.older = (before, limit) => {
      const start = Math.max(0, before - limit);
      const messages = [line(0), { ...line(1), clientQuestion: ask }].slice(start, before);
      return { id: sid, total: older, offset: 0, first_seq: start, start_kept: true, messages };
    };
    render(session(sid));
    await screen.findByText(`reply ${older + LIVE - 1}`, undefined, LONG);
    expect(screen.getAllByTestId("client-question")).toHaveLength(1);
    fireEvent.click(screen.getByTestId("older-more"));
    await screen.findByText("reply 1", undefined, LONG);
    const block = screen.getByTestId("harness-older");
    expect(within(block).queryByTestId("client-question")).toBeNull();
    expect(screen.getAllByTestId("client-question")).toHaveLength(1);
    // A suggestion on an earlier line is not offered either.
    cleanup();
    api.older = (before, limit) => {
      const start = Math.max(0, before - limit);
      const messages = [line(0), { ...line(1), suggestion: offer, clientQuestion: ask }].slice(start, before);
      return { id: sid, total: older, offset: 0, first_seq: start, start_kept: true, messages };
    };
    render(session(sid));
    await screen.findByText(`reply ${older + LIVE - 1}`, undefined, LONG);
    fireEvent.click(screen.getByTestId("older-more"));
    await screen.findByText("reply 1", undefined, LONG);
    const again = screen.getByTestId("harness-older");
    expect(within(again).queryByTestId("pref-suggestion")).toBeNull();
    expect(within(again).queryByTestId("client-question")).toBeNull();
  });

  it("an earlier line offers no Keep and no Undo; the live ones keep theirs", async () => {
    // 48-b review: the attempts card's Keep and a written file's Undo are
    // left out on an earlier line too (the Handbook: "Undo and the other
    // buttons stay on the newer lines").
    const older = 2;
    const FILE = "C:\\work\\shop\\cart.py";
    const git = (name: string) => ({
      repo: "C:\\work\\shop", main: "C:\\work\\shop", checkout: "C:\\work\\shop",
      worktree: `C:\\work\\shop.ij-worktrees\\${name}`, branch: `ij/${name}`,
      base: "ij/session-ab12", base_commit: "abc123", name,
    });
    const card = {
      task: "fix the cart", base: "ij/session-ab12", base_commit: "abc123", adopt: false,
      items: [
        { id: "try1", label: "GPT-5.5 (Codex)", provider: "codex-cli", model: "gpt-5.5", cost: "plan",
          branch: "ij/try-codex-1a2b", state: "done", why: "finished", words: "Finished", steps: 3, ms: 42_000,
          files: ["cart.py"], usage: { input_tokens: 1200, output_tokens: 300 }, git: git("try-codex-1a2b") },
        { id: "try2", label: "glm-local", provider: "ollama", model: "glm-local", cost: "free",
          branch: "ij/try-glm-3c4d", state: "done", why: "finished", words: "Finished", steps: 3, ms: 31_000,
          files: ["cart.py"], usage: { input_tokens: 900, output_tokens: 210 }, git: git("try-glm-3c4d") },
      ],
    };
    const worked = (m: Line) => ({ ...m, documents: [FILE], attempts: card });
    const live = Array.from({ length: LIVE }, (_, i) => line(older + i));
    live[LIVE - 1] = worked(live[LIVE - 1]);
    api.gets[`/build/sessions/${sid}`] = view(sid, older, live);
    api.gets[`/undo?session_id=build%3A${sid}`] = {
      actions: [{ action_id: "u1", kind: "file_restore", path: "cart.py", workspace: "C:\\work\\shop", undoable: true }],
    };
    api.older = (before, limit) => {
      const start = Math.max(0, before - limit);
      const messages = [line(0), worked(line(1))].slice(start, before);
      return { id: sid, total: older, offset: 0, first_seq: start, start_kept: true, messages };
    };
    render(session(sid));
    await screen.findByText(`reply ${older + LIVE - 1}`, undefined, LONG);
    // The live reply keeps Keep.
    await waitFor(() => expect(screen.getAllByTestId("attempt-keep").length).toBeGreaterThan(0), LONG);
    const liveKeeps = screen.getAllByTestId("attempt-keep").length;
    fireEvent.click(screen.getByTestId("older-more"));
    await screen.findByText("reply 1", undefined, LONG);
    const block = screen.getByTestId("harness-older");
    expect(within(block).getAllByTestId("attempts-card")).toHaveLength(1);
    expect(within(block).queryAllByTestId("attempt-keep")).toHaveLength(0);
    expect(screen.getAllByTestId("attempt-keep")).toHaveLength(liveKeeps);
    // Every fold opened: the earlier line names its file, with no Undo.
    for (const b of within(block).queryAllByRole("button", { expanded: false })) fireEvent.click(b);
    await waitFor(() => expect(within(block).getAllByTestId("turn-doc").length).toBeGreaterThan(0), LONG);
    expect(within(block).queryByRole("button", { name: /Undo the write to cart\.py/ })).toBeNull();
    // The live reply's receipt keeps its Undo (the journal matched the file).
    const liveReplies = within(transcript()).getAllByTestId("harness-reply").filter((r) => !block.contains(r));
    const newest = liveReplies[liveReplies.length - 1];
    for (const b of within(newest).queryAllByRole("button", { expanded: false })) fireEvent.click(b);
    expect(await within(newest).findByRole("button", { name: /Undo the write to cart\.py/ }, LONG)).toBeTruthy();
  });

  it("an earlier line of review comments carries no card and no Answer these", async () => {
    // The row itself: even handed the card, an earlier line leaves it out.
    const m = {
      role: "assistant", content: "1 review comment arrived on this session's pull request.",
      at: "2026-11-22T09:30:00", prComments: { ids: ["s1:c1"], n: 1, authors: ["sam"] },
    } as unknown as BuildMessage;
    api.gets["/build/sessions/s1/pr-comments"] = {
      pr_url: "https://github.com/acme/shop/pull/7",
      comments: [{
        id: "s1:c1", kind: "line", author: "sam", author_kind: "member", ticked: true, path: "app.py",
        abs: "C:\\work\\shop\\app.py", line: 2, start_line: 0, side: "new", outdated: false, body: "Words of sam.",
        quote: "+y = 3", url: "", state: "new", answer_files: [], place: "app.py, line 2",
        github_created_at: "2026-11-20T10:00:00Z",
      }],
      quiet: false, done: "", max_answer: 20,
    };
    const none = () => {};
    const row = (readOnly: boolean) => (
      <HarnessMessageRow
        readOnly={readOnly}
        prCard
        m={m}
        index={3}
        threadId="s1"
        sessionId="build:s1"
        archived={false}
        busy={false}
        isPending={false}
        branching={null}
        newest={false}
        carriedOn={false}
        canRewind={false}
        prevUserAt={undefined}
        teamRoster={EMPTY_ROSTER}
        models={[]}
        health={null as never}
        provider="mock"
        undoFor={() => null}
        onUndo={async () => {}}
        h={{ onRewound: none, onBranch: none, onAttemptKept: none, onKeepGoing: none, onRetry: none,
             onOpenRewind: none, onOpenDocument: none, onAnswerComments: none }}
      />
    );
    const { rerender } = render(row(true));
    expect(await screen.findByTestId("pr-comments-line")).toBeTruthy();
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("pr-comments-answer")).toBeNull();
    expect(api.got.filter((p) => p.endsWith("/pr-comments"))).toEqual([]);
    // The same line, live, carries the card with its Answer these.
    rerender(row(false));
    expect(await screen.findByTestId("pr-comments-answer", undefined, LONG)).toBeTruthy();
  });

  it("says 'Start of this session' once the first line is loaded", async () => {
    await openLongSession(60);
    scrollToTop();
    await reachTop();
    await screen.findByText("question 10", undefined, LONG);
    await reachTop();
    await screen.findByText("question 0", undefined, LONG);
    const top = await screen.findByTestId("older-start", undefined, LONG);
    // One word per thing: the top of a coding session is not a chat's.
    expect(top).toHaveTextContent("Start of this session");
    expect(top).not.toHaveTextContent("Start of this chat");
    expect(screen.queryByTestId("older-more")).toBeNull();
  });

  it("never says 'Start of this session' over a session whose first lines may not have been kept", async () => {
    await openLongSession(30, false);
    fireEvent.click(screen.getByTestId("older-more"));
    await screen.findByText("question 0", undefined, LONG);
    expect(await screen.findByTestId("older-start", undefined, LONG)).toHaveTextContent(
      "Earlier messages were not kept before this version",
    );
  });

  it("a failed page says so and offers Try again, never an empty top", async () => {
    await openLongSession();
    const real = api.older;
    api.older = () => new api.ApiError("the daemon is busy", 503);
    scrollToTop();
    await reachTop();
    const failed = await screen.findByTestId("older-failed", undefined, LONG);
    expect(failed).toHaveTextContent("Could not load earlier messages.");
    // Reaching it again does not ask again by itself.
    await reachTop();
    await new Promise((r) => setTimeout(r, 30));
    expect(olderReads()).toHaveLength(1);
    api.older = real;
    fireEvent.click(within(failed).getByText("Try again"));
    await screen.findByText("question 250", undefined, LONG);
  });

  it("scrolling up loads when the line was already in view while following the newest line", async () => {
    await openLongSession();
    const box = transcript();
    layOut(box);
    // Following: at the bottom, the line reported in view (and was ignored).
    scrollTop = box.scrollHeight - 600;
    fireEvent.scroll(box);
    await reachTop();
    await new Promise((r) => setTimeout(r, 20));
    expect(olderReads()).toEqual([]);
    // The reader leaves the bottom upward: that is the reach.
    scrollTop = box.scrollHeight - 1200;
    fireEvent.scroll(box);
    await screen.findByText("question 250", undefined, LONG);
  });

  it("a later read whose count grew fetches just the lines in between", async () => {
    const { rerender } = await openLongSession();
    scrollToTop();
    await reachTop();
    await screen.findByText("question 250", undefined, LONG);
    // Two turns more: four lines left the live ones (the daemon moved them).
    const live = Array.from({ length: LIVE }, (_, i) => line(304 + i));
    api.gets[`/build/sessions/${sid}`] = view(sid, 304, live);
    serveOlder(sid, 304);
    api.got.length = 0;
    // The Code view reads the session again (as when its list asks it to).
    rerender(
      <HarnessSession
        key={sid}
        threadId={sid}
        terminal={terminal}
        listRunning={false}
        onChanged={() => {}}
        onGone={() => {}}
        rereadRequest={1}
      />,
    );
    await screen.findByText(`reply ${304 + LIVE - 1}`, undefined, LONG);
    // The view held 300; the daemon now says 304: just those 4 are read.
    await waitFor(() => expect(olderReads()).toEqual([`/chat/threads/${sid}/older?before=304&limit=4`]), LONG);
    await screen.findByText("question 302", undefined, LONG);
    const words = within(transcript())
      .getAllByTestId("harness-user-bubble")
      .map((b) => b.textContent);
    // Every question from 250 on, each once, in order: no gap, no double.
    expect(words).toEqual(Array.from({ length: 27 + LIVE / 2 }, (_, k) => `question ${250 + 2 * k}`));
  });

  it("a view read before the last and answered late keeps what the reader scrolled back to", async () => {
    // Two reads were in flight and answered in the wrong order: the later
    // answer is the older view, whose live lines start where they did
    // before the newer view moved four of them. A session's count never
    // goes down, so its earlier lines held before that point stay.
    const { rerender } = await openLongSession();
    scrollToTop();
    await reachTop();
    await screen.findByText("question 250", undefined, LONG);
    const reread = (k: number) =>
      rerender(
        <HarnessSession
          key={sid}
          threadId={sid}
          terminal={terminal}
          listRunning={false}
          onChanged={() => {}}
          onGone={() => {}}
          rereadRequest={k}
        />,
      );
    // The newer view: four lines left the live ones.
    api.gets[`/build/sessions/${sid}`] = view(sid, 304, Array.from({ length: LIVE }, (_, i) => line(304 + i)));
    serveOlder(sid, 304);
    reread(1);
    await screen.findByText(`reply ${304 + LIVE - 1}`, undefined, LONG);
    await screen.findByText("question 302", undefined, LONG);
    // Then the older view lands: its live lines start at 300 again.
    api.got.length = 0;
    api.gets[`/build/sessions/${sid}`] = view(sid, 300, Array.from({ length: LIVE }, (_, i) => line(300 + i)));
    reread(2);
    await waitFor(() => expect(screen.queryByText(`reply ${304 + LIVE - 1}`)).toBeNull(), LONG);
    // What the reader scrolled back to is still there, each line once, in order.
    expect(screen.getByText("question 250")).toBeTruthy();
    const words = within(transcript())
      .getAllByTestId("harness-user-bubble")
      .map((b) => b.textContent);
    expect(words).toEqual(Array.from({ length: 25 + LIVE / 2 }, (_, k) => `question ${250 + 2 * k}`));
    expect(olderReads()).toEqual([]);
    // The newer view again: just the four in between are read back.
    api.gets[`/build/sessions/${sid}`] = view(sid, 304, Array.from({ length: LIVE }, (_, i) => line(304 + i)));
    reread(3);
    await waitFor(() => expect(olderReads()).toEqual([`/chat/threads/${sid}/older?before=304&limit=4`]), LONG);
    await screen.findByText(`reply ${304 + LIVE - 1}`, undefined, LONG);
    await waitFor(
      () =>
        expect(
          within(transcript())
            .getAllByTestId("harness-user-bubble")
            .map((b) => b.textContent),
        ).toEqual(Array.from({ length: 27 + LIVE / 2 }, (_, k) => `question ${250 + 2 * k}`)),
      LONG,
    );
  });

  it("a read that fails after the session started over marks nothing", async () => {
    const { rerender } = await openLongSession();
    let answer: () => void = () => {};
    api.older = () => new Promise((_r, no) => (answer = () => no(new api.ApiError("gone", 503)))) as unknown;
    scrollToTop();
    await reachTop();
    await waitFor(() => expect(olderReads()).toHaveLength(1), LONG);
    // Meanwhile the session's lines were replaced: it keeps fewer older ones.
    api.gets[`/build/sessions/${sid}`] = view(sid, 10, Array.from({ length: LIVE }, (_, i) => line(10 + i)));
    rerender(
      <HarnessSession
        key={sid}
        threadId={sid}
        terminal={terminal}
        listRunning={false}
        onChanged={() => {}}
        onGone={() => {}}
        rereadRequest={1}
      />,
    );
    await screen.findByText("question 10", undefined, LONG);
    await act(async () => {
      answer();
      await new Promise((r) => setTimeout(r, 20));
    });
    // The read asked of the old count failed, but it was not this state's:
    // the line still offers the earlier messages, with no failure on it.
    expect(screen.queryByTestId("older-failed")).toBeNull();
    expect(screen.getByTestId("older-more")).toHaveTextContent("Earlier messages");
  });

  it("leaving the session drops what was read, and a read still on its way lands nowhere", async () => {
    const { rerender } = await openLongSession();
    let answer: (v: unknown) => void = () => {};
    const real = api.older;
    api.older = (before, limit) => new Promise((r) => (answer = () => r(real!(before, limit)))) as unknown;
    scrollToTop();
    await reachTop();
    await waitFor(() => expect(olderReads()).toHaveLength(1), LONG);
    // Another session opens in this column before the page comes back.
    api.gets["/build/sessions/other"] = view("other", 0, [line(0), line(1)]);
    rerender(session("other"));
    await screen.findByText("reply 1", undefined, LONG);
    await act(async () => {
      answer(null);
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(screen.queryByTestId("harness-older")).toBeNull();
    expect(screen.queryByText("question 250")).toBeNull();
  });
});
