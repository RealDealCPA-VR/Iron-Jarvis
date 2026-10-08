/**
 * Calm UI redesign S3/S4 — settings and credentials in the chat page.
 *
 * A change made in chat rides the reply as a "Setting changed: old → new
 * [Undo]" card whose Undo reverses the ledger row; a credential is pasted into
 * a secure card that posts straight to the vault; a key pasted into the
 * composer is HELD (AUDIT Q9) and offered the vault instead; every turn names
 * the device that asked (per-device settings).
 *
 * Harness: the edit-and-resend page harness, with per-turn done-frame extras.
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
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: {
      bodies: [] as Record<string, unknown>[],
      replies: [] as string[],
      extras: [] as Record<string, unknown>[],
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
    if (typeof r === "function") return (r as (b: Record<string, unknown>) => unknown)(body);
    return r ?? {};
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
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
      const reply = H.stream.replies.shift() ?? "done";
      const extra = H.stream.extras.shift() ?? {};
      onDelta(reply, reply);
      return { reply, ...extra };
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
import { CredentialCard } from "@/components/chat/ConfigCards";
import { decodeConfigCards, looksLikeSecret, type ConfigCard, type SecretRequestCard } from "@/lib/configCards";
import { readFileSync } from "node:fs";
import { join } from "node:path";

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.replies.length = 0;
  H.stream.extras.length = 0;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = {
    "/models": { models: [] },
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

const SECRET = "sk-proj-NEVERINTHECHAT0123456789";

async function send(text: string, reply: string, extra: Record<string, unknown> = {}) {
  const el = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  H.stream.replies.push(reply);
  H.stream.extras.push(extra);
  fireEvent.change(el, { target: { value: text } });
  fireEvent.keyDown(el, { key: "Enter" });
  await screen.findByText(reply);
}

function lastSavedMessages(): Record<string, unknown>[] {
  const save = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/")).at(-1);
  return (save?.body.messages as Record<string, unknown>[]) ?? [];
}

const CHANGE: ConfigCard = {
  kind: "change",
  key: "autonomy_dry_run",
  label: "Dry run",
  old: false,
  new: true,
  restart: false,
  change_id: "cfg_1",
};

describe("settings cards in chat (redesign S3/S4)", () => {
  it("a change made in chat shows old → new, and Undo reverses it through the ledger and is saved", async () => {
    H.api.getResponses["/config/changes/cfg_1"] = { action_id: "tool_1", undone: false };
    render(<ChatPage />);
    await send("turn on dry run mode", "Done.", { configCards: [CHANGE] });
    const card = await screen.findByTestId("config-card-change");
    expect(card.textContent).toContain("Setting changed");
    expect(card.textContent).toContain("Dry run");
    expect(card.textContent).toContain("Off → On");

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await screen.findByText(/Undone — Dry run is Off again/);
    expect(H.api.posts.map((p) => p.path)).toContain("/undo/tool_1");
    await waitFor(() => {
      const reply = lastSavedMessages().find((m) => m.role === "assistant");
      expect((reply?.configCards as ConfigCard[] | undefined)?.[0]).toMatchObject({ undone: true });
    });
  });

  it("a credential card posts the value straight to the vault — never to the chat, the saved thread or the page", async () => {
    H.api.postResponses["/config/secret"] = { status: "stored", action_id: "tool_9" };
    render(<ChatPage />);
    await send("connect my OpenAI api key", "Paste it on the card.", {
      configCards: [
        { kind: "secret", name: "connection.openai", label: "API key for a model provider: openai", request_id: "seq_1" },
      ],
    });
    const form = await screen.findByTestId("credential-card");
    const field = form.querySelector("input") as HTMLInputElement;
    expect(field.type).toBe("password");
    fireEvent.change(field, { target: { value: SECRET } });
    fireEvent.click(screen.getByRole("button", { name: "Save securely" }));
    await screen.findByTestId("credential-card-saved");
    expect(H.api.posts.find((p) => p.path === "/config/secret")?.body).toEqual({
      name: "connection.openai",
      value: SECRET,
    });
    expect(document.body.innerHTML).not.toContain(SECRET);
    await waitFor(() => {
      const reply = lastSavedMessages().find((m) => m.role === "assistant");
      expect((reply?.configCards as SecretRequestCard[] | undefined)?.[0]?.saved).toBe("stored");
    });
    expect(JSON.stringify(H.api.puts)).not.toContain(SECRET);
    expect(JSON.stringify(H.stream.bodies)).not.toContain(SECRET);
  });

  it("a key pasted into the box is HELD — nothing is sent — and Save it securely stores it and keeps the rest", async () => {
    H.api.getResponses["/settings/schema"] = { secrets: [] };
    H.api.postResponses["/config/secret"] = { status: "stored", action_id: "tool_3" };
    render(<ChatPage />);
    const el = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(el, { target: { value: `here is my key ${SECRET} thanks` } });
    fireEvent.keyDown(el, { key: "Enter" });
    await screen.findByTestId("secret-held");
    expect(H.stream.bodies).toHaveLength(0);
    expect(el.value).toContain(SECRET);

    fireEvent.click(screen.getByTestId("secret-held-save"));
    const arg = (await screen.findByLabelText("Which one?")) as HTMLInputElement;
    expect(arg.value).toBe("openai");
    fireEvent.click(screen.getByRole("button", { name: "Save securely" }));
    await waitFor(() => expect(screen.queryByTestId("secret-held")).toBeNull());
    expect(H.api.posts.find((p) => p.path === "/config/secret")?.body).toEqual({
      name: "connection.openai",
      value: SECRET,
    });
    expect(el.value).toBe("here is my key thanks");
    expect(H.stream.bodies).toHaveLength(0);
  });

  it("Send anyway lets that one message through", async () => {
    render(<ChatPage />);
    const el = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    H.stream.replies.push("ok");
    fireEvent.change(el, { target: { value: `test ${SECRET}` } });
    fireEvent.keyDown(el, { key: "Enter" });
    fireEvent.click(await screen.findByTestId("secret-held-send"));
    await screen.findByText("ok");
    expect(JSON.stringify(H.stream.bodies.at(-1))).toContain(SECRET);
    expect(screen.queryByTestId("secret-held")).toBeNull();
  });

  it("an ordinary message is never held", async () => {
    render(<ChatPage />);
    await send("what is a skeleton key?", "a key that opens many locks");
    expect(screen.queryByTestId("secret-held")).toBeNull();
  });

  it("every turn says which device asked, the same id each time", async () => {
    render(<ChatPage />);
    await send("one", "first");
    await send("two", "second");
    const ids = H.stream.bodies.map((b) => b.device_id);
    expect(ids[0]).toMatch(/^dev_[A-Za-z0-9]{6,}$/);
    expect(ids[1]).toBe(ids[0]);
  });

  it("the device id is minted once and kept in this browser", async () => {
    vi.resetModules();
    window.localStorage.clear();
    const { getDeviceId } = await import("@/lib/device");
    const id = getDeviceId();
    expect(window.localStorage.getItem("ij_device_id")).toBe(id);
    vi.resetModules();
    const again = await import("@/lib/device");
    expect(again.getDeviceId()).toBe(id);
  });
});

describe("the card decode is a whitelist", () => {
  it("drops junk and unknown kinds, and a secret card never carries a value", () => {
    const cards = decodeConfigCards([
      CHANGE,
      { kind: "change", key: "x" }, // no change_id
      { kind: "secret", name: "connection.openai", request_id: "seq_2", value: SECRET },
      { kind: "other", key: "y", change_id: "z" },
      { kind: "other", name: "n", request_id: "q" },
      null,
      "nope",
    ]);
    expect(cards.map((c) => c.kind)).toEqual(["change", "secret"]);
    expect(JSON.stringify(cards)).not.toContain(SECRET);
    expect(decodeConfigCards({})).toEqual([]);
  });

  it("knows the credential shapes the daemon masks, and not ordinary words", () => {
    expect(looksLikeSecret(`key: ${SECRET}`)).toBe(true);
    expect(looksLikeSecret("ghp_abcdefghijklmnopqrstuvwxyz0123")).toBe(true);
    expect(looksLikeSecret("123456789:AAbbCCddEEffGGhhIIjjKKllMMnnOOppQQ")).toBe(true);
    expect(looksLikeSecret("my skeleton key sk- is short")).toBe(false);
    expect(looksLikeSecret("Bearer of bad news")).toBe(false);
  });

  it("the real hook carries the done frame's cards onto the result", async () => {
    const { useChatStream } = await vi.importActual<typeof import("@/lib/useChatStream")>(
      "@/lib/useChatStream",
    );
    const { renderHook, act } = await import("@testing-library/react");
    const frames = `event: done
data: ${JSON.stringify({ reply: "ok", config_cards: [CHANGE, { kind: "x" }] })}

`;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(frames));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, { status: 200 })));
    try {
      const { result } = renderHook(() => useChatStream());
      let settled: { configCards?: ConfigCard[] } | null = null;
      await act(async () => {
        settled = (await result.current.run({ messages: [] })) as typeof settled;
      });
      expect(settled!.configCards).toEqual([CHANGE]);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("a saved credential is cleared from the field even if the card stays", async () => {
    H.api.postResponses["/config/secret"] = { status: "stored" };
    const onSettle = vi.fn();
    render(
      <CredentialCard
        card={{ kind: "secret", name: "voice_transcribe_key", label: "Voice key", request_id: "seq_5" }}
        onSettle={onSettle}
      />,
    );
    const field = screen.getByLabelText("Voice key") as HTMLInputElement;
    fireEvent.change(field, { target: { value: SECRET } });
    fireEvent.click(screen.getByRole("button", { name: "Save securely" }));
    await waitFor(() => expect(onSettle).toHaveBeenCalledWith(expect.objectContaining({ saved: "stored" })));
    expect(field.value).toBe("");
  });

  it("both chat lanes put the cards on the message (lock-step)", () => {
    const src = readFileSync(join(__dirname, "..", "app", "chat", "page.tsx"), "utf-8").replace(/\r\n/g, "\n");
    expect(src).toContain("...(configCards?.length ? { configCards } : {}),");
    expect(src).toContain("const configCardsPost = decodeConfigCards(res.config_cards);");
    expect(src).toContain("...(configCardsPost.length ? { configCards: configCardsPost } : {}),");
  });
});
