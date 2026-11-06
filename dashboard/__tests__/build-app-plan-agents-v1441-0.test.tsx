/**
 * v1.441.0 — what the REAL daemon sends (fixtures/build-app-plan-agents-v1441-0.json,
 * written by tests/test_build_app_codex_plan_v14410.py) draws as it should:
 *
 *   - Codex's own Plan: the plan card every tool's plan uses — the plan itself,
 *     "Approve, then ask first" and Deny, no session-wide allow;
 *   - Codex's mode menu offers Plan; Auto review is a greyed row with its reason;
 *   - OpenCode's mode menu offers the user's own agent by its name, before
 *     Full access, and says what it does.
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  get: () => Promise.reject(new Error("unmocked")),
  post: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

import { CodeAskCard } from "@/components/buildCode/CodeAskCard";
import { ModeChip } from "@/components/buildCode/ModeChip";
import { decodeCodeEvent, decodeCodingTools, modeLabel, planAllowLabels, type OpenAsk } from "@/lib/buildApp";

const SEAM = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "build-app-plan-agents-v1441-0.json"), "utf-8").replace(/\r\n/g, "\n"),
) as {
  codex_plan_card: Record<string, unknown>;
  opencode_modes: unknown[];
  codex_modes: unknown[];
  codex_hidden: unknown[];
};

function tool(id: string, modes: unknown[], hidden: unknown[] = []) {
  const [t] = decodeCodingTools({
    harnesses: [
      { id, label: id === "codex" ? "Codex" : "OpenCode", installed: true, modes, hidden_modes: hidden, models: [] },
    ],
  });
  return t;
}

afterEach(() => cleanup());

describe("Codex's plan card", () => {
  it("shows the plan and approves into Ask first, with no session-wide allow", () => {
    const ask = decodeCodeEvent(SEAM.codex_plan_card) as OpenAsk;
    expect(ask?.type).toBe("approval");
    if (ask.type !== "approval") throw new Error("not an approval");
    expect(planAllowLabels(ask).once).toBe("Approve, then ask first");
    const answers: string[] = [];
    render(
      <CodeAskCard
        asks={[ask]}
        toolName="Codex"
        dangerousMode={false}
        armDelayMs={0}
        onAnswer={async (_a, ans) => {
          answers.push(ans.decision);
        }}
      />,
    );
    const card = screen.getByTestId("code-ask-approval");
    expect(within(card).getByTestId("code-ask-plan").textContent).toContain("append a new line containing");
    expect(within(card).getByTestId("code-ask-allow").textContent).toBe("Approve, then ask first");
    expect(within(card).queryByTestId("code-ask-allow-session")).toBeNull();
    fireEvent.click(within(card).getByTestId("code-ask-allow"));
    expect(answers).toEqual(["allow"]);
  });
});

describe("the mode menus", () => {
  it("Codex offers Plan; Auto review is greyed with its reason", () => {
    const codex = tool("codex", SEAM.codex_modes, SEAM.codex_hidden);
    expect(codex.modes.map((m) => m.id)).toContain("plan");
    const auto = codex.hiddenModes.find((m) => m.id === "auto");
    expect(auto?.reason).toMatch(/reviewer/);
    render(<ModeChip tool={codex} value="ask" onChange={() => {}} />);
    fireEvent.click(screen.getByTestId("code-mode-chip-button"));
    expect(screen.getByTestId("code-mode-chip-option-plan").textContent).toBe("Plan");
    expect(screen.getByTestId("code-mode-chip-hidden-auto").getAttribute("aria-disabled")).toBe("true");
  });

  it("OpenCode offers the user's own agent by its name, before Full access", () => {
    const oc = tool("opencode", SEAM.opencode_modes);
    expect(oc.modes.map((m) => m.id)).toEqual(["ask", "edits", "plan", "agent:docs", "full"]);
    expect(modeLabel("agent:docs", oc)).toBe("docs");
    const onChange = vi.fn();
    render(<ModeChip tool={oc} value="ask" onChange={onChange} />);
    fireEvent.click(screen.getByTestId("code-mode-chip-button"));
    const row = screen.getByTestId("code-mode-chip-option-agent:docs");
    expect(row.textContent).toBe("docs");
    const about = document.getElementById(row.getAttribute("aria-describedby") ?? "");
    expect(about?.textContent).toContain("own permission rules");
    fireEvent.click(row);
    expect(onChange.mock.calls[0][0]).toBe("agent:docs");
  });
});
