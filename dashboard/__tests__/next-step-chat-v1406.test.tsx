/**
 * v1.406.0 part 4 — the NEXT-STEP BOX on the CHAT page (app/chat/page.tsx).
 *
 * Driven through the real page with the daemon's stream mocked at fetch:
 *  - a plain answer draws no box;
 *  - a reply that ends on an offer of more work offers "Yes, do it", and the
 *    press is the page's own send: one new turn whose last user message is
 *    exactly "Yes, go ahead.";
 *  - an offer that is the user's call is named and only FILLS the message
 *    box (no turn is sent);
 *  - the recap and buttons step aside while the user types; the box is on
 *    the newest reply only (the next answer retires it);
 *  - the model-written follow-ups (`chat_followups`) render INSIDE the box.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    posts: [] as { path: string; body: Record<string, unknown> }[],
    postResponses: {} as Record<string, unknown>,
    getResponses: {} as Record<string, unknown>,
    streamBodies: [] as Record<string, unknown>[],
    dones: [] as Record<string, unknown>[],
    /** Review: an upload the test holds until it says so. */
    uploadGate: null as Promise<void> | null,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://d",
  ijToken: () => "tok",
  flattenDetail: (d: unknown) => String(d),
  get: async (path: string) => {
    const r = H.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.posts.push({ path, body });
    if (path === "/documents/upload" && H.uploadGate) await H.uploadGate;
    return H.postResponses[path] ?? {};
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
  del: async () => ({}),
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
    readAloud: () => {}, readingKey: null,
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { YES_TEXT } from "@/lib/nextStep";

function sse(done: Record<string, unknown>): Response {
  const text = `event: done\ndata: ${JSON.stringify(done)}\n\n`;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(new TextEncoder().encode(text));
      c.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

/** Review: a turn that fails (the lane's error frame). */
function sseError(detail: string): Response {
  const text = `event: error\ndata: ${JSON.stringify({ detail })}\n\n`;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(new TextEncoder().encode(text));
      c.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

beforeEach(() => {
  H.posts.length = 0;
  H.streamBodies.length = 0;
  H.dones.length = 0;
  H.postResponses = {};
  H.uploadGate = null;
  H.getResponses = {
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
    "/connectors": { connectors: [] },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).startsWith("http://d/chat/stream")) {
        H.streamBodies.push(JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>);
        const done = H.dones.shift() ?? { reply: "ok" };
        if (typeof done.__error === "string") return sseError(done.__error);
        return sse(done);
      }
      return new Response("{}", { status: 404 });
    }),
  );
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const lastUser = (body: Record<string, unknown> | undefined): string => {
  const msgs = (body?.messages as { role: string; content: string }[] | undefined) ?? [];
  return [...msgs].reverse().find((m) => m.role === "user")?.content ?? "";
};

async function ask(question: string, reply: string) {
  H.dones.push({ reply, route: { provider: "mock", model: "mock" } });
  const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: question } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(H.streamBodies.length).toBeGreaterThan(0));
  await screen.findByText(reply);
  return box;
}

describe("the chat's next-step box", () => {
  it("is not drawn under a plain answer", async () => {
    render(<ChatPage />);
    await ask("what is the capital of France?", "Paris is the capital of France.");
    expect(screen.queryByTestId("next-step-box")).toBeNull();
  });

  it("offers Yes, do it under an offer, and the press is the page's own send", async () => {
    render(<ChatPage />);
    await ask("draft a budget", "Here is the draft. I can also add a summary table if you want.");
    const next = await screen.findByTestId("next-step-box");
    expect(within(next).getByTestId("next-step-recap")).toHaveTextContent("Offered next: add a summary table.");
    H.dones.push({ reply: "Added the table.", route: { provider: "mock", model: "mock" } });
    fireEvent.click(within(next).getByRole("button", { name: "Yes, do it" }));
    await waitFor(() => expect(H.streamBodies.length).toBe(2));
    expect(lastUser(H.streamBodies[1])).toBe(YES_TEXT);
    // The next answer is a plain one: the box is gone (newest reply only).
    await screen.findByText("Added the table.");
    expect(screen.queryByTestId("next-step-box")).toBeNull();
  });

  it("an offer that is the user's call only fills the box, and the box steps aside while typing", async () => {
    render(<ChatPage />);
    const box = await ask("write the summary", "Done. Want me to email the summary to Dana?");
    const next = await screen.findByTestId("next-step-box");
    expect(within(next).getByTestId("next-step-recap")).toHaveTextContent("That one is your call.");
    fireEvent.click(within(next).getByRole("button", { name: "Email the summary to Dana…" }));
    await waitFor(() => expect(box.value).toBe("Yes, email the summary to Dana."));
    expect(screen.queryByTestId("next-step-box")).toBeNull();
    await new Promise((r) => setTimeout(r, 30));
    expect(H.streamBodies.length).toBe(1);
    fireEvent.change(box, { target: { value: "" } });
    expect(await screen.findByTestId("next-step-box")).toBeInTheDocument();
  });

  // Review (v1.406.0 part 4): a send pressed while a file is still going up is
  // QUEUED and fires with the BOX's words (v1.275.0) — the press's words were
  // never in the box, so "Yes, do it" mid-upload sent the file alone.
  it("Yes, do it pressed while a file is going up keeps its words (they go out with the file)", async () => {
    H.postResponses["/documents/upload"] = { name: "notes.pdf", path: "C:\\up\\notes.pdf" };
    H.postResponses["/documents/workfolder"] = { folder: "", files: [], note: "" };
    render(<ChatPage />);
    await ask("draft a budget", "Here is the draft. I can also add a summary table if you want.");
    const next = await screen.findByTestId("next-step-box");
    let release: () => void = () => {};
    H.uploadGate = new Promise<void>((r) => {
      release = r;
    });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["abc"], "notes.pdf", { type: "application/pdf" })] } });
    await waitFor(() => expect(H.posts.some((p) => p.path === "/documents/upload")).toBe(true));
    H.dones.push({ reply: "Added the table.", route: { provider: "mock", model: "mock" } });
    fireEvent.click(within(next).getByRole("button", { name: "Yes, do it" }));
    release();
    await waitFor(() => expect(H.streamBodies.length).toBe(2), { timeout: 10_000 });
    expect(lastUser(H.streamBodies[1])).toBe(YES_TEXT);
  });

  // Review (v1.406.0 part 4): a failed turn's line (Retry) is the next step —
  // the earlier reply's offer must not come back beside it. (The failed
  // question stays in the thread today, so the page's `failedTurn` guard is a
  // second lock; this pins the behaviour either way.)
  it("is not drawn beside a failed turn's line (the earlier reply's offer stays retired)", async () => {
    render(<ChatPage />);
    const box = await ask("draft a budget", "Here is the draft. I can also add a summary table if you want.");
    await screen.findByTestId("next-step-box");
    H.dones.push({ __error: "The model could not answer." });
    fireEvent.change(box, { target: { value: "and a chart" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(H.streamBodies.length).toBe(2));
    await screen.findByText(/could not answer/);
    fireEvent.change(box, { target: { value: "" } });
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("next-step-box")).toBeNull();
  });

  it("the model's follow-up suggestions render inside the same box", async () => {
    H.getResponses["/settings"] = { settings: { chat_followups: true } };
    H.postResponses["/chat/followups"] = { suggestions: ["Add a chart?"] };
    render(<ChatPage />);
    await ask("draft a budget", "Here is the draft. Want me to also add a chart?");
    const next = await screen.findByTestId("next-step-box");
    await waitFor(() => expect(within(next).getByTestId("followup-chips")).toBeInTheDocument());
    expect(screen.getAllByTestId("followup-chips")).toHaveLength(1);
    expect(within(next).getByRole("button", { name: "Yes, do it" })).toBeInTheDocument();
  });
});
