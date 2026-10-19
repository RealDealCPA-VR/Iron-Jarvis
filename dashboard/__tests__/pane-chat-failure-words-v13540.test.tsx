/**
 * v1.354.0 independent re-review (G28, seam a): the Build pane's chat says a
 * failed answer in the SAME sentence as the chat page — `title. fix` from the
 * daemon's plain words (lib/failureLine) — never the server's raw text. The
 * re-review found the pane still printed `e.message` ("openrouter API error
 * 401: No auth credentials found") while /chat said "OpenRouter refused your
 * key. Update it in Settings › Connections." for the same cause.
 *
 * Values: what the REAL daemon said (`fixtures/wave4-seams-v13540.json`).
 * Harness: the REAL PaneChat, the pane-chat-retry-v13530 harness (only the
 * network, the stream hook and the daemon hook are stubbed).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const SEAMS = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "wave4-seams-v13540.json"), "utf8"),
) as { refused_key: { detail: string; title: string; fix: string; technical: string; sentence: string } };

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
    api: {
      threads: {} as Record<string, unknown>,
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    if (path === "/projects") return { projects: [] };
    if (path === "/models") return { models: [] };
    if (path === "/undo?session_id=chat") return { actions: [] };
    if (/^\/chat\/turns\?thread_id=/.test(path)) return { turns: [] };
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    if (m) {
      const t = H.api.threads[decodeURIComponent(m[1])];
      if (!t) throw new H.FakeApiError("thread not found", 404);
      return t;
    }
    throw new H.FakeApiError(`unexpected GET ${path}`, 500);
  },
  post: async () => ({ ok: true }),
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "th_1", title: "t" };
  },
}));

const S = vi.hoisted(() => {
  class FakeStreamError extends Error {
    status: number;
    committed: boolean;
    offline: boolean;
    partial: string;
    title = "";
    fix = "";
    technical = "";
    constructor(message: string, status = 0, committed = false, offline = false, partial = "") {
      super(message);
      this.name = "StreamError";
      this.status = status;
      this.committed = committed;
      this.offline = offline;
      this.partial = partial;
    }
  }
  return {
    FakeStreamError,
    stream: { outcomes: [] as Array<Error | Record<string, unknown>> },
  };
});

vi.mock("@/lib/useChatStream", () => ({
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  StreamError: S.FakeStreamError,
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async () => {
      const next = S.stream.outcomes.shift() ?? { reply: "ok" };
      if (next instanceof Error) throw next;
      return next;
    },
    abort: () => {},
  }),
}));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    refresh: () => {},
    health: { status: "ok", version: "test", default_provider: "mock", default_model: "m", providers: [] },
  }),
}));

vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import { PaneChat } from "@/components/terminal/PaneChat";
import { __resetApiCache } from "@/lib/apiCache";

const CWD = "C:\\work\\demo";

beforeEach(() => {
  __resetApiCache();
  H.api.threads = {};
  S.stream.outcomes = [];
  window.localStorage.clear();
});
afterEach(() => cleanup());

function box(): HTMLTextAreaElement {
  return screen.getByLabelText("Message") as HTMLTextAreaElement;
}

async function typeAndSend(text: string) {
  await waitFor(() => expect(box()).toBeEnabled());
  fireEvent.change(box(), { target: { value: text } });
  fireEvent.click(screen.getByLabelText("Send"));
}

describe("a failed pane answer speaks the chat page's one sentence (G28)", () => {
  it("shows `title. fix` from the daemon's words, never the server's raw text", async () => {
    const k = SEAMS.refused_key;
    const err = new S.FakeStreamError(k.detail, 0, true);
    err.title = k.title;
    err.fix = k.fix;
    err.technical = k.technical;
    S.stream.outcomes = [err];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await typeAndSend("summarise the lease");
    // The thing asserted is the sentence itself (the Release runner is slower).
    expect(await screen.findByText(k.sentence)).toBeInTheDocument();
    expect(screen.queryByText(/No auth credentials found/)).not.toBeInTheDocument();
    // Retry is still offered beside it (G51 unchanged).
    expect(screen.getByTestId("pane-chat-retry")).toBeInTheDocument();
  });

  it("CONTROL: a failure with no plain words keeps its own message, as before", async () => {
    S.stream.outcomes = [new S.FakeStreamError("provider exploded", 500, true)];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await typeAndSend("go");
    expect(await screen.findByText(/provider exploded/)).toBeInTheDocument();
  });
});
