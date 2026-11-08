/**
 * v1.449.0 — Wave B ("Did it work?, everywhere"), builder B3: the chat's
 * own answers to "did it work?".
 *
 *  - Item 16 (chat list): a chat list whose FIRST read fails used to look
 *    empty — the chat page's rail said "No saved chats yet", the sidebar
 *    "Your chats appear here." — and a user with chats read that as a loss.
 *    Now both say "Your chats couldn't load. Try again." (lib/loadFailed's
 *    words) with a Try again that re-reads; the empty words are for a list
 *    that really answered empty.
 *  - Item 16 (small errors): a failed upload, a failed Remember or any
 *    small action shared the TURN's error line (`chat-turn-error`, the
 *    alert with Retry and no ×), so a failed upload read as a failed reply.
 *    They have their own dismissible notice now (`chat-notice`,
 *    role="status", an × that clears it), apart from the turn-error line.
 *  - Item 16 (screen reader): ONE always-present role="status" announcer
 *    (`chat-reply-live`, the bell's v1.352.0 pattern: mounted with the page,
 *    empty, filled when a turn ends) says "Reply ready." when a turn ends
 *    cleanly, the failure sentence when it fails, and is cleared when the
 *    next turn starts — so one reply is never read twice.
 *  - Item 17 (eleven pages): a source scan of the pages that printed a raw
 *    `error.message` — each goes through LoadFailedLine / lib/loadFailed now,
 *    except the daemon's own 4xx sentences meant for the user, which are
 *    allow-listed here with a reason.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { type ReactNode } from "react";
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
    title = "";
    fix = "";
    technical = "";
    fixKind = "";
    fixProvider = "";
  }
  const stream = {
    bodies: [] as Record<string, unknown>[],
    streaming: false,
    version: 0,
    listeners: new Set<() => void>(),
    settle: null as null | { ok: (r: Record<string, unknown>) => void; fail: (e: unknown) => void },
    bump() {
      stream.version += 1;
      for (const l of [...stream.listeners]) l();
    },
    release(r: Record<string, unknown>) {
      const s = stream.settle;
      stream.settle = null;
      stream.streaming = false;
      stream.bump();
      s?.ok(r);
    },
    fail(e: unknown) {
      const s = stream.settle;
      stream.settle = null;
      stream.streaming = false;
      stream.bump();
      s?.fail(e);
    },
  };
  return {
    FakeApiError,
    FakeStreamError,
    stream,
    nav: { pathname: "/usage" },
    gets: [] as string[],
    responses: {} as Record<string, unknown>,
    /** path -> a promise the GET waits on (held reads). */
    holds: {} as Record<string, Promise<void>>,
    postResponses: {} as Record<string, unknown>,
    posts: [] as { path: string; body: Record<string, unknown> }[],
  };
});

vi.mock("next/navigation", () => ({
  usePathname: () => H.nav.pathname,
  useRouter: () => ({ push: () => {}, replace: () => {}, prefetch: () => {} }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: ReactNode; href: string } & Record<string, unknown>) => (
    <a href={href} {...(rest as Record<string, never>)}>
      {children}
    </a>
  ),
}));
vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (path: string) => {
    H.gets.push(path);
    const hold = H.holds[path];
    if (hold) await hold;
    const r = H.responses[path];
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.posts.push({ path, body });
    const r = H.postResponses[path];
    if (r instanceof Error) throw r;
    if (typeof r === "function") return (r as (b: Record<string, unknown>) => unknown)(body);
    return r ?? {};
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t-new", title: "t", updated_at: "2026-11-07T10:00:00" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useChatStream", async () => {
  const React = await import("react");
  const subscribe = (cb: () => void) => {
    H.stream.listeners.add(cb);
    return () => {
      H.stream.listeners.delete(cb);
    };
  };
  const run = (body: Record<string, unknown>) => {
    H.stream.bodies.push(body);
    H.stream.streaming = true;
    H.stream.bump();
    return new Promise<Record<string, unknown>>((ok, fail) => {
      H.stream.settle = { ok, fail };
    });
  };
  const abort = () => {
    if (H.stream.settle) H.stream.release({ reply: "" });
  };
  return {
    StreamError: H.FakeStreamError,
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    useChatStream: () => {
      React.useSyncExternalStore(subscribe, () => H.stream.version);
      return {
        streaming: H.stream.streaming,
        text: "",
        tools: [],
        approval: null,
        phase: H.stream.streaming ? "working" : null,
        withFiles: false,
        startedAt: null,
        lastEventAt: null,
        run,
        abort,
      };
    },
  };
});
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
  useProviderHealth: () => ({
    byProvider: {}, signedOutByProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {},
  }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { AppSidebar } from "@/components/AppSidebar";
import { __resetApiCache } from "@/lib/apiCache";
import { __resetChatList } from "@/lib/chatList";
import { couldnt, failureOf, serverSentence } from "@/lib/loadFailed";
import { REPLY_READY_WORDS, REPLY_STOPPED_WORDS, replyLiveWords } from "@/lib/replyLive";
import { setChatSlot } from "@/lib/sidebarSlot";

const THREADS = [{ id: "t1", title: "Quarterly numbers", updated_at: new Date().toISOString() }];
const FAILED_FIRST_READ = "Your chats couldn't load. Try again.";
const EMPTY_SIDEBAR = "Your chats appear here.";
const EMPTY_RAIL = "No saved chats yet. Conversations appear here after the first reply.";

function setWide(wide: boolean) {
  window.matchMedia = ((q: string) => ({
    matches: q.includes("min-width") ? wide : false,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

const count = (path: string) => H.gets.filter((g) => g === path).length;

beforeEach(() => {
  __resetApiCache();
  __resetChatList();
  setChatSlot(null);
  H.nav.pathname = "/usage";
  H.gets.length = 0;
  H.posts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.streaming = false;
  H.stream.settle = null;
  for (const k of Object.keys(H.holds)) delete H.holds[k];
  for (const k of Object.keys(H.postResponses)) delete H.postResponses[k];
  H.responses = {
    "/chat/threads": { threads: [] },
    "/projects": { projects: [] },
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/settings": { settings: {} },
    "/chat/approvals/pending": { approvals: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
  };
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState({}, "", "/chat");
  Element.prototype.scrollIntoView = vi.fn();
  setWide(true);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  setChatSlot(null);
});

async function startTurn(text: string) {
  const n = H.stream.bodies.length;
  const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(H.stream.bodies.length).toBe(n + 1));
  await waitFor(() => expect(H.stream.settle).not.toBeNull());
}

function attach(...names: string[]) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: names.map((n) => new File(["abc"], n, { type: "application/pdf" })) } });
}

/** Uploads that settle only when the test says so (no work folder here). */
function heldUploads() {
  H.postResponses["/documents/workfolder"] = new H.FakeApiError("no folder", 503);
  const pending: { name: string; resolve: () => void; reject: (e: Error) => void }[] = [];
  H.postResponses["/documents/upload"] = (body: Record<string, unknown>) =>
    new Promise((resolve, reject) => {
      const name = String(body.filename);
      pending.push({
        name,
        resolve: () => resolve({ name, path: `C:/home/uploads/${name}` }),
        reject,
      });
    });
  return pending;
}

/* ================================================= item 16: the chat list */

describe("a chat list whose first read failed says so (v1.449.0, item 16)", () => {
  it("sidebar: a 500 on the first read is the couldn't-load line with Try again, never the empty words", async () => {
    H.responses["/chat/threads"] = new H.FakeApiError("Internal Server Error", 500);
    render(<AppSidebar />);
    const line = await screen.findByTestId("sidebar-chats-failed");
    expect(line.textContent).toBe(FAILED_FIRST_READ);
    expect(line.getAttribute("data-load-failed")).toBe("error");
    expect(line.getAttribute("title")).toBe("HTTP 500: Internal Server Error");
    expect(screen.queryByText(EMPTY_SIDEBAR)).toBeNull();
    // The server's own text never reaches the words.
    expect(line.textContent).not.toContain("Internal Server Error");
  });

  it("sidebar: Try again re-reads, and the list that answers replaces the line", async () => {
    H.responses["/chat/threads"] = new H.FakeApiError("boom", 500);
    render(<AppSidebar />);
    const line = await screen.findByTestId("sidebar-chats-failed");
    expect(count("/chat/threads")).toBe(1);
    H.responses["/chat/threads"] = { threads: THREADS };
    fireEvent.click(within(line).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(count("/chat/threads")).toBe(2));
    await screen.findByTitle("Quarterly numbers");
    expect(screen.queryByTestId("sidebar-chats-failed")).toBeNull();
  });

  it("sidebar: nothing answered (status 0) names that, not Try again", async () => {
    H.responses["/chat/threads"] = new H.FakeApiError("Failed to fetch", 0);
    render(<AppSidebar />);
    const line = await screen.findByTestId("sidebar-chats-failed");
    expect(line.getAttribute("data-load-failed")).toBe("no-answer");
    expect(line.textContent).toBe("Your chats couldn't load. Iron Jarvis isn't responding.");
    expect(screen.queryByText(EMPTY_SIDEBAR)).toBeNull();
  });

  it("sidebar CONTROL: a delayed read that answers EMPTY shows nothing while held, then the empty words", async () => {
    let release = () => {};
    H.holds["/chat/threads"] = new Promise<void>((r) => (release = r));
    render(<AppSidebar />);
    await act(async () => {});
    expect(screen.queryByTestId("sidebar-recent-chats")).toBeNull();
    expect(screen.queryByTestId("sidebar-chats-failed")).toBeNull();
    await act(async () => {
      release();
      await new Promise((r) => setTimeout(r, 0));
    });
    expect((await screen.findByTestId("sidebar-recent-chats")).textContent).toBe(EMPTY_SIDEBAR);
    expect(screen.queryByTestId("sidebar-chats-failed")).toBeNull();
  });

  it("the chat page's rail: a failed first read is the line, not 'No saved chats yet'", async () => {
    H.nav.pathname = "/chat";
    H.responses["/chat/threads"] = new H.FakeApiError("Internal Server Error", 500);
    render(<ChatPage />);
    const line = await screen.findByTestId("thread-rail-failed");
    expect(line.textContent).toBe(FAILED_FIRST_READ);
    expect(line.getAttribute("title")).toBe("HTTP 500: Internal Server Error");
    expect(screen.queryByText(EMPTY_RAIL)).toBeNull();
    // Try again re-reads the rail's own list; the chat that answers replaces the line.
    const before = count("/chat/threads");
    H.responses["/chat/threads"] = { threads: THREADS };
    fireEvent.click(within(line).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(count("/chat/threads")).toBe(before + 1));
    await screen.findByTitle("Quarterly numbers");
    expect(screen.queryByTestId("thread-rail-failed")).toBeNull();
  });

  it("the chat page's rail CONTROL: a list that answers empty keeps the empty words", async () => {
    H.nav.pathname = "/chat";
    render(<ChatPage />);
    expect(await screen.findByText(EMPTY_RAIL)).toBeTruthy();
    expect(screen.queryByTestId("thread-rail-failed")).toBeNull();
  });
});

/* ===================================== item 16: small errors, their own line */

describe("a small action's failure has its own dismissible notice (v1.449.0, item 16)", () => {
  it("a failed upload is a status notice with an ×, never the turn-error alert", async () => {
    const held = heldUploads();
    render(<ChatPage />);
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    // AN review: the notice's announcer is mounted with the page, empty.
    const noticeLive = screen.getByTestId("chat-notice-live");
    expect(noticeLive).toHaveAttribute("role", "status");
    expect(noticeLive.textContent).toBe("");
    attach("a.pdf", "b.pdf");
    await waitFor(() => expect(held).toHaveLength(2));
    await act(async () => {
      held.find((h) => h.name === "a.pdf")!.resolve();
      held.find((h) => h.name === "b.pdf")!.reject(new H.FakeApiError("disk full", 507));
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(await screen.findByText("a.pdf")).toBeTruthy();
    const notice = await screen.findByTestId("chat-notice");
    expect(notice.textContent).toContain("Couldn't attach b.pdf: disk full");
    // AN review: the visible line mounts WITH its words (a freshly mounted
    // live region is often never read — the v1.352.0 lesson), so it carries
    // no live role; the ALWAYS-mounted sr-only announcer speaks for it.
    expect(notice.getAttribute("role")).toBeNull();
    expect(notice.getAttribute("aria-live")).toBeNull();
    expect(noticeLive.textContent).toBe(notice.textContent);
    expect(screen.getByTestId("chat-notice-live")).toBe(noticeLive);
    // The TURN's line is untouched: no alert, no Retry — nothing says a reply failed.
    expect(screen.queryByTestId("chat-turn-error")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Retry/ })).toBeNull();
    // Nor does the announcer say anything about it (no turn ended).
    expect(screen.getByTestId("chat-reply-live").textContent).toBe("");
    // The × clears it.
    fireEvent.click(screen.getByTestId("chat-notice-dismiss"));
    expect(screen.queryByTestId("chat-notice")).toBeNull();
    // ...and empties the announcer too (the same element, never unmounted).
    expect(screen.getByTestId("chat-notice-live").textContent).toBe("");
    expect(screen.getByTestId("chat-notice-live")).toBe(noticeLive);
  });

  it("the attachment cap is the same quiet notice", async () => {
    heldUploads();
    render(<ChatPage />);
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    attach("1.pdf", "2.pdf", "3.pdf", "4.pdf", "5.pdf");
    const notice = await screen.findByTestId("chat-notice");
    expect(notice.textContent).toContain("Up to 4 files per message.");
    expect(screen.queryByTestId("chat-turn-error")).toBeNull();
  });
});

/* ===================================== item 16: the screen reader's answer */

describe("one announcer says when a reply is ready (v1.449.0, item 16)", () => {
  it("mounted with the page and empty; 'Reply ready.' once a turn ends cleanly; cleared when the next starts", async () => {
    render(<ChatPage />);
    const live = await screen.findByTestId("chat-reply-live");
    expect(live).toHaveAttribute("role", "status");
    expect(live.textContent).toBe("");
    await startTurn("hello there");
    // While the reply streams the region stays empty (the live bubble is the story).
    expect(screen.getByTestId("chat-reply-live").textContent).toBe("");
    act(() => H.stream.release({ reply: "Here is the answer." }));
    await waitFor(() => expect(screen.getByTestId("chat-reply-live").textContent).toBe(REPLY_READY_WORDS));
    // It is the SAME element the page mounted with (never a region mounted with its words).
    expect(screen.getByTestId("chat-reply-live")).toBe(live);
    // The next turn clears it, so the second reply is a second change.
    await startTurn("and then?");
    await waitFor(() => expect(screen.getByTestId("chat-reply-live").textContent).toBe(""));
    act(() => H.stream.release({ reply: "More." }));
    await waitFor(() => expect(screen.getByTestId("chat-reply-live").textContent).toBe(REPLY_READY_WORDS));
  });

  it("a failed turn is announced by the failure line itself (an alert); the announcer never says 'Reply ready.'", async () => {
    render(<ChatPage />);
    await startTurn("hello there");
    const e = new H.FakeStreamError("the server's own detail", 500);
    Object.assign(e, { title: "Claude Code isn't signed in", fix: "Press Sign in.", fixKind: "sign_in" });
    act(() => H.stream.fail(e));
    const line = await screen.findByTestId("chat-turn-error");
    // The failure sentence is a live region of its own.
    expect(line).toHaveAttribute("role", "alert");
    expect(line.textContent).toMatch(/^Claude Code isn't signed in/);
    // Give the announcer's effect every chance to fire, then read it.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    const heard = screen.getByTestId("chat-reply-live").textContent ?? "";
    expect(heard).not.toBe(REPLY_READY_WORDS);
    // Said once: the alert carries it, the announcer does not repeat it.
    expect(heard).toBe("");
  });

  it("a turn the user stopped says 'Reply stopped.', never 'Reply ready.'", async () => {
    render(<ChatPage />);
    await startTurn("hello there");
    fireEvent.click(await screen.findByRole("button", { name: /stop/i }));
    await waitFor(() => expect(screen.getByTestId("chat-reply-live").textContent).toBe(REPLY_STOPPED_WORDS));
    // Distinct from the transcript's own "Stopped." line (one text, one place).
    expect(screen.getByText("Stopped.")).toBeTruthy();
  });

  it("replyLiveWords: a failure line silences it (the alert speaks), then Reply stopped., else Reply ready.", () => {
    expect(replyLiveWords({ stopped: false, errorLine: null })).toBe(REPLY_READY_WORDS);
    expect(replyLiveWords({ stopped: true, errorLine: "" })).toBe(REPLY_STOPPED_WORDS);
    expect(replyLiveWords({ stopped: true, errorLine: "OpenRouter refused your key" })).toBe("");
    expect(replyLiveWords({ stopped: false, errorLine: "   " })).toBe(REPLY_READY_WORDS);
  });
});

/* ============================================ item 17: eleven pages' words */

describe("lib/loadFailed's new helpers (v1.449.0, item 17)", () => {
  it("couldnt() shapes a failed press like a failed read", () => {
    expect(couldnt("end the session", { status: 500, message: "boom" })).toBe("Couldn't end the session. Try again.");
    expect(couldnt("upload", { status: 0 })).toBe("Couldn't upload. Iron Jarvis isn't responding.");
    expect(couldnt("upload", null)).toBe("Couldn't upload. Iron Jarvis isn't responding.");
  });

  it("failureOf() reads a caught value without lib/api; serverSentence() is a 4xx with words", () => {
    expect(failureOf(new H.FakeApiError("nope", 503))).toEqual({ status: 503, message: "nope" });
    expect(failureOf("a thrown string")).toEqual({ status: 0, message: "a thrown string" });
    expect(failureOf(undefined)).toEqual({ status: 0, message: undefined });
    expect(serverSentence({ status: 409, message: "The terminal has exited." })).toBe(true);
    expect(serverSentence({ status: 500, message: "Internal Server Error" })).toBe(false);
    expect(serverSentence({ status: 404, message: "   " })).toBe(false);
    expect(serverSentence(null)).toBe(false);
  });
});

/** The pages the v1.447.0 re-audit found printing the engine's own words. */
const RAW_MESSAGE_FILES = [
  "app/creative/page.tsx",
  "components/terminal/DirectoryTree.tsx",
  "components/TimeTravelFeed.tsx",
  "components/connections/IronProxyCard.tsx",
  "components/settings/pages/UpdatesPage.tsx",
  "components/settings/pages/BrowserPage.tsx",
  "app/usage/page.tsx",
  "app/projects/page.tsx",
  "app/fleet/page.tsx",
  "app/artifacts/page.tsx",
  "app/skills/page.tsx",
];

/** A line may keep a raw `.message` only here, with its reason: the daemon's
 *  own 4xx `detail` is a sentence written for the user, and a `title` is the
 *  hover that keeps the technical text off the visible words. */
const RAW_MESSAGE_ALLOWED: { file: string; line: string; why: string }[] = [
  {
    file: "app/creative/page.tsx",
    line: "? `The terminal is gone or has exited — ${err.message}`",
    why: "a 404/409 is the daemon's own sentence for the user (the branch is gated on those codes)",
  },
  {
    file: "app/creative/page.tsx",
    line: "serverSentence(ae) ? `Couldn't end the session: ${ae.message}` : couldnt(\"end the session\", ae)",
    why: "a 4xx keeps the daemon's words (serverSentence); a 5xx or no answer is couldnt()",
  },
  {
    file: "app/creative/page.tsx",
    line: 'errs.push(serverSentence(ae) ? `"${file.name}": ${ae.message}` : couldnt(`upload "${file.name}"`, ae));',
    why: "a 4xx keeps the daemon's words (serverSentence); a 5xx or no answer is couldnt()",
  },
  {
    file: "components/TimeTravelFeed.tsx",
    line: "title={`HTTP ${error.status}: ${error.message}`}",
    why: "the technical text rides the title (hover) only; the words are couldntLoad()",
  },
];

const read = (rel: string) => readFileSync(join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");

/** AN review: this scan reads only a `.message` RENDERED in place (a JSX
 *  `{x.message}` or a template `${x.message}`) — the eleven pages' failed
 *  READS. A PRESS that fails (`setErr(e.message)` into an ErrorNote: Fleet's
 *  probe/save, Skills' card/apply/form, Artifacts' run/remove, Updates'
 *  apply, Browser's save, Creative's mkdir/delete/publish) still shows the
 *  daemon's own sentence — right for a 4xx, engine text for a 5xx with no
 *  `detail` ("500 Internal Server Error") — and is NOT covered here. */
describe("the eleven pages never print a raw error.message for a failed READ (v1.449.0, item 17)", () => {
  it("every rendered `.message` reach is a LoadFailedLine / couldnt() now, or allow-listed with a reason", () => {
    const offenders: string[] = [];
    for (const file of RAW_MESSAGE_FILES) {
      const src = read(file);
      src.split("\n").forEach((line, i) => {
        if (!/\$?\{[A-Za-z_][A-Za-z0-9_]*\.message\}/.test(line)) return;
        const allowed = RAW_MESSAGE_ALLOWED.some((a) => a.file === file && line.includes(a.line));
        if (!allowed) offenders.push(`${file}:${i + 1}: ${line.trim()}`);
      });
    }
    expect(offenders).toEqual([]);
  });

  it("the allow-list names lines that exist (a stale entry is deleted, never kept)", () => {
    for (const a of RAW_MESSAGE_ALLOWED) {
      expect(read(a.file).includes(a.line), `${a.file}: ${a.line}`).toBe(true);
    }
  });

  it("each of the eleven uses the shared words (LoadFailedLine or lib/loadFailed)", () => {
    for (const file of RAW_MESSAGE_FILES) {
      const src = read(file);
      expect(
        src.includes("@/components/LoadFailedLine") || src.includes("@/lib/loadFailed"),
        file,
      ).toBe(true);
    }
  });
});

/* ======================================================= AN review (B3) */

/** An escalated hand-off whose POST /sessions answers only when the test
 *  says so — the REAL shape: the chat lane lets go at once, the session is
 *  named some hundreds of milliseconds later, and that gap is where a wrong
 *  "Reply ready." would be heard (an instant mock lands both in one commit
 *  and hides the gap, so the hold is the test). */
function heldHandOff() {
  const h = { open: () => {}, refuse: (_e: Error) => {} };
  H.postResponses["/sessions"] = () =>
    new Promise((resolve, reject) => {
      h.open = () => resolve({ id: "s1", status: "active" });
      h.refuse = reject;
    });
  H.responses["/sessions/s1?transcript=0"] = { session: { id: "s1", status: "active" } };
  H.responses["/sessions/s1/result"] = {};
  H.responses["/sessions/s1/plan"] = {};
  return h;
}

describe("AN review: the announcer's edges (v1.449.0)", () => {
  it("leaving the conversation mid-turn (New chat) announces NOTHING — no reply landed", async () => {
    render(<ChatPage />);
    const live = await screen.findByTestId("chat-reply-live");
    await startTurn("hello there");
    expect(live.textContent).toBe("");
    // New chat: the turn is torn down (stream.abort → the hook resolves with
    // an empty reply), busy falls, nothing was answered.
    act(() => {
      window.dispatchEvent(new Event("ij:new-chat"));
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(screen.getByTestId("chat-reply-live").textContent).toBe("");
    // The next turn, in the new conversation, is announced as usual (the
    // conversation mark is re-read at each start, so "left" never sticks).
    await startTurn("second question");
    act(() => H.stream.release({ reply: "An answer." }));
    await waitFor(() => expect(screen.getByTestId("chat-reply-live").textContent).toBe(REPLY_READY_WORDS));
  });

  it("an escalated turn is announced when the AGENT's reply lands, not when the chat lane lets go", async () => {
    const handOff = heldHandOff();
    render(<ChatPage />);
    const live = await screen.findByTestId("chat-reply-live");
    // EVERY word the region ever holds — a "Reply ready." set and cleared a
    // moment later (the chat lane letting go, then the session named) is
    // read by a screen reader all the same, and a plain read of the region
    // after the fact would never see it.
    const heard: string[] = [];
    const watch = new MutationObserver(() => heard.push(live.textContent ?? ""));
    watch.observe(live, { childList: true, characterData: true, subtree: true });
    await startTurn("rename these files");
    act(() => H.stream.release({ reply: "", escalate: true, escalateReason: "this one needs the full agent" }));
    // The chat lane let go and the session was asked for: the hand-off's
    // gap, then the agent's wait. Neither is "Reply ready.".
    await waitFor(() => expect(H.posts.some((p) => p.path === "/sessions")).toBe(true));
    // The gap: the chat lane is done, the session not yet named.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(heard.filter((w) => w !== "")).toEqual([]);
    act(() => handOff.open());
    await waitFor(() => expect(count("/sessions/s1?transcript=0")).toBeGreaterThan(0), { timeout: 4000 });
    expect(screen.getByTestId("chat-reply-live").textContent).toBe("");
    expect(heard.filter((w) => w !== "")).toEqual([]);
    // The agent's run ends: THAT is the reply.
    H.responses["/sessions/s1?transcript=0"] = {
      session: { id: "s1", status: "completed", summary: "Renamed 3 files." },
    };
    await waitFor(() => expect(screen.getByTestId("chat-reply-live").textContent).toBe(REPLY_READY_WORDS), {
      timeout: 4000,
    });
    expect(await screen.findByText("Renamed 3 files.")).toBeTruthy();
    // Said exactly once, at the agent's end.
    expect(heard.filter((w) => w !== "")).toEqual([REPLY_READY_WORDS]);
    watch.disconnect();
  });

  it("a hand-off that fails to start is said by the failure line; the announcer stays quiet, and the NEXT turn is still announced", async () => {
    const handOff = heldHandOff();
    render(<ChatPage />);
    await screen.findByTestId("chat-reply-live");
    await startTurn("rename these files");
    act(() => H.stream.release({ reply: "", escalate: true }));
    await waitFor(() => expect(H.posts.some((p) => p.path === "/sessions")).toBe(true));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    act(() => handOff.refuse(new H.FakeApiError("No agent can take this right now.", 503)));
    const line = await screen.findByTestId("chat-turn-error");
    expect(line.textContent).toContain("No agent can take this right now.");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(screen.getByTestId("chat-reply-live").textContent).toBe("");
    // A New chat, then an ordinary turn there: announced. (The conversation
    // mark is read at EVERY start — a failed hand-off leaves the "busy"
    // memory armed, and a mark read only on the first rise would still be
    // the old conversation's, silencing this reply as "left".)
    act(() => {
      window.dispatchEvent(new Event("ij:new-chat"));
    });
    await startTurn("just answer then");
    act(() => H.stream.release({ reply: "Sure." }));
    await waitFor(() => expect(screen.getByTestId("chat-reply-live").textContent).toBe(REPLY_READY_WORDS));
  });
});

describe("AN review: the notice never survives into another conversation (v1.449.0)", () => {
  it("New chat clears a standing notice and its announcer", async () => {
    heldUploads();
    render(<ChatPage />);
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    attach("1.pdf", "2.pdf", "3.pdf", "4.pdf", "5.pdf");
    await screen.findByTestId("chat-notice");
    expect(screen.getByTestId("chat-notice-live").textContent).toContain("Up to 4 files");
    act(() => {
      window.dispatchEvent(new Event("ij:new-chat"));
    });
    await waitFor(() => expect(screen.queryByTestId("chat-notice")).toBeNull());
    expect(screen.getByTestId("chat-notice-live").textContent).toBe("");
  });
});
