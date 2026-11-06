/**
 * v1.441.0 (lane g4) — a new Grok session in Build › Code offers to share the
 * profile with Grok, like Claude Code, Codex, Pi and OpenCode. The daemon now
 * lists Grok (`profile/share.CLIS["grok"]`: `<GROK_HOME or ~/.grok>/AGENTS.md`,
 * measured with `grok inspect --json`); the offer turns it on through the same
 * `PUT /profile/share` and says what really happened.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  puts: [] as unknown[],
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
    put: (_path: string, body: unknown) => {
      H.puts.push(body);
      return Promise.resolve(H.putReply);
    },
    patch: () => Promise.resolve({}),
  };
});

import { ProfileShareOffer } from "@/components/buildCode/JarvisTools";
import { shareCliOf } from "@/lib/buildApp";

const PATH = "C:\\Users\\me\\.grok\\AGENTS.md";

function view(on: boolean) {
  return {
    clis: [
      {
        cli: "grok",
        label: "Grok",
        vendor: "xAI",
        available: true,
        on,
        file_name: "AGENTS.md",
        files: on ? [{ path: PATH, account: null, exists: true, last_written: null, drift: null, held: false, error: null }] : [],
        targets: [{ path: PATH, account: null }],
      },
    ],
    limit: 4000,
  };
}

beforeEach(() => {
  window.localStorage.clear();
  H.gets = { "/profile/share": view(false) };
  H.puts = [];
});
afterEach(() => cleanup());

describe("Grok's share offer", () => {
  it("Grok is a tool the profile can be shared with", () => {
    expect(shareCliOf("grok")).toBe("grok");
    expect(shareCliOf("antigravity")).toBe("");
  });

  it("offers, turns Grok's share on and says it", async () => {
    H.putReply = view(true);
    render(<ProfileShareOffer tool="grok" toolName="Grok" />);
    await screen.findByTestId("code-share-offer");
    fireEvent.click(screen.getByTestId("code-share-offer-yes"));
    expect(await screen.findByTestId("code-share-offer-done")).toHaveTextContent("shared with Grok");
    expect(H.puts).toEqual([{ cli: "grok", on: true }]);
  });
});
