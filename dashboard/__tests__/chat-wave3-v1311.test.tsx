/**
 * Wave 3 (SPEED), v1.311.0 — opening Chat is instant, and it remembers.
 *
 * Plain words: opening Chat used to ask the daemon for the thread list twice
 * (once for everything, once for the project you last used), could briefly
 * show the wrong project's chats, drew grey placeholders on EVERY visit even
 * seconds after leaving, and dropped the conversation you were in when you
 * went to Overview and back. It also redrew the whole 9,000-line page for
 * every live event, including browser tab switches and router noise it never
 * reads.
 *
 * These pins COUNT work (GETs issued, React commits under a Profiler), never
 * time it. Harness: the v1.285.0 chat-page mocks, with `@/lib/apiCache` left
 * REAL (it is not `@/lib/api`, so a payload cache the page writes is the
 * cache the next mount reads — that is the behaviour under test).
 *
 * THE EVENTS FAKE ENCODES THE W3-4 CONTRACT, NOT TRACK C's CODE: `useEvents`
 * may take an optional second argument `{ types }`; a frame whose type is not
 * listed causes no state update in the caller. The fake honours exactly that
 * (array or Set, plus a `filter` fn variant), so these tests pass on the
 * page's USE of the contract whatever C does inside the hook.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Profiler, type ReactNode } from "react";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

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
  type Ev = { id: string; type: string; session_id?: string | null; payload: Record<string, unknown> };
  type Opts = { types?: Iterable<string>; filter?: (e: Ev) => boolean } | ((e: Ev) => boolean) | undefined;
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      gets: [] as string[],
      resolved: [] as string[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      responses: {} as Record<string, unknown>,
      /** Paths whose GET stays pending until the test releases it. */
      holds: new Map<string, { promise: Promise<unknown>; release: () => void }>(),
    },
    events: {
      list: [] as Ev[],
      /** One entry per mounted subscriber: its setter and the options it passed. */
      subs: new Set<{ set: (l: Ev[]) => void; opts: () => Opts }>(),
      /** Every options value the page passed, one per render. */
      seenOpts: [] as Opts[],
      accepts(opts: Opts, e: Ev): boolean {
        if (!opts) return true;
        if (typeof opts === "function") return opts(e);
        if (opts.filter) return opts.filter(e);
        if (opts.types) return new Set(opts.types).has(e.type);
        return true;
      },
      push(e: Ev) {
        this.list = [e, ...this.list];
        for (const s of this.subs) {
          // THE CONTRACT: a non-matching frame causes NO state update.
          if (this.accepts(s.opts(), e)) s.set(this.list.filter((x) => this.accepts(s.opts(), x)));
        }
      },
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.api.gets.push(path);
    const held = H.api.holds.get(path);
    if (held) await held.promise;
    const r = H.api.responses[path];
    H.api.resolved.push(path);
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  post: async () => ({}),
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
    run: async () => ({ reply: "Noted.", tools_used: [] }),
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", async () => {
  const React = await import("react");
  type Ev = (typeof H.events.list)[number];
  return {
    useEvents: (_max?: number, opts?: unknown) => {
      H.events.seenOpts.push(opts as never);
      const optsRef = React.useRef(opts);
      optsRef.current = opts;
      const [list, setList] = React.useState<Ev[]>(() =>
        H.events.list.filter((e) => H.events.accepts(opts as never, e)),
      );
      React.useEffect(() => {
        const sub = { set: setList, opts: () => optsRef.current as never };
        H.events.subs.add(sub);
        return () => {
          H.events.subs.delete(sub);
        };
      }, []);
      return { events: list, connected: true };
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
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { __resetApiCache } from "@/lib/apiCache";

const PROJECT_KEY = "ij_chat_project";
const UNSCOPED = { threads: [{ id: "tu", title: "Unscoped chat", updated_at: "2026-10-01T10:00:00Z", messages: 2 }] };
const SCOPED = { threads: [{ id: "tp", title: "Project chat", project_id: "p1", updated_at: "2026-10-01T10:00:00Z", messages: 2 }] };
const T9 = {
  id: "t9",
  title: "Ledger",
  updated_at: "2026-10-01T10:00:00Z",
  messages: [
    { role: "user", content: "total the ledger please" },
    { role: "assistant", content: "The ledger totals 1.2M." },
  ],
};
const T7 = {
  id: "t7",
  title: "Invoices",
  updated_at: "2026-10-01T09:00:00Z",
  messages: [
    { role: "user", content: "list the open invoices" },
    { role: "assistant", content: "Three invoices are open." },
  ],
};

function hold(path: string): () => void {
  let release!: () => void;
  const promise = new Promise<void>((r) => {
    release = r;
  });
  H.api.holds.set(path, { promise, release });
  return () => {
    H.api.holds.delete(path);
    release();
  };
}

/** The thread-LIST requests (not a single thread's GET). */
function listGets(): string[] {
  return H.api.gets.filter((p) => p === "/chat/threads" || p.startsWith("/chat/threads?"));
}

/** Let any queued effects/fetches run, so a negative ("no second request") is
 *  asserted after the work that would have issued it. */
async function settle(): Promise<void> {
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.resolved.length = 0;
  H.api.puts.length = 0;
  H.api.holds.clear();
  H.events.list = [];
  H.events.subs.clear();
  H.events.seenOpts.length = 0;
  H.api.responses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": {
      threads: [
        { id: "t9", title: "Ledger", updated_at: "2026-10-01T10:00:00Z", messages: 2 },
        { id: "t7", title: "Invoices", updated_at: "2026-10-01T09:00:00Z", messages: 2 },
      ],
    },
    "/chat/threads/t9": T9,
    "/chat/threads/t7": T7,
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  __resetApiCache();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// chat-entry-waterfall-no-restore
// ---------------------------------------------------------------------------

describe("opening Chat asks for the thread list once, already scoped", () => {
  it("a remembered project: exactly ONE list request, and it is the scoped one", async () => {
    window.localStorage.setItem(PROJECT_KEY, "p1");
    H.api.responses["/projects"] = { projects: [{ id: "p1", name: "Books" }] };
    H.api.responses["/chat/threads"] = UNSCOPED;
    H.api.responses["/chat/threads?project_id=p1"] = SCOPED;
    render(<ChatPage />);
    await screen.findByTitle("Project chat");
    await waitFor(() => expect(H.api.resolved).toContain("/projects"));
    await settle();
    // Today: ["/chat/threads", "/chat/threads?project_id=p1"] — the unscoped
    // list is fetched on mount, then fetched again once /projects resolves.
    expect(listGets()).toEqual(["/chat/threads?project_id=p1"]);
  });

  it("never paints another scope's list while /projects is still loading", async () => {
    window.localStorage.setItem(PROJECT_KEY, "p1");
    H.api.responses["/projects"] = { projects: [{ id: "p1", name: "Books" }] };
    H.api.responses["/chat/threads"] = UNSCOPED;
    H.api.responses["/chat/threads?project_id=p1"] = SCOPED;
    const releaseProjects = hold("/projects");
    render(<ChatPage />);
    await settle();
    // The window the finding describes: the unscoped answer is back, /projects
    // is not. The user's project chats are what they asked for.
    expect(screen.queryByTitle("Unscoped chat")).toBeNull();
    releaseProjects();
    await screen.findByTitle("Project chat");
    expect(screen.queryByTitle("Unscoped chat")).toBeNull();
  });

  it("CONTROL: no remembered project — one unscoped request (already true)", async () => {
    render(<ChatPage />);
    await screen.findByTitle("Ledger");
    await waitFor(() => expect(H.api.resolved).toContain("/projects"));
    await settle();
    expect(listGets()).toEqual(["/chat/threads"]);
  });

  it("CONTROL: a remembered project that no longer exists still ends on the full list", async () => {
    // The fix reads the remembered id BEFORE /projects confirms it. A deleted
    // project must not strand the sidebar on an empty scoped list.
    window.localStorage.setItem(PROJECT_KEY, "gone");
    H.api.responses["/projects"] = { projects: [{ id: "p1", name: "Books" }] };
    H.api.responses["/chat/threads"] = UNSCOPED;
    H.api.responses["/chat/threads?project_id=gone"] = { threads: [] };
    render(<ChatPage />);
    expect(await screen.findByTitle("Unscoped chat")).toBeTruthy();
  });
});

describe("a return visit paints the last list at once", () => {
  it("the second mount shows the cached threads, not a skeleton, while the refetch is in flight", async () => {
    const first = render(<ChatPage />);
    await screen.findByTitle("Ledger");
    first.unmount();

    hold("/chat/threads"); // the revalidation never answers in this test
    render(<ChatPage />);
    // Today: threadsLoading starts true and nothing seeds the list, so the
    // rail is four grey bars until the GET answers.
    expect(screen.queryByTitle("Ledger")).not.toBeNull();
    expect(document.querySelectorAll(".skeleton").length).toBe(0);
    // ...and it still revalidates behind the cached paint.
    await waitFor(() => expect(listGets().length).toBe(2));
  });

  it("the cache is per SCOPE: an unscoped list is never painted for a project", async () => {
    H.api.responses["/chat/threads"] = UNSCOPED;
    const first = render(<ChatPage />);
    await screen.findByTitle("Unscoped chat");
    first.unmount();

    window.history.replaceState({}, "", "/chat");
    window.localStorage.setItem(PROJECT_KEY, "p1");
    H.api.responses["/projects"] = { projects: [{ id: "p1", name: "Books" }] };
    H.api.responses["/chat/threads?project_id=p1"] = SCOPED;
    hold("/chat/threads?project_id=p1");
    render(<ChatPage />);
    await settle();
    expect(screen.queryByTitle("Unscoped chat")).toBeNull();
  });
});

describe("Chat reopens the conversation you were in", () => {
  async function openLedger() {
    fireEvent.click(await screen.findByTitle("Ledger"));
    await screen.findByText("The ledger totals 1.2M.");
  }

  it("Chat -> elsewhere -> Chat restores the open thread (per window)", async () => {
    const first = render(<ChatPage />);
    await openLedger();
    first.unmount();

    window.history.replaceState({}, "", "/chat");
    const second = render(<ChatPage />);
    // Today: a blank new chat — the thread is saved, but you must find it again.
    expect(await screen.findByText("The ledger totals 1.2M.")).toBeTruthy();
    second.unmount();

    // PER WINDOW: the memory lives in sessionStorage. Clearing it (a different
    // window has its own) leaves localStorage untouched and restores nothing.
    window.sessionStorage.clear();
    H.api.gets.length = 0;
    render(<ChatPage />);
    await screen.findByTitle("Ledger");
    await settle();
    expect(H.api.gets).not.toContain("/chat/threads/t9");
    expect(screen.queryByText("The ledger totals 1.2M.")).toBeNull();
  });

  it("the restored conversation paints from the cache before its GET answers", async () => {
    const first = render(<ChatPage />);
    await openLedger();
    first.unmount();

    hold("/chat/threads/t9");
    render(<ChatPage />);
    // Only a cached copy of /chat/threads/t9 can put this on screen.
    expect(await screen.findByText("The ledger totals 1.2M.")).toBeTruthy();
  });

  it("New chat forgets it: the next visit opens a fresh conversation", async () => {
    const first = render(<ChatPage />);
    await openLedger();
    first.unmount();

    const second = render(<ChatPage />);
    await screen.findByText("The ledger totals 1.2M."); // restored (red today)
    fireEvent.click(screen.getByTitle("Start a new conversation"));
    await waitFor(() => expect(screen.queryByText("The ledger totals 1.2M.")).toBeNull());
    second.unmount();

    H.api.gets.length = 0;
    render(<ChatPage />);
    await screen.findByTitle("Ledger");
    await settle();
    expect(H.api.gets).not.toContain("/chat/threads/t9");
  });

  it("a ?thread= deep link wins over the remembered one, and becomes the remembered one", async () => {
    const first = render(<ChatPage />);
    await openLedger();
    first.unmount();

    window.history.replaceState({}, "", "/chat?thread=t7");
    H.api.gets.length = 0;
    const second = render(<ChatPage />);
    await screen.findByText("Three invoices are open.");
    await settle();
    expect(H.api.gets).not.toContain("/chat/threads/t9");
    second.unmount();

    // The deep link was stripped (by design); the window now remembers t7.
    window.history.replaceState({}, "", "/chat");
    render(<ChatPage />);
    expect(await screen.findByText("Three invoices are open.")).toBeTruthy();
  });

  it("a remembered thread that was deleted opens nothing, says nothing, and is forgotten", async () => {
    const first = render(<ChatPage />);
    await openLedger();
    first.unmount();

    H.api.responses["/chat/threads/t9"] = new H.FakeApiError("thread not found", 404);
    __resetApiCache(); // no cached copy of the deleted thread either
    H.api.gets.length = 0;
    const second = render(<ChatPage />);
    // The restore was attempted (red today: nothing is remembered)...
    await waitFor(() => expect(H.api.gets).toContain("/chat/threads/t9"));
    await settle();
    // ...and failed quietly: a vanished thread is not an error the user made.
    expect(screen.queryByText(/thread not found/i)).toBeNull();
    second.unmount();

    H.api.gets.length = 0;
    render(<ChatPage />);
    await screen.findByTitle("Ledger");
    await settle();
    expect(H.api.gets).not.toContain("/chat/threads/t9");
  });

  it("GUARD: storage that throws never breaks the page (passes today: nothing is read yet)", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    render(<ChatPage />);
    fireEvent.click(await screen.findByTitle("Ledger"));
    expect(await screen.findByText("The ledger totals 1.2M.")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// events-unfiltered-page-subscribers
// ---------------------------------------------------------------------------

let commits = 0;
function Counted({ children }: { children: ReactNode }) {
  return (
    <Profiler id="chat" onRender={() => (commits += 1)}>
      {children}
    </Profiler>
  );
}

let evSeq = 0;
function ev(type: string, payload: Record<string, unknown> = {}, session_id: string | null = null) {
  evSeq += 1;
  return { id: `e${evSeq}`, type, session_id, payload };
}

const NOISE = [
  "browser.tab_activated",
  "browser.navigation_completed",
  "provider.routed",
  "llm.completed",
  "comm.desktop",
  "artifact.generated",
];

describe("the chat page re-renders only for the events it reads", () => {
  it("20 frames of browser/router noise commit NOTHING", async () => {
    render(
      <Counted>
        <ChatPage />
      </Counted>,
    );
    await screen.findByTitle("Ledger");
    await waitFor(() => expect(H.api.resolved).toContain("/projects"));
    await settle();
    commits = 0;
    for (let i = 0; i < 20; i++) {
      // One act() per frame, so React cannot batch twenty updates into one
      // commit and hide the per-frame cost (the S-02 lesson).
      act(() => {
        H.events.push(ev(NOISE[i % NOISE.length], { tab_id: i }));
      });
    }
    // Today: 20 — every frame re-runs the whole page.
    expect(commits).toBe(0);
  });

  it("CONTROL: a frame it DOES read still re-renders and still acts", async () => {
    render(
      <Counted>
        <ChatPage />
      </Counted>,
    );
    await screen.findByTitle("Ledger");
    await waitFor(() => expect(H.api.resolved).toContain("/projects"));
    await settle();
    const listsBefore = listGets().length;
    commits = 0;
    act(() => {
      H.events.push(ev("chat.thread_updated", { thread_id: "t-other" }));
    });
    expect(commits).toBeGreaterThan(0);
    // The comm effect refreshes the sidebar on a thread update.
    await waitFor(() => expect(listGets().length).toBe(listsBefore + 1));
  });

  it("the page passes ONE stable, module-level type list (no resubscribe per render)", async () => {
    render(<ChatPage />);
    await screen.findByTitle("Ledger");
    await settle();
    const typeLists = H.events.seenOpts.map((o) =>
      o && typeof o === "object" ? (o as { types?: unknown }).types : undefined,
    );
    expect(typeLists.length).toBeGreaterThan(1);
    // Today: undefined on every render (no filter at all).
    expect(typeLists[0]).toBeDefined();
    for (const t of typeLists) expect(t).toBe(typeLists[0]);
  });
});

/** Event types the page's own effects compare against, derived from SOURCE so
 *  a new consumer added later must join the list or this goes red. */
const LF = String.fromCharCode(10);
const CRLF = String.fromCharCode(13) + LF;
function src(rel: string): string {
  return readFileSync(resolve(process.cwd(), rel), "utf-8").split(CRLF).join(LF);
}

describe("the chat's event type list covers every type the page reads", () => {
  it("includes every literal the page, stepLabel, the undo fold and the batch card compare to", async () => {
    // Imported at RUN time, by absolute path (a computed "@/..." specifier is
    // not alias-resolved at run time), so a missing module fails THIS test,
    // not the whole file.
    const modPath = resolve(process.cwd(), "components/chat/chatEventTypes.ts");
    expect(existsSync(modPath), "components/chat/chatEventTypes.ts must exist").toBe(true);
    const mod = (await import(/* @vite-ignore */ modPath)) as { CHAT_EVENT_TYPES?: Iterable<string> };
    expect(mod.CHAT_EVENT_TYPES, "CHAT_EVENT_TYPES export").toBeDefined();
    const have = new Set(mod.CHAT_EVENT_TYPES);

    const need = new Set<string>();
    const page = src("app/chat/page.tsx");
    for (const m of page.matchAll(/\.type\s*[!=]==\s*"([a-z_]+\.[a-z_.]+)"/g)) need.add(m[1]);
    for (const m of src("components/chat/stepLabel.ts").matchAll(/case "([a-z_]+\.[a-z_.]+)"/g)) need.add(m[1]);
    for (const m of src("components/chat/ArtifactsRail.tsx").matchAll(/\.type\s*[!=]==\s*"([a-z_]+\.[a-z_.]+)"/g)) need.add(m[1]);
    for (const m of src("components/chat/BatchSuggestCard.tsx").matchAll(/\.type\s*[!=]==\s*"([a-z_]+\.[a-z_.]+)"/g)) need.add(m[1]);
    // Anti-vacuity: the derivation found the consumers the finding names.
    for (const t of ["chat.thread_updated", "agent_thread.updated", "remote.message", "agent.completed", "approval.requested", "approval.resolved", "action.reverted", "batch.file_done", "tool.executed", "plan.step_started"]) {
      expect(need.has(t), `derivation lost ${t}`).toBe(true);
    }
    const missing = [...need].filter((t) => !have.has(t));
    expect(missing, "types the page reads but would never receive").toEqual([]);
    // And it is a FILTER: the noise the finding measured is not in it.
    for (const t of NOISE) expect(have.has(t), `${t} should be filtered out`).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Implementer's addition (v1.311.0): the cached paint lets the user act
// BEFORE the thread's GET answers, so a late answer must not undo what they
// did. Mutation-checked: deleting openThread's generation guard puts the
// conversation back on screen and back into the window's memory.
// ---------------------------------------------------------------------------

describe("a late thread answer never undoes New chat", () => {
  it("New chat pressed while the restored thread's GET is out: the answer is dropped", async () => {
    const first = render(<ChatPage />);
    fireEvent.click(await screen.findByTitle("Ledger"));
    await screen.findByText("The ledger totals 1.2M.");
    first.unmount();

    const release = hold("/chat/threads/t9");
    render(<ChatPage />);
    await screen.findByText("The ledger totals 1.2M."); // the cached paint
    fireEvent.click(screen.getByTitle("Start a new conversation"));
    await waitFor(() => expect(screen.queryByText("The ledger totals 1.2M.")).toBeNull());
    release();
    await waitFor(() => expect(H.api.resolved).toContain("/chat/threads/t9"));
    await settle();
    expect(screen.queryByText("The ledger totals 1.2M.")).toBeNull();
    expect(window.sessionStorage.length).toBe(0);
  });
});
