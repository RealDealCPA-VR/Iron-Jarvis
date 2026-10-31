/**
 * v1.414.0 review — the share offer in a new Build › Code session says what
 * REALLY happened. `PUT /profile/share` answers the view after the write; a
 * file that could not be written (OpenCode's AGENTS.md is never created
 * while OpenCode reads the user's Claude Code CLAUDE.md) used to be shown as
 * "Your profile is shared with OpenCode" — nothing had been written.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  putReply: {} as unknown,
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError: FakeApiError,
    get: (path: string) => {
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(r);
    },
    put: () => Promise.resolve(H.putReply),
    patch: () => Promise.resolve({}),
  };
});

import { ProfileShareOffer } from "@/components/buildCode/JarvisTools";

const FALLBACK =
  "OpenCode reads your Claude Code instructions file (CLAUDE.md) while it has no AGENTS.md of its own; " +
  "making one would stop it reading that file, so nothing was written — share with Claude Code instead, " +
  "and OpenCode reads it there";
const PATH = "C:\\Users\\me\\.config\\opencode\\AGENTS.md";

function view(on: boolean, error: string | null) {
  return {
    clis: [
      {
        cli: "opencode",
        label: "OpenCode",
        vendor: "the model provider you chose in OpenCode",
        available: true,
        on,
        file_name: "AGENTS.md",
        files: on ? [{ path: PATH, account: null, exists: false, last_written: null, drift: null, held: false, error }] : [],
        targets: [{ path: PATH, account: null }],
      },
    ],
    limit: 4000,
  };
}

beforeEach(() => {
  window.localStorage.clear();
  H.gets = { "/profile/share": view(false, null) };
});
afterEach(() => cleanup());

describe("the share offer says what really happened", () => {
  it("a write the daemon refused is said, never 'shared'", async () => {
    H.putReply = view(true, FALLBACK);
    render(<ProfileShareOffer tool="opencode" toolName="OpenCode" />);
    await screen.findByTestId("code-share-offer");
    fireEvent.click(screen.getByTestId("code-share-offer-yes"));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("nothing was written");
    expect(screen.queryByTestId("code-share-offer-done")).toBeNull();
  });

  it("control: a write that landed says it is shared", async () => {
    H.putReply = view(true, null);
    render(<ProfileShareOffer tool="opencode" toolName="OpenCode" />);
    await screen.findByTestId("code-share-offer");
    fireEvent.click(screen.getByTestId("code-share-offer-yes"));
    await screen.findByTestId("code-share-offer-done");
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
