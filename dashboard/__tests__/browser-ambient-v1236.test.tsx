/**
 * v1.236.0 — the Build pane tells the daemon WHICH pane is asking.
 *
 * The daemon's `ChatBody.pane_id` is what a pane-scoped rule keys on: the
 * Browser capability gate (`chat_turn._filter_browser_tools`) needs to know
 * which pane a turn came from, and until this change a chat request carried
 * `workspace_dir` — the pane's FOLDER — and nothing identifying the pane. A
 * folder is not an identity: two panes can be open on the same one, and the user
 * can repoint a pane at any time.
 *
 * WHY THIS TEST MOUNTS THE REAL COMPONENT rather than only calling
 * `buildTurnBody`. Both halves can be right and the feature still dead: the
 * field on the body type, the branch in the builder, and no caller passing
 * `paneId`. That is the exact shape this repository keeps paying for — a green
 * suite over a path a user cannot reach — so the headline case drives PaneChat
 * and reads the body the stream hook was actually handed. The builder's own
 * contract (omitted, never `""`) is pinned separately, because "the daemon
 * treats `""` as a pane-less surface" is a statement about the value and not
 * about the component.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

// ------------------------------------------------------------- module mocks

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return { FakeApiError };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  get: async (path: string) => {
    if (path.startsWith("/projects")) return { projects: [] };
    if (path.startsWith("/chat/threads/")) throw new H.FakeApiError("not found", 404);
    return {};
  },
  post: async () => ({}),
  put: async (path: string, body?: Record<string, unknown>) => ({
    id: "th_1",
    title: (body?.title as string) ?? "t",
  }),
}));

const S = vi.hoisted(() => {
  class FakeStreamError extends Error {
    status: number;
    committed: boolean;
    offline: boolean;
    partial: string;
    constructor(message: string) {
      super(message);
      this.name = "StreamError";
      this.status = 0;
      this.committed = false;
      this.offline = false;
      this.partial = "";
    }
  }
  return {
    FakeStreamError,
    bodies: [] as Record<string, unknown>[],
  };
});

vi.mock("@/lib/useChatStream", () => ({
  StreamError: S.FakeStreamError,
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async (body: Record<string, unknown>) => {
      S.bodies.push(body);
      return {
    // v1.250.0 (S-03): the live bubble reads the streamed text from the hook's
    // store through `useLiveText`, so a mock without it throws on render.
    useLiveText: (s: { text?: string }) => s?.text ?? "", reply: "ok" };
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
    health: {
      status: "ok",
      version: "test",
      default_provider: "mock",
      default_model: "m",
      providers: [{ provider: "mock", available: true, class: "mock" }],
    },
  }),
}));

// The markdown pipeline is not under test here (house idiom).
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import { PaneChat } from "@/components/terminal/PaneChat";
import { buildTurnBody } from "@/components/terminal/paneChatCore";

const CWD = "C:\\work\\demo";
const PANE = "term_pane1";

beforeEach(() => {
  S.bodies = [];
  window.localStorage.clear();
});
afterEach(() => cleanup());

async function typeAndSend(text: string) {
  const box = screen.getByLabelText("Message");
  await waitFor(() => expect(box).toBeEnabled());
  fireEvent.change(box, { target: { value: text } });
  fireEvent.click(screen.getByLabelText("Send"));
}

describe("the pane chat request identifies its pane", () => {
  it("POSTs pane_id alongside workspace_dir", async () => {
    render(<PaneChat paneId={PANE} cwd={CWD} />);
    await typeAndSend("what page do I have open?");
    await waitFor(() => expect(S.bodies.length).toBe(1));
    const body = S.bodies[0];
    expect(body.pane_id).toBe(PANE);
    // The folder still rides: pane_id ADDS an identity, it replaces nothing.
    expect(body.workspace_dir).toBe(CWD);
  });

  it("sends the pane's OWN id, not the folder's basename", async () => {
    // A cheap-looking substitute a later refactor might reach for. The daemon
    // looks the id up in the terminal manager; a folder name resolves to no
    // pane, so gate 2 would silently never apply.
    render(<PaneChat paneId={PANE} cwd={CWD} />);
    await typeAndSend("hi");
    await waitFor(() => expect(S.bodies.length).toBe(1));
    expect(S.bodies[0].pane_id).not.toBe("demo");
  });
});

describe("buildTurnBody: omitted, never an empty string", () => {
  it("omits pane_id when there is no pane identity", () => {
    const body = buildTurnBody({ history: [], cwd: CWD });
    expect("pane_id" in body).toBe(false);
  });

  it("omits pane_id for an empty id rather than sending a blank one", () => {
    // The daemon reads "" as "no pane", so a blank value is a field that looks
    // present and identifies nothing.
    const body = buildTurnBody({ history: [], cwd: CWD, paneId: "" });
    expect("pane_id" in body).toBe(false);
  });

  it("carries the id when there is one", () => {
    expect(buildTurnBody({ history: [], cwd: CWD, paneId: PANE }).pane_id).toBe(PANE);
  });
});
