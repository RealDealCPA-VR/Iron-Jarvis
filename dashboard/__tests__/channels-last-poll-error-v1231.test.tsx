import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

/**
 * v1.231.0 (audit Wave 5, AE8) — a dead phone token is visible ON ITS ROW.
 *
 * `GET /comm/channels` carries `last_poll_error` (the daemon's last failed
 * inbound poll: a revoked Telegram bot token, a refused IMAP login). The
 * Channels page must render it, or the daemon's honesty never reaches the
 * user and the row keeps saying "Working — tested 3 days ago" while nothing
 * is listening. A healthy row renders no such text.
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
  post: () => Promise.resolve({ ok: true }),
  patch: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({
      href,
      children,
      ...rest
    }: {
      href: string;
      children?: React.ReactNode;
    }) => createElement("a", { href, ...rest }, children),
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

import ChannelsPage from "@/app/channels/page";

afterEach(() => {
  cleanup();
  api.responses = {};
});

function seed(channels: unknown[]) {
  api.responses["/comm/channels"] = { channels };
  api.responses["/comm/channel-types"] = { types: [] };
}

function telegram(over: Record<string, unknown> = {}) {
  return {
    name: "tg",
    type: "telegram",
    builtin: false,
    last_test_ok: true,
    last_test_at: "2026-09-02T10:00:00Z",
    events: [],
    inbound_enabled: true,
    chat_enabled: true,
    allowed_senders_count: 1,
    last_poll_error: null,
    last_poll_error_at: null,
    ...over,
  };
}

describe("Channels page renders a destination's last_poll_error on its row", () => {
  it("says 'Not listening — <reason>' when the daemon's last poll was refused", async () => {
    seed([
      telegram({
        last_poll_error:
          "telegram: getUpdates refused (HTTP 401: Unauthorized) — the bot token was revoked or rotated; paste the current token from @BotFather",
        last_poll_error_at: new Date().toISOString(),
      }),
    ]);
    render(<ChannelsPage />);
    const note = await screen.findByTestId("channel-poll-error-tg");
    expect(note.textContent).toContain("Not listening — telegram: getUpdates refused (HTTP 401");
    expect(note.textContent).toContain("@BotFather");
  });

  it("renders nothing for a destination whose polls come back", async () => {
    seed([telegram()]);
    render(<ChannelsPage />);
    await screen.findAllByText("tg"); // the row and the send-test picker
    expect(screen.queryByTestId("channel-poll-error-tg")).toBeNull();
    expect(screen.queryByText(/Not listening/)).toBeNull();
  });
});
