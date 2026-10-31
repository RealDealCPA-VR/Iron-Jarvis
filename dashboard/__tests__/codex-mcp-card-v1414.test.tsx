/**
 * v1.414.0 (fix) — what the REAL daemon sends (fixtures/codex-mcp-card-v1414.json,
 * written by tests/test_build_app_codex_mcp_v1414.py) draws as it should:
 *
 *   - Codex asking before an MCP tool call (Iron Jarvis memory) is an
 *     ordinary card: the tool in words, its arguments, Allow once · Allow for
 *     this session · Deny — never declined for the user;
 *   - a sign-in failure (the live "OAuth session expired" text) says the tool
 *     must sign in again and offers the ONE Sign in press, never "Retry".
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
import { CodeConversation } from "@/components/buildCode/CodeConversation";
import { decodeCodeEvent, foldConversation, type OpenAsk } from "@/lib/buildApp";

const SEAM = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "codex-mcp-card-v1414.json"), "utf-8").replace(/\r\n/g, "\n"),
) as { approval: Record<string, unknown>; sign_in_error: Record<string, unknown> };

afterEach(() => cleanup());

describe("Codex's MCP tool-call approval is a card", () => {
  it("names the tool and its arguments and offers the three answers", async () => {
    const ask = decodeCodeEvent(SEAM.approval) as OpenAsk;
    expect(ask?.type).toBe("approval");
    // One card per answer (a pressed card waits for its answer to land).
    for (const [testId, decision] of [
      ["code-ask-allow", "allow"],
      ["code-ask-allow-session", "allow_session"],
      ["code-ask-deny", "deny"],
    ] as const) {
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
      expect(card.textContent).toContain('Codex wants to: Search Iron Jarvis memory for "project codename"');
      expect(card.textContent).toContain("query: project codename");
      fireEvent.click(within(card).getByTestId(testId));
      expect(answers).toEqual([decision]);
      cleanup();
    }
  });
});

describe("a sign-in failure in the Code view", () => {
  it("says sign in again, offers the Sign in press, never Retry", () => {
    const err = decodeCodeEvent(SEAM.sign_in_error);
    const user = decodeCodeEvent({ type: "user", text: "hi" });
    const { items } = foldConversation([user!, err!]);
    render(<CodeConversation items={items} folder="C:\\work" tool="claude-code" />);
    const box = screen.getByTestId("code-error");
    expect(box.dataset.fixKind).toBe("sign_in");
    expect(box.textContent).toContain("Claude Code needs to sign in again");
    expect(within(box).getByTestId("code-sign-in-claude-cli")).toBeInTheDocument();
    expect(box.textContent).not.toMatch(/Retry|another model/);
  });
});
