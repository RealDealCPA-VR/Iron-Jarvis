/**
 * v1.455.0 — the independent review of readiness Wave H. Each case was RED on
 * the builder's commit (1430f0c8):
 *
 *  - the Connected tab listed a saved web sign-in (class "browser") and a
 *    keyless API name answering through a plan's CLI as models of their own —
 *    THE rule is lib/demoExit.realModelRows, and an inherited row is its plan;
 *  - a destination read as connected from its TYPE alone ("telegram"), even
 *    with its last test failed or its token refused — now its last test /
 *    poll decides the word, in the warning tone when it failed.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  get: vi.fn(() => Promise.resolve({})),
  post: vi.fn(() => Promise.resolve({})),
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, health: null, refresh: () => {} }),
}));
vi.mock("next/dynamic", () => ({ default: () => () => null }));

import { ConnectionsHome, connectedRows, destinationState } from "@/components/settings/ConnectionsHome";

afterEach(() => cleanup());

describe("the Connected tab says only what is true", () => {
  it("a saved web sign-in and an inherited API name are not models of their own", () => {
    const rows = connectedRows(
      [],
      [
        { provider: "claude-cli", available: true, class: "cli" },
        { provider: "anthropic", available: true, class: "api", inherited_from: "claude-cli" },
        { provider: "chatgpt-web", available: true, class: "browser" },
        { provider: "openai", available: true, class: "api" },
      ],
      [],
    );
    expect(rows.map((r) => r.key)).toEqual(["model:claude-cli", "model:openai"]);
  });

  it("a destination's word comes from its last test or poll, never its type", () => {
    expect(destinationState({ name: "p", type: "telegram", last_poll_error: "401 Unauthorized" })).toEqual({
      state: "Needs attention",
      warn: true,
    });
    expect(destinationState({ name: "p", type: "telegram", last_test_ok: false })).toEqual({
      state: "Last test failed",
      warn: true,
    });
    expect(destinationState({ name: "p", type: "telegram", last_test_ok: true })).toEqual({
      state: "Working",
      warn: false,
    });
    expect(destinationState({ name: "p", type: "telegram" })).toEqual({ state: "Not tested yet", warn: false });
    const [row] = connectedRows([], [], [{ name: "phone", type: "telegram", last_test_ok: false }]);
    expect(row.state).toBe("Last test failed");
    expect(row.warn).toBe(true);
  });

  it("the tabs move with the arrow keys and say which one is selected", () => {
    render(<ConnectionsHome onGo={() => {}} />);
    const discover = screen.getByTestId("connections-tab-discover");
    discover.focus();
    fireEvent.keyDown(discover, { key: "ArrowRight" });
    expect(screen.getByTestId("connections-tab-connected").getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(screen.getByTestId("connections-tab-connected"));
    fireEvent.keyDown(screen.getByTestId("connections-tab-connected"), { key: "ArrowLeft" });
    expect(screen.getByTestId("connections-tab-discover").getAttribute("aria-selected")).toBe("true");
  });
});
