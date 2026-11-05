/**
 * v1.435.0 (chat-first plan, Wave 3 group I, decision D3): Local only changed
 * FROM CHAT, and "Start new chats local only".
 *
 *  - the daemon's `chat_local_only` answers with a "Local only changed" card
 *    (key `chat.local_only`); the page's switch follows it, so this chat's
 *    next turn says `local_only: true` and its save carries it (without this
 *    the page's save would put the old value back);
 *  - an Undo of that card puts the switch back;
 *  - a held chat (Microsoft 365) never turns off through a card;
 *  - a NEW chat starts on when the setting `chat_new_local_only` is on;
 *    a press on the switch before the setting arrives is never overridden.
 *
 * Harness: the local-only-chat-v1332 page mocks (transport hooks mocked at the
 * usual seams) with a `stream.run` whose result the test sets.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
      settingsHold: null as null | Promise<void>,
    },
    run: {
      bodies: [] as Record<string, unknown>[],
      results: [] as Record<string, unknown>[],
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
    if (path === "/settings" && H.api.settingsHold) await H.api.settingsHold;
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
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async (body: Record<string, unknown>) => {
      H.run.bodies.push(body);
      return H.run.results.shift() ?? { reply: "Noted.", tools_used: [] };
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
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { LOCAL_ONLY_KEY } from "@/lib/localOnly";
import {
  LOCAL_ONLY_CARD_KEY,
  NEW_CHATS_LOCAL_ONLY_SETTING,
  localOnlyAfterUndo,
  localOnlyFromCards,
  newChatLocalOnly,
} from "@/lib/localOnlyChat";
import type { ConfigCard } from "@/lib/configCards";
import { NEW_CHAT_EVENT } from "@/lib/sidebarSlot";

const LOCAL = { provider: "ollama", model: "qwen3:14b", kind: "local", available: true };

function card(old: string, next: string, extra: Partial<ConfigCard> = {}): ConfigCard {
  return {
    kind: "change",
    title: "Local only changed",
    key: LOCAL_ONLY_CARD_KEY,
    label: "This chat: Local only",
    old,
    new: next,
    change_id: "rec_lo",
    ...extra,
  } as ConfigCard;
}

async function chip(): Promise<HTMLButtonElement> {
  await screen.findByRole("textbox", { name: "Message" });
  return (await screen.findByTestId("local-only-chip")) as HTMLButtonElement;
}

async function send(text: string): Promise<Record<string, unknown>> {
  const n = H.run.bodies.length;
  const b = (await screen.findByRole("textbox", { name: "Message" })) as HTMLTextAreaElement;
  fireEvent.change(b, { target: { value: text } });
  fireEvent.keyDown(b, { key: "Enter" });
  await waitFor(() => expect(H.run.bodies.length).toBe(n + 1));
  return H.run.bodies[n];
}

function readRepo(rel: string): string {
  return readFileSync(join(__dirname, "..", "..", rel), "utf8").replace(/\r\n/g, "\n");
}

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.puts.length = 0;
  H.api.posts.length = 0;
  H.api.settingsHold = null;
  H.run.bodies.length = 0;
  H.run.results.length = 0;
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

/* ===================================================== the pure helpers */

describe("lib/localOnlyChat", () => {
  it("names the daemon's card key and setting exactly", () => {
    const py = readRepo("src/iron_jarvis/settings/records_history.py");
    expect(py).toContain(`LOCAL_ONLY_CARD_KEY = "${LOCAL_ONLY_CARD_KEY}"`);
    const schema = readRepo("src/iron_jarvis/settings/schema.py");
    expect(schema).toContain(`S("${NEW_CHATS_LOCAL_ONLY_SETTING}", "Start new chats local only"`);
  });

  it("a new chat is on when the setting is, else the user's last choice", () => {
    expect(newChatLocalOnly(false, true)).toBe(true);
    expect(newChatLocalOnly(true, false)).toBe(true);
    expect(newChatLocalOnly(false, false)).toBe(false);
  });

  it("reads the newest Local only card that is not undone, and nothing else", () => {
    expect(localOnlyFromCards([card("off", "on")])).toBe(true);
    expect(localOnlyFromCards([card("off", "on"), card("on", "off")])).toBe(false);
    expect(localOnlyFromCards([card("off", "on", { undone: true })])).toBeNull();
    expect(localOnlyFromCards([card("off", "on", { key: "schedule.nightly" })])).toBeNull();
    expect(localOnlyFromCards(undefined)).toBeNull();
  });

  it("an undone card puts back its old state", () => {
    expect(localOnlyAfterUndo(card("off", "on", { undone: true }))).toBe(false);
    expect(localOnlyAfterUndo(card("on", "off", { undone: true }))).toBe(true);
    expect(localOnlyAfterUndo(card("off", "on"))).toBeNull();
    expect(localOnlyAfterUndo(card("off", "on", { key: "x", undone: true }))).toBeNull();
  });
});

/* ============================================= turned on / off from chat */

describe("Local only changed from chat (the daemon's card)", () => {
  it("turned on in a turn: the switch follows, the next turn and the save say local_only", async () => {
    H.run.results.push({ reply: "Done, Local only is on.", tools_used: ["chat_local_only"], configCards: [card("off", "on")] });
    render(<ChatPage />);
    expect((await chip()).getAttribute("aria-pressed")).toBe("false");
    expect((await send("keep this chat on my PC")).local_only).toBe(false);
    await waitFor(async () => expect((await chip()).getAttribute("aria-pressed")).toBe("true"));
    await waitFor(() =>
      expect(
        H.api.puts.some((p) => (p.body.setup as Record<string, unknown> | undefined)?.local_only === true),
      ).toBe(true),
    );
    expect((await send("next")).local_only).toBe(true);
    // The user's default for NEW chats is not touched by a chat's own switch.
    expect(window.localStorage.getItem(LOCAL_ONLY_KEY)).toBeNull();
  });

  it("Undo on the card puts the switch back, and the next turn says so", async () => {
    H.run.results.push({ reply: "Local only is on.", tools_used: ["chat_local_only"], configCards: [card("off", "on")] });
    H.api.responses["/config/changes/rec_lo"] = { action_id: "tool_lo", undone: false };
    render(<ChatPage />);
    await send("keep this chat on my PC");
    await waitFor(async () => expect((await chip()).getAttribute("aria-pressed")).toBe("true"));
    const changed = await screen.findByTestId("config-card-change");
    fireEvent.click(Array.from(changed.querySelectorAll("button")).find((b) => /undo/i.test(b.textContent ?? ""))!);
    await screen.findByTestId("config-card-undone");
    expect(H.api.posts.some((p) => p.path === "/undo/tool_lo")).toBe(true);
    await waitFor(async () => expect((await chip()).getAttribute("aria-pressed")).toBe("false"));
    expect((await send("next")).local_only).toBe(false);
  });

  it("CONTROL: a turn with no Local only card leaves the switch as it was", async () => {
    H.run.results.push({ reply: "Noted.", tools_used: [], configCards: [card("off", "on", { key: "schedule.nightly", title: "Schedule changed" })] });
    render(<ChatPage />);
    await send("move my schedule");
    await screen.findAllByText("Noted.");
    expect((await chip()).getAttribute("aria-pressed")).toBe("false");
    expect((await send("next")).local_only).toBe(false);
  });

  it("turned off in a turn: the switch goes off", async () => {
    window.localStorage.setItem(LOCAL_ONLY_KEY, "1");
    H.run.results.push({ reply: "Local only is off.", tools_used: ["chat_local_only"], configCards: [card("on", "off")] });
    render(<ChatPage />);
    await waitFor(async () => expect((await chip()).getAttribute("aria-pressed")).toBe("true"));
    expect((await send("turn off local only")).local_only).toBe(true);
    await waitFor(async () => expect((await chip()).getAttribute("aria-pressed")).toBe("false"));
    expect((await send("next")).local_only).toBe(false);
  });

  it("a chat held by an app never turns off through a card", async () => {
    H.api.responses["/chat/threads/t9"] = {
      id: "t9",
      title: "Client mail",
      messages: [
        { role: "user", content: "what did the client send" },
        { role: "assistant", content: "mail reply" },
      ],
      setup: { local_only: true, local_only_holds: ["microsoft_365"] },
    };
    window.history.replaceState({}, "", "/chat?thread=t9");
    H.run.results.push({ reply: "x", tools_used: [], configCards: [card("on", "off")] });
    render(<ChatPage />);
    await screen.findByText("mail reply");
    await waitFor(async () => expect((await chip()).getAttribute("data-held")).toBe("true"));
    await send("turn off local only");
    await screen.findAllByText("x");
    expect((await chip()).getAttribute("aria-pressed")).toBe("true");
  });
});

/* ======================================== Start new chats local only */

describe("Start new chats local only (setting chat_new_local_only)", () => {
  it("a new chat starts on when the setting is on, and its turn says so", async () => {
    H.api.responses["/settings"] = { settings: { chat_new_local_only: true } };
    render(<ChatPage />);
    await waitFor(async () => expect((await chip()).getAttribute("aria-pressed")).toBe("true"));
    expect((await send("hello")).local_only).toBe(true);
  });

  it("New chat starts on again after a chat the user turned off", async () => {
    H.api.responses["/settings"] = { settings: { chat_new_local_only: true } };
    render(<ChatPage />);
    await waitFor(async () => expect((await chip()).getAttribute("aria-pressed")).toBe("true"));
    fireEvent.click(await chip());
    await waitFor(async () => expect((await chip()).getAttribute("aria-pressed")).toBe("false"));
    act(() => {
      window.dispatchEvent(new Event(NEW_CHAT_EVENT));
    });
    await waitFor(async () => expect((await chip()).getAttribute("aria-pressed")).toBe("true"));
  });

  it("CONTROL: the setting off leaves a new chat on the user's last choice (off)", async () => {
    H.api.responses["/settings"] = { settings: { chat_new_local_only: false } };
    render(<ChatPage />);
    await waitFor(() => expect(H.api.gets).toContain("/settings"));
    expect((await chip()).getAttribute("aria-pressed")).toBe("false");
    expect((await send("hello")).local_only).toBe(false);
  });

  it("a press before the setting arrives is the user's choice and is never overridden", async () => {
    let release: () => void = () => {};
    H.api.settingsHold = new Promise<void>((ok) => {
      release = ok;
    });
    H.api.responses["/settings"] = { settings: { chat_new_local_only: true } };
    render(<ChatPage />);
    fireEvent.click(await chip()); // on
    fireEvent.click(await chip()); // and off again: the user's choice
    await waitFor(async () => expect((await chip()).getAttribute("aria-pressed")).toBe("false"));
    await act(async () => {
      release();
    });
    await waitFor(() => expect(H.api.gets).toContain("/settings"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect((await chip()).getAttribute("aria-pressed")).toBe("false");
  });

  it("an opened saved chat keeps its own setting whatever the default", async () => {
    H.api.responses["/settings"] = { settings: { chat_new_local_only: true } };
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
    await waitFor(() => expect(H.api.gets).toContain("/settings"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect((await chip()).getAttribute("aria-pressed")).toBe("false");
  });
});
