/**
 * v1.439.0 — "Grok isn't signed in" in Build › Code offers the ONE Sign in
 * press (Grok's own, provider grok-cli), drawn from what the REAL daemon
 * sends (fixtures/build-app-parity-v1439.json, written by
 * tests/test_build_app_parity_v1439.py). Pi and OpenCode sign in per
 * provider in their own screens: no press for them.
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  get: () => Promise.reject(new Error("unmocked")),
  post: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

import { CodeConversation } from "@/components/buildCode/CodeConversation";
import { decodeCodeEvent, foldConversation, signInProvider } from "@/lib/buildApp";

const SEAM = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "build-app-parity-v1439.json"), "utf-8").replace(/\r\n/g, "\n"),
) as { grok_sign_in: Record<string, unknown> };

afterEach(() => cleanup());

describe("Grok's sign-in failure in the Code view", () => {
  it("offers Grok's own Sign in press, never Retry", () => {
    const err = decodeCodeEvent(SEAM.grok_sign_in);
    const user = decodeCodeEvent({ type: "user", text: "hi" });
    const { items } = foldConversation([user!, err!]);
    render(<CodeConversation items={items} folder="C:\\work" tool="grok" />);
    const box = screen.getByTestId("code-error");
    expect(box.dataset.fixKind).toBe("sign_in");
    expect(box.textContent).toContain("Grok isn't signed in");
    expect(within(box).getByTestId("code-sign-in-grok-cli")).toBeInTheDocument();
    expect(box.textContent).not.toMatch(/Retry/);
  });

  it("names the press for each tool that has one", () => {
    expect(signInProvider("grok")).toBe("grok-cli");
    expect(signInProvider("claude-code")).toBe("claude-cli");
    expect(signInProvider("pi")).toBe("");
    expect(signInProvider("opencode")).toBe("");
  });
});
