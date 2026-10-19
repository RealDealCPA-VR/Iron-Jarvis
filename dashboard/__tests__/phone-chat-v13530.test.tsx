/**
 * v1.353.0, lane 3E — chatting with Iron Jarvis from your phone is easy to
 * find, set up on one screen, and Help tells the truth about the phone.
 *
 * G56 (Notifications): Ctrl K described Notifications as "where alerts go";
 * turning on two-way Telegram chat needed the Advanced fold AND typing your
 * own Telegram user id — which the page itself said is usually the chat ID it
 * had just detected. Now "phone" in Ctrl K finds "chat with Iron Jarvis from
 * your phone", and the Telegram form shows the chat switch and the allowed
 * senders beside the token and chat ID; ticking chat turns listening on and
 * fills the allowed senders from a detected PRIVATE chat (lib/phoneChat).
 *
 * G58 (Help): the phone card told users to open http://100.x.y.z:8788 over
 * Tailscale with "no daemon settings to change" — the packaged app binds both
 * servers to 127.0.0.1 and the dashboard calls http://127.0.0.1:8787. The card
 * now leads with messaging and marks the dashboard recipe Advanced with what
 * it really requires.
 *
 * G66 (Help part): a "Something not working?" card opens a chat with a
 * question the self-care tools answer (typed, never sent).
 *
 * Every pin drives the REAL page/component; only the daemon is faked.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    cancelled = false;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    responses: {} as Record<string, unknown>,
    postResults: {} as Record<string, unknown>,
    posts: [] as { path: string; body: unknown }[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => "",
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (path: string) => {
    const r = api.responses[path];
    if (r instanceof api.FakeApiError) throw r;
    return r ?? {};
  },
  post: async (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    return api.postResults[path] ?? { ok: true };
  },
  put: async () => ({}),
  patch: async () => ({}),
  del: async () => ({}),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/channels",
  useSearchParams: () => new URLSearchParams(),
}));

import NotificationsPage from "@/components/settings/pages/NotificationsPage";
import HelpPage from "@/app/help/page";
import { AUTO_SENDERS_KEY, privateChatId, withPhoneChat } from "@/lib/phoneChat";
import { NAV_ENTRIES } from "@/lib/nav";
import { __resetApiCache } from "@/lib/apiCache";

/** The daemon's REAL Telegram field list (daemon/app.py _CHANNEL_TYPE_FIELDS). */
const TELEGRAM_TYPE = {
  type: "telegram",
  fields: [
    { key: "token", label: "Bot token", secret: true, help: "From @BotFather." },
    { key: "chat_id", label: "Chat ID", secret: false },
    { key: "inbound_enabled", label: "Enable two-way (true/false)", secret: false },
    { key: "chat_enabled", label: "Chat with Iron Jarvis (true/false)", secret: false },
    { key: "allowed_senders", label: "Allowlist (Telegram user IDs)", secret: false },
  ],
};

beforeEach(() => {
  __resetApiCache();
  window.localStorage.clear();
  api.responses = {
    "/comm/channels": { channels: [] },
    "/comm/channel-types": { types: [TELEGRAM_TYPE] },
    // Help's two reads, in the daemon's real shapes.
    "/helpdocs": { docs: [] },
    "/guide/status": { docs: [], missing: [], doc_sections: 0, live_sections: 0 },
  };
  api.postResults = {};
  api.posts = [];
});
afterEach(() => cleanup());

/* ------------------------------------------------------------ the rule */

describe("lib/phoneChat: who may talk once chat is ticked", () => {
  it("a private chat's id is the sender; a group, a blank or junk is nobody", () => {
    expect(privateChatId("4242")).toBe("4242");
    expect(privateChatId(" 4242 ")).toBe("4242");
    expect(privateChatId("-100123")).toBeNull(); // a group chat names no user
    expect(privateChatId("")).toBeNull();
    expect(privateChatId("abc")).toBeNull();
    expect(privateChatId("0")).toBeNull();
  });

  it("chat on: listening on, and an EMPTY list gets the private chat id", () => {
    // The form also notes what it filled (review, v1.353.0) so a corrected
    // chat ID can move it; the note is not a field and is never posted.
    expect(withPhoneChat({ chat_enabled: "true", chat_id: "4242" })).toEqual({
      chat_enabled: "true",
      chat_id: "4242",
      inbound_enabled: "true",
      allowed_senders: "4242",
      [AUTO_SENDERS_KEY]: "4242",
    });
  });

  it("a list the form filled follows the chat ID; a group keeps it; an edited list is the person's", () => {
    const filled = withPhoneChat({ chat_enabled: "true", chat_id: "7070" });
    const moved = withPhoneChat({ ...filled, chat_id: "6161" });
    expect(moved.allowed_senders).toBe("6161");
    expect(moved[AUTO_SENDERS_KEY]).toBe("6161");
    // A group names nobody: the user's own id stays the sender.
    expect(withPhoneChat({ ...moved, chat_id: "-100123" }).allowed_senders).toBe("6161");
    // Edited by the person: never moved again.
    const edited = { ...moved, allowed_senders: "6161, 9090" };
    expect(withPhoneChat({ ...edited, chat_id: "5555" }).allowed_senders).toBe("6161, 9090");
  });

  it("never replaces a list the person typed, never fills from a group, does nothing with chat off", () => {
    expect(withPhoneChat({ chat_enabled: "true", chat_id: "4242", allowed_senders: "77, 88" }).allowed_senders).toBe(
      "77, 88",
    );
    const group = withPhoneChat({ chat_enabled: "true", chat_id: "-100123" });
    expect(group.allowed_senders).toBeUndefined();
    expect(group.inbound_enabled).toBe("true");
    const off = { chat_enabled: "false", chat_id: "4242" };
    expect(withPhoneChat(off)).toBe(off);
  });
});

/* ------------------------------------------------------------- Ctrl K */

describe("Ctrl K: 'phone' finds chatting with Iron Jarvis from your phone", () => {
  it("the Notifications entry carries the phone words", () => {
    const entry = NAV_ENTRIES.find((e) => e.href === "/channels")!;
    expect(entry.blurb).toMatch(/chat with Iron Jarvis from your phone/);
    expect(entry.aliases).toEqual(expect.arrayContaining(["phone", "chat from my phone", "two-way"]));
    // The old alerts-only words are kept as aliases, never lost.
    expect(entry.aliases).toEqual(expect.arrayContaining(["alerts to phone", "channels", "telegram"]));
  });

  it("typing 'phone' in the real palette lists Notifications, saying so", async () => {
    const { CommandPalette } = await import("@/components/CommandPalette");
    render(<CommandPalette />);
    await act(async () => {
      window.dispatchEvent(new Event("ij:open-palette"));
    });
    const box = screen.getByRole("combobox");
    await act(async () => {
      fireEvent.change(box, { target: { value: "phone" } });
    });
    const row = await waitFor(() => {
      const r = screen.queryAllByRole("option").find((o) => o.getAttribute("data-href") === "/channels");
      expect(r, "no Notifications row for 'phone'").toBeTruthy();
      return r!;
    });
    expect(row.textContent).toMatch(/chat with Iron Jarvis from your phone/);
  });
});

/* ------------------------------------------------ Notifications: one screen */

/** Open Add destination, pick Telegram, name it, paste a token. */
async function startTelegram() {
  render(<NotificationsPage />);
  fireEvent.click((await screen.findAllByRole("button", { name: /Add destination/ }))[0]);
  fireEvent.click(await screen.findByRole("radio", { name: /Telegram/ }));
  fireEvent.change(screen.getByLabelText("Destination name"), { target: { value: "phone" } });
  fireEvent.change(await screen.findByLabelText("Bot token"), { target: { value: "123:abc" } });
}

const chatSwitch = () =>
  screen.getByRole("checkbox", { name: "Chat with Iron Jarvis from this destination" }) as HTMLInputElement;
const senders = () => screen.getByLabelText("Allowed senders") as HTMLInputElement;
const chatId = () => screen.getByLabelText("Chat ID") as HTMLInputElement;
const advancedFold = () => screen.getByRole("button", { name: /^Advanced/ });

describe("Notifications: Telegram chat on one screen, no Advanced, no typed id", () => {
  it("detect, tick chat, save: the saved destination chats with YOU", async () => {
    api.postResults["/comm/telegram/detect-chat"] = { chats: [{ id: 4242, label: "You (private)" }] };
    await startTelegram();
    // The chat switch and the allowed senders are on the main form.
    expect(advancedFold().getAttribute("aria-expanded")).toBe("false");
    expect(chatSwitch().checked).toBe(false);
    expect(senders().value).toBe("");
    // Listening stays folded (chat turns it on by itself).
    expect(screen.queryByRole("checkbox", { name: "Listen for incoming messages" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Detect my chat ID/ }));
    await waitFor(() => expect(chatId().value).toBe("4242"));
    fireEvent.click(chatSwitch());
    expect(chatSwitch().checked).toBe(true);
    expect(senders().value).toBe("4242");

    fireEvent.click(screen.getAllByRole("button", { name: /Add destination/ }).at(-1)!);
    const add = await waitFor(() => {
      const p = api.posts.find((x) => x.path === "/comm/channels");
      expect(p).toBeTruthy();
      return p!;
    });
    const cfg = (add.body as { config: Record<string, string> }).config;
    expect(cfg.chat_enabled).toBe("true");
    expect(cfg.inbound_enabled).toBe("true");
    expect(cfg.allowed_senders).toBe("4242");
    expect(cfg.chat_id).toBe("4242");
    // Saved and tested without Advanced ever being opened (the fold read
    // aria-expanded=false above, and the form closes on a delivered test).
    expect(await screen.findByText(/“phone” added — test message delivered/)).toBeTruthy();
  });

  it("tick chat first, detect after: the detected id still lands in the list", async () => {
    api.postResults["/comm/telegram/detect-chat"] = { chats: [{ id: 5151, label: "You" }] };
    await startTelegram();
    fireEvent.click(chatSwitch());
    expect(senders().value).toBe(""); // nothing to fill yet
    fireEvent.click(screen.getByRole("button", { name: /Detect my chat ID/ }));
    await waitFor(() => expect(senders().value).toBe("5151"));
  });

  it("several chats answered: the one picked fills the list too", async () => {
    api.postResults["/comm/telegram/detect-chat"] = {
      chats: [
        { id: 6161, label: "You" },
        { id: -1009, label: "Team group" },
      ],
    };
    await startTelegram();
    fireEvent.click(chatSwitch());
    fireEvent.click(screen.getByRole("button", { name: /Detect my chat ID/ }));
    fireEvent.click(await screen.findByRole("button", { name: "You" }));
    expect(chatId().value).toBe("6161");
    expect(senders().value).toBe("6161");
  });

  it("a typed chat id counts when the box is left; a group id never becomes a sender", async () => {
    await startTelegram();
    fireEvent.click(chatSwitch());
    fireEvent.change(chatId(), { target: { value: "-100777" } });
    fireEvent.blur(chatId());
    expect(senders().value).toBe("");
    fireEvent.change(chatId(), { target: { value: "8080" } });
    expect(senders().value).toBe(""); // not per keystroke
    fireEvent.blur(chatId());
    expect(senders().value).toBe("8080");
  });

  it("a list the person typed is never replaced", async () => {
    api.postResults["/comm/telegram/detect-chat"] = { chats: [{ id: 4242, label: "You" }] };
    await startTelegram();
    fireEvent.change(senders(), { target: { value: "111, 222" } });
    fireEvent.click(screen.getByRole("button", { name: /Detect my chat ID/ }));
    await waitFor(() => expect(chatId().value).toBe("4242"));
    fireEvent.click(chatSwitch());
    expect(senders().value).toBe("111, 222");
  });

  // Review (v1.353.0): the list the FORM filled must follow a corrected chat
  // ID. Picking the wrong private chat (a friend who also messaged the bot)
  // and then detecting again used to correct the chat ID but leave the
  // friend's id as the only allowed sender: the friend could talk to Iron
  // Jarvis and the user could not.
  it("a corrected chat ID moves the id the form filled, after a wrong pick", async () => {
    api.postResults["/comm/telegram/detect-chat"] = {
      chats: [
        { id: 7070, label: "Friend" },
        { id: 6161, label: "You" },
      ],
    };
    await startTelegram();
    fireEvent.click(chatSwitch());
    fireEvent.click(screen.getByRole("button", { name: /Detect my chat ID/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Friend" }));
    expect(senders().value).toBe("7070");
    // Detect again; this time only the user's own chat answers.
    api.postResults["/comm/telegram/detect-chat"] = { chats: [{ id: 6161, label: "You" }] };
    fireEvent.click(screen.getByRole("button", { name: /Detect my chat ID/ }));
    await waitFor(() => expect(chatId().value).toBe("6161"));
    await waitFor(() => expect(senders().value).toBe("6161"));
    // The save posts only the destination's own fields (the form's note of
    // what it filled never reaches the daemon).
    fireEvent.click(screen.getAllByRole("button", { name: /Add destination/ }).at(-1)!);
    const add = await waitFor(() => {
      const p = api.posts.find((x) => x.path === "/comm/channels");
      expect(p).toBeTruthy();
      return p!;
    });
    const cfg = (add.body as { config: Record<string, string> }).config;
    expect(Object.keys(cfg).sort()).toEqual(TELEGRAM_TYPE.fields.map((f) => f.key).sort());
    expect(cfg.allowed_senders).toBe("6161");
  });

  it("a typed chat ID fixed after leaving the box moves the filled id too", async () => {
    await startTelegram();
    fireEvent.click(chatSwitch());
    fireEvent.change(chatId(), { target: { value: "123" } });
    fireEvent.blur(chatId());
    expect(senders().value).toBe("123");
    fireEvent.change(chatId(), { target: { value: "1234" } });
    fireEvent.blur(chatId());
    expect(senders().value).toBe("1234");
    // Moving to a GROUP keeps the user's own id: in a group the sender is
    // still that person, and a group id names nobody.
    fireEvent.change(chatId(), { target: { value: "-100777" } });
    fireEvent.blur(chatId());
    expect(senders().value).toBe("1234");
  });

  it("once the person edits the filled list it is theirs and never moves", async () => {
    await startTelegram();
    fireEvent.click(chatSwitch());
    fireEvent.change(chatId(), { target: { value: "8080" } });
    fireEvent.blur(chatId());
    expect(senders().value).toBe("8080");
    fireEvent.change(senders(), { target: { value: "8080, 9090" } });
    fireEvent.change(chatId(), { target: { value: "7070" } });
    fireEvent.blur(chatId());
    expect(senders().value).toBe("8080, 9090");
  });

  it("the walkthrough's step 2 no longer sends people to Advanced", async () => {
    render(<NotificationsPage />);
    const step = await screen.findByText(/Tick it right under the chat ID/);
    expect(step.textContent).toMatch(/allowed senders for you/);
    expect(document.body.textContent).not.toMatch(/It's under Advanced/);
  });

  it("anti-vacuity: Slack keeps its allowed senders under Advanced", async () => {
    api.responses["/comm/channel-types"] = {
      types: [
        {
          type: "slack",
          fields: [
            { key: "webhook_url", label: "Incoming Webhook URL", secret: true },
            { key: "allowed_senders", label: "Allowlist (Slack member IDs)", secret: false },
          ],
        },
      ],
    };
    render(<NotificationsPage />);
    fireEvent.click((await screen.findAllByRole("button", { name: /Add destination/ }))[0]);
    fireEvent.click(await screen.findByRole("radio", { name: /Slack/ }));
    expect(screen.queryByLabelText("Allowlist (Slack member IDs)")).toBeNull();
    fireEvent.click(advancedFold());
    expect(screen.getByLabelText("Allowlist (Slack member IDs)")).toBeTruthy();
  });
});

/* -------------------------------------------------------------- Help */

describe("Help: from a phone, messaging first; the dashboard recipe says what it needs", () => {
  it("the phone card leads with messaging and answers asks there", async () => {
    render(<HelpPage />);
    const lead = await screen.findByTestId("help-phone-lead");
    expect(lead.textContent).toMatch(/to use Iron Jarvis from your phone is to message it/);
    const chat = screen.getByTestId("help-phone-chat");
    const dash = screen.getByTestId("help-phone-dashboard");
    // Messaging comes BEFORE the dashboard recipe.
    expect(chat.compareDocumentPosition(dash) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(chat.textContent).toMatch(/Start here/);
    expect(chat.textContent).toMatch(/reply approve or deny/);
    // Step 2 matches the one-screen form (no Advanced, no typed id).
    expect(chat.textContent).toMatch(/right under the chat ID/);
    expect(chat.textContent).not.toMatch(/Under Advanced/);
    expect(within(chat).getByRole("link", { name: "Notifications" }).getAttribute("href")).toBe("/channels");
  });

  it("the dashboard-on-phone recipe is Advanced and names its real requirements", async () => {
    render(<HelpPage />);
    const dash = await screen.findByTestId("help-phone-dashboard");
    const text = dash.textContent ?? "";
    expect(text).toMatch(/Advanced/);
    expect(text).toMatch(/127\.0\.0\.1:8787/);
    expect(text).toMatch(/127\.0\.0\.1:8788/);
    expect(text).toMatch(/Both servers listening beyond 127\.0\.0\.1/);
    expect(text).toMatch(/NEXT_PUBLIC_IJ_API/);
    expect(text).toMatch(/tunnel or reverse proxy/);
    expect(text).toMatch(/IRONJARVIS_HOST_ALLOWLIST/);
    expect(text).toMatch(/IRONJARVIS_TOKEN/);
    expect(text).toMatch(/has not been tried from a phone yet/);
    // The false promise is gone, and nothing calls the VPN path "Recommended".
    expect(document.body.textContent).not.toMatch(/no daemon settings to change/);
    expect(dash.textContent).not.toMatch(/Recommended/);
  });

  it("'Something not working?' opens a chat with the question typed (never sent)", async () => {
    render(<HelpPage />);
    const title = await screen.findByText("Something not working?");
    const link = title.closest("a")!;
    const href = link.getAttribute("href")!;
    expect(href.startsWith("/chat?ask=")).toBe(true);
    const ask = new URLSearchParams(href.slice("/chat?".length)).get("ask");
    expect(ask).toBe("Something is broken. Can you check what's wrong?");
    expect(link.textContent).toMatch(/Ask Jarvis what's wrong/);
  });
});
