/**
 * v1.244.0 — attaching a file to a chat with NO project gives the conversation
 * its own folder and the file tools a project would.
 *
 * The user's report: "When I attach documents in the chat module and ask for a
 * task to be completed, I often get a lagging delay, a request for information
 * and then a completed screen with absolutely no output." Replayed on the live
 * model: with no project the chat had no folder and no file tools, so it handed
 * the job to an agent in a hidden scratch folder, which built the workbook,
 * stopped twice for approval, ran out of steps and reported "Task failed"
 * beside a file nobody could find. In a project none of that happens, because
 * selecting a project binds the project folder and arms the file essentials.
 *
 * Pinned here, on the real page with only the transport mocked:
 *  - the first attachment asks the daemon for a conversation folder, and the
 *    chip's path becomes the copy INSIDE it;
 *  - the send carries that folder as `workspace_dir` and the project file
 *    essentials as `tools`, so the turn can finish the job itself;
 *  - a later attachment joins the SAME folder (`into`), never a second one;
 *  - a folder refusal degrades to the old behaviour (the upload still
 *    attaches) instead of losing the file;
 *  - the folder is per conversation: it is never written to the sticky
 *    localStorage default a plain New chat returns to.
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
      getResponses: {} as Record<string, unknown>,
      /** POST outcome per path: an Error rejects, a function is called with the
       *  body, any other value resolves. */
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
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  // v1.250.0 (S-03): see the real hook — no store on a mock, so the text is
  // read straight off the object these fakes return.
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
      H.stream.bodies.push(body);
      await new Promise<void>((r) => setTimeout(r, 0));
      onDelta("done", "done");
      return { reply: "done" };
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


import { TurnReceipt } from "@/components/chat/TurnReceipt";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * v1.263.0 — the reasoning level, chosen in chat, only where it is an option.
 *
 * The user: "In the chat module I should be able to select the reasoning level
 * of the model if it is an option." The daemon's catalog row says which models
 * offer one (`reasoning: ["low","medium","high"]`); the composer draws the
 * control ONLY for such a model, sends the pick on every turn, remembers it with
 * the thread setup, and the receipt names the level that actually reached the
 * model. Header, mocks and helpers are the chat-workfolder harness verbatim.
 */

const CATALOG = {
  models: [
    { provider: "openai", model: "gpt-5", available: true, kind: "api", reasoning: ["low", "medium", "high"] },
    { provider: "openai", model: "gpt-4o", available: true, kind: "api", reasoning: [] },
  ],
};

beforeEach(() => {
  H.api.posts.length = 0;
  H.stream.bodies.length = 0;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = {
    "/models": CATALOG,
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

async function pickModel(model: string) {
  await screen.findByPlaceholderText(/Message Iron Jarvis/);
  fireEvent.click(await screen.findByTitle("Switch model"));
  // The provider row's accessible name carries its badge; the model row's text
  // is the id itself. Both are reached by their visible words, then clicked as
  // the buttons they sit in. v1.277.0: the menu also opens on the last picks,
  // whose rows carry the provider's name as a badge — the provider row is the
  // one that expands (`aria-expanded`).
  const providerRow = (await screen.findAllByText(/^openai$/i))
    .map((el) => el.closest("button"))
    .find((b) => b?.hasAttribute("aria-expanded")) as HTMLButtonElement;
  fireEvent.click(providerRow);
  const modelRow = (await screen.findByText(model)).closest("button") as HTMLButtonElement;
  fireEvent.click(modelRow);
}

async function send(text: string) {
  const box = await screen.findByPlaceholderText(/Message Iron Jarvis/);
  fireEvent.change(box, { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(H.stream.bodies.length).toBeGreaterThan(0));
  return H.stream.bodies[H.stream.bodies.length - 1];
}

describe("the reasoning control exists only where it is an option (v1.263.0)", () => {
  it("is absent for the default model and for a model with no knob", async () => {
    render(<ChatPage />);
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    expect(screen.queryByTestId("reasoning-level")).toBeNull();
    await pickModel("gpt-4o");
    await waitFor(() => expect(screen.queryByTestId("reasoning-level")).toBeNull());
    const body = await send("hello");
    expect("reasoning" in body).toBe(false);
  });

  it("appears for a model that offers levels, sends the pick, and remembers it", async () => {
    render(<ChatPage />);
    await pickModel("gpt-5");
    const select = (await screen.findByTestId("reasoning-level")) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["", "low", "medium", "high"]);
    fireEvent.change(select, { target: { value: "high" } });
    const body = await send("think hard about this");
    expect(body.reasoning).toBe("high");
    expect(body.model).toBe("gpt-5");
    // The thread setup carries it and the reopen restores it. The harness's
    // `put` records nothing, so this half is pinned at the source: the setup
    // object names the field, and the restore reads it back through the
    // vocabulary guard.
    const page = readFileSync(join(__dirname, "..", "app", "chat", "page.tsx"), "utf8").replace(/\r\n/g, "\n");
    expect(page).toMatch(/approval_mode: approvalMode,\s*reasoning,\s*\};/);
    expect(page).toMatch(/setReasoning\(REASONING_LEVELS\.includes\(setup\.reasoning \?\? ""\)/);
  });

  it("switching to a model with no knob drops the level from the request", async () => {
    render(<ChatPage />);
    await pickModel("gpt-5");
    const select = (await screen.findByTestId("reasoning-level")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "medium" } });
    await pickModel("gpt-4o");
    await waitFor(() => expect(screen.queryByTestId("reasoning-level")).toBeNull());
    const body = await send("hello again");
    expect("reasoning" in body).toBe(false);
  });
});

describe("the receipt names the level that reached the model", () => {
  it("says 'reasoning high' when the route carries it, nothing otherwise", () => {
    const { unmount } = render(
      <TurnReceipt route={{ requested: "", provider: "openai", model: "gpt-5", reason: "explicit", reasoning: "high" }} />,
    );
    expect(screen.getByTestId("turn-reasoning")).toHaveTextContent("reasoning high");
    unmount();
    render(<TurnReceipt route={{ requested: "", provider: "openai", model: "gpt-4o", reason: "explicit", reasoning: "" }} />);
    expect(screen.queryByTestId("turn-reasoning")).toBeNull();
  });
});
