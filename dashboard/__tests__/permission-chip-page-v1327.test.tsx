/**
 * v1.327.0 — Calm chat W2-2: ONE permission chip in the composer instead of
 * the approvals select.
 *
 * The chip (components/chat/PermissionChip.tsx, pinned on its own in
 * permission-chip-v1327) now IS the chat page's approval control. Only the
 * control changed: a pick drives exactly the state and request fields the old
 * `#chat-approval-mode` select did. Pinned here, with ChatPage rendered for
 * real:
 *  - the chip sits in the composer toolbar and keeps the old id;
 *  - choosing a level sets the conversation's posture, remembers it as the
 *    default for new chats (`ij_chat_approval_mode`) and saves it with the
 *    thread's setup;
 *  - the next turn carries `approval_mode` for a non-default level, and none
 *    for the default (a pre-v1.188.0 daemon still sees a body it knows);
 *  - a new chat opens on the remembered default;
 *  - the keyboard: Enter opens with the current level active, arrows move,
 *    Enter picks, Esc closes and gives focus back to the chip;
 *  - a phone shows only the shield (the other toolbar chips' rule).
 *
 * Harness: the chat-wave3-seeded-race-v1311 mocks (transport hooks mocked at
 * the usual seams) with a `stream.run` that records each body and answers.
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
      gets: [] as string[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      responses: {} as Record<string, unknown>,
    },
    run: { bodies: [] as Record<string, unknown>[] },
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
  post: async () => ({}),
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
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";

const KEY = "ij_chat_approval_mode";

function classes(el: Element | null | undefined): string[] {
  return ((el?.getAttribute("class") ?? "") as string).split(/\s+/).filter(Boolean);
}

async function chip(): Promise<HTMLButtonElement> {
  await screen.findByRole("textbox", { name: "Message" });
  const el = document.getElementById("chat-approval-mode");
  expect(el, "the composer has no #chat-approval-mode").not.toBeNull();
  return el as HTMLButtonElement;
}

/** A press on the chip, then a press on the level in its menu. */
async function pick(mode: string): Promise<void> {
  fireEvent.click(await chip());
  const menu = screen.getByTestId("permission-menu");
  const item = within(menu)
    .getAllByTestId("permission-item")
    .find((i) => i.getAttribute("data-mode") === mode);
  expect(item, `no "${mode}" level in the menu`).toBeTruthy();
  fireEvent.click(item!);
}

/** Type a message and press Enter; wait for the turn's body to be recorded. */
async function send(text: string): Promise<Record<string, unknown>> {
  const n = H.run.bodies.length;
  const box = (await screen.findByRole("textbox", { name: "Message" })) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(H.run.bodies.length).toBe(n + 1));
  await screen.findAllByText("Noted.");
  return H.run.bodies[n];
}

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.puts.length = 0;
  H.run.bodies.length = 0;
  H.api.responses = {
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
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("the permission chip is the composer's approval control", () => {
  it("sits in the composer toolbar, keeps the old id, and the old select is gone", async () => {
    render(<ChatPage />);
    const c = await chip();
    expect(screen.getByTestId("composer-toolbar").contains(c)).toBe(true);
    expect(c.tagName).toBe("BUTTON");
    expect(c.getAttribute("data-testid")).toBe("permission-chip");
    expect(c.getAttribute("data-mode")).toBe("approve_for_me");
    expect(c.getAttribute("aria-haspopup")).toBe("menu");
    expect(document.querySelector('select option[value="yolo"]')).toBeNull();
    expect(screen.queryByLabelText("Approval mode")).toBeNull();
  });

  it("choosing a level sets the posture, remembers it for new chats, and the next turn sends it", async () => {
    render(<ChatPage />);
    await pick("always_ask");
    const c = await chip();
    expect(c.getAttribute("data-mode")).toBe("always_ask");
    expect(c.getAttribute("aria-label")).toBe("Permissions: Ask first");
    expect(window.localStorage.getItem(KEY)).toBe("always_ask");
    const body = await send("tidy the folder");
    expect(body.approval_mode).toBe("always_ask");
    // The posture is saved with the conversation's setup.
    await waitFor(() =>
      expect(
        H.api.puts.some((p) => (p.body.setup as Record<string, unknown> | undefined)?.approval_mode === "always_ask"),
      ).toBe(true),
    );
  });

  it("the no-ask level rides as yolo and reads amber; back to the default sends no posture at all", async () => {
    render(<ChatPage />);
    await pick("yolo");
    const c = await chip();
    expect(c.getAttribute("data-mode")).toBe("yolo");
    expect(classes(c)).toContain("text-tone-warn");
    expect(window.localStorage.getItem(KEY)).toBe("yolo");
    expect((await send("first")).approval_mode).toBe("yolo");
    await pick("approve_for_me");
    expect((await chip()).getAttribute("data-mode")).toBe("approve_for_me");
    expect(window.localStorage.getItem(KEY)).toBe("approve_for_me");
    const second = await send("second");
    expect("approval_mode" in second).toBe(false);
  });

  it("CONTROL: with no pick, a turn sends no posture (the daemon's default rules)", async () => {
    render(<ChatPage />);
    const body = await send("hello");
    expect("approval_mode" in body).toBe(false);
  });

  it("a new chat opens on the remembered default", async () => {
    window.localStorage.setItem(KEY, "always_ask");
    render(<ChatPage />);
    await waitFor(async () => expect((await chip()).getAttribute("data-mode")).toBe("always_ask"));
    expect((await send("go")).approval_mode).toBe("always_ask");
  });

  it("an unknown stored default reads as the everyday level, never the no-ask one", async () => {
    window.localStorage.setItem(KEY, "full_auto");
    render(<ChatPage />);
    const c = await chip();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(c.getAttribute("data-mode")).toBe("approve_for_me");
  });
});

describe("the keyboard", () => {
  it("Enter opens on the current level, arrows move, Enter picks, Esc closes and gives focus back", async () => {
    render(<ChatPage />);
    const c = await chip();
    c.focus();
    fireEvent.keyDown(c, { key: "Enter" });
    // A real browser turns Enter on a button into a click.
    fireEvent.click(c);
    const menu = screen.getByTestId("permission-menu");
    const active = () => menu.querySelector('[data-active="true"]')?.getAttribute("data-mode");
    expect(active()).toBe("approve_for_me");
    fireEvent.keyDown(menu, { key: "ArrowUp" });
    expect(active()).toBe("always_ask");
    fireEvent.keyDown(menu, { key: "Enter" });
    await waitFor(() => expect(screen.queryByTestId("permission-menu")).toBeNull());
    expect(c.getAttribute("data-mode")).toBe("always_ask");
    expect(document.activeElement).toBe(c);
    expect(window.localStorage.getItem(KEY)).toBe("always_ask");
    // ArrowDown on the chip opens too; Esc closes without a pick.
    fireEvent.keyDown(c, { key: "ArrowDown" });
    const again = screen.getByTestId("permission-menu");
    fireEvent.keyDown(again, { key: "ArrowDown" });
    fireEvent.keyDown(again, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("permission-menu")).toBeNull());
    expect(document.activeElement).toBe(c);
    expect(c.getAttribute("data-mode")).toBe("always_ask");
  });
});

describe("a phone", () => {
  it("shows only the shield: the words and the chevron hide below sm, the chip is a 30px square", async () => {
    render(<ChatPage />);
    const c = await chip();
    expect(classes(c)).toEqual(expect.arrayContaining(["max-sm:w-[30px]", "max-sm:px-0", "max-sm:justify-center"]));
    const words = within(c).getByText("Ask when risky");
    expect(classes(words)).toContain("max-sm:hidden");
    const svgs = Array.from(c.querySelectorAll("svg"));
    expect(svgs).toHaveLength(2);
    // The shield stays; the chevron hides.
    expect(classes(svgs[0])).not.toContain("max-sm:hidden");
    expect(classes(svgs[1])).toContain("max-sm:hidden");
    // The level is still named for a screen reader and on hover.
    expect(c.getAttribute("aria-label")).toBe("Permissions: Ask when risky");
    expect(c.title.length).toBeGreaterThan(0);
  });
});
