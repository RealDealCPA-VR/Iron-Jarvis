/**
 * v1.435.0 review (chat-first plan, Wave 3 group I): Local only changed FROM
 * CHAT, the parts the first cut missed.
 *
 *  - Turning Local only OFF and an undo from chat are SAFETY cards
 *    (`once_reason` "privacy" / "undo", lib/onceReason): Enter never allows
 *    them, the card says why it asks every time.
 *  - A turn that turned Local only ON and then FAILED (or was stopped) has no
 *    done frame, so no card: the page used to save the switch as it was and
 *    put the old value back over the daemon's write. It now follows the step
 *    itself (lib/localOnlyChat `localOnlyFromTools`) before that save.
 *
 * Harness: the local-only-from-chat-v1435 page mocks, with a stream whose
 * steps (`tools`), `streaming` and failure the test controls.
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
    committed = true;
    offline = false;
    partial = "";
    code = "";
    provider = "";
    model = "";
  }
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      gets: [] as string[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      posts: [] as { path: string; body: Record<string, unknown> }[],
      responses: {} as Record<string, unknown>,
    },
    run: {
      bodies: [] as Record<string, unknown>[],
      tools: [] as Record<string, unknown>[],
      streaming: false,
      fail: false,
      hang: false,
      release: null as null | (() => void),
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (path: string) => {
    H.api.gets.push(path);
    const r = H.api.responses[path];
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    return {};
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: H.run.streaming,
    text: "",
    tools: H.run.tools,
    approval: null,
    run: async (body: Record<string, unknown>) => {
      H.run.bodies.push(body);
      if (H.run.hang) {
        await new Promise<void>((ok) => {
          H.run.release = ok;
        });
        return { reply: "", tools_used: [] };
      }
      if (H.run.fail) throw new H.FakeStreamError("The model stopped answering.", 500);
      return { reply: "Noted.", tools_used: [] };
    },
    abort: () => H.run.release?.(),
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
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
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { CLICK_TO_ALLOW_HINT, dockAskKeyHint, enterAllows } from "@/lib/dockAsk";
import { LOCAL_ONLY_TOOL, localOnlyFromTools } from "@/lib/localOnlyChat";
import { onceReasonOf } from "@/lib/onceReason";
import type { PendingApproval } from "@/lib/useChatStream";

const LOCAL = { provider: "ollama", model: "qwen3:14b", kind: "local", available: true };

function step(on: boolean, extra: Record<string, unknown> = {}) {
  return { id: "c1", name: LOCAL_ONLY_TOOL, status: "done", ok: true, args: { on }, ...extra };
}

async function chip(): Promise<HTMLButtonElement> {
  await screen.findByRole("textbox", { name: "Message" });
  return (await screen.findByTestId("local-only-chip")) as HTMLButtonElement;
}

function lastThreadSetup(): Record<string, unknown> | undefined {
  const saves = H.api.puts.filter((p) => p.path === "/chat/threads/t7" && p.body.setup);
  return saves.length ? (saves[saves.length - 1].body.setup as Record<string, unknown>) : undefined;
}

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.puts.length = 0;
  H.api.posts.length = 0;
  H.run.bodies.length = 0;
  H.run.tools = [];
  H.run.streaming = false;
  H.run.fail = false;
  H.run.hang = false;
  H.run.release = null;
  H.api.responses = {
    "/models": { models: [LOCAL] },
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
    // A SAVED chat with a setup: every later save carries the setup (the
    // page's sendSetupRef), which is how a stale switch overwrote the chat.
    "/chat/threads/t7": {
      id: "t7",
      title: "Notes",
      messages: [
        { role: "user", content: "a" },
        { role: "assistant", content: "earlier reply" },
      ],
      setup: { tools: [] },
    },
  };
  window.history.replaceState({}, "", "/chat?thread=t7");
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  H.run.release?.();
  cleanup();
  vi.restoreAllMocks();
});

async function sendText(text: string) {
  const b = (await screen.findByRole("textbox", { name: "Message" })) as HTMLTextAreaElement;
  fireEvent.change(b, { target: { value: text } });
  fireEvent.keyDown(b, { key: "Enter" });
  await waitFor(() => expect(H.run.bodies.length).toBeGreaterThan(0));
}

/* =================================================== safety cards */

describe("turning Local only off and an undo from chat are safety cards", () => {
  for (const reason of ["privacy", "undo"] as const) {
    const ask = {
      callId: "c1",
      approvalId: "a1",
      tool: reason === "privacy" ? "chat_local_only" : "change_undo",
      args: {},
      onceOnly: true,
      onceReason: reason,
    } as unknown as PendingApproval;

    it(`knows "${reason}" and Enter never allows it`, () => {
      expect(onceReasonOf(reason)).toBe(reason);
      const item = { kind: "approval" as const, id: "a1", approval: ask };
      expect(enterAllows(item)).toBe(false);
      expect(dockAskKeyHint("approval", item)).toBe(CLICK_TO_ALLOW_HINT);
    });

    it(`says why "${reason}" asks every time, never the local-only line`, () => {
      render(<ApprovalCard approval={ask} scope="chat" />);
      expect(screen.getByTestId(`approval-${reason}-once`).textContent).toMatch(/asks you every time/);
      expect(screen.queryByTestId("approval-local-only")).toBeNull();
    });
  }

  it("CONTROL: a once-only card with no reason is still allowed by Enter", () => {
    const ask = { callId: "c1", approvalId: "a1", tool: "x", args: {}, onceOnly: true } as unknown as PendingApproval;
    expect(enterAllows({ kind: "approval", id: "a1", approval: ask })).toBe(true);
  });
});

/* ======================================= a turn that failed after the step */

describe("lib/localOnlyChat localOnlyFromTools", () => {
  it("reads the newest finished, ok chat_local_only step", () => {
    expect(localOnlyFromTools([step(true)])).toBe(true);
    expect(localOnlyFromTools([step(true), step(false)])).toBe(false);
    expect(localOnlyFromTools([step(true, { ok: false })])).toBeNull();
    expect(localOnlyFromTools([step(true, { status: "running" })])).toBeNull();
    expect(localOnlyFromTools([step(true, { name: "config_set" })])).toBeNull();
    expect(localOnlyFromTools(undefined)).toBeNull();
  });
});

describe("Local only turned on, then the turn failed or was stopped", () => {
  it("a FAILED turn's save carries Local only on (the daemon's write is not put back)", async () => {
    render(<ChatPage />);
    await screen.findByText("earlier reply");
    expect((await chip()).getAttribute("aria-pressed")).toBe("false");
    H.run.tools = [step(true)];
    H.run.fail = true;
    await sendText("keep this chat on my PC");
    await waitFor(() => expect(lastThreadSetup()?.local_only).toBe(true));
    expect((await chip()).getAttribute("aria-pressed")).toBe("true");
  });

  it("CONTROL: a failed turn with no Local only step saves the switch as it was", async () => {
    render(<ChatPage />);
    await screen.findByText("earlier reply");
    H.run.fail = true;
    await sendText("something else");
    await waitFor(() => expect(lastThreadSetup()).toBeDefined());
    expect(lastThreadSetup()?.local_only).toBe(false);
  });

  it("a STOPPED turn's save carries Local only on too", async () => {
    render(<ChatPage />);
    await screen.findByText("earlier reply");
    H.run.tools = [step(true)];
    H.run.streaming = true;
    H.run.hang = true;
    await sendText("keep this chat on my PC");
    fireEvent.click(await screen.findByRole("button", { name: "Stop" }));
    await waitFor(() => expect(lastThreadSetup()?.local_only).toBe(true));
  });
});
