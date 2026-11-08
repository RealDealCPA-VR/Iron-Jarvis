/**
 * v1.455.0 (readiness Wave F, item 16): a media line that says a picture
 * could not be made offers Try again.
 *
 * The daemon appends "Your image could not be made: <reason>" with
 * `panelKind: "media"` and `mediaJob: <id>` (media/jobs.failed_line). The
 * press reads that job's own request (GET /media/jobs/{id}) and sends it as
 * the next message, so every card and spend check applies to the turn it
 * starts. The "is ready" line never offers it. The chat page is rendered for
 * real (the chat-home-wave-c-v1449 harness).
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
  return {
    FakeApiError,
    getResponses: {} as Record<string, unknown>,
    gets: [] as string[],
    runs: [] as Array<Record<string, unknown>>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.gets.push(path);
    const r = H.getResponses[path];
    if (r instanceof Error) throw r;
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async () => ({}),
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    epoch: 0,
    provided: true,
    health: { status: "ok", version: "1.455.0", default_provider: "anthropic", default_model: "x", providers: [] },
    refresh: () => {},
  }),
}));

vi.mock("@/lib/useChatStream", () => {
  class StreamError extends Error {
    status = 0;
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    StreamError,
    useChatStream: () => ({
      streaming: false,
      text: "",
      tools: [],
      approval: null,
      run: async (body: Record<string, unknown>) => {
        H.runs.push(body);
        return { reply: "On it." };
      },
      abort: () => {},
    }),
  };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false,
    reason: null,
    engine: null,
    listening: false,
    processing: false,
    transcript: "",
    interim: "",
    error: null,
    start: () => {},
    stop: () => {},
    reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false,
    enabled: false,
    speaking: false,
    enable: () => {},
    disable: () => {},
    toggle: () => {},
    speak: () => {},
    resetStream: () => {},
    speakMore: () => {},
    cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({
    byProvider: { anthropic: true },
    signedOutByProvider: {},
    defaultProvider: "anthropic",
    loading: false,
    stale: false,
    refresh: () => {},
  }),
}));
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { isFailedMediaLine, MEDIA_RETRY_GONE, mediaRetryText } from "@/lib/mediaRetry";

const FAILED = "Your image could not be made: the service said the prompt was too long.";
const READY = "Your image is ready.";

function seed(messages: Array<Record<string, unknown>>) {
  for (const k of Object.keys(H.getResponses)) delete H.getResponses[k];
  Object.assign(H.getResponses, {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/threads": { threads: [{ id: "t1", title: "A picture", updated_at: "2026-11-08T10:00:00", message_count: 3 }] },
    "/chat/threads/t1": { id: "t1", title: "A picture", messages },
  });
}

beforeEach(() => {
  H.gets = [];
  H.runs = [];
  window.history.replaceState({}, "", "/chat?thread=t1");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const lastUserText = (body: Record<string, unknown>) => {
  const msgs = (body.messages as Array<{ role: string; content: string }>) ?? [];
  return [...msgs].reverse().find((m) => m.role === "user")?.content;
};

describe("a failed media line offers Try again", () => {
  it("the press sends the job's own request as the next message", async () => {
    seed([
      { role: "user", content: "make a picture of a red apple" },
      { role: "assistant", content: "Making your picture now." },
      { role: "assistant", content: FAILED, panelKind: "media", mediaJob: "mj_1" },
    ]);
    H.getResponses["/media/jobs/mj_1"] = { id: "mj_1", kind: "image", prompt: "a red apple on a wooden table" };
    render(<ChatPage />);
    await screen.findByText(FAILED);
    fireEvent.click(screen.getByTestId("media-try-again"));
    await waitFor(() => expect(H.runs.length).toBe(1));
    expect(lastUserText(H.runs[0])).toBe("Make the image again: a red apple on a wooden table");
  });

  it("CONTROL: the ready line offers nothing", async () => {
    seed([
      { role: "user", content: "make a picture" },
      { role: "assistant", content: READY, panelKind: "media", mediaJob: "mj_2", documents: ["C:/a.png"] },
    ]);
    render(<ChatPage />);
    await screen.findByText(READY, { exact: false });
    expect(screen.queryByTestId("media-try-again")).toBeNull();
  });

  it("a job that can no longer be read says so and sends nothing", async () => {
    seed([
      { role: "user", content: "make a picture" },
      { role: "assistant", content: FAILED, panelKind: "media", mediaJob: "mj_gone" },
    ]);
    H.getResponses["/media/jobs/mj_gone"] = new H.FakeApiError("no such media task", 404);
    render(<ChatPage />);
    await screen.findByText(FAILED);
    fireEvent.click(screen.getByTestId("media-try-again"));
    await waitFor(() => expect(screen.getByTestId("chat-notice").textContent).toContain(MEDIA_RETRY_GONE));
    expect(H.runs).toHaveLength(0);
  });

  it("the line reader and the words", () => {
    expect(isFailedMediaLine({ role: "assistant", panelKind: "media", mediaJob: "m", content: FAILED })).toBe(true);
    expect(isFailedMediaLine({ role: "assistant", panelKind: "media", mediaJob: "m", content: READY })).toBe(false);
    expect(isFailedMediaLine({ role: "assistant", panelKind: "progress", mediaJob: "m", content: FAILED })).toBe(false);
    expect(isFailedMediaLine({ role: "assistant", panelKind: "media", content: FAILED })).toBe(false);
    expect(mediaRetryText("video", " a sunrise ")).toBe("Make the video again: a sunrise");
    expect(mediaRetryText("image", "")).toBe("");
  });
});
