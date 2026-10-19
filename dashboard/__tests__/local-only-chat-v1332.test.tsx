/**
 * v1.332.0: "Local only" chats, the dashboard half (L2).
 *
 * The user asked for "a local toggle in the chat to indicate nothing hits the
 * cloud in a specific session without user permission". The daemon enforces
 * it on every model call; the page is the friendly front door. Pinned here,
 * with ChatPage rendered for real:
 *  - the composer chip right after the permission chip: a real button with
 *    aria-pressed, quiet when off, the success tone when on, its words on
 *    hover; the user's choice for new chats (`ij.chat.localOnly`) and saved
 *    with the chat's setup;
 *  - every turn's body carries `local_only`;
 *  - a cloud model under local only: the ask-first card takes the composer's
 *    place and NOTHING is sent; "Send once" sends with `allow_cloud_once`;
 *    "Keep it local" closes and the words stay in the box; Retry and
 *    Try again ask too;
 *  - a local model sends at once, no card;
 *  - the daemon's refusal (stream error frame, or the POST lane's 409) shows
 *    its sentence with "Send to … once" and "Choose a local model", and no
 *    Retry;
 *  - the model menu's ONE quiet line, only while local only;
 *  - the ⋯ menu's row is the same switch; a saved local-only chat reopens on,
 *    and a new chat returns to the user's last choice;
 *  - the receipt says "sent with your OK" from the daemon's word only;
 *  - the ask-first card is a DockAsk kind: Enter / Esc press its buttons,
 *    and it offers no Stop (nothing is running).
 *
 * Harness: the permission-chip-page-v1327 mocks (transport hooks mocked at
 * the usual seams) with a `stream.run` that records each body and answers,
 * or throws what the test queued.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    body?: unknown;
    constructor(message: string, status = 500, _cancelled = false, body?: unknown) {
      super(message);
      this.status = status;
      this.name = "ApiError";
      if (body !== undefined) this.body = body;
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
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
      postResponses: {} as Record<string, unknown>,
    },
    run: {
      bodies: [] as Record<string, unknown>[],
      fail: [] as Error[],
      // Review: hold the next turn open (a reply still streaming) until
      // `settle` is called, so a message can be queued behind it.
      hold: false,
      settle: null as null | ((v: Record<string, unknown>) => void),
    },
    health: { byProvider: {} as Record<string, boolean>, defaultProvider: "" },
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
    run: async (body: Record<string, unknown>) => {
      H.run.bodies.push(body);
      const f = H.run.fail.shift();
      if (f) throw f;
      if (H.run.hold) {
        H.run.hold = false;
        return new Promise<Record<string, unknown>>((ok) => {
          H.run.settle = ok;
        });
      }
      return { reply: "Noted.", tools_used: [] };
    },
    abort: () => {},
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
  useProviderHealth: () => ({ ...H.health, loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { TurnReceipt, type TurnRoute } from "@/components/chat/TurnReceipt";
import { DockAsk } from "@/components/chat/DockAsk";
import { collectDockAsks, dockAskKeyHint } from "@/lib/dockAsk";
import {
  LOCAL_ONLY_KEY,
  cloudAskFor,
  localOnlyRefusalFrom,
  turnModel,
} from "@/lib/localOnly";
import { NEW_CHAT_EVENT } from "@/lib/sidebarSlot";

const LOCAL = { provider: "ollama", model: "qwen3:14b", kind: "local", available: true };
const CLOUD = {
  provider: "claude-cli",
  model: "claude-opus-4-8",
  label: "Opus 4.8",
  kind: "cli",
  available: true,
};
const REFUSAL = "This chat is local only, so nothing was sent to Claude. Pick a local model, or allow Claude for this one message.";

function classes(el: Element | null | undefined): string[] {
  return ((el?.getAttribute("class") ?? "") as string).split(/\s+/).filter(Boolean);
}

async function box(): Promise<HTMLTextAreaElement> {
  return (await screen.findByRole("textbox", { name: "Message" })) as HTMLTextAreaElement;
}

async function chip(): Promise<HTMLButtonElement> {
  await box();
  return (await screen.findByTestId("local-only-chip")) as HTMLButtonElement;
}

/** Pick a model through the composer's model menu (type, Enter). */
async function pickModel(words: string): Promise<void> {
  fireEvent.click(await screen.findByTitle("Switch model"));
  const filter = (await screen.findByTestId("model-filter")) as HTMLInputElement;
  fireEvent.change(filter, { target: { value: words } });
  fireEvent.keyDown(filter, { key: "Enter" });
  await waitFor(() => expect(screen.queryByTestId("model-filter")).toBeNull());
}

/** Type a message and press Enter (no wait: the caller says what follows). */
async function type(text: string): Promise<void> {
  const b = await box();
  fireEvent.change(b, { target: { value: text } });
  fireEvent.keyDown(b, { key: "Enter" });
}

/** Type, press Enter, and wait for the turn's body. */
async function send(text: string): Promise<Record<string, unknown>> {
  const n = H.run.bodies.length;
  await type(text);
  await waitFor(() => expect(H.run.bodies.length).toBe(n + 1));
  await screen.findAllByText("Noted.");
  return H.run.bodies[n];
}

/** A refusal as the stream lane throws it (an error frame with code). */
function refusal(): Error {
  return Object.assign(new H.FakeStreamError(REFUSAL, 0), {
    code: "local_only",
    provider: "claude-cli",
    model: "claude-opus-4-8",
  });
}

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.puts.length = 0;
  H.api.posts.length = 0;
  H.run.bodies.length = 0;
  H.run.fail.length = 0;
  H.run.hold = false;
  H.run.settle = null;
  H.health = { byProvider: {}, defaultProvider: "" };
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.responses = {
    "/models": { models: [LOCAL, CLOUD] },
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
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/* ============================================================== the chip */

describe("the Local only chip", () => {
  it("sits right after the permission chip as a real toggle button, quiet when off", async () => {
    render(<ChatPage />);
    const c = await chip();
    expect(c.tagName).toBe("BUTTON");
    expect(c.getAttribute("aria-pressed")).toBe("false");
    expect(c.getAttribute("title")).toBe("Keep this chat on this PC.");
    expect(classes(c)).toContain("text-zinc-400");
    expect(classes(c)).not.toContain("text-tone-success");
    expect(screen.getByTestId("composer-toolbar").contains(c)).toBe(true);
    // Right after the permission chip: the next element in the toolbar.
    expect(screen.getByTestId("permission-chip-wrap").nextElementSibling).toBe(c);
    // v1.356.0 (G75): a phone shows its word when off too (it showed the
    // house alone) — updated on purpose; the desktop words are unchanged.
    const words = Array.from(c.querySelectorAll("span"));
    expect(words.map((w) => w.textContent)).toEqual(["Local", "Local only"]);
    expect(classes(words[0])).toContain("sm:hidden");
    expect(classes(words[1])).toContain("hidden");
  });

  it("a press turns it on: pressed, the success tone, the on words, remembered, saved with the chat", async () => {
    render(<ChatPage />);
    fireEvent.click(await chip());
    const c = await chip();
    expect(c.getAttribute("aria-pressed")).toBe("true");
    expect(classes(c)).toContain("text-tone-success");
    expect(c.getAttribute("title")).toBe("Nothing in this chat leaves this PC without asking you.");
    // A phone says "Local" beside the house when on.
    expect(Array.from(c.querySelectorAll("span")).map((w) => w.textContent)).toEqual(["Local", "Local only"]);
    expect(window.localStorage.getItem(LOCAL_ONLY_KEY)).toBe("1");
    const body = await send("hello");
    expect(body.local_only).toBe(true);
    await waitFor(() =>
      expect(
        H.api.puts.some((p) => (p.body.setup as Record<string, unknown> | undefined)?.local_only === true),
      ).toBe(true),
    );
  });

  it("CONTROL: off, every turn still says so (local_only false), and no card ever shows", async () => {
    render(<ChatPage />);
    await pickModel("opus");
    const body = await send("hello");
    expect(body.local_only).toBe(false);
    expect("allow_cloud_once" in body).toBe(false);
    expect(screen.queryByTestId("cloud-ask-card")).toBeNull();
  });

  it("a new chat starts on the user's last choice", async () => {
    window.localStorage.setItem(LOCAL_ONLY_KEY, "1");
    render(<ChatPage />);
    await waitFor(async () => expect((await chip()).getAttribute("aria-pressed")).toBe("true"));
    expect((await send("hi")).local_only).toBe(true);
  });

  it("the ⋯ menu's row is the same switch, checked while on", async () => {
    render(<ChatPage />);
    fireEvent.click(await screen.findByTestId("chat-more-trigger"));
    const row = await screen.findByTestId("chat-more-local-only");
    expect(row.textContent).toContain("Keep this chat on this PC");
    expect(row.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(row);
    await waitFor(async () => expect((await chip()).getAttribute("aria-pressed")).toBe("true"));
    expect(screen.getByTestId("chat-more-local-only").getAttribute("aria-pressed")).toBe("true");
    expect(window.localStorage.getItem(LOCAL_ONLY_KEY)).toBe("1");
  });

  it("a saved local-only chat reopens on; New chat goes back to the last choice", async () => {
    H.api.responses["/chat/threads/t7"] = {
      id: "t7",
      title: "Payroll",
      messages: [
        { role: "user", content: "earlier" },
        { role: "assistant", content: "earlier reply" },
      ],
      setup: { local_only: true },
    };
    window.history.replaceState({}, "", "/chat?thread=t7");
    render(<ChatPage />);
    await screen.findByText("earlier reply");
    await waitFor(async () => expect((await chip()).getAttribute("aria-pressed")).toBe("true"));
    act(() => {
      window.dispatchEvent(new Event(NEW_CHAT_EVENT));
    });
    await waitFor(async () => expect((await chip()).getAttribute("aria-pressed")).toBe("false"));
  });

  it("CONTROL: a saved chat without local_only reopens off, even when the last choice was on", async () => {
    window.localStorage.setItem(LOCAL_ONLY_KEY, "1");
    H.api.responses["/chat/threads/t8"] = {
      id: "t8",
      title: "Notes",
      messages: [
        { role: "user", content: "a" },
        { role: "assistant", content: "b reply" },
      ],
      setup: { tools: [] },
    };
    window.history.replaceState({}, "", "/chat?thread=t8");
    render(<ChatPage />);
    await screen.findByText("b reply");
    await waitFor(async () => expect((await chip()).getAttribute("aria-pressed")).toBe("false"));
  });
});

/* ======================================================= asking first */

describe("a cloud model in a local-only chat asks first", () => {
  async function onWithCloud(): Promise<void> {
    render(<ChatPage />);
    fireEvent.click(await chip());
    await pickModel("opus");
  }

  it("the card takes the composer's place and NOTHING is sent", async () => {
    await onWithCloud();
    await type("summarize the payroll file");
    const card = await screen.findByTestId("cloud-ask-card");
    expect(within(card).getByText("This chat is local only")).toBeTruthy();
    expect(screen.getByTestId("cloud-ask-body").textContent).toBe(
      "Send this message to Opus 4.8? It will leave this PC.",
    );
    expect(within(card).getByRole("button", { name: "Send once" })).toBeTruthy();
    expect(within(card).getByRole("button", { name: "Keep it local" })).toBeTruthy();
    expect(screen.getByTestId("dock-ask").getAttribute("data-kind")).toBe("cloud");
    expect(classes(screen.getByTestId("chat-composer"))).toContain("hidden");
    // Nothing is running behind it, so the dock offers no Stop.
    expect(screen.queryByTestId("dock-ask-stop")).toBeNull();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(H.run.bodies).toHaveLength(0);
    expect(H.api.posts.filter((p) => p.path === "/chat")).toHaveLength(0);
  });

  it("Send once sends this message with allow_cloud_once for that provider", async () => {
    await onWithCloud();
    await type("summarize the payroll file");
    fireEvent.click(await screen.findByRole("button", { name: "Send once" }));
    await waitFor(() => expect(H.run.bodies).toHaveLength(1));
    const body = H.run.bodies[0];
    expect(body.allow_cloud_once).toBe("claude-cli");
    expect(body.provider).toBe("claude-cli");
    expect(body.model).toBe("claude-opus-4-8");
    expect(body.local_only).toBe(true);
    await screen.findAllByText("Noted.");
    expect(screen.queryByTestId("cloud-ask-card")).toBeNull();
  });

  it("the OK is for ONE message: the next one asks again", async () => {
    await onWithCloud();
    await type("first");
    fireEvent.click(await screen.findByRole("button", { name: "Send once" }));
    await screen.findAllByText("Noted.");
    await type("second");
    await screen.findByTestId("cloud-ask-card");
    expect(H.run.bodies).toHaveLength(1);
  });

  it("Keep it local closes the card, sends nothing, and the words stay in the box", async () => {
    await onWithCloud();
    await type("summarize the payroll file");
    fireEvent.click(await screen.findByRole("button", { name: "Keep it local" }));
    await waitFor(() => expect(screen.queryByTestId("cloud-ask-card")).toBeNull());
    expect((await box()).value).toBe("summarize the payroll file");
    expect(classes(screen.getByTestId("chat-composer"))).not.toContain("hidden");
    expect(H.run.bodies).toHaveLength(0);
  });

  it("Enter presses Send once and Esc presses Keep it local, like the other dock questions", async () => {
    await onWithCloud();
    await type("one");
    const dock = await screen.findByTestId("dock-ask");
    // The dock ignores a key in the first moment after a question appears.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 450));
    });
    fireEvent.keyDown(dock, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("cloud-ask-card")).toBeNull());
    expect(H.run.bodies).toHaveLength(0);
    await type("one");
    const again = await screen.findByTestId("dock-ask");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 450));
    });
    fireEvent.keyDown(again, { key: "Enter" });
    await waitFor(() => expect(H.run.bodies).toHaveLength(1));
    expect(H.run.bodies[0].allow_cloud_once).toBe("claude-cli");
  });

  it("a local model sends at once: no card, no allow_cloud_once", async () => {
    render(<ChatPage />);
    fireEvent.click(await chip());
    await pickModel("qwen");
    const body = await send("tidy my notes");
    expect(body.provider).toBe("ollama");
    expect(body.local_only).toBe(true);
    expect("allow_cloud_once" in body).toBe(false);
    expect(screen.queryByTestId("cloud-ask-card")).toBeNull();
  });

  it("Retry of a failed turn on a cloud model asks first too", async () => {
    render(<ChatPage />);
    await pickModel("opus");
    H.run.fail.push(new H.FakeStreamError("the model timed out", 0));
    await type("hello");
    await screen.findByTestId("chat-turn-error");
    fireEvent.click(await chip()); // now local only
    fireEvent.click(await screen.findByRole("button", { name: /Retry/ }));
    await screen.findByTestId("cloud-ask-card");
    expect(H.run.bodies).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Send once" }));
    await waitFor(() => expect(H.run.bodies).toHaveLength(2));
    expect(H.run.bodies[1].allow_cloud_once).toBe("claude-cli");
  });

  it("Try again with a cloud model from a local chat asks first, then sends that model once", async () => {
    render(<ChatPage />);
    fireEvent.click(await chip());
    await pickModel("qwen");
    await send("tidy my notes");
    fireEvent.click(await screen.findByRole("button", { name: "Try again with another model" }));
    const opus = (await screen.findAllByTestId("regen-item")).find(
      (el) => el.getAttribute("data-choice") === "claude-cli::claude-opus-4-8",
    );
    expect(opus).toBeTruthy();
    fireEvent.click(opus!);
    const card = await screen.findByTestId("cloud-ask-card");
    expect(card.getAttribute("data-provider")).toBe("claude-cli");
    expect(H.run.bodies).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Send once" }));
    await waitFor(() => expect(H.run.bodies).toHaveLength(2));
    expect(H.run.bodies[1].provider).toBe("claude-cli");
    expect(H.run.bodies[1].allow_cloud_once).toBe("claude-cli");
  });

  it("a message queued behind a reply goes back to the box instead of leaving this PC unasked", async () => {
    await onWithCloud();
    H.run.hold = true;
    await type("first");
    fireEvent.click(await screen.findByRole("button", { name: "Send once" }));
    await waitFor(() => expect(H.run.settle).not.toBeNull());
    const b = await box();
    fireEvent.change(b, { target: { value: "second" } });
    fireEvent.keyDown(b, { key: "Enter", ctrlKey: true });
    expect((await screen.findByTestId("queued-message")).textContent).toContain("second");
    act(() => {
      H.run.settle?.({ reply: "Noted.", tools_used: [] });
    });
    await waitFor(() => expect(screen.queryByTestId("queued-message")).toBeNull());
    await waitFor(async () => expect((await box()).value).toBe("second"));
    expect(H.run.bodies).toHaveLength(1);
    expect(screen.queryByTestId("cloud-ask-card")).toBeNull();
  });
});

/* ===================================================== the daemon says no */

describe("the daemon's refusal", () => {
  async function refusedTurn(): Promise<void> {
    render(<ChatPage />);
    fireEvent.click(await chip());
    await pickModel("qwen");
    H.run.fail.push(refusal());
    await type("read this scan");
    await screen.findByTestId("chat-turn-error");
  }

  it("shows the server's sentence with the two quiet actions, and no Retry", async () => {
    await refusedTurn();
    expect(screen.getByTestId("chat-turn-error").textContent).toBe(REFUSAL);
    expect(screen.getByTestId("local-refusal-send-once").textContent).toBe("Send to Opus 4.8 once");
    expect(screen.getByTestId("local-refusal-choose-local").textContent).toBe("Choose a local model");
    expect(screen.queryByRole("button", { name: /^Retry/ })).toBeNull();
    expect(screen.queryByText("Retry with the default model")).toBeNull();
  });

  it("Send to … once resends that turn on the refused model with allow_cloud_once", async () => {
    await refusedTurn();
    fireEvent.click(screen.getByTestId("local-refusal-send-once"));
    await waitFor(() => expect(H.run.bodies).toHaveLength(2));
    const body = H.run.bodies[1];
    expect(body.allow_cloud_once).toBe("claude-cli");
    expect(body.provider).toBe("claude-cli");
    expect(body.model).toBe("claude-opus-4-8");
    // The same question, not a new one.
    const msgs = body.messages as { role: string; content: string }[];
    expect(msgs[msgs.length - 1]).toEqual({ role: "user", content: "read this scan" });
    await screen.findAllByText("Noted.");
    expect(screen.queryByTestId("local-refusal-send-once")).toBeNull();
  });

  it("Choose a local model opens the model menu, which says the chat is local only", async () => {
    await refusedTurn();
    fireEvent.click(screen.getByTestId("local-refusal-choose-local"));
    const menu = await screen.findByTestId("model-menu");
    expect(within(menu).getByTestId("model-menu-local-only").textContent).toBe(
      "Local only · cloud models ask first",
    );
  });

  it("the POST lane's 409 reads the same way", async () => {
    render(<ChatPage />);
    fireEvent.click(await chip());
    await pickModel("qwen");
    H.run.fail.push(new H.FakeStreamError("not found", 404)); // no /chat/stream
    H.api.postResponses["/chat"] = new H.FakeApiError(REFUSAL, 409, false, {
      detail: REFUSAL,
      code: "local_only",
      provider: "claude-cli",
      model: "claude-opus-4-8",
    });
    await type("read this scan");
    await screen.findByTestId("chat-turn-error");
    expect(screen.getByTestId("chat-turn-error").textContent).toBe(REFUSAL);
    expect(screen.getByTestId("local-refusal-send-once").textContent).toBe("Send to Opus 4.8 once");
  });

  it("CONTROL: an ordinary failure keeps Retry and offers no local-only actions", async () => {
    render(<ChatPage />);
    fireEvent.click(await chip());
    await pickModel("qwen");
    H.run.fail.push(new H.FakeStreamError("the model timed out", 0));
    await type("hello");
    await screen.findByTestId("chat-turn-error");
    expect(screen.getByRole("button", { name: /Retry/ })).toBeTruthy();
    expect(screen.queryByTestId("local-refusal-send-once")).toBeNull();
  });
});

/* ============================================ "Retry with the default model" */

describe("Retry with the default model", () => {
  const CODEX = { provider: "codex-cli", model: "gpt-5.5", kind: "cli", available: true };

  async function failedOnDownPick(): Promise<void> {
    H.api.responses["/models"] = { models: [LOCAL, CLOUD, CODEX] };
    render(<ChatPage />);
    await pickModel("opus");
    H.run.fail.push(new H.FakeStreamError("claude-cli is not reachable", 0));
    await type("hello");
    await screen.findByTestId("chat-turn-error");
  }

  it("is hidden while the chat is local only and the default would leave this PC", async () => {
    H.health = { byProvider: { "claude-cli": false, "codex-cli": true }, defaultProvider: "codex-cli" };
    await failedOnDownPick();
    // CONTROL first: off, the page offers it (the v1.275.0 case).
    expect(screen.getByText("Retry with the default model")).toBeTruthy();
    fireEvent.click(await chip());
    await waitFor(() => expect(screen.queryByText("Retry with the default model")).toBeNull());
  });

  it("stays when the default is a local model", async () => {
    H.health = { byProvider: { "claude-cli": false, ollama: true }, defaultProvider: "ollama" };
    await failedOnDownPick();
    fireEvent.click(await chip());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(screen.getByText("Retry with the default model")).toBeTruthy();
  });

  it("pressed in a local-only chat with a local default: no card, and the resend names no model (the default)", async () => {
    H.health = { byProvider: { "claude-cli": false, ollama: true }, defaultProvider: "ollama" };
    await failedOnDownPick();
    fireEvent.click(await chip());
    fireEvent.click(await screen.findByText("Retry with the default model"));
    await waitFor(() => expect(H.run.bodies).toHaveLength(2));
    expect(screen.queryByTestId("cloud-ask-card")).toBeNull();
    expect("provider" in H.run.bodies[1]).toBe(false);
    expect("allow_cloud_once" in H.run.bodies[1]).toBe(false);
  });

  it("CONTROL: off, the resend names no model either (not the pick that just failed)", async () => {
    H.health = { byProvider: { "claude-cli": false, "codex-cli": true }, defaultProvider: "codex-cli" };
    await failedOnDownPick();
    fireEvent.click(await screen.findByText("Retry with the default model"));
    await waitFor(() => expect(H.run.bodies).toHaveLength(2));
    expect(H.run.bodies[0].provider).toBe("claude-cli");
    expect("provider" in H.run.bodies[1]).toBe(false);
  });
});

/* ======================================================= the model menu */

describe("the model menu", () => {
  it("shows ONE quiet line at the top only while the chat is local only", async () => {
    render(<ChatPage />);
    fireEvent.click(await screen.findByTitle("Switch model"));
    let menu = await screen.findByTestId("model-menu");
    expect(within(menu).queryByTestId("model-menu-local-only")).toBeNull();
    fireEvent.click(await screen.findByTitle("Switch model")); // close
    fireEvent.click(await chip());
    fireEvent.click(await screen.findByTitle("Switch model"));
    menu = await screen.findByTestId("model-menu");
    const line = within(menu).getByTestId("model-menu-local-only");
    expect(menu.firstElementChild).toBe(line);
    expect(line.textContent).toBe("Local only · cloud models ask first");
    expect(classes(line)).toContain("text-zinc-500");
    expect(line.className).not.toMatch(/border|bg-/);
  });
});

/* ======================================== the other doors carry the flag */

describe("the chat's other doors carry local_only", () => {
  it("follow-up suggestions (POST /chat/followups) carry local_only and the saved chat's id", async () => {
    H.api.responses["/settings"] = { settings: { chat_followups: true } };
    H.api.postResponses["/chat/followups"] = { suggestions: [] };
    render(<ChatPage />);
    fireEvent.click(await chip());
    await send("first");
    await send("second");
    await waitFor(() =>
      expect(H.api.posts.filter((p) => p.path === "/chat/followups").length).toBeGreaterThanOrEqual(2),
    );
    const asks = H.api.posts.filter((p) => p.path === "/chat/followups");
    expect(asks.every((p) => p.body.local_only === true)).toBe(true);
    expect(asks[asks.length - 1].body.thread_id).toBe("t1");
  });

  it("CONTROL: off, a follow-up ask says nothing about local only", async () => {
    H.api.responses["/settings"] = { settings: { chat_followups: true } };
    H.api.postResponses["/chat/followups"] = { suggestions: [] };
    render(<ChatPage />);
    await send("first");
    await waitFor(() => expect(H.api.posts.some((p) => p.path === "/chat/followups")).toBe(true));
    expect("local_only" in H.api.posts.find((p) => p.path === "/chat/followups")!.body).toBe(false);
  });

  it("an @-mention round (POST /chat/panel) carries local_only", async () => {
    H.api.responses["/agents/mentionable"] = {
      agents: [
        {
          mention: "builder",
          name: "builder",
          kind: "builtin",
          source: "builtin",
          description: "",
          healthy: true,
          delegable: true,
        },
      ],
    };
    H.api.postResponses["/chat/panel"] = { mode: "panel", entries: [], spoke: [], thread_id: "room1" };
    render(<ChatPage />);
    fireEvent.click(await chip());
    await type("@builder hello");
    await waitFor(() => expect(H.api.posts.some((p) => p.path === "/chat/panel")).toBe(true));
    const panel = H.api.posts.find((p) => p.path === "/chat/panel")!;
    expect(panel.body.local_only).toBe(true);
  });
});

/* ============================================================ the receipt */

describe("the receipt says 'sent with your OK' from the daemon's word only", () => {
  const ROUTE: TurnRoute = {
    requested: "claude-cli",
    provider: "claude-cli",
    model: "claude-opus-4-8",
    reason: "explicit",
    local_only: true,
    allowed_once: true,
  };

  it("allowed_once true: a quiet part right after who answered", () => {
    render(<TurnReceipt inline route={ROUTE} modelName="Opus 4.8" />);
    const part = screen.getByTestId("turn-allowed-once");
    expect(part.textContent).toBe("sent with your OK");
    expect(classes(part)).toContain("text-zinc-500");
    expect(part.className).not.toMatch(/tone-warn|border|bg-/);
    expect(screen.getByTestId("turn-receipt").textContent ?? "").toMatch(
      /answered by Opus 4\.8\s*·\s*sent with your OK/,
    );
  });

  it.each([
    ["false", { ...ROUTE, allowed_once: false }],
    ["absent", { ...ROUTE, allowed_once: undefined }],
    ["a string", { ...ROUTE, allowed_once: "true" as unknown as boolean }],
  ])("allowed_once %s: nothing is said", (_n, route) => {
    render(<TurnReceipt inline route={route} modelName="Opus 4.8" />);
    expect(screen.queryByTestId("turn-allowed-once")).toBeNull();
  });
});

/* ========================================================= the dock kind */

describe("the ask-first card is a DockAsk kind", () => {
  const ASK = { id: "c1", provider: "claude-cli", model: "claude-opus-4-8", name: "Opus 4.8" };

  it("comes first in the dock list, and its keys line says what Enter and Esc do", () => {
    const list = collectDockAsks({ cloud: ASK });
    expect(list.map((x) => x.kind)).toEqual(["cloud"]);
    expect(dockAskKeyHint("cloud")).toBe("Enter to send once · Esc to keep it local");
    expect(collectDockAsks({ cloud: null })).toEqual([]);
  });

  it("its buttons are the dock's primary and decline, and answer the page", () => {
    const answers: boolean[] = [];
    render(
      <DockAsk
        asks={collectDockAsks({ cloud: ASK })}
        onAnswerElicitation={async () => ({ ok: true })}
        onDecideSampling={async () => true}
        onCloudAnswer={(send) => answers.push(send)}
        onStop={() => {}}
      />,
    );
    const send = screen.getByRole("button", { name: "Send once" });
    const keep = screen.getByRole("button", { name: "Keep it local" });
    expect(send.hasAttribute("data-dock-primary")).toBe(true);
    expect(keep.hasAttribute("data-dock-decline")).toBe(true);
    expect(screen.queryByTestId("dock-ask-stop")).toBeNull();
    fireEvent.click(send);
    fireEvent.click(keep);
    expect(answers).toEqual([true, false]);
  });
});

/* ====================================================== the pure helpers */

describe("lib/localOnly", () => {
  const MODELS = [LOCAL, CLOUD];

  it("the message's model: the one-turn choice, else the pick, else the project, else the default", () => {
    const all = {
      turnChoice: "a::1",
      choice: "b::2",
      projectProvider: "c",
      projectModel: "3",
      defaultProvider: "d",
      defaultModel: "4",
    };
    expect(turnModel(all)).toEqual({ provider: "a", model: "1" });
    expect(turnModel({ ...all, turnChoice: null })).toEqual({ provider: "b", model: "2" });
    expect(turnModel({ ...all, turnChoice: null, choice: "" })).toEqual({ provider: "c", model: "3" });
    expect(turnModel({ ...all, turnChoice: null, choice: "", projectProvider: "" })).toEqual({
      provider: "d",
      model: "4",
    });
  });

  it("asks for a non-local row, a row the catalog does not list (fail closed); never for a local row, Auto or nothing known", () => {
    expect(cloudAskFor("claude-cli", "claude-opus-4-8", MODELS)).toEqual({
      provider: "claude-cli",
      model: "claude-opus-4-8",
      name: "Opus 4.8",
    });
    expect(cloudAskFor("openrouter", "x", MODELS)?.provider).toBe("openrouter");
    expect(cloudAskFor("ollama", "qwen3:14b", MODELS)).toBeNull();
    // Another model of a local provider is still local (kind is per provider).
    expect(cloudAskFor("ollama", "llama3", MODELS)).toBeNull();
    expect(cloudAskFor("auto", "", MODELS)).toBeNull();
    expect(cloudAskFor("", "", MODELS)).toBeNull();
  });

  it("reads a refusal off a stream error and off a 409 body; anything else is null", () => {
    expect(localOnlyRefusalFrom(refusal())).toEqual({
      detail: REFUSAL,
      provider: "claude-cli",
      model: "claude-opus-4-8",
    });
    expect(
      localOnlyRefusalFrom(
        new H.FakeApiError(REFUSAL, 409, false, { detail: REFUSAL, code: "local_only", provider: "codex-cli" }),
      ),
    ).toEqual({ detail: REFUSAL, provider: "codex-cli", model: "" });
    expect(localOnlyRefusalFrom(new H.FakeApiError("boom", 500))).toBeNull();
    expect(localOnlyRefusalFrom(new H.FakeApiError("x", 409, false, { code: "conflict" }))).toBeNull();
    expect(localOnlyRefusalFrom(Object.assign(new Error("x"), { code: "local_only" }))).toBeNull();
    expect(localOnlyRefusalFrom(null)).toBeNull();
  });
});
