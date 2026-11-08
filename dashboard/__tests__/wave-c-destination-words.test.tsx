/**
 * Wave C (readiness plan 0h.5, item 29): the destination switches speak
 * plainly EVERYWHERE they appear. The Slack add form had no copy of its own
 * and drew the catalog's "(true/false)" / "Set to true" words; the catalog
 * speaks plainly now (tests/test_wave_c_destination_words.py) and every
 * switch is typed `bool`, so the page draws a real toggle for all three
 * destinations. This file pins the page: no source line and no screen holds
 * the wire's words, and a Slack add form draws its two switches as toggles.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { readSrc } from "./helpers/dashGuard";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return { responses: {} as Record<string, unknown>, FakeApiError };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({ ok: true }),
  patch: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {actions}
    </div>
  ),
}));

import ChannelsPage from "@/components/settings/pages/NotificationsPage";

/** The catalog as the daemon sends it since Wave C (its words, its `type`). */
const SLACK_FIELDS = [
  { key: "token", label: "Bot token (option B)", secret: true },
  { key: "channel", label: "Channel (option B)", secret: false },
  { key: "inbound_enabled", label: "Listen for incoming messages", secret: false, type: "bool",
    help: "Let the people on the allowlist message the bot and get Iron Jarvis's replies. Off until you turn it on." },
  { key: "chat_enabled", label: "Chat with Iron Jarvis", secret: false, type: "bool",
    help: "Hold a real conversation with Iron Jarvis in your Slack messages with the bot." },
  { key: "allowed_senders", label: "Allowlist (Slack member IDs)", secret: false },
];

const WIRE = /true\/false|set to true|set to false/i;

afterEach(() => {
  cleanup();
  api.responses = {};
});

describe("destination switches in plain words (Wave C, item 29)", () => {
  it("the page's own source holds none of the wire's words", () => {
    const src = readSrc("components/settings/pages/NotificationsPage.tsx");
    expect(src.match(WIRE)).toBeNull();
  });

  it("a Slack add form draws both switches as toggles, with no wire words on screen", async () => {
    api.responses["/comm/channels"] = { channels: [] };
    api.responses["/comm/channel-types"] = {
      types: [{ type: "slack", fields: SLACK_FIELDS, manifest: null, manifest_help: null }],
    };
    render(<ChannelsPage />);
    fireEvent.click((await screen.findAllByRole("button", { name: /Add destination/ }))[0]);
    fireEvent.click(await screen.findByRole("radio", { name: /Slack/ }));
    // Some of a destination's fields fold under Advanced; open it so every
    // field is on screen.
    const advanced = screen.queryByRole("button", { name: /^Advanced/ });
    if (advanced) fireEvent.click(advanced);
    const listen = await screen.findByRole("checkbox", { name: "Listen for incoming messages" });
    expect((listen as HTMLInputElement).type).toBe("checkbox");
    expect((screen.getByRole("checkbox", { name: "Chat with Iron Jarvis" }) as HTMLInputElement).type).toBe("checkbox");
    expect(document.body.textContent ?? "").not.toMatch(WIRE);
  });
});
