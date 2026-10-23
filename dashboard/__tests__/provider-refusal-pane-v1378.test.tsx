/**
 * v1.378.0 — a provider's SAFETY REFUSAL in a Build terminal's chat
 * (PaneChat). The harness is pane-chat-retry-v13530's (copied).
 *
 * The pane's failed turn offers Retry (v1.353.0, G51). After a refusal it
 * must not: the provider's check judges the whole conversation, so a
 * re-send is declined again. The line still says the daemon's sentence;
 * the message is back in the box. Control: any other failure keeps Retry.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
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
    committed = false;
    offline = false;
    partial = "";
    title = "";
    fix = "";
    technical = "";
    fixKind = "";
    fixProvider = "";
    constructor(message: string, status = 0) {
      super(message);
      this.name = "StreamError";
      this.status = status;
    }
  }
  return {
    FakeStreamError,
    stream: { outcomes: [] as Array<Error | Record<string, unknown>>, bodies: [] as unknown[] },
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
    run: async (body: Record<string, unknown>) => {
      S.stream.bodies.push(body);
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
const TITLE = "Claude Code declined to answer this conversation";
const FIX =
  "Its safety check on computer security flagged something in this conversation, so every new message " +
  "here will be declined too. Start a new chat, or edit an earlier message to go back before the part it flagged.";

function failure(words: Record<string, string>) {
  const e = new S.FakeStreamError("the server's own detail", 0);
  Object.assign(e, words);
  return e;
}

beforeEach(() => {
  __resetApiCache();
  H.api.threads = {};
  S.stream.outcomes = [];
  S.stream.bodies = [];
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

describe("v1.378.0: a refused pane turn has no Retry", () => {
  it("says the sentence, puts the message back, and offers no Retry", async () => {
    S.stream.outcomes = [failure({ title: TITLE, fix: FIX, fixKind: "refusal", fixProvider: "claude-cli" })];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await typeAndSend("Say hello");
    expect(await screen.findByText(`${TITLE}. ${FIX}`)).toBeInTheDocument();
    await waitFor(() => expect(box().value).toBe("Say hello"));
    expect(screen.queryByTestId("pane-chat-retry")).toBeNull();
  });

  it("control: a failure that is not a refusal keeps Retry", async () => {
    S.stream.outcomes = [failure({ title: "Claude Code is busy", fix: "Try again shortly." })];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await typeAndSend("Say hello");
    expect(await screen.findByTestId("pane-chat-retry")).toHaveTextContent("Retry");
  });
});
