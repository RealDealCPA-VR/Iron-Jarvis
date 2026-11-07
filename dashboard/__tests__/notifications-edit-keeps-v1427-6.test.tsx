import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * v1.427.6 — Edit keeps what is saved, and two-way says why it cannot hear you.
 *
 * The user's report: "when i go back in to edit, i need to re-enter the tokens
 * because they are cleared". Edit re-submits the add form; the daemon now keeps
 * a field whose box is blank (tests/test_slack_keep_tokens_v14276.py). This file
 * pins the page's half: the stored plain values fill their boxes, a saved
 * secret says "Saved — leave blank to keep it", "Remove the saved one" and an
 * erased shown value travel as `clear`, Advanced opens when the saved tokens
 * live there, and the daemon's `two_way_note` is on the row.
 */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    responses: {} as Record<string, unknown>,
    posts: [] as Array<{ path: string; body: unknown }>,
    saveAnswer: { name: "hq", type: "slack", added: true, two_way_note: "" } as Record<string, unknown>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    }
    return Promise.resolve(r);
  },
  post: (path: string, body: unknown) => {
    api.posts.push({ path, body });
    if (path === "/comm/channels") return Promise.resolve(api.saveAnswer);
    return Promise.resolve({ ok: true });
  },
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

const SLACK_FIELDS = [
  { key: "webhook_url", label: "Incoming webhook URL (option A)", secret: false },
  { key: "token", label: "Bot token (option B)", secret: true },
  { key: "channel", label: "Channel (option B)", secret: false },
  { key: "app_token", label: "App-level token (two-way, no exposure)", secret: true },
  { key: "inbound_enabled", label: "Enable two-way (true/false)", secret: false },
  { key: "chat_enabled", label: "Chat with Iron Jarvis (true/false)", secret: false },
  { key: "allowed_senders", label: "Allowlist (Slack member IDs)", secret: false },
];

function slackRow(over: Record<string, unknown> = {}) {
  return {
    name: "hq",
    type: "slack",
    builtin: false,
    last_test_ok: true,
    last_test_at: "2026-11-04T10:00:00Z",
    events: [],
    inbound_enabled: true,
    chat_enabled: false,
    allowed_senders_count: 1,
    saved_fields: ["token", "channel", "app_token", "inbound_enabled", "allowed_senders"],
    values: { channel: "#general" },
    two_way_note: null,
    ...over,
  };
}

function seed(row = slackRow()) {
  api.responses["/comm/channels"] = { channels: [row] };
  api.responses["/comm/channel-types"] = {
    types: [{ type: "slack", fields: SLACK_FIELDS, manifest: null, manifest_help: null }],
  };
}

afterEach(() => {
  cleanup();
  api.responses = {};
  api.posts = [];
  api.saveAnswer = { name: "hq", type: "slack", added: true, two_way_note: "" };
});

async function openEdit() {
  render(<ChannelsPage />);
  fireEvent.click(await screen.findByTestId("channel-edit-hq")); // v1.447.0: the pencil (was its title)
  await screen.findByText(/a blank box keeps what/i);
}

function savedConfig(): Record<string, unknown> {
  const save = api.posts.find((p) => p.path === "/comm/channels");
  expect(save).toBeTruthy();
  return (save!.body as { config: Record<string, unknown> }).config;
}

describe("Edit keeps what is saved (v1.427.6)", () => {
  it("fills shown values, marks saved secrets, and opens Advanced where they live", async () => {
    seed();
    await openEdit();
    expect((screen.getByLabelText("Channel (option B)") as HTMLInputElement).value).toBe("#general");
    const token = screen.getByLabelText("Bot token (option B)") as HTMLInputElement;
    expect(token.value).toBe("");
    expect(token.placeholder).toBe("Saved — leave blank to keep it");
    expect((screen.getByLabelText("App-level token (two-way, no exposure)") as HTMLInputElement).placeholder).toBe(
      "Saved — leave blank to keep it",
    );
    expect((screen.getByLabelText("Allowlist (Slack member IDs)") as HTMLInputElement).placeholder).toBe(
      "1 saved — leave blank to keep",
    );
    // Nothing saved in the webhook box: no "saved" words there.
    expect((screen.getByLabelText("Incoming webhook URL (option A)") as HTMLInputElement).placeholder).toBe("");
  });

  it("a save that changes nothing sends blanks and no `clear`", async () => {
    seed();
    await openEdit();
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(api.posts.some((p) => p.path === "/comm/channels")).toBe(true));
    const config = savedConfig();
    expect(config.token).toBe("");
    expect(config.app_token).toBe("");
    expect(config.channel).toBe("#general");
    expect(config.inbound_enabled).toBe("true");
    expect(config).not.toHaveProperty("clear");
  });

  it("Remove the saved one, and an erased shown value, travel as `clear`", async () => {
    seed();
    await openEdit();
    fireEvent.click(screen.getByTestId("channel-clear-app_token"));
    expect(screen.getByTestId("channel-clear-note-app_token")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Channel (option B)"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(api.posts.some((p) => p.path === "/comm/channels")).toBe(true));
    expect([...(savedConfig().clear as string[])].sort()).toEqual(["app_token", "channel"]);
  });

  it("typing a new value after Remove replaces instead of removing", async () => {
    seed();
    await openEdit();
    fireEvent.click(screen.getByTestId("channel-clear-token"));
    fireEvent.change(screen.getByLabelText("Bot token (option B)"), { target: { value: "xoxb-new" } });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(api.posts.some((p) => p.path === "/comm/channels")).toBe(true));
    const config = savedConfig();
    expect(config.token).toBe("xoxb-new");
    expect(config).not.toHaveProperty("clear");
  });

  it("the row says why two-way cannot hear you, and the save's answer says it too", async () => {
    const note = "Two-way is on, but no one is on the allowlist, so Jarvis isn't listening.";
    seed(slackRow({ two_way_note: note, allowed_senders_count: 0 }));
    api.saveAnswer = { name: "hq", type: "slack", added: true, two_way_note: note };
    render(<ChannelsPage />);
    expect((await screen.findByTestId("channel-two-way-note-hq")).textContent).toBe(note);
    fireEvent.click(screen.getByTestId("channel-edit-hq"));
    await screen.findByText(/a blank box keeps what/i);
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() =>
      expect(screen.getAllByText(new RegExp("delivered ✓ Two-way is on")).length).toBeGreaterThan(0),
    );
  });

  it("a destination whose two-way can work shows no note", async () => {
    seed();
    render(<ChannelsPage />);
    await screen.findByTestId("channel-edit-hq"); // v1.447.0: the pencil
    expect(screen.queryByTestId("channel-two-way-note-hq")).toBeNull();
  });
});
