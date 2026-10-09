/**
 * v1.328.0 (calm chat W3-2) — what a reply changed: the quiet
 * "N files changed +a −r" line under a chat reply, each file opening its diff.
 *
 * Pinned here:
 *  - `turnWindow`: a reply that reported no files has no window (never asks);
 *    the stream lane's `timing` is the window; without it the question's send
 *    time and the reply's settle time are;
 *  - `ReplyChanges` asks ONLY once the reply is on screen (an
 *    IntersectionObserver), ONCE per reply (a second mount and an in-flight
 *    second mount share the one request), never without an observer, and a
 *    failed ask draws nothing and is not kept;
 *  - a file opens its DiffView; Undo goes through the CALLER's undo (the
 *    receipt's handler) and then the rows are read again;
 *  - on the chat page: a reply with files asks once and shows the line, a
 *    reply without files never asks, and a reopened saved chat asks by the
 *    stored timing.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: { extra: {} as Record<string, unknown> },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
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
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
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
    run: async (_body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
      await new Promise<void>((r) => setTimeout(r, 0));
      onDelta("Noted.", "Noted.");
      return {
        reply: "Noted.",
        route: { requested: "", provider: "mock", model: "mock", reason: "default" },
        ...H.stream.extra,
      };
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

import { ReplyChanges } from "@/components/chat/ReplyChanges";
import { resetTurnChanges, turnChangesKey, turnWindow } from "@/lib/turnChanges";
import ChatPage from "@/app/chat/page";

/* ------------------------------------------------------------- fixtures -- */

const MEMO = "C:/work/notes/memo.txt";
const MEMO_DIFF = [
  "--- a/notes/memo.txt",
  "+++ b/notes/memo.txt",
  "@@ -3,4 +3,5 @@",
  " three",
  "-four",
  "+FOUR",
  "+four and a half",
  " five",
].join("\n");

function answer(over: Record<string, unknown> = {}) {
  return {
    changes: [
      {
        path: MEMO,
        rel: "notes/memo.txt",
        name: "memo.txt",
        status: "modified",
        added: 2,
        removed: 1,
        diff: MEMO_DIFF,
        changed_since: false,
        undone: false,
        ...over,
      },
    ],
    files: 1,
    added: 2,
    removed: 1,
    truncated_files: false,
  };
}

const T0 = Date.UTC(2026, 9, 9, 12, 0, 0);
const TIMING = { startedAt: T0, firstTokenAt: T0 + 900, endedAt: T0 + 14_000 };

const changePosts = () => H.api.posts.filter((p) => p.path === "/chat/changes");

/* ------------------------------------------- a controllable "on screen" -- */

type IOCallback = (entries: { isIntersecting: boolean; target: Element }[], io: unknown) => void;
const observers: FakeIO[] = [];
class FakeIO {
  els: Element[] = [];
  off = false;
  constructor(private cb: IOCallback) {
    observers.push(this);
  }
  observe(el: Element) {
    this.els.push(el);
  }
  unobserve() {}
  takeRecords() {
    return [];
  }
  disconnect() {
    this.off = true;
  }
  /** The browser says: these elements are now on screen. */
  show() {
    if (this.off) return;
    this.cb(this.els.map((target) => ({ isIntersecting: true, target })), this);
  }
}
function showAll() {
  act(() => {
    for (const io of observers) io.show();
  });
}
/** For the page: every observed element is on screen at once. */
class AutoIO extends FakeIO {
  observe(el: Element) {
    super.observe(el);
    queueMicrotask(() => this.show());
  }
}

beforeEach(() => {
  resetTurnChanges();
  observers.length = 0;
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.extra = {};
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.postResponses["/chat/changes"] = () => answer();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

/* --------------------------------------------------------- the window -- */

describe("turnWindow", () => {
  it("a reply that reported no files has no window", () => {
    expect(turnWindow({ timing: TIMING })).toBeNull();
    expect(turnWindow({ documents: [], timing: TIMING })).toBeNull();
    expect(turnWindow({ documents: ["  "], timing: TIMING })).toBeNull();
  });

  it("is the stream lane's timing, the files deduped", () => {
    expect(turnWindow({ documents: [MEMO, MEMO], timing: TIMING })).toEqual({
      since: T0,
      until: T0 + 14_000,
      paths: [MEMO],
    });
  });

  it("without timing: the question's send time to the reply's settle time", () => {
    const q = "2026-10-09T12:00:00.000Z";
    const a = "2026-10-09T12:00:20.000Z";
    expect(turnWindow({ documents: [MEMO], at: a }, q)).toEqual({ since: q, until: a, paths: [MEMO] });
    // No start to ask from: nothing is asked.
    expect(turnWindow({ documents: [MEMO], at: a })).toBeNull();
    expect(turnWindow({ documents: [MEMO], at: a }, "not a time")).toBeNull();
  });

  it("the key is the same for the same reply, whatever the file order", () => {
    expect(turnChangesKey({ since: 1, until: 2, paths: ["b", "a"] })).toBe(
      turnChangesKey({ since: 1, until: 2, paths: ["a", "b"] }),
    );
  });
});

/* ------------------------------------------------------ the component -- */

describe("ReplyChanges", () => {
  const TURN = { since: T0, until: T0 + 14_000, paths: [MEMO] };

  it("asks only once the reply is on screen, then shows the line", async () => {
    vi.stubGlobal("IntersectionObserver", FakeIO);
    render(<ReplyChanges turn={TURN} />);
    await act(async () => {});
    expect(changePosts()).toHaveLength(0);
    showAll();
    await waitFor(() =>
      expect(screen.getByTestId("changed-files-line").textContent).toContain("1 file changed"),
    );
    expect(changePosts()).toHaveLength(1);
    expect(changePosts()[0].body).toEqual({
      since: "2026-10-09T12:00:00.000Z",
      until: "2026-10-09T12:00:14.000Z",
      paths: [MEMO],
    });
  });

  it("asks ONCE per reply: a second mount reads the held answer, an in-flight one joins", async () => {
    vi.stubGlobal("IntersectionObserver", FakeIO);
    let release: (v: unknown) => void = () => {};
    H.api.postResponses["/chat/changes"] = () => new Promise((r) => (release = r));
    render(
      <>
        <ReplyChanges turn={TURN} />
        <ReplyChanges turn={{ ...TURN, paths: [MEMO] }} />
      </>,
    );
    showAll();
    await act(async () => {});
    expect(changePosts()).toHaveLength(1);
    await act(async () => release(answer()));
    await waitFor(() => expect(screen.getAllByTestId("changed-files-line")).toHaveLength(2));
    cleanup();
    observers.length = 0;
    render(<ReplyChanges turn={TURN} />);
    // Held: drawn at once, with no new request even once it is on screen.
    expect(screen.getByTestId("changed-files-line").textContent).toContain("1 file changed");
    showAll();
    await act(async () => {});
    expect(changePosts()).toHaveLength(1);
  });

  it("a reply that wrote nothing never asks and draws nothing", async () => {
    vi.stubGlobal("IntersectionObserver", FakeIO);
    const { container } = render(<ReplyChanges turn={null} />);
    showAll();
    await act(async () => {});
    expect(observers).toHaveLength(0);
    expect(changePosts()).toHaveLength(0);
    expect(container.innerHTML).toBe("");
  });

  it("without an IntersectionObserver nothing is asked", async () => {
    vi.stubGlobal("IntersectionObserver", undefined);
    render(<ReplyChanges turn={TURN} />);
    await act(async () => {});
    expect(changePosts()).toHaveLength(0);
    expect(screen.queryByTestId("changed-files-line")).toBeNull();
  });

  it("a failed ask draws nothing and is not kept: a later mount asks again", async () => {
    vi.stubGlobal("IntersectionObserver", FakeIO);
    H.api.postResponses["/chat/changes"] = new H.FakeApiError("daemon offline", 0);
    render(<ReplyChanges turn={TURN} />);
    showAll();
    await waitFor(() => expect(changePosts()).toHaveLength(1));
    await act(async () => {});
    expect(screen.queryByTestId("changed-files-line")).toBeNull();
    cleanup();
    observers.length = 0;
    H.api.postResponses["/chat/changes"] = () => answer();
    render(<ReplyChanges turn={TURN} />);
    showAll();
    await waitFor(() => expect(screen.getByTestId("changed-files-line")).toBeTruthy());
    expect(changePosts()).toHaveLength(2);
  });

  it("a file opens its diff", async () => {
    vi.stubGlobal("IntersectionObserver", FakeIO);
    render(<ReplyChanges turn={TURN} />);
    showAll();
    fireEvent.click(await screen.findByTestId("changed-files-line"));
    fireEvent.click(within(screen.getByTestId("changed-file-0")).getByRole("button", { name: "notes/memo.txt" }));
    const dialog = await screen.findByRole("dialog", { name: "Changes to memo.txt" });
    const row = within(dialog).getAllByTestId("diff-split-row")[1];
    expect(row.querySelector('[data-side="left"]')?.textContent).toContain("four");
    expect(row.querySelector('[data-side="right"]')?.textContent).toContain("FOUR");
  });

  it("says when the daemon listed only the first files", async () => {
    vi.stubGlobal("IntersectionObserver", FakeIO);
    H.api.postResponses["/chat/changes"] = () => ({ ...answer(), truncated_files: true });
    render(<ReplyChanges turn={TURN} />);
    showAll();
    fireEvent.click(await screen.findByTestId("changed-files-line"));
    expect(screen.getByText("Only the first 1 files are listed.")).toBeTruthy();
  });

  it("Undo goes through the caller's undo, then the rows are read again", async () => {
    vi.stubGlobal("IntersectionObserver", FakeIO);
    const onUndo = vi.fn(async () => {});
    const undoFor = vi.fn((p: string) => (p === MEMO ? { actionId: "act-1", undoable: true } : null));
    render(<ReplyChanges turn={TURN} undoFor={undoFor} onUndo={onUndo} />);
    showAll();
    fireEvent.click(await screen.findByTestId("changed-files-line"));
    H.api.postResponses["/chat/changes"] = () => answer({ undone: true, diff: "", added: 0, removed: 0 });
    fireEvent.click(screen.getByTestId("changed-undo-0"));
    await waitFor(() => expect(screen.getByTestId("changed-file-0").textContent).toContain("undone"));
    expect(onUndo).toHaveBeenCalledWith("act-1", MEMO);
    expect(changePosts()).toHaveLength(2);
    // An undone file offers no second Undo.
    expect(screen.queryByTestId("changed-undo-0")).toBeNull();
  });

  it("no Undo for a file the journal did not match", async () => {
    vi.stubGlobal("IntersectionObserver", FakeIO);
    render(<ReplyChanges turn={TURN} undoFor={() => null} onUndo={vi.fn()} />);
    showAll();
    fireEvent.click(await screen.findByTestId("changed-files-line"));
    expect(screen.queryByTestId("changed-undo-0")).toBeNull();
  });
});

/* ------------------------------------------------------------ the page -- */

function pageGets(extra: Record<string, unknown> = {}) {
  H.api.getResponses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
    ...extra,
  };
}

async function send(text: string, reply: string) {
  const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
  return screen.findByText(reply);
}

describe("on the chat page (calm chat W3-2)", () => {
  beforeEach(() => {
    pageGets();
    window.history.replaceState({}, "", "/chat");
    window.localStorage.clear();
    window.sessionStorage.clear();
    Element.prototype.scrollIntoView = vi.fn();
    vi.stubGlobal("IntersectionObserver", AutoIO);
  });

  it("a reply whose turn wrote a file asks once and shows the line under it", async () => {
    H.stream.extra = { documents: [MEMO], timing: TIMING };
    render(<ChatPage />);
    const text = await send("tidy the memo", "Noted.");
    const reply = text.closest('[data-testid="reply"]') as HTMLElement;
    await waitFor(() =>
      expect(within(reply).getByTestId("changed-files-line").textContent).toContain("1 file changed"),
    );
    expect(changePosts()).toHaveLength(1);
    expect(changePosts()[0].body).toEqual({
      since: "2026-10-09T12:00:00.000Z",
      until: "2026-10-09T12:00:14.000Z",
      paths: [MEMO],
    });
    // The page re-renders (saves, a second turn) and still never asks again.
    H.stream.extra = {};
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "thanks" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(screen.getAllByText("Noted.")).toHaveLength(2));
    await act(async () => {});
    expect(changePosts()).toHaveLength(1);
  });

  it("a reply that wrote nothing never asks", async () => {
    H.stream.extra = { timing: TIMING };
    render(<ChatPage />);
    await send("just say hi", "Noted.");
    await act(async () => {});
    await act(async () => {});
    expect(screen.queryByTestId("reply-changes")).toBeNull();
    expect(changePosts()).toHaveLength(0);
  });

  it("a reopened saved chat asks by the stored timing (or, without it, the question's time)", async () => {
    const Q = "2026-10-09T11:00:00.000Z";
    const A = "2026-10-09T11:00:30.000Z";
    pageGets({
      "/chat/threads/t1": {
        id: "t1",
        title: "Memo",
        setup: {},
        messages: [
          { role: "user", content: "an older ask", at: Q },
          { role: "assistant", content: "Older reply.", at: A, documents: ["C:/work/old.txt"] },
          { role: "user", content: "tidy the memo", at: "2026-10-09T11:59:59.000Z" },
          { role: "assistant", content: "Tidied.", at: "2026-10-09T12:00:14.000Z", documents: [MEMO], timing: TIMING },
          { role: "user", content: "say hi", at: "2026-10-09T12:01:00.000Z" },
          { role: "assistant", content: "Hi.", at: "2026-10-09T12:01:02.000Z" },
        ],
      },
    });
    window.history.replaceState({}, "", "/chat?thread=t1");
    render(<ChatPage />);
    const tidied = await screen.findByText("Tidied.");
    const reply = tidied.closest('[data-testid="reply"]') as HTMLElement;
    await waitFor(() => expect(within(reply).getByTestId("changed-files-line")).toBeTruthy());
    await waitFor(() => expect(changePosts()).toHaveLength(2));
    const bodies = changePosts().map((p) => p.body);
    expect(bodies).toContainEqual({
      since: "2026-10-09T12:00:00.000Z",
      until: "2026-10-09T12:00:14.000Z",
      paths: [MEMO],
    });
    expect(bodies).toContainEqual({ since: Q, until: A, paths: ["C:/work/old.txt"] });
    // The reply that wrote nothing has no line and asked nothing.
    const hi = (await screen.findByText("Hi.")).closest('[data-testid="reply"]') as HTMLElement;
    expect(within(hi).queryByTestId("reply-changes")).toBeNull();
  });
});
