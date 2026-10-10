/**
 * v1.330.0 (calm chat W11 M5) - the receipt says when a turn ran without
 * tools, and names the endpoint by the label the user gave it.
 *
 * The wave-10 audit drove a real turn through a fleet endpoint whose tool use
 * is not verified, with the composer on "Auto tools". The daemon served it
 * text only (its explicit text-only pick) and nothing on screen said so; the
 * model then said it had no file-listing tool. And the "answered by" tooltip
 * read "fleet-sparkl4 · glm" while the model menu calls the row
 * "Spark proxy (L4)".
 *
 * Now the daemon's route carries `text_only` and `label` (both LAST, both
 * lanes; tests/test_text_only_receipt_v1330.py). Pinned here:
 *  - the reply's row says "· no tools" quietly when, and only when, the
 *    route says text_only === true; the reason is on hover and, for a phone,
 *    in the expanded receipt;
 *  - the label leads the tooltip and the expanded line, the raw id kept;
 *  - both page lanes (stream and the POST fallback) and a reopened chat carry
 *    the route through to the row, nothing inferred in the browser.
 */

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
      postResponses: {} as Record<string, unknown>,
    },
    stream: {
      bodies: [] as Record<string, unknown>[],
      result: { reply: "done" } as Record<string, unknown>,
      missing: false,
    },
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
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
      H.stream.bodies.push(body);
      await new Promise<void>((r) => setTimeout(r, 0));
      if (H.stream.missing) {
        // An older daemon without /chat/stream: nothing committed, so the
        // page falls back to POST /chat.
        throw new H.FakeStreamError("not found", 404);
      }
      onDelta("done", "done");
      return H.stream.result;
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
import { NO_TOOLS_TITLE, TurnReceipt, type TurnRoute } from "@/components/chat/TurnReceipt";

const DASH = "—";
const LABEL = "Spark proxy (L4)";
const TEXT_ONLY: TurnRoute = {
  requested: "fleet-sparkl4",
  provider: "fleet-sparkl4",
  model: "glm",
  reason: "explicit",
  from: "",
  why: "",
  reasoning: "",
  label: LABEL,
  text_only: true,
};

beforeEach(() => {
  H.api.posts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.result = { reply: "done" };
  H.stream.missing = false;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = {
    "/models": { models: [{ provider: "fleet-sparkl4", model: "glm", available: true, kind: "local" }] },
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

/* ================================================== the receipt on its own */

describe("the receipt says 'no tools' from the daemon's word only", () => {
  it("text_only true: a quiet 'no tools' beside who answered, the reason on hover", () => {
    render(<TurnReceipt inline route={TEXT_ONLY} modelName="glm" />);
    const part = screen.getByTestId("turn-no-tools");
    expect(part.textContent).toBe("no tools");
    expect(part.getAttribute("title")).toBe(NO_TOOLS_TITLE);
    // Quiet: the receipt's zinc, no warning tone, no chip box.
    expect(part.className).toContain("text-zinc-500");
    expect(part.className).not.toMatch(/tone-warn|border|bg-/);
    // It reads right after who answered.
    const line = screen.getByTestId("turn-receipt").textContent ?? "";
    expect(line).toMatch(/answered by glm\s*·\s*no tools/);
  });

  it.each([
    ["false", { ...TEXT_ONLY, text_only: false }],
    ["absent (an older daemon or chat)", { ...TEXT_ONLY, text_only: undefined }],
    ["a string, not the boolean", { ...TEXT_ONLY, text_only: "true" as unknown as boolean }],
  ])("text_only %s: nothing is said", (_name, route) => {
    render(<TurnReceipt inline route={route} modelName="glm" />);
    expect(screen.queryByTestId("turn-no-tools")).toBeNull();
  });

  it("the expanded receipt says the reason in words a phone can read", () => {
    render(<TurnReceipt inline route={TEXT_ONLY} modelName="glm" />);
    fireEvent.click(screen.getByTitle(/Show the receipt/));
    expect(screen.getByTestId("turn-no-tools-detail").textContent).toBe(NO_TOOLS_TITLE);
  });

  it("the words are plain: no dash aside, the advice says what to do", () => {
    expect(NO_TOOLS_TITLE).not.toContain(DASH);
    expect(NO_TOOLS_TITLE).toBe(
      "This model has not shown it can use tools, so this reply was text only. Pick a model with tools to let Jarvis act.",
    );
  });
});

describe("the receipt names the endpoint by its label, the raw id kept", () => {
  it("the 'answered by' tooltip leads with the label and keeps the id last", () => {
    render(<TurnReceipt inline route={TEXT_ONLY} modelName="glm" />);
    expect(screen.getByTestId("turn-answered-by").getAttribute("title")).toBe(
      "Spark proxy (L4) · glm · fleet-sparkl4",
    );
  });

  it("the expanded line names the label, then the raw id quietly", () => {
    render(<TurnReceipt inline route={TEXT_ONLY} modelName="glm" />);
    fireEvent.click(screen.getByTitle(/Show the receipt/));
    const detail = screen.getByTestId("turn-receipt-detail");
    expect(within(detail).getByText(LABEL)).toBeTruthy();
    expect(detail.textContent).toContain(`${LABEL} (fleet-sparkl4) · glm`);
  });

  it("the stand-alone receipt (Build's pane chat) names the label too", () => {
    const { container } = render(<TurnReceipt route={TEXT_ONLY} />);
    const line = container.textContent ?? "";
    expect(line).toContain(LABEL);
    expect(line).toContain("no tools");
  });

  it("no label: the tooltip is the old raw record, unchanged", () => {
    render(
      <TurnReceipt
        inline
        route={{ requested: "", provider: "claude-cli", model: "claude-opus-4-8", reason: "default", label: "" }}
        modelName="Opus 4.8"
      />,
    );
    expect(screen.getByTestId("turn-answered-by").getAttribute("title")).toBe("claude-cli · claude-opus-4-8");
  });
});

/* ================================================== through the chat page */

async function send(text: string) {
  const box = await screen.findByPlaceholderText(/Message Iron Jarvis/);
  fireEvent.change(box, { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(H.stream.bodies.length).toBeGreaterThan(0));
}

async function lastRowNoTools() {
  await waitFor(() => {
    const row = screen.getAllByTestId("reply-actions").at(-1)!;
    expect(within(row).getByTestId("turn-no-tools").textContent).toBe("no tools");
  });
  return screen.getAllByTestId("reply-actions").at(-1)!;
}

describe("the chat page carries the daemon's route to the row", () => {
  it("stream lane: a text-only turn's row says 'no tools' and names the label on hover", async () => {
    H.stream.result = { reply: "done", provider: "fleet-sparkl4", route: TEXT_ONLY };
    render(<ChatPage />);
    await send("list my folder");
    const row = await lastRowNoTools();
    expect(within(row).getByTestId("turn-answered-by").getAttribute("title")).toBe(
      "Spark proxy (L4) · glm · fleet-sparkl4",
    );
  });

  it("stream lane: a turn the daemon served with tools says nothing extra", async () => {
    H.stream.result = { reply: "done", provider: "fleet-sparkl4", route: { ...TEXT_ONLY, text_only: false } };
    render(<ChatPage />);
    await send("list my folder");
    await waitFor(() => {
      const row = screen.getAllByTestId("reply-actions").at(-1)!;
      expect(within(row).getByTestId("turn-answered-by")).toBeTruthy();
    });
    expect(screen.queryByTestId("turn-no-tools")).toBeNull();
  });

  it("POST fallback lane: the same route, the same words", async () => {
    H.stream.missing = true;
    H.api.postResponses["/chat"] = {
      reply: "done",
      provider: "fleet-sparkl4",
      model: "glm",
      route: TEXT_ONLY,
      tools_used: [],
    };
    render(<ChatPage />);
    await send("list my folder");
    await waitFor(() => expect(H.api.posts.some((p) => p.path === "/chat")).toBe(true));
    await lastRowNoTools();
  });

  it("a reopened chat keeps saying it (the route is saved on the message)", async () => {
    H.api.getResponses["/chat/threads/t9"] = {
      id: "t9",
      title: "Folder",
      messages: [
        { role: "user", content: "list my folder", at: "2026-10-10T08:00:00" },
        { role: "assistant", content: "I can only answer in text here.", at: "2026-10-10T08:00:02", route: TEXT_ONLY },
      ],
    };
    window.history.replaceState({}, "", "/chat?thread=t9");
    render(<ChatPage />);
    await screen.findByText("I can only answer in text here.");
    await lastRowNoTools();
  });
});
