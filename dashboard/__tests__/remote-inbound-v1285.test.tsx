/**
 * v1.285.0 — a remote agent can be allowed to MESSAGE BACK from its row on the
 * Agents page, and the inbound token is shown exactly once.
 *
 * Pins: the row offers "Let it message back" when inbound is off and "Turn off
 * message-back" + "Rotate token" when on; enabling POSTs the enable route and
 * shows the minted token with the address to post to; the page never shows a
 * token it did not just mint (a re-render from the daemon's listing carries
 * none); disabling POSTs the disable route.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({
  posts: [] as { path: string; body: unknown }[],
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError,
    API_BASE: "http://test",
    ijToken: () => "tok-1",
    get: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    post: (path: string, body?: unknown) => {
      api.posts.push({ path, body });
      if (path.endsWith("/inbound/enable"))
        return Promise.resolve({
          name: "hermes",
          inbound_enabled: true,
          inbound_url: "http://desk:8787/agents/remote/hermes/inbound",
          token: "tok_shown_once_ABC123",
          url: "http://desk:8787/agents/remote/hermes/inbound",
        });
      return Promise.resolve({ name: "hermes", inbound_enabled: false });
    },
  };
});

import { RemoteAgentsSection } from "@/components/agents/SetupCard";
import type { RemoteAgentInfo } from "@/components/agents/identity";

const HERMES: RemoteAgentInfo = {
  name: "hermes",
  base_url: "http://192.168.1.50:8080/run",
  kind: "http-task",
  enabled: true,
  has_credential: true,
  inbound_enabled: false,
};

function renderSection(remote: RemoteAgentInfo, onChanged = () => {}) {
  return render(
    <RemoteAgentsSection
      remotes={[remote]}
      faces={{}}
      facesSupported={false}
      onChanged={onChanged}
      onFaceChanged={() => {}}
    />,
  );
}

beforeEach(() => {
  api.posts = [];
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("a remote agent can message back (v1.285.0)", () => {
  it("enabling mints the token, shows it once with the address, and reports the change", async () => {
    const onChanged = vi.fn();
    renderSection(HERMES, onChanged);
    expect(screen.queryByTestId("inbound-on")).toBeNull();
    const toggle = screen.getByTestId("inbound-toggle");
    expect(toggle).toHaveTextContent("Let it message back");
    fireEvent.click(toggle);
    const box = await screen.findByTestId("inbound-minted");
    expect(box).toHaveTextContent("tok_shown_once_ABC123");
    expect(box).toHaveTextContent("http://desk:8787/agents/remote/hermes/inbound");
    expect(box).toHaveTextContent(/shown once/i);
    expect(api.posts.at(-1)?.path).toBe("/agents/remote/hermes/inbound/enable");
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    // Done hides it — and nothing on the row can bring the token back.
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByTestId("inbound-minted")).toBeNull();
  });

  it("an enabled remote wears the pill and offers rotate + turn off; the listing carries no token", async () => {
    renderSection({ ...HERMES, inbound_enabled: true, inbound_url: "http://desk:8787/agents/remote/hermes/inbound" });
    expect(screen.getByTestId("inbound-on")).toHaveTextContent("messages back");
    expect(screen.queryByTestId("inbound-minted")).toBeNull();
    expect(screen.getByTestId("inbound-toggle")).toHaveTextContent("Turn off message-back");
    fireEvent.click(screen.getByTestId("inbound-rotate"));
    const box = await screen.findByTestId("inbound-minted");
    expect(box).toHaveTextContent("tok_shown_once_ABC123");
    expect(api.posts.at(-1)?.path).toBe("/agents/remote/hermes/inbound/enable");
    fireEvent.click(screen.getByTestId("inbound-toggle"));
    await waitFor(() =>
      expect(api.posts.some((p) => p.path === "/agents/remote/hermes/inbound/disable")).toBe(true),
    );
    // turning it off retires the minted box too — the token is gone
    await waitFor(() => expect(screen.queryByTestId("inbound-minted")).toBeNull());
  });
});
