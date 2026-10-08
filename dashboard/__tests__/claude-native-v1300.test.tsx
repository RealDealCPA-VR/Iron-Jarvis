/**
 * v1.300.0 — the Claude subscription path, dashboard half.
 *
 * The daemon now routes the subscription to the exact model the account's
 * live picker offers, and reports each turn's tokens with a LIST-PRICE
 * EQUIVALENT cost. Every new wire field is OPTIONAL (absent → today's look):
 *
 *  - the done frame / POST /chat `usage` grows cache reads + `cost_usd`; the
 *    decode whitelists five keys, finite non-negative numbers only;
 *  - both chat lanes (the chat page, the Build pane) store it on the message
 *    and hand it to the TurnReceipt, which says ONE quiet line —
 *    "cached 97% · ~$0.03 list" — and nothing when there is nothing to say;
 *  - /models rows carry `label` ("Opus 5.5"), `context_window`, and
 *    `usage_credits`: both pickers show the label, a "1M" chip and a muted
 *    "uses credits" note, and the composer's filter matches id AND label;
 *  - the Usage page says a flagged claude-cli row's cost as
 *    "~$x list-price equivalent", never as a bill.
 *
 * Harness: the model-menu (v1.277.0) and Build-pane (v1.206.0) harnesses —
 * only the transport, the stream hook and the daemon context are mocked.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
    },
    stream: {
      bodies: [] as Record<string, unknown>[],
      result: { reply: "ok" } as Record<string, unknown>,
      reject: null as Error | null,
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
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async (body: Record<string, unknown>, onDelta?: (d: string, f: string) => void) => {
      H.stream.bodies.push(body);
      await new Promise<void>((r) => setTimeout(r, 0));
      if (H.stream.reject) throw H.stream.reject;
      const reply = String(H.stream.result.reply ?? "");
      onDelta?.(reply, reply);
      return H.stream.result;
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
      default_provider: "claude-cli",
      default_model: "claude-opus-5-5",
      providers: [
        { provider: "claude-cli", available: true, class: "cli" },
        { provider: "openai", available: true, class: "api" },
      ],
    },
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
import UsagePage from "@/app/usage/page";
import { PaneChat } from "@/components/terminal/PaneChat";
import { ModelSwitcher, resolveTiers } from "@/components/ModelSwitcher";
import { TurnReceipt, usageWords } from "@/components/chat/TurnReceipt";
import { LIST_PRICE_TITLE, turnUsageFrom } from "@/lib/types";
import { matchModels } from "@/lib/recentModels";

const CATALOG = {
  models: [
    { provider: "claude-cli", model: "subscription", name: "Claude", kind: "cli", available: true },
    {
      provider: "claude-cli", model: "claude-opus-5-5", name: "Claude", kind: "cli", available: true,
      label: "Opus 5.5", description: "Most capable", context_window: 1_000_000,
      usage_credits: false, native: "opus", pinned: true,
    },
    {
      provider: "claude-cli", model: "claude-fable-5-1", name: "Claude", kind: "cli", available: true,
      label: "Fable 5.1", context_window: 200_000, usage_credits: true, native: "fable",
    },
    {
      provider: "claude-cli", model: "claude-haiku-4-5-20251001", name: "Claude", kind: "cli",
      available: true, label: "Haiku 4.5", context_window: null,
    },
    { provider: "openai", model: "gpt-5", name: "OpenAI", kind: "api", available: true },
  ],
};

const ROUTE = { requested: "", provider: "claude-cli", model: "claude-opus-5-5", reason: "default" };
const USAGE = {
  input_tokens: 1000,
  output_tokens: 50,
  cache_read_input_tokens: 970,
  cache_creation_input_tokens: 30,
  cost_usd: 0.0312,
  list_price_equivalent: true,
};

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.result = { reply: "ok" };
  H.stream.reject = null;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = {
    "/models": CATALOG,
    "/routing": { enabled: false, routing_model: "", connected: [], suggested: null, tiers: {} },
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

/* -------------------------------------------------------- the wire decode */

describe("usage on the done frame is decoded, numbers only", () => {
  it("keeps the five known keys as finite non-negative numbers and drops the junk", async () => {
    const { decodeSSE } = await vi.importActual<typeof import("@/lib/useChatStream")>(
      "@/lib/useChatStream",
    );
    const ev = decodeSSE(
      "done",
      JSON.stringify({
        reply: "r",
        usage: {
          input_tokens: 1000,
          output_tokens: "50", // a string is not a count
          cache_read_input_tokens: 970,
          cache_creation_input_tokens: -3, // negative is not a count
          cost_usd: 0.0312,
          list_price_equivalent: true,
          extra: 5, // an un-listed key dies here
        },
      }),
    ) as { usage?: Record<string, unknown> };
    expect(ev.usage).toEqual({
      input_tokens: 1000,
      cache_read_input_tokens: 970,
      cost_usd: 0.0312,
      list_price_equivalent: true,
    });
    // The flag is a BOOLEAN or nothing.
    const notBool = decodeSSE(
      "done",
      JSON.stringify({ reply: "r", usage: { cost_usd: 1, list_price_equivalent: "yes" } }),
    ) as { usage?: Record<string, unknown> };
    expect(notBool.usage).toEqual({ cost_usd: 1 });
    // Anti-vacuity: all-junk, a non-object, or no usage → no key at all.
    for (const usage of [{ output_tokens: null, cost_usd: "free" }, "lots", [1, 2], null]) {
      const none = decodeSSE("done", JSON.stringify({ reply: "r", usage })) as Record<string, unknown>;
      expect("usage" in none).toBe(false);
    }
    const absent = decodeSSE("done", JSON.stringify({ reply: "r" })) as Record<string, unknown>;
    expect("usage" in absent).toBe(false);
  });

  it("turnUsageFrom drops NaN and Infinity (they never cross JSON, but a caller can hand them over)", () => {
    expect(turnUsageFrom({ input_tokens: Number.NaN, cost_usd: Number.POSITIVE_INFINITY })).toBeUndefined();
    expect(turnUsageFrom({ cost_usd: 0 })).toEqual({ cost_usd: 0 });
    expect(turnUsageFrom(undefined)).toBeUndefined();
  });

  it("the real hook carries usage onto the resolved result", async () => {
    const { useChatStream } = await vi.importActual<typeof import("@/lib/useChatStream")>(
      "@/lib/useChatStream",
    );
    const { renderHook } = await import("@testing-library/react");
    const frames = `event: done\ndata: ${JSON.stringify({ reply: "ok", usage: { ...USAGE, junk: "x" } })}\n\n`;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(frames));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 200 })));
    try {
      const { result } = renderHook(() => useChatStream());
      let settled: { usage?: unknown } | null = null;
      await act(async () => {
        settled = (await result.current.run({ messages: [] })) as typeof settled;
      });
      expect(settled!.usage).toEqual(USAGE);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

/* ------------------------------------------------------------ the receipt */

describe("TurnReceipt — one quiet usage line", () => {
  it("cache + cost: 'cached 97% · ~$0.03 list', the cost titled as a list-price equivalent", () => {
    render(<TurnReceipt route={ROUTE} usage={USAGE} />);
    const line = screen.getByTestId("turn-usage");
    expect(line.textContent).toBe("cached 97% · ~$0.03 list");
    expect(screen.getByTestId("turn-usage-cost")).toHaveAttribute("title", LIST_PRICE_TITLE);
    // On the collapsed line — visible without expanding.
    expect(screen.getByRole("button", { expanded: false })).toContainElement(line);
    // Quiet class: zinc, never amber.
    expect(line.className).toContain("text-zinc-500");
    expect(line.className).not.toMatch(/amber/);
  });

  it("cost only: no cache share when nothing was read from cache", () => {
    render(<TurnReceipt route={ROUTE} usage={{ input_tokens: 1000, output_tokens: 50, cost_usd: 0.5, list_price_equivalent: true }} />);
    expect(screen.getByTestId("turn-usage").textContent).toBe("~$0.50 list");
    cleanup();
    render(
      <TurnReceipt
        route={ROUTE}
        usage={{ input_tokens: 1000, cache_read_input_tokens: 0, cost_usd: 1.234, list_price_equivalent: true }}
      />,
    );
    expect(screen.getByTestId("turn-usage").textContent).toBe("~$1.23 list");
  });

  it("cache only: the share without a cost (a cost is never inferred)", () => {
    render(<TurnReceipt route={ROUTE} usage={{ input_tokens: 400, cache_read_input_tokens: 100 }} />);
    expect(screen.getByTestId("turn-usage").textContent).toBe("cached 25%");
    expect(screen.queryByTestId("turn-usage-cost")).not.toBeInTheDocument();
  });

  it("neither: nothing — and tokens alone do not make a receipt (anti-vacuity)", () => {
    const { container } = render(<TurnReceipt usage={{ input_tokens: 1000, output_tokens: 50 }} />);
    expect(container).toBeEmptyDOMElement();
    cleanup();
    const r2 = render(<TurnReceipt />);
    expect(r2.container).toBeEmptyDOMElement();
    cleanup();
    render(<TurnReceipt route={ROUTE} />);
    expect(screen.getByText("Claude Code")).toBeInTheDocument(); // v1.314.0: providerDisplay
    expect(screen.queryByTestId("turn-usage")).not.toBeInTheDocument();
    expect(usageWords(null)).toEqual({ cache: null, cost: null, list: false });
  });
});

describe("cache share under both token conventions; a sub-cent cost", () => {
  const share = (u: Record<string, number>) => usageWords(u).cache;
  it("claude-cli: input_tokens is the TOTAL prompt (cache included)", () => {
    expect(share({ input_tokens: 1000, cache_read_input_tokens: 970, cache_creation_input_tokens: 30 })).toBe(
      "cached 97%",
    );
  });
  it("raw API: input_tokens is the UNcached part, so the parts are summed", () => {
    expect(share({ input_tokens: 30, cache_read_input_tokens: 970 })).toBe("cached 97%");
    expect(share({ input_tokens: 10, cache_read_input_tokens: 970, cache_creation_input_tokens: 20 })).toBe(
      "cached 97%",
    );
    // Never over 100, even with no input count at all.
    expect(share({ cache_read_input_tokens: 500 })).toBe("cached 100%");
    expect(share({ input_tokens: 0, cache_read_input_tokens: 0 })).toBeNull();
  });
  it("a cost under half a cent says <$0.01, never $0.00", () => {
    const sub = { list_price_equivalent: true };
    expect(usageWords({ ...sub, cost_usd: 0.0049 }).cost).toBe("<$0.01 list");
    expect(usageWords({ ...sub, cost_usd: 0.0001 }).cost).toBe("<$0.01 list");
    expect(usageWords({ ...sub, cost_usd: 0.02 }).cost).toBe("~$0.02 list");
    expect(usageWords({ cost_usd: 0.0049 }).cost).toBe("<$0.01"); // metered
  });
});

describe("whose cost it is: subscription, metered, free", () => {
  it("subscription: '~$0.03 list' with the subscription title", () => {
    render(<TurnReceipt route={ROUTE} usage={{ cost_usd: 0.031, list_price_equivalent: true }} />);
    expect(screen.getByTestId("turn-usage").textContent).toBe("~$0.03 list");
    expect(screen.getByTestId("turn-usage-cost")).toHaveAttribute("title", LIST_PRICE_TITLE);
  });

  it("metered API: '~$0.03' — no 'list', no subscription title", () => {
    render(<TurnReceipt route={{ provider: "anthropic" }} usage={{ cost_usd: 0.031 }} />);
    expect(screen.getByTestId("turn-usage").textContent).toBe("~$0.03");
    expect(screen.getByTestId("turn-usage-cost")).not.toHaveAttribute("title");
    cleanup();
    render(<TurnReceipt route={{ provider: "anthropic" }} usage={{ cost_usd: 0.031, list_price_equivalent: false }} />);
    expect(screen.getByTestId("turn-usage").textContent).toBe("~$0.03");
  });

  it("free / local (cost_usd 0, not a subscription turn): no dollar figure at all", () => {
    render(<TurnReceipt route={{ provider: "lmstudio" }} usage={{ input_tokens: 900, cost_usd: 0 }} />);
    expect(screen.getByText("lmstudio")).toBeInTheDocument();
    expect(screen.queryByTestId("turn-usage")).toBeNull();
    expect(document.body.textContent).not.toContain("$0.00");
    cleanup();
    const { container } = render(<TurnReceipt usage={{ cost_usd: 0 }} />);
    expect(container).toBeEmptyDOMElement();
    // A tiny SUBSCRIPTION turn still says so.
    expect(usageWords({ cost_usd: 0.001, list_price_equivalent: true }).cost).toBe("<$0.01 list");
  });
});

/* ---------------------------------------------------- both chat lanes */

async function sendOnChatPage(text: string) {
  const box = await screen.findByPlaceholderText(/Message Iron Jarvis/);
  fireEvent.change(box, { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

function lastSavedAssistant(): Record<string, unknown> | undefined {
  for (let i = H.api.puts.length - 1; i >= 0; i--) {
    const msgs = H.api.puts[i].body?.messages as Record<string, unknown>[] | undefined;
    const last = msgs?.[msgs.length - 1];
    if (last?.role === "assistant") return last;
  }
  return undefined;
}

describe("both chat lanes store usage on the message and pass it to the receipt", () => {
  it("chat page, stream lane", async () => {
    H.stream.result = { reply: "done", route: ROUTE, usage: USAGE };
    render(<ChatPage />);
    await sendOnChatPage("hello");
    await waitFor(() =>
      expect(screen.getByTestId("turn-usage").textContent).toBe("cached 97% · ~$0.03 list"),
    );
    await waitFor(() => expect(lastSavedAssistant()?.usage).toEqual(USAGE));
  });

  it("chat page, POST lane (no /chat/stream on this daemon): decoded through the same whitelist", async () => {
    H.stream.reject = new H.FakeStreamError("no stream route", 404);
    H.api.postResponses["/chat"] = {
      reply: "done",
      route: ROUTE,
      usage: {
        input_tokens: 200,
        cache_read_input_tokens: 150,
        cost_usd: 0.019,
        list_price_equivalent: true,
        bogus: "x",
        output_tokens: "7",
      },
    };
    render(<ChatPage />);
    await sendOnChatPage("hello");
    await waitFor(() => expect(screen.getByTestId("turn-usage").textContent).toBe("cached 75% · ~$0.02 list"));
    await waitFor(() =>
      expect(lastSavedAssistant()?.usage).toEqual({
        input_tokens: 200,
        cache_read_input_tokens: 150,
        cost_usd: 0.019,
        list_price_equivalent: true,
      }),
    );
  });

  it("chat page: a turn without usage carries no usage key and no line (anti-vacuity)", async () => {
    H.stream.result = { reply: "done", route: ROUTE };
    render(<ChatPage />);
    await sendOnChatPage("hello");
    await waitFor(() => expect(lastSavedAssistant()?.content).toBe("done"));
    expect("usage" in (lastSavedAssistant() ?? {})).toBe(false);
    expect(screen.queryByTestId("turn-usage")).not.toBeInTheDocument();
  });

  it("the Build pane (a METERED turn: dollars, no 'list', no subscription title)", async () => {
    H.stream.result = {
      reply: "All done.",
      route: ROUTE,
      usage: { input_tokens: 500, output_tokens: 20, cost_usd: 0.031 },
    };
    render(<PaneChat paneId="p1" cwd={"C:\\work\\demo"} />);
    const box = screen.getByLabelText("Message");
    await waitFor(() => expect(box).toBeEnabled());
    fireEvent.change(box, { target: { value: "do it" } });
    fireEvent.click(screen.getByLabelText("Send"));
    await waitFor(() => expect(screen.getByTestId("turn-usage").textContent).toBe("~$0.03"));
    expect(screen.getByTestId("turn-usage-cost")).not.toHaveAttribute("title");
    await waitFor(() =>
      expect(lastSavedAssistant()?.usage).toEqual({ input_tokens: 500, output_tokens: 20, cost_usd: 0.031 }),
    );
  });
});

/* ------------------------------------------------------- the model menus */

async function openComposerMenu() {
  await screen.findByPlaceholderText(/Message Iron Jarvis/);
  fireEvent.click(await screen.findByTitle("Switch model"));
  return (await screen.findByTestId("model-filter")) as HTMLInputElement;
}

function rowFor(scope: HTMLElement, text: string): HTMLElement {
  const btn = within(scope).getByText(text).closest("button");
  expect(btn).not.toBeNull();
  return btn as HTMLElement;
}

describe("the composer's model menu shows the picker's label, the 1M chip and the credits note", () => {
  it("the Claude submenu: labels over ids, 1M on the million-token model, credits only where it applies", async () => {
    render(<ChatPage />);
    await openComposerMenu();
    const menu = screen.getByTestId("model-menu");
    fireEvent.click(rowFor(menu, "Claude"));
    const opus = rowFor(menu, "Opus 5.5");
    expect(within(opus).getByTestId("model-1m")).toHaveTextContent("1M");
    expect(within(opus).queryByTestId("model-credits")).toBeNull();
    // The raw id is the hover, not the text.
    expect(within(opus).getByText("Opus 5.5")).toHaveAttribute("title", "claude-opus-5-5");
    expect(within(menu).queryByText("claude-opus-5-5")).toBeNull();
    const fable = rowFor(menu, "Fable 5.1");
    expect(within(fable).getByTestId("model-credits")).toHaveTextContent("uses credits");
    expect(within(fable).queryByTestId("model-1m")).toBeNull();
    // A null window and no credits flag: no chips at all; a row with no
    // label keeps its raw id (today's look).
    expect(within(rowFor(menu, "Haiku 4.5")).queryByTestId("model-1m")).toBeNull();
    expect(within(menu).getByText("subscription")).toBeInTheDocument();
    expect(within(menu).getAllByTestId("model-credits")).toHaveLength(1);
    expect(within(menu).getAllByTestId("model-1m")).toHaveLength(1);
  });

  it("the filter matches the label AND the id; Enter picks, and the trigger says the label", async () => {
    render(<ChatPage />);
    const box = await openComposerMenu();
    fireEvent.change(box, { target: { value: "opus 5.5" } });
    let rows = await screen.findAllByTestId("model-match");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("Opus 5.5");
    expect(within(rows[0]).getByTestId("model-1m")).toBeInTheDocument();
    fireEvent.change(box, { target: { value: "claude-opus-5-5" } });
    rows = await screen.findAllByTestId("model-match");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("Opus 5.5");
    fireEvent.change(box, { target: { value: "fable" } });
    rows = await screen.findAllByTestId("model-match");
    expect(within(rows[0]).getByTestId("model-credits")).toBeInTheDocument();
    fireEvent.change(box, { target: { value: "opus 5.5" } });
    await waitFor(() => expect(screen.getAllByTestId("model-match")).toHaveLength(1));
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(screen.queryByTestId("model-filter")).toBeNull());
    expect((await screen.findByTitle("Switch model")).textContent).toContain("Opus 5.5");
  });

  it("matchModels: label is a match key; a row without one matches as before", () => {
    const rows = CATALOG.models;
    expect(matchModels(rows, "Haiku 4.5", 10).map((r) => r.model)).toEqual(["claude-haiku-4-5-20251001"]);
    expect(matchModels(rows, "gpt", 10).map((r) => r.model)).toEqual(["gpt-5"]);
  });
});

describe("the title-bar ModelSwitcher shows the same facts", () => {
  it("label on the trigger and the rows, 1M chip, credits note", async () => {
    render(<ModelSwitcher />);
    const trigger = await screen.findByRole("button", { name: /switch the active model/i });
    await waitFor(() => expect(trigger.textContent).toContain("Opus 5.5"));
    fireEvent.click(trigger);
    const list = await screen.findByTestId("ij-model-list");
    const active = within(list).getByTestId("ij-active-model-row");
    expect(active.textContent).toContain("Opus 5.5");
    expect(within(active).getByTestId("model-1m")).toBeInTheDocument();
    const fable = rowFor(list, "Fable 5.1");
    expect(within(fable).getByTestId("model-credits")).toBeInTheDocument();
    expect(within(list).getAllByTestId("model-credits")).toHaveLength(1);
    expect(within(rowFor(list, "gpt-5")).queryByTestId("model-1m")).toBeNull();
  });
});

describe("the quality dial resolves Claude tiers from the live rows", () => {
  const NEW_ONLY = {
    models: [
      { provider: "claude-cli", model: "subscription", name: "Claude", kind: "cli", available: true },
      { provider: "claude-cli", model: "claude-opus-4-8", label: "Opus 4.8", name: "Claude", kind: "cli", available: true },
      { provider: "claude-cli", model: "claude-opus-5-5", label: "Opus 5.5", name: "Claude", kind: "cli", available: true },
      { provider: "claude-cli", model: "claude-sonnet-5-5", label: "Sonnet 5.5", name: "Claude", kind: "cli", available: true },
      { provider: "claude-cli", model: "claude-haiku-4-5-20251001", label: "Haiku 4.5", name: "Claude", kind: "cli", available: true },
      { provider: "anthropic", model: "claude-opus-5-5", name: "Anthropic", kind: "api", available: true },
      { provider: "anthropic", model: "claude-sonnet-5-5", name: "Anthropic", kind: "api", available: true },
      { provider: "anthropic", model: "claude-haiku-4-5-20251001", name: "Anthropic", kind: "api", available: true },
    ],
  };

  it("by family, newest version first — label or id", () => {
    const rows = NEW_ONLY.models as Parameters<typeof resolveTiers>[1];
    const want = {
      fast: "claude-haiku-4-5-20251001",
      balanced: "claude-sonnet-5-5",
      best: "claude-opus-5-5",
    };
    expect(resolveTiers("claude-cli", rows)).toEqual(want); // labels
    expect(resolveTiers("anthropic", rows)).toEqual(want); // ids only
    // A provider outside the Claude pair keeps its static table; claude-cli
    // with no family rows gets no dial.
    expect(resolveTiers("openai", rows)?.best).toBe("gpt-5.5");
    expect(resolveTiers("claude-cli", [rows[0]])).toBeUndefined();
  });

  it("on the switcher every tier resolves and none reads n/a (anti-vacuity)", async () => {
    H.api.getResponses["/models"] = NEW_ONLY;
    render(<ModelSwitcher />);
    fireEvent.click(await screen.findByRole("button", { name: /switch the active model/i }));
    const best = await screen.findByRole("button", { name: /^Best/ });
    await waitFor(() => expect(best).toBeEnabled());
    for (const name of [/^Fast/, /^Balanced/, /^Best/]) {
      const b = screen.getByRole("button", { name });
      expect(b).toBeEnabled();
      expect(b.textContent).not.toContain("n/a");
    }
    expect(best).toHaveAttribute("title", "claude-opus-5-5");
    expect(screen.getByRole("button", { name: /^Fast/ })).toHaveAttribute(
      "title",
      "claude-haiku-4-5-20251001",
    );
  });
});

/* ---------------------------------------------------------- Usage page */

describe("the Usage page says a subscription's cost as a list-price equivalent", () => {
  const USAGE_PAGE = {
    totals: { input_tokens: 1110, output_tokens: 510, cost_usd: 1.734, runs: 3 },
    by_day: [],
    by_model: [
      {
        provider: "claude-cli", model: "claude-opus-5-5", input_tokens: 1000, output_tokens: 500,
        cost_usd: 1.234, runs: 2, list_price_equivalent: true,
      },
      { provider: "openai", model: "gpt-5", input_tokens: 100, output_tokens: 10, cost_usd: 0.5, runs: 1 },
    ],
  };

  it("the flagged row reads '~$1.23 list-price equivalent' with the title; a metered row is unchanged", async () => {
    H.api.getResponses["/usage?days=30"] = USAGE_PAGE;
    H.api.getResponses["/usage?days=365"] = USAGE_PAGE;
    render(<UsagePage />);
    const cell = await screen.findByTestId("usage-list-price");
    expect(cell.textContent).toBe(" · ~$1.23 list-price equivalent");
    expect(cell).toHaveAttribute("title", LIST_PRICE_TITLE);
    // Anti-vacuity: one flagged row, one phrase; the metered row keeps "$0.50".
    expect(screen.getAllByTestId("usage-list-price")).toHaveLength(1);
    const openai = screen.getByText("openai · gpt-5").parentElement as HTMLElement;
    expect(openai.textContent).toContain("$0.50");
    expect(openai.textContent).not.toContain("list-price");
  });

  it("the list-price total is its own muted line, never added to Total cost", async () => {
    const withTotal = {
      ...USAGE_PAGE,
      totals: { ...USAGE_PAGE.totals, cost_usd: 0.5, list_price_equivalent_usd: 12.5 },
    };
    H.api.getResponses["/usage?days=30"] = withTotal;
    H.api.getResponses["/usage?days=365"] = withTotal;
    render(<UsagePage />);
    const line = await screen.findByTestId("usage-total-list-price");
    expect(line.textContent).toBe("~$12.50 list-price equivalent (subscription)");
    expect(line).toHaveAttribute("title", LIST_PRICE_TITLE);
    expect(screen.getByText("$0.50", { selector: "div" })).toBeInTheDocument();
  });

  it("no list-price total (absent or 0) → no line", async () => {
    H.api.getResponses["/usage?days=30"] = USAGE_PAGE;
    H.api.getResponses["/usage?days=365"] = USAGE_PAGE;
    render(<UsagePage />);
    await screen.findByTestId("usage-list-price");
    expect(screen.queryByTestId("usage-total-list-price")).toBeNull();
    cleanup();
    const zero = { ...USAGE_PAGE, totals: { ...USAGE_PAGE.totals, list_price_equivalent_usd: 0 } };
    H.api.getResponses["/usage?days=30"] = zero;
    H.api.getResponses["/usage?days=365"] = zero;
    render(<UsagePage />);
    await screen.findByTestId("usage-list-price");
    expect(screen.queryByTestId("usage-total-list-price")).toBeNull();
  });

  it("a flagged row at $0 still says what it is, never a bare '$0.00'", async () => {
    const zero = {
      ...USAGE_PAGE,
      by_model: [{ ...USAGE_PAGE.by_model[0], cost_usd: 0 }],
    };
    H.api.getResponses["/usage?days=30"] = zero;
    H.api.getResponses["/usage?days=365"] = zero;
    render(<UsagePage />);
    expect((await screen.findByTestId("usage-list-price")).textContent).toBe(
      " · ~$0.00 list-price equivalent",
    );
  });
});
