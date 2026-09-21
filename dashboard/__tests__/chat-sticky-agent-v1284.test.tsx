/**
 * v1.284.0 — an @-mentioned agent remembers the chat, stays addressed, and
 * can act.
 *
 * The user's report: "i need to continually use the @ to target a specific
 * agent and it basically starts up with no memory of the previous
 * conversation … i tested it to ask for a PDF and it seemed to only answer in
 * text and i was unable to see the actual PDF". The page-side halves, each
 * pinned on the REAL page:
 *
 *  1. after a round the conversation is WITH that agent: the strip says so, the
 *     box says so, and a plain follow-up posts to /chat/panel with the
 *     addressees on the side (`mentions`), the words verbatim;
 *  2. the panel body carries the chat so far (`history`, attributed) and the
 *     room the last round answered with (`panel_thread_id`);
 *  3. "Back to Jarvis" ends it, and the panel reply that Iron Jarvis then reads
 *     is LABELLED as the agent's, not sent as Jarvis's own earlier words;
 *  4. a `mode: "session"` verdict opens the tooled session lane on that agent
 *     (POST /sessions, agent_type builder, the address stripped off the task)
 *     and the reply that lands is attributed to the agent;
 *  5. "Have builder do this" does the same on the user's press;
 *  6. reopening a saved conversation restores the addressee.
 *
 * Harness: the v1.282.0 chat-page mocks, with a per-call responder for
 * /chat/panel and a completed session behind /sessions/s1.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
    stream: { bodies: [] as Record<string, unknown>[] },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    const r = H.api.getResponses[path];
    // Unknown reads answer an empty object: the page has many optional panels
    // and this file pins the panel lane, not their absence.
    return r === undefined ? {} : r;
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
    run: async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
      H.stream.bodies.push(body);
      await new Promise<void>((r) => setTimeout(r, 0));
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

/** A round where builder answers `content` in room `room`. */
function round(content: string, room = "athr1", dropped = 0) {
  return {
    mode: "panel",
    thread_id: room,
    entries: [
      { who: "user", content: "(echoed)", at: "2026-09-20T10:00:00Z" },
      { who: "builtin:builder", content, at: "2026-09-20T10:00:03Z" },
    ],
    spoke: ["builtin:builder"],
    skipped: [],
    unknown_mentions: [],
    context: { chat_messages: 0, chat_dropped: dropped },
  };
}

const SESSION_DONE = {
  session: {
    id: "s1",
    task: "write me a PDF summary",
    agent_type: "builder",
    provider: "mock",
    model: "m",
    status: "completed",
    workspace_path: "C:\\w",
    summary: "Made the PDF.",
  },
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
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [BUILDER] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
    "/sessions/s1": SESSION_DONE,
    "/sessions/s1/result": RESULT_DONE,
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function box() {
  return screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement;
}
/** The "@" catalog is IN HAND when the picker can list it (the repo's waitFor
 *  rule: wait for the thing itself, not a tick). Type "@" + two letters, wait
 *  for that agent's row, clear the box. Without this, a message typed before
 *  the catalog arrived is an ordinary Jarvis message — silently. */
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
function sessionPosts() {
  return H.api.posts.filter((p) => p.path === "/sessions");
}
/** The last saved conversation (the thread PUT's messages). */
function lastSaved() {
  const saved = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/")).at(-1);
  return (saved?.body.messages ?? []) as {
    role: string;
    content: string;
    panelWho?: string;
    panelNote?: string;
    fromSession?: string;
  }[];
}

describe("the conversation stays with the agent (v1.284.0)", () => {
  it("a plain follow-up goes to builder, verbatim, with the chat and the room", async () => {
    let calls = 0;
    H.api.postResponses["/chat/panel"] = () =>
      round(calls++ === 0 ? "Here is the brief." : "Shorter title done.");
    render(<ChatPage />);
    await catalogReady();
    await type("@builder draft the brief");
    await screen.findByText("Here is the brief.");

    // 1. the strip + the box say who the conversation is with
    const strip = await screen.findByTestId("addressee-strip");
    expect(strip).toHaveTextContent(/Talking to builder/);
    expect(box().placeholder).toMatch(/^Message builder/);

    // 2. the follow-up has no "@" and still reaches builder
    await type("make the title shorter");
    await screen.findByText("Shorter title done.");
    const posts = panelPosts();
    expect(posts).toHaveLength(2);
    const second = posts[1].body;
    expect(second.message).toBe("make the title shorter"); // verbatim
    expect(second.mentions).toEqual(["builder"]);
    expect(second.panel_thread_id).toBe("athr1"); // the room round one answered with
    // ...and the chat so far, attributed, without the message being sent
    const history = second.history as { who: string; content: string }[];
    expect(history).toEqual([
      { who: "user", content: "@builder draft the brief" },
      { who: "builtin:builder", content: "Here is the brief." },
    ]);
    // the first round had nothing before it
    expect(posts[0].body.history).toEqual([]);
    expect(posts[0].body.mentions).toEqual(["builder"]);
    // nothing went to the Jarvis lane
    expect(H.stream.bodies).toHaveLength(0);
  });

  it("Back to Jarvis ends it, and Jarvis reads the panel reply as builder's words", async () => {
    H.api.postResponses["/chat/panel"] = () => round("Here is the brief.");
    render(<ChatPage />);
    await catalogReady();
    await type("@builder draft the brief");
    await screen.findByText("Here is the brief.");
    fireEvent.click(await screen.findByRole("button", { name: "Back to Jarvis" }));
    await waitFor(() => expect(screen.queryByTestId("addressee-strip")).toBeNull());
    expect(box().placeholder).toMatch(/^Message Iron Jarvis/);

    await type("thanks, what next?");
    await screen.findByText("Noted.");
    expect(panelPosts()).toHaveLength(1); // the follow-up did NOT go to the panel
    const body = H.stream.bodies.at(-1)!;
    const msgs = body.messages as { role: string; content: string }[];
    const labelled = msgs.find((m) => m.content.includes("Here is the brief."));
    expect(labelled?.role).toBe("assistant");
    expect(labelled?.content).toBe(
      "[Reply from the agent builder, on the agent panel — not Iron Jarvis]\nHere is the brief.",
    );
    // the user's own lines are untouched
    expect(msgs.find((m) => m.role === "user")?.content).toBe("@builder draft the brief");
  });

  it("what the speakers were not shown is said under the reply", async () => {
    H.api.postResponses["/chat/panel"] = () => round("Here is the brief.", "athr1", 3);
    render(<ChatPage />);
    await catalogReady();
    await type("@builder draft the brief");
    await screen.findByText("Here is the brief.");
    expect(await screen.findByTestId("panel-note")).toHaveTextContent(
      "3 earlier messages did not fit the agent's context window",
    );
    await waitFor(() => expect(lastSaved().at(-1)?.panelNote).toMatch(/3 earlier messages/));
  });
});

describe("work goes to a real session of that agent (v1.284.0)", () => {
  it("a session verdict opens the tooled lane and the reply is builder's", async () => {
    H.api.postResponses["/chat/panel"] = () => ({
      mode: "session",
      target: "builder",
      who: "builtin:builder",
      tools: ["write_document"],
      reason: "this is work, not a question — builder is doing it in a real session, with tools",
      unknown_mentions: [],
    });
    H.api.postResponses["/sessions"] = { id: "s1", status: "active", task: "x", agent_type: "builder" };
    render(<ChatPage />);
    await catalogReady();
    await type("@builder write me a PDF summary of what we discussed");

    // the SAME lane a chat escalation takes, on the named agent, address off
    await waitFor(() => expect(sessionPosts()).toHaveLength(1));
    const body = sessionPosts()[0].body;
    expect(body.agent_type).toBe("builder");
    // sendAgent prepends the chat recap (which may quote the "@builder" line
    // verbatim) above a "---" rule; the TASK is what follows it.
    const taskProper = String(body.task).split("\n---\n").pop() ?? "";
    expect(taskProper).toContain("write me a PDF summary of what we discussed");
    expect(taskProper).not.toContain("@builder");
    // the hand-off is visible where the user is standing
    expect(screen.getByText(/builder is doing it in a real session/)).toBeTruthy();

    // the run lands (the 1.5 s poll reads the completed session) — attributed
    await screen.findByText("Made the PDF.", {}, { timeout: 8000 });
    const strip = await screen.findByTestId("addressee-strip");
    expect(strip).toHaveTextContent(/Talking to builder/);
    await waitFor(() => {
      const last = lastSaved().at(-1)!;
      expect(last.fromSession).toBe("s1");
      expect(last.panelWho).toBe("builtin:builder");
    });
    // the file the agent made reached the conversation's rail
    expect(await screen.findAllByText("brief.pdf")).not.toHaveLength(0);
    // ...AND the reply keeps its ledger card (the agent-attributed bubble
    // branch renders before the run-result one — review finding #1), with no
    // "do this" chip on a reply that already IS the run.
    expect(await screen.findByTestId("run-result-headline")).toBeTruthy();
    expect(screen.queryByTestId("panel-hand-off")).toBeNull();
  });

  it("a hand-off to a DIFFERENT agent opens its own session, never continues the first", async () => {
    // Review finding #2: the escalation lane used to `continue` whatever
    // session the conversation already had, so "@taxpro draft a memo" after a
    // builder run would have run BUILDER under taxpro's name.
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
    let verdicts = 0;
    H.api.postResponses["/chat/panel"] = () =>
      verdicts++ === 0
        ? { mode: "session", target: "builder", who: "builtin:builder", tools: ["write_document"], reason: "work", unknown_mentions: [] }
        : { mode: "session", target: "custom:taxpro", who: "dynamic:taxpro", tools: ["write_document"], reason: "work", unknown_mentions: [] };
    H.api.postResponses["/sessions"] = { id: "s1", status: "active", task: "x", agent_type: "builder" };
    H.api.postResponses["/agents/taxpro/spawn"] = { id: "s2", status: "active", task: "y", agent_type: "builder" };
    H.api.getResponses["/sessions/s2"] = {
      session: { ...SESSION_DONE.session, id: "s2", summary: "Memo drafted." },
      transcript: [],
    };
    H.api.getResponses["/sessions/s2/result"] = { ...RESULT_DONE, session_id: "s2", files_created: ["memo.docx"], documents: ["C:\\w\\memo.docx"] };
    render(<ChatPage />);
    await catalogReady();
    await type("@builder write me a PDF summary");
    await screen.findByText("Made the PDF.", {}, { timeout: 8000 });
    await type("@taxpro draft a memo");
    await waitFor(() => expect(H.api.posts.some((p) => p.path === "/agents/taxpro/spawn")).toBe(true));
    expect(H.api.posts.some((p) => p.path === "/sessions/s1/continue")).toBe(false);
    await screen.findByText("Memo drafted.", {}, { timeout: 8000 });
    await waitFor(() => expect(lastSaved().at(-1)?.panelWho).toBe("dynamic:taxpro"));
    expect(await screen.findByTestId("addressee-strip")).toHaveTextContent(/Talking to taxpro/);
  });

  it("'Have builder do this' hands a panel reply to a session on the user's press", async () => {
    H.api.postResponses["/chat/panel"] = () => round("I would lay it out as three sections.");
    H.api.postResponses["/sessions"] = { id: "s1", status: "active", task: "x", agent_type: "builder" };
    render(<ChatPage />);
    await catalogReady();
    await type("@builder how would you structure the brief?");
    await screen.findByText("I would lay it out as three sections.");
    fireEvent.click(await screen.findByTestId("panel-hand-off"));
    await waitFor(() => expect(sessionPosts()).toHaveLength(1));
    const body = sessionPosts()[0].body;
    expect(body.agent_type).toBe("builder");
    const task = String(body.task);
    const taskProper = task.split("\n---\n").pop() ?? "";
    expect(taskProper).toContain("how would you structure the brief?");
    expect(taskProper).not.toContain("@builder"); // the address is not the task
    expect(taskProper).toMatch(/DO it with your tools/);
    // the recap sendAgent prepends carries the proposal the seat made
    expect(task).toContain("I would lay it out as three sections.");
    await screen.findByText("Made the PDF.", {}, { timeout: 8000 });
    await waitFor(() => expect(lastSaved().at(-1)?.panelWho).toBe("builtin:builder"));
  });

  it("a remote agent's reply offers no chip — there is no local session to open", async () => {
    H.api.postResponses["/chat/panel"] = () => ({
      ...round("Remote says hi."),
      entries: [
        { who: "user", content: "(echoed)", at: "2026-09-20T10:00:00Z" },
        { who: "remote:hermes", content: "Remote says hi.", at: "2026-09-20T10:00:03Z" },
      ],
      spoke: ["remote:hermes"],
    });
    H.api.getResponses["/agents/mentionable"] = {
      agents: [{ ...BUILDER, mention: "hermes", name: "remote:hermes", kind: "remote", source: "remote" }],
    };
    render(<ChatPage />);
    await catalogReady("hermes");
    await type("@hermes status?");
    await screen.findByText("Remote says hi.");
    expect(screen.queryByTestId("panel-hand-off")).toBeNull();
    expect(await screen.findByTestId("addressee-strip")).toHaveTextContent(/Talking to hermes/);
  });
});

describe("a reopened conversation is still with its agent (v1.284.0)", () => {
  it("restores the addressee and the room from the saved messages", async () => {
    H.api.getResponses["/chat/threads"] = {
      threads: [{ id: "t9", title: "Brief", updated_at: "2026-09-20T10:00:00Z", messages: 2 }],
    };
    H.api.getResponses["/chat/threads/t9"] = {
      id: "t9",
      title: "Brief",
      messages: [
        { role: "user", content: "@builder draft the brief" },
        { role: "assistant", content: "Here it is.", panelWho: "builtin:builder", panelThreadId: "athr9" },
      ],
    };
    H.api.postResponses["/chat/panel"] = () => round("Shorter.", "athr9");
    window.history.replaceState({}, "", "/chat?thread=t9");
    render(<ChatPage />);
    await screen.findByText("Here it is.");
    expect(await screen.findByTestId("addressee-strip")).toHaveTextContent(/Talking to builder/);
    await type("make it shorter");
    await screen.findByText("Shorter.");
    const body = panelPosts()[0].body;
    expect(body.mentions).toEqual(["builder"]);
    expect(body.panel_thread_id).toBe("athr9");
    expect(body.chat_thread_id).toBe("t9");
  });

  it("a conversation that ends on a Jarvis reply is with Jarvis", async () => {
    H.api.getResponses["/chat/threads/t9"] = {
      id: "t9",
      title: "Brief",
      messages: [
        { role: "user", content: "@builder draft the brief" },
        { role: "assistant", content: "Here it is.", panelWho: "builtin:builder", panelThreadId: "athr9" },
        { role: "user", content: "thanks" },
        { role: "assistant", content: "You're welcome." },
      ],
    };
    window.history.replaceState({}, "", "/chat?thread=t9");
    render(<ChatPage />);
    await screen.findByText("You're welcome.");
    expect(screen.queryByTestId("addressee-strip")).toBeNull();
  });
});
