/**
 * v1.345.0 — "an @ reply comes home" (lane U-page).
 *
 * THE BUG (the user's install, 2026-10-16): in a NEW unsaved chat they typed
 * "@hermes-mac-mini do some tax research…" (POST /chat/panel, a remote round
 * that took six minutes), then opened another saved chat ("find me a place to
 * eat in orlando"). When the round answered, sendPanel's continuation did
 * setMessages([@, reply]) + queueSave over WHATEVER CHAT WAS OPEN: the saved
 * chat's two messages were overwritten on disk and the reply never appeared
 * in the chat it was asked in.
 *
 * Pinned here, on the REAL page, with the panel POST HELD until the test
 * releases it (the race is reproduced, not hoped for):
 *  1. THE BUG: the @ chat is saved AT SEND (so the round carries thread_id);
 *     the user opens another chat; the round answers `appended` → the other
 *     chat's messages are untouched, NO PUT goes to it, and reopening the @
 *     chat shows the reply the daemon appended under the @;
 *  2. the same race against an OLDER daemon (no `appended`): the round is
 *     saved into the chat it was asked in, never the open one;
 *  3. still there when it answers: the reply renders via the re-read, and no
 *     PUT carries it;
 *  4. while a round runs the bubble names who is working, with a clock;
 *  5. every agent door the page opens from chat carries chat_thread_id, and
 *     finalize renders the line the daemon appended (receipt included)
 *     instead of composing + PUTting its own;
 *  6. finalize after leaving writes nothing;
 *  7. a chat.thread_updated for the OPEN user-owned chat from another writer
 *     re-reads it and ADOPTS the server's version (the next save carries the
 *     adopted stamp), never overwrites.
 *
 * Harness: the v1.284.0 chat-page mocks + the v1.285.0 real `useEvents` over
 * a hoisted store, plus a per-call responder for /chat/panel that can hold.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
  type Ev = { id: string; type: string; payload: Record<string, unknown> };
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      gets: [] as string[],
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
      /** A per-call PUT responder: answer a call yourself (a promise the test
       *  holds and resolves or rejects by hand), or return undefined to let
       *  the default below answer it. */
      putHook: null as null | ((path: string, body: Record<string, unknown>) => unknown),
    },
    stream: { bodies: [] as Record<string, unknown>[], held: null as Promise<void> | null },
    events: {
      list: [] as Ev[],
      setters: new Set<(l: Ev[]) => void>(),
      push(e: Ev) {
        this.list = [e, ...this.list];
        for (const s of this.setters) s(this.list);
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
    const r = H.api.getResponses[path];
    // A real fetch parses fresh JSON every time; the page's save rebase keys
    // on object identity (v1.232.0), so a fixture handed back BY REFERENCE
    // would make two reads of one thread look like one array.
    return r === undefined ? {} : JSON.parse(JSON.stringify(r));
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
    // A held/rejected promise handed back here is what the page awaits.
    const hooked = H.api.putHook?.(path, body);
    if (hooked !== undefined) return hooked;
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    // A NEW chat is named t1 and stamped S1; an existing one keeps its id.
    return m && m[1] !== "new" ? { id: m[1], title: "t" } : { id: "t1", title: "t", updated_at: "S1" };
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
    run: async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
      H.stream.bodies.push(body);
      await new Promise<void>((r) => setTimeout(r, 0));
      if (H.stream.held) await H.stream.held; // a turn HELD mid-flight by the test
      onDelta("Noted.", "Noted.");
      return {
        reply: "Noted.",
        route: { requested: "", provider: "mock", model: "mock", reason: "default" },
        tools_used: [],
        remembered: [],
      };
    },
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", async () => {
  const React = await import("react");
  return {
    useEvents: () => {
      const [list, setList] = React.useState(H.events.list);
      React.useEffect(() => {
        H.events.setters.add(setList);
        return () => {
          H.events.setters.delete(setList);
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

const BUILDER = {
  mention: "builder",
  name: "builder",
  kind: "builtin",
  source: "builtin",
  description: "Builds things",
  healthy: true,
  delegable: true,
};

/** The saved chat the user opens while the round runs — the one the bug
 *  overwrote (its id is the live install's). */
const ORLANDO = {
  id: "a060b60221c5",
  title: "Orlando",
  updated_at: "2026-10-16T10:00:00+00:00",
  messages: [
    { role: "user", content: "find me a place to eat in orlando" },
    { role: "assistant", content: "Try Se7en Bites." },
  ],
};
const ORLANDO_PATH = `/chat/threads/${ORLANDO.id}`;
const ROOM = "athr1";
const REPLY = "Hi from builder.";

/** A round where builder answers `content`; `appended` = a v1.345.0 daemon
 *  that wrote it to the chat and stamped it `updated_at`. */
function round(content: string, extra: Record<string, unknown> = {}) {
  return {
    mode: "panel",
    thread_id: ROOM,
    entries: [
      { who: "user", content: "(echoed)", at: "2026-10-16T10:00:00Z" },
      { who: "builtin:builder", content, at: "2026-10-16T10:00:03Z" },
    ],
    spoke: ["builtin:builder"],
    skipped: [],
    unknown_mentions: [],
    context: { chat_messages: 0, chat_dropped: 0 },
    ...extra,
  };
}
/** The @ chat as the daemon holds it after it appended the round. */
function appendedThread() {
  return {
    id: "t1",
    title: "Hello",
    updated_at: "S2",
    messages: [
      { role: "user", content: "@builder hello" },
      { role: "assistant", content: REPLY, panelWho: "builtin:builder", panelThreadId: ROOM, at: "2026-10-16T10:00:03+00:00" },
    ],
  };
}
/** POST /chat/panel HELD until `release(answer)` — the six-minute round.
 *  Released with an Error, the held POST REJECTS with it (the daemon
 *  answered 500 / the link dropped); `later` answers every call after the
 *  first (a second @ in another chat while the first round is airborne). */
function holdPanel(later?: (body: Record<string, unknown>) => unknown) {
  let resolve!: (answer: unknown) => void;
  let reject!: (err: Error) => void;
  const held = new Promise<unknown>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  let calls = 0;
  H.api.postResponses["/chat/panel"] = (body: Record<string, unknown>) =>
    ++calls === 1 || !later ? held : later(body);
  return (answer: unknown) => (answer instanceof Error ? reject(answer) : resolve(answer));
}

const SESSION_DONE = {
  session: { id: "s1", task: "write me a PDF summary", agent_type: "builder", provider: "mock", model: "m", status: "completed", workspace_path: "C:\\w", summary: "Made the PDF." },
  transcript: [],
};
const RESULT_DONE = {
  found: true,
  session_id: "s1",
  status: "completed",
  task: "write me a PDF summary",
  summary: "",
  steps: 2,
  tools_used: [{ tool: "write_document", count: 1 }],
  tools_failed: [],
  files_created: ["brief.pdf"],
  files_changed: [],
  documents: ["C:\\w\\brief.pdf"],
  errors: [],
  revertable: 1,
  duration_s: 3,
};

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.held = null;
  H.api.putHook = null;
  H.events.list = [];
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": {
      threads: [
        { id: ORLANDO.id, title: "Orlando", updated_at: ORLANDO.updated_at, messages: 2 },
        { id: "t1", title: "Hello", updated_at: "S1", messages: 1 },
      ],
    },
    [ORLANDO_PATH]: ORLANDO,
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [BUILDER] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear(); // the page reopens the chat this window last had open
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function box() {
  return screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement;
}
async function catalogReady(mention = "builder") {
  await waitFor(() => expect(box()).toBeTruthy());
  fireEvent.change(box(), { target: { value: `@${mention.slice(0, 2)}` } });
  await screen.findByRole("option", { name: new RegExp(mention) });
  fireEvent.change(box(), { target: { value: "" } });
}
async function type(text: string) {
  fireEvent.change(box(), { target: { value: text } });
  fireEvent.keyDown(box(), { key: "Enter" });
}
function panelPosts() {
  return H.api.posts.filter((p) => p.path === "/chat/panel");
}
function putsTo(path: string) {
  return H.api.puts.filter((p) => p.path === path);
}
function putsCarrying(text: string) {
  return H.api.puts.filter((p) =>
    ((p.body.messages ?? []) as { content?: string }[]).some((m) => (m.content ?? "").includes(text)),
  );
}
/** Let the released POST's continuation run to its end (it has no awaited
 *  hop after the answer when the user has left — nothing it could signal). */
async function settle(ticks = 4) {
  for (let i = 0; i < ticks; i++) {
    await act(async () => {
      await new Promise<void>((r) => setTimeout(r, 0));
    });
  }
}
/** Send "@builder hello" in a new chat and wait for the round to be airborne
 *  WITH the chat's id (the at-send save named it first). */
async function sendAtInNewChat() {
  render(<ChatPage />);
  await catalogReady();
  await type("@builder hello");
  // THE QUESTION IS SAVED AT SEND — the chat is named before the daemon is asked
  await waitFor(() => expect(putsTo("/chat/threads/new")).toHaveLength(1));
  expect((putsTo("/chat/threads/new")[0].body.messages as { content: string }[]).map((m) => m.content)).toEqual([
    "@builder hello",
  ]);
  await waitFor(() => expect(panelPosts()).toHaveLength(1));
  const body = panelPosts()[0].body;
  expect(body.thread_id).toBe("t1"); // ...and the round carries its id
  expect(body.chat_thread_id).toBe("t1");
  expect(body.mentions).toEqual(["builder"]);
}
async function openOrlando() {
  fireEvent.click(await screen.findByTestId(`thread-row-${ORLANDO.id}`));
  await screen.findByText("find me a place to eat in orlando");
  await screen.findByText("Try Se7en Bites.");
}

describe("an @ reply comes home (v1.345.0)", () => {
  it("THE BUG: the round answers after the user opened another chat — that chat is untouched, and the reply is in the chat it was asked in", async () => {
    const release = holdPanel();
    await sendAtInNewChat();
    // the user opens the saved chat while the round runs (six minutes, live)
    await openOrlando();
    expect(putsTo(ORLANDO_PATH)).toHaveLength(0);
    // the daemon appended the round to t1 and answers `appended`
    H.api.getResponses["/chat/threads/t1"] = appendedThread();
    release(round(REPLY, { appended: true, updated_at: "S2" }));
    await settle();
    // THE OLD SHAPE: setMessages([@, reply]) + queueSave over the open chat —
    // "Try Se7en Bites." gone from the screen and a PUT to the Orlando chat
    // carrying "Hi from builder." over its two messages.
    expect(screen.getByText("find me a place to eat in orlando")).toBeTruthy();
    expect(screen.getByText("Try Se7en Bites.")).toBeTruthy();
    expect(screen.queryByText(REPLY)).toBeNull();
    expect(putsTo(ORLANDO_PATH)).toHaveLength(0);
    expect(putsCarrying(REPLY)).toHaveLength(0); // the daemon wrote it, not the page
    // reopening the @ chat shows the reply the daemon appended, under the @
    fireEvent.click(await screen.findByTestId("thread-row-t1"));
    await screen.findByText(REPLY);
    expect(screen.getByText("@builder hello")).toBeTruthy();
    expect(screen.queryByText("Try Se7en Bites.")).toBeNull();
    expect(putsTo(ORLANDO_PATH)).toHaveLength(0);
  });

  it("the same race against an OLDER daemon (no `appended`): the round is saved into the chat it was asked in, never the open one", async () => {
    const release = holdPanel();
    await sendAtInNewChat();
    await openOrlando();
    release(round(REPLY)); // no appended, no updated_at
    // the positive signal: the round reaches disk — in t1
    await waitFor(() => expect(putsCarrying(REPLY)).toHaveLength(1));
    expect(putsCarrying(REPLY)[0].path).toBe("/chat/threads/t1");
    expect(putsTo(ORLANDO_PATH)).toHaveLength(0);
    // ...and the open chat's screen is still its own
    expect(screen.getByText("Try Se7en Bites.")).toBeTruthy();
    expect(screen.queryByText(REPLY)).toBeNull();
  });

  it("still there when it answers: the reply renders through the re-read, and no PUT carries it", async () => {
    const release = holdPanel();
    await sendAtInNewChat();
    H.api.getResponses["/chat/threads/t1"] = appendedThread();
    release(round(REPLY, { appended: true, updated_at: "S2" }));
    await screen.findByText(REPLY);
    // the re-read happened, the page saved nothing of the round
    expect(H.api.gets.filter((p) => p === "/chat/threads/t1").length).toBeGreaterThan(0);
    expect(putsCarrying(REPLY)).toHaveLength(0);
    // the working bubble is gone and the conversation stays with builder
    await waitFor(() => expect(screen.queryByTestId("panel-working")).toBeNull());
    expect(await screen.findByTestId("addressee-strip")).toHaveTextContent(/Talking to builder/);
    // the adopted stamp rides the next save (adopt, never overwrite) — a
    // Jarvis turn, so the save goes through the chat lane's at-send PUT
    fireEvent.click(await screen.findByRole("button", { name: "Back to Jarvis" }));
    await type("thanks");
    await screen.findByText("Noted.");
    await waitFor(() => expect(putsCarrying("thanks").length).toBeGreaterThan(0));
    expect(putsCarrying("thanks")[0].body.if_updated_at).toBe("S2");
    expect(putsCarrying("thanks")[0].path).toBe("/chat/threads/t1");
  });

  it("the round FAILS after the user opened another chat — the error path restores nothing over the open chat", async () => {
    const release = holdPanel();
    await sendAtInNewChat();
    await openOrlando();
    expect(box().value).toBe(""); // the open chat's box is empty
    // the daemon answers 500 (or the link dropped) six minutes in
    release(new H.FakeApiError("The panel broke.", 500));
    await settle();
    // THE OLD SHAPE: setMessages(before) painted the @ chat's `before` (= [])
    // over the open chat, the error line and the typed "@builder hello" went
    // into the open chat's box.
    expect(screen.getByText("find me a place to eat in orlando")).toBeTruthy();
    expect(screen.getByText("Try Se7en Bites.")).toBeTruthy();
    expect(screen.queryByText(/The panel broke/)).toBeNull();
    expect(screen.queryByText("@builder hello")).toBeNull();
    expect(box().value).toBe("");
    expect(putsTo(ORLANDO_PATH)).toHaveLength(0);
    // ...and the open chat is still usable: a turn there goes out normally
    await type("thanks");
    await screen.findByText("Noted.");
  });

  it("the old round landing does not drop the next conversation's double-send guard", async () => {
    // An agent send sits with `busy` false until its session is named (the
    // POST /sessions is airborne); `sendingRef` is the only guard then. The
    // old round's finally reset it unconditionally, so Enter in that window
    // started a SECOND turn in the new conversation.
    const verdict = {
      mode: "session",
      target: "builder",
      who: "builtin:builder",
      tools: ["write_document"],
      reason: "work",
      unknown_mentions: [],
    };
    const release = holdPanel(() => verdict);
    let openSession!: (v: unknown) => void;
    H.api.postResponses["/sessions"] = () => new Promise((r) => (openSession = r));
    await sendAtInNewChat();
    await openOrlando();
    // a hand-off in the Orlando chat — its POST /sessions is held
    await type("@builder write me a PDF summary");
    await waitFor(() => expect(H.api.posts.filter((p) => p.path === "/sessions")).toHaveLength(1));
    expect(H.api.posts.filter((p) => p.path === "/sessions")[0].body.chat_thread_id).toBe(ORLANDO.id);
    expect(box().disabled).toBe(false); // `busy` is false in this window
    // the OLD round lands now
    H.api.getResponses["/chat/threads/t1"] = appendedThread();
    release(round(REPLY, { appended: true, updated_at: "S2" }));
    await settle();
    // Enter in the window: the guard holds — nothing new goes out
    const panelsBefore = panelPosts().length;
    await type("and a cover letter");
    await settle();
    expect(panelPosts()).toHaveLength(panelsBefore);
    expect(H.api.posts.filter((p) => p.path === "/sessions")).toHaveLength(1);
    expect(box().value).toBe("and a cover letter"); // not consumed
    openSession({ id: "s1", status: "active", task: "x", agent_type: "builder" });
  });

  it("while a round runs the bubble names who is working, with a clock", async () => {
    const release = holdPanel();
    await sendAtInNewChat();
    const working = await screen.findByTestId("panel-working");
    expect(working).toHaveTextContent("builder is working");
    expect(await screen.findByTestId("turn-clock", {}, { timeout: 5000 })).toBeTruthy();
    H.api.getResponses["/chat/threads/t1"] = appendedThread();
    release(round(REPLY, { appended: true, updated_at: "S2" }));
    await screen.findByText(REPLY);
    await waitFor(() => expect(screen.queryByTestId("panel-working")).toBeNull());
  });
});

describe("an agent session started from chat comes home (v1.345.0)", () => {
  function sessionVerdict() {
    H.api.postResponses["/chat/panel"] = () => ({
      mode: "session",
      target: "builder",
      who: "builtin:builder",
      tools: ["write_document"],
      reason: "work",
      unknown_mentions: [],
    });
    H.api.postResponses["/sessions"] = { id: "s1", status: "active", task: "x", agent_type: "builder" };
  }
  function sessionPosts() {
    return H.api.posts.filter((p) => p.path === "/sessions");
  }

  it("POST /sessions carries chat_thread_id, and finalize renders the line the daemon appended instead of composing + PUTting", async () => {
    sessionVerdict();
    render(<ChatPage />);
    await catalogReady();
    await type("@builder write me a PDF summary");
    await waitFor(() => expect(sessionPosts()).toHaveLength(1));
    expect(sessionPosts()[0].body.chat_thread_id).toBe("t1");
    // the session completes and the DAEMON appended its line (its keys)
    H.api.getResponses["/sessions/s1?transcript=0"] = SESSION_DONE;
    H.api.getResponses["/sessions/s1/result"] = RESULT_DONE;
    H.api.getResponses["/chat/threads/t1"] = {
      id: "t1",
      title: "PDF",
      updated_at: "S3",
      messages: [
        { role: "user", content: "@builder write me a PDF summary" },
        { role: "assistant", content: "Made the PDF.", sessionId: "s1", agentType: "builder", panelWho: "builtin:builder", documents: ["C:\\w\\brief.pdf"], receipt: RESULT_DONE },
      ],
    };
    await screen.findByText("Made the PDF.", {}, { timeout: 8000 });
    // the daemon's line reads as the page's own: attributed, with its receipt card
    expect(await screen.findByTestId("addressee-strip")).toHaveTextContent(/Talking to builder/);
    expect(await screen.findByTestId("run-result-headline")).toBeTruthy();
    // ...and the page COMPOSED nothing. The files rail's own setup save
    // (showDocPreview → queueSaveDocs, v1.155.0) still re-sends the array
    // this window holds — which is now the daemon's, under the adopted stamp:
    // the line is the daemon's (its `sessionId`), once, never a page-made
    // `fromSession`-only line, and never under the pre-append stamp.
    await settle();
    for (const p of putsCarrying("Made the PDF.")) {
      const lines = (p.body.messages as { content: string; sessionId?: string; fromSession?: string }[]).filter(
        (m) => m.content === "Made the PDF.",
      );
      expect(lines).toHaveLength(1);
      expect(lines[0].sessionId).toBe("s1");
      expect(p.body.if_updated_at).toBe("S3");
    }
    // the box is the user's again
    await waitFor(() => expect(box().disabled).toBe(false));
  });

  it("the continue door carries chat_thread_id: a second @builder after the first session answered posts /sessions/s1/continue with it", async () => {
    sessionVerdict();
    H.api.postResponses["/sessions/s1/continue"] = { id: "s1", status: "active", task: "y", agent_type: "builder" };
    render(<ChatPage />);
    await catalogReady();
    await type("@builder write me a PDF summary");
    await waitFor(() => expect(sessionPosts()).toHaveLength(1));
    H.api.getResponses["/sessions/s1?transcript=0"] = SESSION_DONE;
    H.api.getResponses["/sessions/s1/result"] = RESULT_DONE;
    H.api.getResponses["/chat/threads/t1"] = {
      id: "t1",
      title: "PDF",
      updated_at: "S3",
      messages: [
        { role: "user", content: "@builder write me a PDF summary" },
        { role: "assistant", content: "Made the PDF.", sessionId: "s1", agentType: "builder", panelWho: "builtin:builder", documents: ["C:\\w\\brief.pdf"], receipt: RESULT_DONE },
      ],
    };
    await screen.findByText("Made the PDF.", {}, { timeout: 8000 });
    await waitFor(() => expect(box().disabled).toBe(false));
    // the page chains on the returned session id: the same agent CONTINUES
    await type("@builder and a cover letter");
    await waitFor(() => expect(H.api.posts.filter((p) => p.path === "/sessions/s1/continue")).toHaveLength(1));
    const cont = H.api.posts.filter((p) => p.path === "/sessions/s1/continue")[0].body;
    expect(cont.chat_thread_id).toBe("t1");
    expect(sessionPosts()).toHaveLength(1); // no second opener
  });

  it("the spawn door carries chat_thread_id: one of YOUR agents named by the verdict opens through /agents/<slug>/spawn with it", async () => {
    const TAXPRO = {
      mention: "taxpro",
      name: "custom:taxpro",
      kind: "dynamic",
      source: "dynamic",
      description: "Sharp tax accountant",
      healthy: true,
      delegable: true,
    };
    H.api.getResponses["/agents/mentionable"] = { agents: [BUILDER, TAXPRO] };
    H.api.postResponses["/chat/panel"] = () => ({
      mode: "session",
      target: "custom:taxpro",
      who: "dynamic:taxpro",
      tools: ["write_document"],
      reason: "work",
      unknown_mentions: [],
    });
    H.api.postResponses["/agents/taxpro/spawn"] = { id: "s2", status: "active", task: "y", agent_type: "builder" };
    render(<ChatPage />);
    await catalogReady("taxpro");
    await type("@taxpro draft a memo");
    await waitFor(() => expect(H.api.posts.filter((p) => p.path === "/agents/taxpro/spawn")).toHaveLength(1));
    const spawn = H.api.posts.filter((p) => p.path === "/agents/taxpro/spawn")[0].body;
    expect(spawn.chat_thread_id).toBe("t1");
    expect(sessionPosts()).toHaveLength(0);
  });

  it("an older daemon that appended nothing still gets the composed reply, saved", async () => {
    sessionVerdict();
    render(<ChatPage />);
    await catalogReady();
    await type("@builder write me a PDF summary");
    await waitFor(() => expect(sessionPosts()).toHaveLength(1));
    H.api.getResponses["/sessions/s1?transcript=0"] = SESSION_DONE;
    H.api.getResponses["/sessions/s1/result"] = RESULT_DONE;
    H.api.getResponses["/chat/threads/t1"] = {
      id: "t1",
      title: "PDF",
      updated_at: "S1",
      messages: [{ role: "user", content: "@builder write me a PDF summary" }],
    };
    await screen.findByText("Made the PDF.", {}, { timeout: 8000 });
    await waitFor(() => expect(putsCarrying("Made the PDF.")).toHaveLength(1));
    expect(putsCarrying("Made the PDF.")[0].path).toBe("/chat/threads/t1");
  });

  it("finalize after leaving writes nothing — the session's completion reaches no other chat", async () => {
    sessionVerdict();
    render(<ChatPage />);
    await catalogReady();
    await type("@builder write me a PDF summary");
    await waitFor(() => expect(sessionPosts()).toHaveLength(1));
    // the hand-off mark is on disk in t1 (its own box), then the user leaves
    await waitFor(() => expect(putsTo("/chat/threads/t1").length).toBeGreaterThan(0));
    await openOrlando();
    H.api.getResponses["/sessions/s1?transcript=0"] = SESSION_DONE;
    H.api.getResponses["/sessions/s1/result"] = RESULT_DONE;
    const readsAtLeave = H.api.gets.filter((p) => p.startsWith("/sessions/s1")).length;
    // two polling periods of quiet: nothing reads the session, nothing writes
    await act(async () => {
      await new Promise<void>((r) => setTimeout(r, 3200));
    });
    expect(H.api.gets.filter((p) => p.startsWith("/sessions/s1")).length).toBe(readsAtLeave);
    expect(putsCarrying("Made the PDF.")).toHaveLength(0);
    expect(putsTo(ORLANDO_PATH)).toHaveLength(0);
    expect(screen.getByText("Try Se7en Bites.")).toBeTruthy();
    expect(screen.queryByText("Made the PDF.")).toBeNull();
  }, 10_000);
});

describe("a chat.thread_updated from another writer (v1.345.0)", () => {
  it("re-reads the OPEN user-owned chat and adopts the server's version; the next save carries the adopted stamp", async () => {
    window.history.replaceState({}, "", `/chat?thread=${ORLANDO.id}`);
    render(<ChatPage />);
    await screen.findByText("Try Se7en Bites.");
    const readsAtOpen = H.api.gets.filter((p) => p === ORLANDO_PATH).length;
    // another writer (a remote reporting back through the daemon) appended
    H.api.getResponses[ORLANDO_PATH] = {
      ...ORLANDO,
      updated_at: "S9",
      messages: [...ORLANDO.messages, { role: "assistant", content: "Hermes reporting back.", panelWho: "remote:hermes", panelThreadId: ROOM }],
    };
    await act(async () => {
      H.events.push({ id: "e1", type: "chat.thread_updated", payload: { thread_id: ORLANDO.id, who: "remote:hermes", entries: 1 } });
    });
    await screen.findByText("Hermes reporting back.");
    expect(H.api.gets.filter((p) => p === ORLANDO_PATH).length).toBe(readsAtOpen + 1);
    // ADOPTED, never overwritten: nothing was PUT, the conversation is with
    // hermes now, and the next save carries the server's stamp
    expect(putsTo(ORLANDO_PATH)).toHaveLength(0);
    expect(screen.getByText("Try Se7en Bites.")).toBeTruthy();
    expect(await screen.findByTestId("addressee-strip")).toHaveTextContent(/Talking to hermes/);
    fireEvent.click(await screen.findByRole("button", { name: "Back to Jarvis" }));
    await type("thanks");
    await screen.findByText("Noted.");
    await waitFor(() => expect(putsCarrying("thanks").length).toBeGreaterThan(0));
    expect(putsCarrying("thanks")[0].path).toBe(ORLANDO_PATH);
    expect(putsCarrying("thanks")[0].body.if_updated_at).toBe("S9");
    const saved = putsCarrying("thanks")[0].body.messages as { content: string }[];
    expect(saved.map((m) => m.content)).toEqual([
      "find me a place to eat in orlando",
      "Try Se7en Bites.",
      "Hermes reporting back.",
      "thanks",
    ]);
  });

  it("adopted MID-TURN: the turn's own end save is rebased onto the server's array, so the appended line survives", async () => {
    window.history.replaceState({}, "", `/chat?thread=${ORLANDO.id}`);
    render(<ChatPage />);
    await screen.findByText("Try Se7en Bites.");
    // a Jarvis turn goes out and is HELD before its first word
    let releaseStream!: () => void;
    H.stream.held = new Promise<void>((r) => (releaseStream = r));
    await type("what about brunch?");
    await waitFor(() => expect(putsCarrying("what about brunch?")).toHaveLength(1)); // the at-send save
    // while it runs, another writer appends to this chat
    H.api.getResponses[ORLANDO_PATH] = {
      ...ORLANDO,
      updated_at: "S9",
      messages: [
        ...ORLANDO.messages,
        { role: "user", content: "what about brunch?" },
        { role: "assistant", content: "Hermes reporting back.", panelWho: "remote:hermes", panelThreadId: ROOM },
      ],
    };
    await act(async () => {
      H.events.push({ id: "e3", type: "chat.thread_updated", payload: { thread_id: ORLANDO.id, who: "remote:hermes", entries: 1 } });
    });
    await screen.findByText("Hermes reporting back.");
    // the turn finishes: its end save builds on the array it captured at
    // send — and lands on the SERVER's, with Hermes kept and the stamp adopted
    releaseStream();
    await screen.findByText("Noted.");
    await waitFor(() => expect(putsCarrying("Noted.")).toHaveLength(1));
    const end = putsCarrying("Noted.")[0];
    expect(end.path).toBe(ORLANDO_PATH);
    expect(end.body.if_updated_at).toBe("S9");
    expect((end.body.messages as { content: string }[]).map((m) => m.content)).toEqual([
      "find me a place to eat in orlando",
      "Try Se7en Bites.",
      "what about brunch?",
      "Hermes reporting back.",
      "Noted.",
    ]);
    // ...and that is what the screen shows
    expect(screen.getByText("Hermes reporting back.")).toBeTruthy();
    expect(screen.getByText("Noted.")).toBeTruthy();
  });

  it("the 409 merge cuts at the sync point AS OF THE SEND: an adopt that landed while the at-send PUT was airborne does not drop the question", async () => {
    // The real 409 case: the at-send PUT of "what about brunch?" is still
    // airborne (held here) when another writer's append lands and this
    // window ADOPTS it (syncedLen moves to the server's length, 3). The PUT
    // then comes back 409. putThread's merge must cut the sent array where
    // it matched the server AT SEND (2), so the question rides the retry —
    // cutting at the moved syncedLen (3) drops it: the retry carries
    // [orlando x2, Hermes] and the user's own words never reach disk.
    window.history.replaceState({}, "", `/chat?thread=${ORLANDO.id}`);
    render(<ChatPage />);
    await screen.findByText("Try Se7en Bites.");
    H.stream.held = new Promise<void>(() => {}); // the turn itself stays out
    let rejectPut!: (e: Error) => void;
    const heldPut = new Promise<unknown>((_, rej) => (rejectPut = rej));
    let holdUsed = false;
    H.api.putHook = (path, body) => {
      const carries = ((body.messages ?? []) as { content?: string }[]).some((m) => m.content === "what about brunch?");
      if (path === ORLANDO_PATH && carries && !holdUsed) {
        holdUsed = true;
        return heldPut;
      }
      return undefined;
    };
    await type("what about brunch?");
    await waitFor(() => expect(putsTo(ORLANDO_PATH)).toHaveLength(1)); // the at-send save, airborne
    expect(putsTo(ORLANDO_PATH)[0].body.if_updated_at).toBe(ORLANDO.updated_at);
    // another writer appended meanwhile — WITHOUT the brunch line
    H.api.getResponses[ORLANDO_PATH] = {
      ...ORLANDO,
      updated_at: "S9",
      messages: [...ORLANDO.messages, { role: "assistant", content: "Hermes reporting back.", panelWho: "remote:hermes", panelThreadId: ROOM }],
    };
    await act(async () => {
      H.events.push({ id: "e4", type: "chat.thread_updated", payload: { thread_id: ORLANDO.id, who: "remote:hermes", entries: 1 } });
    });
    await screen.findByText("Hermes reporting back."); // adopted: syncedLen is the server's 3 now
    expect(putsTo(ORLANDO_PATH)).toHaveLength(1); // nothing else went out
    // the held PUT is refused: the row is newer (S9)
    await act(async () => {
      rejectPut(new H.FakeApiError("the thread changed", 409));
    });
    await waitFor(() => expect(putsTo(ORLANDO_PATH)).toHaveLength(2));
    const retry = putsTo(ORLANDO_PATH)[1];
    expect(retry.body.if_updated_at).toBe("S9");
    expect((retry.body.messages as { content: string }[]).map((m) => m.content)).toEqual([
      "find me a place to eat in orlando",
      "Try Se7en Bites.",
      "Hermes reporting back.",
      "what about brunch?",
    ]);
    // ...and the merged array is what this window shows
    await waitFor(() => expect(screen.getByText("what about brunch?")).toBeTruthy());
    expect(screen.getByText("Hermes reporting back.")).toBeTruthy();
  });

  it("another chat's frame does not re-read the open one", async () => {
    window.history.replaceState({}, "", `/chat?thread=${ORLANDO.id}`);
    render(<ChatPage />);
    await screen.findByText("Try Se7en Bites.");
    const readsAtOpen = H.api.gets.filter((p) => p === ORLANDO_PATH).length;
    await act(async () => {
      H.events.push({ id: "e2", type: "chat.thread_updated", payload: { thread_id: "t1", who: "remote:hermes", entries: 1 } });
    });
    await settle();
    expect(H.api.gets.filter((p) => p === ORLANDO_PATH).length).toBe(readsAtOpen);
  });
});
