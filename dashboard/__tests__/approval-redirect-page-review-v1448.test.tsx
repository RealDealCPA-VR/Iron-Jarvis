/**
 * A3 review (v1.448.0, Wave A) — the chat PAGE's half of "No, tell it what
 * to do instead", driven on the real page (the builder pinned its handler's
 * source only). The transport is mocked; the stream mock holds the turn
 * open and surfaces an approval the way the hook does, so the dock card is
 * the real DockAsk + ApprovalCard on the real composer.
 *
 *  - Send on the field POSTs the Deny FIRST and only then the steer note to
 *    the RUNNING turn (`/chat/turns/{id}/steer`), as the user's next words.
 *  - A Deny the daemon refused (409) sends NOTHING: no steer, no message; the
 *    field keeps the words.
 *  - A steer the daemon refused with 404 (the turn ended first) while the
 *    page still holds the turn open keeps the words in the BOX — never a
 *    second turn started under a running one, never lost.
 *  - A steer refused any other way (409) keeps the words in the box and says
 *    so; nothing else goes out.
 *  - With the turn over, the words go out as the NEXT message (a new turn).
 *  - The hidden `inert` composer comes back when the card is answered.
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
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: {
      bodies: [] as Record<string, unknown>[],
      release: null as null | ((r: Record<string, unknown>) => void),
      setApproval: null as null | ((a: Record<string, unknown> | null) => void),
    },
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

vi.mock("@/lib/useChatStream", async () => {
  const React = await import("react");
  return {
    StreamError: H.FakeStreamError,
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    useChatStream: () => {
      const [approval, setApproval] = React.useState<Record<string, unknown> | null>(null);
      H.stream.setApproval = setApproval;
      return {
        streaming: false,
        text: "",
        tools: [],
        approval,
        mcpAsks: [],
        run: (body: Record<string, unknown>) => {
          H.stream.bodies.push(body);
          return new Promise<Record<string, unknown>>((res) => {
            H.stream.release = res;
          });
        },
        abort: () => {},
      };
    },
  };
});
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

const WRITE = {
  id: "a1",
  callId: "c1",
  tool: "write_file",
  args: { path: "notes/plan.md", content: "# Plan" },
  timeoutS: 0,
  reversible: true,
  why: "Saving a file is not in this chat's grants.",
};

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.release = null;
  H.stream.setApproval = null;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
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
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const composer = async () => (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
const box = () => screen.getByTestId("chat-composer").querySelector("textarea") as HTMLTextAreaElement;
const posts = (re: RegExp) => H.api.posts.filter((p) => re.test(p.path));
const steers = () => posts(/^\/chat\/turns\/.+\/steer$/);
const denies = () => posts(/^\/chat\/approvals\/a1$/);

async function startTurn(text: string) {
  const el = await composer();
  fireEvent.change(el, { target: { value: text } });
  fireEvent.keyDown(el, { key: "Enter" });
  await waitFor(() => expect(H.stream.bodies.length).toBe(1));
  await waitFor(() => expect(H.stream.release).not.toBeNull());
  return H.stream.bodies[0];
}

/** The turn asks: the card takes the composer's place. */
async function askOnScreen() {
  await act(async () => {
    H.stream.setApproval?.(WRITE);
  });
  await screen.findByTestId("approval-redirect");
  expect(screen.getByTestId("chat-composer").getAttribute("inert")).not.toBeNull();
}

async function redirectWith(words: string) {
  fireEvent.click(screen.getByTestId("approval-redirect"));
  const field = (await screen.findByTestId("approval-redirect-input")) as HTMLInputElement;
  fireEvent.change(field, { target: { value: words } });
  fireEvent.click(screen.getByTestId("approval-redirect-send"));
  return field;
}

/** The daemon resolved the ask: the card goes, the composer comes back. */
async function askAnswered() {
  await act(async () => {
    H.stream.setApproval?.(null);
  });
  await waitFor(() => expect(screen.queryByTestId("approval-redirect")).toBeNull());
}

describe("the chat page's redirect (A3 review)", () => {
  it("denies FIRST, then steers the running turn with the words, which show under the live reply", async () => {
    render(<ChatPage />);
    const body = await startTurn("tidy the notes");
    const turnId = body.turn_id as string;
    await askOnScreen();
    await redirectWith("write it to drafts/plan.md instead");
    await waitFor(() => expect(steers()).toHaveLength(1));
    // Order: the deny landed before the note went (the first save's own
    // `/thread` link post is the page's, not the card's).
    const ours = H.api.posts.map((p) => p.path).filter((p) => !p.endsWith("/thread"));
    expect(ours).toEqual(["/chat/approvals/a1", `/chat/turns/${encodeURIComponent(turnId)}/steer`]);
    expect(denies()[0].body).toEqual({ decision: "deny" });
    expect(steers()[0].body).toEqual({ text: "write it to drafts/plan.md instead" });
    // No new turn was started by it.
    expect(H.stream.bodies).toHaveLength(1);
    await askAnswered();
    // The composer is back and empty; the note is shown as a steer under the reply.
    expect(screen.getByTestId("chat-composer").getAttribute("inert")).toBeNull();
    expect(box().value).toBe("");
    expect((await screen.findByTestId("steer-notes")).textContent).toContain("write it to drafts/plan.md instead");
  });

  it("a Deny the daemon refused sends nothing — no steer, no message — and the field keeps the words", async () => {
    render(<ChatPage />);
    await startTurn("tidy the notes");
    await askOnScreen();
    H.api.postResponses["/chat/approvals/a1"] = new H.FakeApiError("that wait already ended", 409);
    const field = await redirectWith("use the other folder");
    await screen.findByText("that wait already ended");
    expect(steers()).toHaveLength(0);
    expect(H.stream.bodies).toHaveLength(1);
    expect(field.value).toBe("use the other folder");
    // Nothing reached the hidden box either.
    expect(box().value).toBe("");
  });

  it("a steer the turn refused with 404 while the page still holds it open keeps the words in the box, never a second turn", async () => {
    render(<ChatPage />);
    const body = await startTurn("tidy the notes");
    await askOnScreen();
    H.api.postResponses[`/chat/turns/${encodeURIComponent(body.turn_id as string)}/steer`] = new H.FakeApiError(
      "no such running turn",
      404,
    );
    await redirectWith("do it in drafts");
    await waitFor(() => expect(steers()).toHaveLength(1));
    await askAnswered();
    expect(H.stream.bodies).toHaveLength(1);
    await waitFor(() => expect(box().value).toBe("do it in drafts"));
    expect(screen.queryByTestId("steer-notes")).toBeNull();
  });

  it("a steer refused any other way keeps the words in the box and says so; no message goes", async () => {
    render(<ChatPage />);
    const body = await startTurn("tidy the notes");
    await askOnScreen();
    H.api.postResponses[`/chat/turns/${encodeURIComponent(body.turn_id as string)}/steer`] = new H.FakeApiError(
      "a note cannot be empty",
      409,
    );
    await redirectWith("do it in drafts");
    await waitFor(() => expect(steers()).toHaveLength(1));
    await askAnswered();
    expect(H.stream.bodies).toHaveLength(1);
    await waitFor(() => expect(box().value).toBe("do it in drafts"));
    expect((await screen.findByText(/Couldn't send that/)).textContent).toContain("a note cannot be empty");
  });

  it("with the turn over, the words go out as the next message — ONE new turn, after the deny", async () => {
    render(<ChatPage />);
    const body = await startTurn("tidy the notes");
    await askOnScreen();
    // The deny lands late: by the time it answers, the turn has ENDED (the
    // card is gone and the stream released), so a steer has nothing to reach.
    let answer: (() => void) | null = null;
    H.api.postResponses["/chat/approvals/a1"] = () =>
      new Promise<Record<string, unknown>>((res) => {
        answer = () => res({});
      });
    await redirectWith("write it to drafts/plan.md instead");
    await waitFor(() => expect(answer).not.toBeNull());
    await askAnswered();
    await act(async () => {
      H.stream.release!({ reply: "I did not save it." });
    });
    await screen.findByText("I did not save it.");
    await act(async () => {
      answer!();
    });
    await waitFor(() => expect(H.stream.bodies).toHaveLength(2));
    expect(steers()).toHaveLength(0);
    expect(H.stream.bodies[1].turn_id).not.toBe(body.turn_id);
    const history = H.stream.bodies[1].messages as { role: string; content: string }[];
    expect(history.at(-1)).toEqual({ role: "user", content: "write it to drafts/plan.md instead" });
    // Deny before the message, every time.
    expect(H.api.posts.map((p) => p.path).filter((p) => !p.endsWith("/thread"))[0]).toBe("/chat/approvals/a1");
    // And the words did not ALSO stay in the box.
    expect(box().value).toBe("");
  });
});
