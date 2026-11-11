/**
 * v1.474.0 — Hand off in Build › Code.
 *
 * - The Iron Jarvis tools chip offers "Hand off" with words that say what it
 *   does, and never in a helper session.
 * - The session view's `helpers` and `handed_from` are decoded and drawn as
 *   quiet lines with Open.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({ patches: [] as { path: string; body: unknown }[] }));
vi.mock("@/lib/api", () => ({
  get: vi.fn(async () => ({})),
  put: vi.fn(async () => ({})),
  patch: vi.fn(async (path: string, body: { capabilities: Record<string, boolean> }) => {
    H.patches.push({ path, body });
    return { id: "s1", harness: "claude-code", capabilities: { memory: false, browser: false, ...body.capabilities } };
  }),
}));

import { JarvisToolsChip } from "@/components/buildCode/JarvisTools";
import { HelperLines } from "@/components/buildCode/HelperLines";
import {
  decodeCodeSession,
  decodeCodeSessionView,
  jarvisToolHint,
  jarvisToolsWords,
} from "@/lib/buildApp";

afterEach(() => {
  cleanup();
  H.patches.length = 0;
});

const session = (extra: Record<string, unknown> = {}) =>
  decodeCodeSession({ id: "s1", harness: "claude-code", capabilities: { memory: false, browser: false }, ...extra })!;

describe("the Hand off box", () => {
  it("is offered, says what it does, and turns on through the session's boxes", async () => {
    const changed = vi.fn();
    render(<JarvisToolsChip session={session()} toolName="Claude Code" onChanged={changed} onInfo={() => {}} />);
    fireEvent.click(screen.getByTestId("code-jarvis-tools"));
    expect(screen.getByText("Hand off")).toBeTruthy();
    expect(screen.getByText(jarvisToolHint("handoff", "Claude Code", "claude-code"))).toBeTruthy();
    fireEvent.click(screen.getByTestId("code-jarvis-tool-handoff"));
    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(H.patches).toEqual([{ path: "/build/app/sessions/s1", body: { capabilities: { handoff: true } } }]);
    expect(changed.mock.calls[0][0].jarvisTools.handoff).toBe(true);
  });

  it("is never offered in a helper session", () => {
    render(
      <JarvisToolsChip session={session({ handed_from: "p1" })} toolName="Codex" onChanged={() => {}} onInfo={() => {}} />,
    );
    fireEvent.click(screen.getByTestId("code-jarvis-tools"));
    expect(screen.queryByTestId("code-jarvis-tool-handoff")).toBeNull();
    expect(screen.getByTestId("code-jarvis-tool-memory")).toBeTruthy();
  });

  it("names it when on", () => {
    expect(jarvisToolsWords({ memory: false, browser: false, handoff: true })).toBe("Iron Jarvis tools: Hand off");
  });

  it("the hint says helpers never get Full access and ask the user themselves", () => {
    const h = jarvisToolHint("handoff", "Codex", "codex");
    expect(h).toMatch(/never gets Full access/);
    expect(h).toMatch(/its own session/);
  });

  it("the hint says the handed-over work goes to the other tool's own service (review)", () => {
    // Like Memory's line: the box says who sees the words BEFORE it is ticked.
    const h = jarvisToolHint("handoff", "Pi", "pi");
    expect(h).toMatch(/goes to that tool's own service/);
    expect(h).not.toMatch(/before what its rules/);
  });
});

describe("helpers in the session view", () => {
  it("decodes helpers and the session a helper belongs to", () => {
    const v = decodeCodeSessionView({
      session: { id: "s1", harness: "claude-code" },
      events: [],
      helpers: [
        { id: "h1", title: "Helper: fix cart", label: "Codex", state: "working" },
        { id: "h2", title: "Helper: tests", label: "Pi", state: "nonsense" },
        { id: "h1", title: "dup", label: "Codex", state: "done" },
      ],
      handed_from: { id: "p1", title: "Shop", label: "Claude Code" },
    })!;
    expect(v.helpers.map((h) => [h.id, h.state])).toEqual([
      ["h1", "working"],
      ["h2", "idle"],
    ]);
    expect(v.handedFrom).toEqual({ id: "p1", title: "Shop", label: "Claude Code" });
    const old = decodeCodeSessionView({ session: { id: "s1", harness: "claude-code" }, events: [] })!;
    expect(old.helpers).toEqual([]);
    expect(old.handedFrom).toBeNull();
  });

  it("draws each helper with how it stands, and Open goes to it", () => {
    const open = vi.fn();
    render(
      <HelperLines
        helpers={[
          { id: "h1", title: "Helper: fix cart", label: "Codex", state: "waiting" },
          { id: "h2", title: "Helper: tests", label: "Pi", state: "done" },
        ]}
        handedFrom={null}
        onOpen={open}
      />,
    );
    expect(screen.getByText(/2 helpers/)).toBeTruthy();
    expect(screen.getByTestId("code-helper-state-h1").textContent).toBe("Waiting for you");
    expect(screen.getByTestId("code-helper-state-h2").textContent).toBe("Done");
    fireEvent.click(screen.getByTestId("code-helper-open-h1"));
    expect(open).toHaveBeenCalledWith("h1");
  });

  it("a helper links back to the session that started it", () => {
    const open = vi.fn();
    render(<HelperLines helpers={[]} handedFrom={{ id: "p1", title: "Shop", label: "Claude Code" }} onOpen={open} />);
    expect(screen.getByTestId("code-handed-from").textContent).toMatch(/Helper for Claude Code's session/);
    fireEvent.click(screen.getByTestId("code-handed-from-open"));
    expect(open).toHaveBeenCalledWith("p1");
  });

  it("draws nothing for a session with no helpers", () => {
    const { container } = render(<HelperLines helpers={[]} handedFrom={null} />);
    expect(container.innerHTML).toBe("");
  });
});
