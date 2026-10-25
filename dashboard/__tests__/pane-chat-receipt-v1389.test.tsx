/**
 * Harness gaps, fix 3 — the Build terminal's chat receipt says what the chat
 * page's says (the v1.388.0 re-audit, A22: PaneChat passed TurnReceipt the
 * route, tools, trust, usage, steps, timing and files, and dropped the
 * approvals given, the older messages left out to fit, a kept preference and
 * the folder's rules — so a pane reply never said which cards the user had
 * allowed).
 *
 * Now the pane SAVES those facts on its reply under the chat page's own names
 * (decoded the chat page's way, lib/receiptFacts) and hands them to the
 * receipt — live, and after the pane is reopened (the saved thread).
 *
 * Harness: the REAL PaneChat with the pane-chat-details-v13580 mocks (only the
 * network, the stream hook and the daemon hook are stubbed).
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
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
    api: {
      threads: {} as Record<string, unknown>,
      puts: [] as { path: string; body: unknown }[],
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
  put: async (path: string, body: unknown) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "th_1", title: "t" };
  },
}));

const S = vi.hoisted(() => ({ stream: { outcomes: [] as Array<Error | Record<string, unknown>> } }));

vi.mock("@/lib/useChatStream", () => {
  class StreamError extends Error {
    status = 0;
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
      run: async () => {
        const next = S.stream.outcomes.shift() ?? { reply: "ok" };
        if (next instanceof Error) throw next;
        return next;
      },
      abort: () => {},
    }),
  };
});

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
const NET = { timeout: 5000 };

const APPROVED = [
  { tool: "shell", decision: "conversation" },
  { tool: "write_file", decision: "once" },
];

beforeEach(() => {
  __resetApiCache();
  H.api.threads = {};
  H.api.puts = [];
  S.stream.outcomes = [];
  window.localStorage.clear();
});
afterEach(() => cleanup());

function box(): HTMLTextAreaElement {
  return screen.getByLabelText("Message") as HTMLTextAreaElement;
}

describe("the pane's receipt carries the approvals, the refit and a kept preference", () => {
  it("live: the reply says what it left out, and the opened receipt lists the approvals given; all are saved", async () => {
    S.stream.outcomes = [
      {
        reply: "Ran the tests and fixed the import.",
        route: { provider: "mock", model: "m", reason: "explicit" },
        approved: APPROVED,
        refit: { dropped: 2 },
        remembered: ["Keep answers short"],
        folderRules: ["AGENTS.md"],
      },
    ];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await waitFor(() => expect(box()).toBeEnabled(), NET);
    fireEvent.change(box(), { target: { value: "fix the import" } });
    fireEvent.click(screen.getByLabelText("Send"));
    const receipt = await screen.findByTestId("pane-reply-receipt", {}, NET);
    expect(within(receipt).getByTestId("turn-refit").textContent).toBe("Left out 2 older messages to fit this model");
    expect(within(receipt).getByTestId("turn-remembered")).toBeTruthy();
    fireEvent.click(within(receipt).getByTestId("turn-receipt"));
    const rows = await within(receipt).findAllByTestId("turn-approved", {}, NET);
    expect(rows).toHaveLength(2);
    // Saved under the chat page's own names (a thread the chat page opens
    // later reads them the same way).
    await waitFor(() => expect(H.api.puts.length).toBeGreaterThan(0), NET);
    const saved = (H.api.puts.at(-1)!.body as { messages: Record<string, unknown>[] }).messages;
    const reply = saved.find((m) => m.role === "assistant")!;
    expect(reply.approved).toEqual(APPROVED);
    expect(reply.refit).toEqual({ dropped: 2 });
    expect(reply.remembered).toEqual(["Keep answers short"]);
    expect(reply.folderRules).toEqual(["AGENTS.md"]);
  });

  it("reopened: a saved pane reply's approvals and refit reach the receipt", async () => {
    window.localStorage.setItem("ij.pane.thread.p1", "th_1");
    H.api.threads["th_1"] = {
      id: "th_1",
      title: "Build: demo",
      messages: [
        { role: "user", content: "fix the import" },
        {
          role: "assistant",
          content: "Ran the tests and fixed the import.",
          route: { provider: "mock", model: "m", reason: "explicit" },
          approved: APPROVED,
          refit: { dropped: 1 },
        },
      ],
      setup: {},
    };
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const receipt = await screen.findByTestId("pane-reply-receipt", {}, NET);
    expect(within(receipt).getByTestId("turn-refit").textContent).toBe("Left out 1 older message to fit this model");
    fireEvent.click(within(receipt).getByTestId("turn-receipt"));
    expect(await within(receipt).findAllByTestId("turn-approved", {}, NET)).toHaveLength(2);
  });
});
