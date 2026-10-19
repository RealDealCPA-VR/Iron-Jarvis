/**
 * v1.358.0 (wave 8, lane 8C) — the Build pane's chat shows a failure's
 * technical detail FOLDED, like the chat page.
 *
 * The re-audit (G. Technical detail is available but not forced on the
 * beginner): the chat page's failure line leads with the plain sentence and
 * folds the daemon's masked technical text under "Details"; the Build pane's
 * chat showed only the sentence (`turnErrorText`), so the technical words
 * were nowhere. Now the pane keeps them for THIS line (lib/failureLine
 * `turnErrorTechnical`) behind the same quiet "Details" toggle
 * (lib/failureActions DETAILS_WORDS), closed by default.
 *
 * Values: what the REAL daemon said (`fixtures/wave4-seams-v13540.json`).
 * Harness: the REAL PaneChat with the pane-chat-failure-words-v13540 mocks
 * (only the network, the stream hook and the daemon hook are stubbed).
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
import { DETAILS_WORDS } from "@/lib/failureActions";
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


describe("a failed pane answer folds its technical detail under 'Details' (the chat page's fold)", () => {
  it("the sentence leads; the server's own words wait behind a closed Details toggle", async () => {
    const k = SEAMS.refused_key;
    const err = new S.FakeStreamError(k.detail, 0, true);
    err.title = k.title;
    err.fix = k.fix;
    err.technical = k.technical;
    S.stream.outcomes = [err];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await typeAndSend("summarise the lease");
    // Wait for the THING asserted: the fold's toggle (it comes with the line).
    const toggle = await screen.findByTestId("pane-chat-error-details-toggle");
    expect(screen.getByText(k.sentence)).toBeInTheDocument();
    expect(toggle.textContent).toBe(DETAILS_WORDS);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    // Closed: the technical text is not forced on the reader.
    expect(screen.queryByTestId("pane-chat-error-details-text")).toBeNull();
    expect(screen.queryByText(k.technical)).toBeNull();
    // Open it: the daemon's masked words, quiet, no box.
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const text = screen.getByTestId("pane-chat-error-details-text");
    expect(text.textContent).toBe(k.technical);
    expect(toggle.getAttribute("aria-controls")).toBe(text.id);
    for (const el of [text, toggle]) {
      expect(el.getAttribute("class") ?? "").not.toMatch(/(^|\s)(border(-|\s|$)|shadow|bg-(white|ink|black|tone))/);
    }
    // Close it again (a native button: Enter / Space press it too).
    fireEvent.click(toggle);
    expect(screen.queryByTestId("pane-chat-error-details-text")).toBeNull();
    // Retry is still offered beside it (G51 unchanged).
    expect(screen.getByTestId("pane-chat-retry")).toBeInTheDocument();
  });

  it("CONTROL: a failure with no plain words (its message IS the line) has no Details fold", async () => {
    S.stream.outcomes = [new S.FakeStreamError("provider exploded", 500, true)];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await typeAndSend("go");
    expect(await screen.findByText(/provider exploded/)).toBeInTheDocument();
    expect(screen.queryByTestId("pane-chat-error-details-toggle")).toBeNull();
  });

  it("a Retry that answers clears the line and its fold", async () => {
    const k = SEAMS.refused_key;
    const err = new S.FakeStreamError(k.detail, 0, true);
    err.title = k.title;
    err.fix = k.fix;
    err.technical = k.technical;
    S.stream.outcomes = [err, { reply: "Here is the summary." }];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await typeAndSend("summarise the lease");
    await screen.findByTestId("pane-chat-error-details-toggle");
    fireEvent.click(screen.getByTestId("pane-chat-retry"));
    expect(await screen.findByText("Here is the summary.")).toBeInTheDocument();
    expect(screen.queryByTestId("pane-chat-error-details-toggle")).toBeNull();
    expect(screen.queryByText(k.sentence)).toBeNull();
  });
});
