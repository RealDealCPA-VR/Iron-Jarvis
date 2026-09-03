/**
 * Build-pane chat autosave v1.226.0 (F-F-3 in PaneChat).
 *
 * What carries weight here:
 *  - a 404 on the pane's PUT resets its save target AND drops the
 *    localStorage thread key, so the next save re-creates the thread via
 *    /chat/threads/new (the load path already did this; no save path did);
 *  - any other non-0 status renders the "Couldn't save" chip whose Retry
 *    re-issues the PUT, and a later success retires the chip.
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
    api: {
      puts: [] as { path: string; body: Record<string, unknown> }[],
      threads: {} as Record<string, unknown>,
      /** Queue of PUT outcomes; an Error rejects, anything else resolves. */
      putQueue: [] as unknown[],
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    if (path === "/projects") return { projects: [] };
    if (path === "/undo?session_id=chat") return { actions: [] };
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    if (m) {
      const t = H.api.threads[decodeURIComponent(m[1])];
      if (!t) throw new H.FakeApiError("thread not found", 404);
      return t;
    }
    throw new H.FakeApiError(`unexpected GET ${path}`, 500);
  },
  post: async () => ({ ok: true }),
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const next = H.api.putQueue.length ? H.api.putQueue.shift() : undefined;
    if (next instanceof Error) throw next;
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "th_new", title: "t" };
  },
}));

vi.mock("@/lib/useChatStream", () => {
  class FakeStreamError extends Error {
    status = 0;
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    StreamError: FakeStreamError,
    useChatStream: () => ({
      streaming: false,
      text: "",
      tools: [],
      approval: null,
      run: async () => ({ reply: "ok" }),
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
    health: {
      status: "ok",
      version: "test",
      default_provider: "mock",
      default_model: "m",
      providers: [{ provider: "lmstudio", available: true, class: "local" }],
    },
  }),
}));

vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import { PaneChat } from "@/components/terminal/PaneChat";
import { paneThreadKey } from "@/components/terminal/paneChatCore";

const CWD = "C:\\work\\demo";

beforeEach(() => {
  H.api.puts = [];
  H.api.threads = {
    th_9: {
      id: "th_9",
      title: "Build: demo",
      messages: [
        { role: "user", content: "earlier" },
        { role: "assistant", content: "sure" },
      ],
      setup: {},
    },
  };
  H.api.putQueue = [];
  window.localStorage.clear();
  window.localStorage.setItem(paneThreadKey("p1"), "th_9");
});
afterEach(() => cleanup());

async function typeAndSend(text: string) {
  const box = screen.getByLabelText("Message");
  await waitFor(() => expect(box).toBeEnabled());
  fireEvent.change(box, { target: { value: text } });
  fireEvent.click(screen.getByLabelText("Send"));
}

describe("PaneChat autosave — a 404 resets the target", () => {
  it("drops the stored key and the next save PUTs /chat/threads/new", async () => {
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await screen.findByText("sure");
    H.api.putQueue.push(new H.FakeApiError("thread not found", 404));
    await typeAndSend("first");
    await waitFor(() => expect(H.api.puts.length).toBe(1));
    expect(H.api.puts[0].path).toBe("/chat/threads/th_9");
    await waitFor(() =>
      expect(window.localStorage.getItem(paneThreadKey("p1"))).toBeNull(),
    );
    // A 404 is handled by the reset, not by a chip.
    expect(screen.queryByText(/Couldn't save this conversation/)).toBeNull();
    await typeAndSend("second");
    await waitFor(() => expect(H.api.puts.length).toBe(2));
    expect(H.api.puts[1].path).toBe("/chat/threads/new");
    expect(window.localStorage.getItem(paneThreadKey("p1"))).toBe("th_new");
  });
});

describe("PaneChat autosave — other failures raise the chip", () => {
  it("a 500 renders the chip; Retry re-issues the PUT and success retires it", async () => {
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await screen.findByText("sure");
    H.api.putQueue.push(new H.FakeApiError("db locked", 500));
    await typeAndSend("first");
    expect(
      await screen.findByText(/Couldn't save this conversation: db locked/),
    ).toBeInTheDocument();
    expect(H.api.puts.length).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(H.api.puts.length).toBe(2));
    expect(H.api.puts[1].path).toBe("/chat/threads/th_9");
    // The exact array whose save failed is what Retry re-sends.
    expect(H.api.puts[1].body.messages).toEqual(H.api.puts[0].body.messages);
    await waitFor(() =>
      expect(screen.queryByText(/Couldn't save this conversation/)).toBeNull(),
    );
  });

  it("status 0 stays silent — the offline hint covers it", async () => {
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await screen.findByText("sure");
    H.api.putQueue.push(new H.FakeApiError("Failed to fetch", 0));
    await typeAndSend("first");
    await waitFor(() => expect(H.api.puts.length).toBe(1));
    expect(screen.queryByText(/Couldn't save this conversation/)).toBeNull();
  });
});
