/**
 * v1.329.0 — calm chat wave 4, F7: whole pixels, tone tokens, plain copy and a
 * quieter model chip on the new surfaces.
 *
 * The completeness audit after wave 3 found:
 *  - the model chip was semibold-ish and printed a raw id ("Default:
 *    claude-opus-4-8") when the catalog row had no label, so it read louder
 *    than the quiet chips beside it; and with no explicit pick no reasoning
 *    chip was drawn, even when the default model offers levels;
 *  - half-pixel text and literal hues on the thread menu and the expanded
 *    TurnReceipt;
 *  - em-dash asides in the dock ask (ApprovalCard) and the thread menu.
 *
 * Harness: the ux-wave2-chat-words-v1314 one (ChatPage rendered for real,
 * transport mocked, @/lib/daemon mocked so every reader sees one /health),
 * with the stream recording the bodies it is handed.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
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
    getResponses: {} as Record<string, unknown>,
    posts: [] as Array<{ path: string; body: unknown }>,
    health: null as Record<string, unknown> | null,
    refresh: vi.fn(),
    bodies: [] as Record<string, unknown>[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (p: string) => {
    const r = H.getResponses[p];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${p}`, 404);
    return r;
  },
  post: async (p: string, body?: unknown) => {
    H.posts.push({ path: p, body });
    return {};
  },
  put: async (p: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(p);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    epoch: 0,
    provided: true,
    health: H.health,
    refresh: H.refresh,
  }),
}));

vi.mock("@/lib/useChatStream", () => {
  class StreamError extends Error {
    status = 0;
    committed = false;
    offline = false;
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
      run: async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
        H.bodies.push(body);
        await new Promise<void>((r) => setTimeout(r, 0));
        onDelta("done", "done");
        return { reply: "done" };
      },
      abort: () => {},
    }),
  };
});

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: false }),
}));
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
  useProviderHealth: () => ({
    byProvider: { anthropic: true },
    signedOutByProvider: {},
    defaultProvider: "anthropic",
    loading: false,
    stale: false,
    refresh: () => {},
  }),
}));
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { friendlyModelName } from "@/lib/friendlyModelName";
import { TurnReceipt } from "@/components/chat/TurnReceipt";
import { ApprovalCard, waitingLine } from "@/components/chat/ApprovalCard";

const DEFAULT_MODEL = "claude-opus-4-8";

function setHealth(defaultProvider = "anthropic", defaultModel = DEFAULT_MODEL) {
  H.health = {
    status: "ok",
    version: "1.329.0",
    default_provider: defaultProvider,
    default_model: defaultModel,
    providers: [{ provider: "anthropic", available: true, class: "api", inherited_from: null }],
  };
}

function resetApi() {
  for (const k of Object.keys(H.getResponses)) delete H.getResponses[k];
  H.getResponses["/models"] = { models: [] };
  H.getResponses["/chat/personas"] = { personas: [] };
  H.getResponses["/chat/threads"] = { threads: [] };
  H.getResponses["/settings"] = { settings: {} };
  H.getResponses["/projects"] = { projects: [] };
  H.getResponses["/agents/mentionable"] = { agents: [] };
  H.getResponses["/skills"] = { skills: [] };
  H.getResponses["/workflows"] = { workflows: [] };
  H.getResponses["/tools"] = { tools: [] };
  H.getResponses["/undo?session_id=chat"] = { actions: [] };
  H.getResponses["/chat/approvals/pending"] = { approvals: [] };
  H.getResponses["/routing"] = { enabled: false, routing_model: "", connected: [], suggested: null, tiers: {} };
}

const read = (rel: string) =>
  readFileSync(path.join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");

const HALF_PIXEL = /text-\[\d+\.5px\]/;
const LITERAL_HUE = /\b(?:text|bg|border)-(?:rose|amber|emerald|red|green|yellow)-\d{2,3}\b/;

beforeEach(() => {
  resetApi();
  H.posts = [];
  H.bodies = [];
  setHealth();
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/* ============================================================ model name */

describe("friendlyModelName: the name an id spells, never more", () => {
  it.each([
    ["claude-opus-4-8", "Opus 4.8"],
    ["claude-sonnet-5", "Sonnet 5"],
    ["claude-haiku-4-5-20251001", "Haiku 4.5"],
    ["claude-sonnet-4-20250514", "Sonnet 4"],
    ["claude-3-5-sonnet-20241022", "Sonnet 3.5"],
    ["claude-3-opus-latest", "Opus 3"],
    ["claude-opus-4-8[1m]", "Opus 4.8 1M"],
    ["CLAUDE-OPUS-5-5", "Opus 5.5"],
  ])("%s reads as %s", (id, words) => {
    expect(friendlyModelName(id)).toBe(words);
  });

  it.each(["gpt-5", "qwen3:30b", "claude-fable-5", "my-claude-opus-4", "claude-opus", "", "default model"])(
    "CONTROL: %j is left exactly as it is (nothing invented)",
    (id) => {
      expect(friendlyModelName(id)).toBe(id);
    },
  );
});

/* ============================================================ model chip */

describe("the model chip is as quiet as the chips beside it", () => {
  it("a default with no catalog label says the friendly name; the raw id stays on the title", async () => {
    render(<ChatPage />);
    const words = await screen.findByTestId("model-chip-words");
    // What a screen reader hears: "Default: Opus 4.8".
    await waitFor(() => expect(words.textContent).toBe("Default: Opus 4.8"));
    // What the eye sees is the name alone (the mockup's chip); "Default:"
    // lives in an sr-only span so the toolbar keeps one line.
    const hidden = words.querySelector(".sr-only");
    expect(hidden?.textContent).toBe("Default: ");
    const visible = Array.from(words.childNodes)
      .filter((n) => !(n instanceof HTMLElement && n.classList.contains("sr-only")))
      .map((n) => n.textContent)
      .join("");
    expect(visible).toBe("Opus 4.8");
    expect(words.getAttribute("title")).toBe(`The default model: anthropic · ${DEFAULT_MODEL}`);
  });

  it("CONTROL: an explicit pick carries no hidden 'Default:' and a bare raw title", async () => {
    H.getResponses["/models"] = {
      models: [{ provider: "anthropic", model: "claude-sonnet-5", name: "Anthropic", kind: "api", available: true }],
    };
    render(<ChatPage />);
    fireEvent.click(await screen.findByTitle("Switch model"));
    fireEvent.change(await screen.findByTestId("model-filter"), { target: { value: "sonnet" } });
    fireEvent.click((await screen.findAllByTestId("model-match"))[0]);
    const words = await screen.findByTestId("model-chip-words");
    await waitFor(() => expect(words.textContent).toBe("Sonnet 5"));
    expect(words.querySelector(".sr-only")).toBeNull();
    expect(words.getAttribute("title")).toBe("anthropic · claude-sonnet-5");
  });

  it("carries no weight or colour of its own: it inherits the chip's quiet zinc", async () => {
    render(<ChatPage />);
    const words = await screen.findByTestId("model-chip-words");
    const cls = words.getAttribute("class") ?? "";
    expect(cls).not.toMatch(/font-(medium|semibold|bold)/);
    expect(cls).not.toMatch(/text-zinc-\d+/);
    const chip = screen.getByTitle("Switch model");
    expect(chip.className).toContain("text-zinc-400");
    expect(chip.className).not.toMatch(/font-(medium|semibold|bold)/);
  });

  it("a picked model with no label reads by name too", async () => {
    H.getResponses["/models"] = {
      models: [{ provider: "anthropic", model: "claude-sonnet-5", name: "Anthropic", kind: "api", available: true }],
    };
    render(<ChatPage />);
    fireEvent.click(await screen.findByTitle("Switch model"));
    fireEvent.change(await screen.findByTestId("model-filter"), { target: { value: "sonnet" } });
    fireEvent.click((await screen.findAllByTestId("model-match"))[0]);
    const words = await screen.findByTestId("model-chip-words");
    await waitFor(() => expect(words.textContent).toBe("Sonnet 5"));
  });
});

/* ======================================================= reasoning chip */

describe("the reasoning chip shows on the default model when it offers levels", () => {
  it("is drawn with no pick, starts at the model's own default, and a pick rides the turn", async () => {
    H.getResponses["/models"] = {
      models: [
        { provider: "anthropic", model: DEFAULT_MODEL, name: "Anthropic", kind: "api", available: true, reasoning: ["low", "medium", "high"] },
      ],
    };
    render(<ChatPage />);
    const select = (await screen.findByTestId("reasoning-level")) as HTMLSelectElement;
    expect(select.value).toBe("");
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["", "low", "medium", "high"]);
    fireEvent.change(select, { target: { value: "high" } });
    const box = await screen.findByPlaceholderText(/Message Iron Jarvis/);
    fireEvent.change(box, { target: { value: "think hard" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(H.bodies.length).toBeGreaterThan(0));
    const body = H.bodies[H.bodies.length - 1];
    expect(body.reasoning).toBe("high");
    // Still the default route: no provider or model was picked.
    expect(body.provider).toBeUndefined();
    expect(body.model).toBeUndefined();
  });

  it("CONTROL: a default whose row offers no levels draws nothing and sends none", async () => {
    H.getResponses["/models"] = {
      models: [{ provider: "anthropic", model: DEFAULT_MODEL, name: "Anthropic", kind: "api", available: true, reasoning: [] }],
    };
    render(<ChatPage />);
    await screen.findByTestId("model-chip-words");
    await waitFor(() => expect(screen.getByTestId("model-chip-words").textContent).toBe("Default: Opus 4.8"));
    expect(screen.queryByTestId("reasoning-level")).toBeNull();
  });

  it("CONTROL: an Auto default matches no row and draws nothing", async () => {
    setHealth("auto", "");
    H.getResponses["/models"] = {
      models: [
        { provider: "anthropic", model: DEFAULT_MODEL, name: "Anthropic", kind: "api", available: true, reasoning: ["low", "high"] },
      ],
    };
    render(<ChatPage />);
    await screen.findByTestId("model-chip-words");
    expect(screen.queryByTestId("reasoning-level")).toBeNull();
  });
});

/* ===================================================== expanded receipt */

describe("the expanded receipt: whole pixels and tone tokens", () => {
  it("a warning route, a failed step, a denial and an undo error use no half pixels and no literal hues", async () => {
    const onUndo = vi.fn(async () => {
      throw new Error("could not undo");
    });
    render(
      <TurnReceipt
        route={{ requested: "anthropic", provider: "claude-cli", model: "claude-opus-4-8", reason: "failover", from: "anthropic", why: "http 500" }}
        deniedTools={["shell"]}
        documents={["C:/w/a.md"]}
        undoFor={() => ({ actionId: "u1", undoable: true })}
        onUndo={onUndo}
        steps={[{ name: "write_file", ms: 1200, ok: false }]}
        timing={{ startedAt: 1000, firstTokenAt: 1400, endedAt: 4000 }}
        usage={{ input_tokens: 1000, output_tokens: 200 }}
        outputTokens={200}
      />,
    );
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    const detail = screen.getByTestId("turn-receipt-detail");
    fireEvent.click(screen.getByRole("button", { name: /Undo the write to a\.md/ }));
    await waitFor(() => expect(detail.textContent).toContain("could not undo"));
    const all = [detail, ...Array.from(detail.querySelectorAll("*"))];
    for (const el of all) {
      const cls = el.getAttribute("class") ?? "";
      expect(cls, `class on <${el.tagName.toLowerCase()}>`).not.toMatch(HALF_PIXEL);
      expect(cls, `class on <${el.tagName.toLowerCase()}>`).not.toMatch(LITERAL_HUE);
    }
    // Anti-vacuity: the warnings are still drawn in the warning tones.
    expect(screen.getByText("blocked: shell").className).toContain("text-tone-warn");
    expect(screen.getByText("could not undo").className).toContain("text-tone-danger");
    expect(detail.querySelector('[data-testid="turn-step"][data-ok="false"]')?.className).toContain("text-tone-danger");
  });
});

/* ============================================================ dock ask */

describe("the dock ask speaks in plain sentences", () => {
  it("no em-dash aside on a single ask, a batch, or the waiting line", () => {
    expect(waitingLine(0)).toBe("Waiting for you. Nothing runs until you answer.");
    expect(waitingLine(600)).toBe("Waiting for you. If nobody answers within 10 min, it is not run.");
    const one = render(
      <ApprovalCard approval={{ id: "a1", callId: "c1", tool: "shell", args: { command: "git status" } }} docked />,
    );
    const card = screen.getByTestId("chat-approval-card");
    expect(card.textContent).toContain("(shell). Your call.");
    expect(card.textContent).not.toContain("—");
    one.unmount();
    render(
      <ApprovalCard
        approval={{
          id: "a2", callId: "c2", tool: "rename_file", count: 3,
          examples: [{ path: "a" }, { path: "b" }, { path: "c" }],
        }}
        docked
      />,
    );
    const batch = screen.getByTestId("chat-approval-card");
    expect(batch.textContent).toContain("× 3. One answer covers all of them.");
    expect(batch.textContent).not.toContain("—");
  });

  it("the card uses tone tokens and whole pixels, docked or not", () => {
    for (const docked of [true, false]) {
      const { unmount } = render(
        <ApprovalCard approval={{ id: "a3", callId: "c3", tool: "write_file", args: { path: "x.md" } }} docked={docked} />,
      );
      const card = screen.getByTestId("chat-approval-card");
      for (const el of [card, ...Array.from(card.querySelectorAll("*"))]) {
        const cls = el.getAttribute("class") ?? "";
        expect(cls).not.toMatch(HALF_PIXEL);
        expect(cls).not.toMatch(LITERAL_HUE);
      }
      // Anti-vacuity: the decline still reads as a warning colour.
      expect(screen.getByRole("button", { name: "Deny" }).className).toContain("text-tone-danger");
      unmount();
    }
  });
});

/* ========================================================== thread menu */

describe("the thread menu: whole pixels, tone tokens, plain copy", () => {
  it("its items, the delete row and the no-projects note follow the rules (source pin)", () => {
    const page = read("app/chat/page.tsx");
    const start = page.indexOf("const item =\n");
    const end = page.indexOf('{deleteArmedId === mt.id ? "Delete for good? Press again" : "Delete chat"}');
    expect(start).toBeGreaterThan(0);
    expect(end).toBeGreaterThan(start);
    const menu = page.slice(start, end + 200);
    expect(menu).not.toMatch(HALF_PIXEL);
    expect(menu).not.toMatch(LITERAL_HUE);
    expect(menu).toContain("No projects yet. Make one with the Project");
    expect(menu).not.toContain("No projects yet —");
    // Anti-vacuity: the delete row still warns, in the tone token.
    expect(menu).toMatch(/text-\[13px\] text-tone-danger transition-colors hover:bg-tone-danger\/10/);
  });
});
