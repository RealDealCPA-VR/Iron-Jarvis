/**
 * v1.400.9: one card for a whole form (`browser_fill_form`).
 *
 * The browser tool's arguments are a list of steps; the card that approves
 * the call lists every step on its own numbered line, in words a person reads
 * ("type 18 characters into 'Email'"), from the REDACTED arguments the card is
 * handed — so the typed text never shows, only its length.
 *
 * The words are written twice — the daemon's `browser/tools.describe_form_step`
 * (the sidebar's card) and `lib/approvalPreview.formStepWords` (this card) —
 * and pinned equal on ONE fixture both test suites read:
 * `fixtures/form-steps-v1400-9.json`, written by the Python test
 * (`IJ_REGEN_FORM_STEPS=1`).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, API_BASE: "http://localhost", ijToken: () => "", post: H.post };
});

import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { approvalPreview, FORM_TOOL, formStepWords, PLAN_STEPS_MAX } from "@/lib/approvalPreview";
import { BROWSER_TOOLS } from "@/lib/toolWords";

type Case = { step: unknown; words: string };
const FIXTURE: { cases: Case[] } = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "form-steps-v1400-9.json"), "utf8").replace(/\r\n/g, "\n"),
);

const SECRET_LEN = 18;
const redacted = {
  tab_id: 42,
  steps: [
    { action: "type", target: { role: "textbox", name: "Email" }, text: "***REDACTED***", chars: SECRET_LEN },
    { action: "click", target: { role: "checkbox", name: "Remember me" } },
    { action: "press_key", key: "Enter", target: { role: "textbox", name: "Email" } },
  ],
};

afterEach(() => {
  cleanup();
  H.post.mockReset();
});

describe("the form's words: one fixture, the daemon's words", () => {
  it("formStepWords says every fixture step exactly as the daemon does", () => {
    expect(FIXTURE.cases.length).toBeGreaterThan(10);
    for (const c of FIXTURE.cases) {
      expect(formStepWords(c.step)).toBe(c.words);
    }
  });

  it("never repeats the redaction marker or the typed text", () => {
    for (const c of FIXTURE.cases) expect(formStepWords(c.step)).not.toContain("REDACTED");
    expect(formStepWords({ action: "type", target: { name: "Email" }, text: "hunter2" })).toBe(
      "type 7 characters into 'Email'",
    );
  });
});

describe("the card lists every step", () => {
  it("a form call is a numbered list labelled as a form, not a plan", () => {
    const model = approvalPreview(FORM_TOOL, redacted);
    expect(model.kind).toBe("form");
    const list = model.parts.find((p) => p.type === "plan");
    expect(list).toEqual({
      type: "plan",
      steps: [
        `type ${SECRET_LEN} characters into 'Email'`,
        "click 'Remember me'",
        "press Enter on 'Email'",
      ],
      more: 0,
      label: "form",
    });
    // The tab stays an ordinary line in its place.
    expect(model.parts.map((p) => p.type)).toEqual(["arg", "plan"]);
  });

  it("an empty or malformed step list keeps the plain preview", () => {
    expect(approvalPreview(FORM_TOOL, { steps: [] }).kind).toBe("args");
    expect(approvalPreview(FORM_TOOL, { steps: "type my email" }).kind).toBe("args");
  });

  it("past the ceiling the card says how many more", () => {
    const steps = Array.from({ length: PLAN_STEPS_MAX + 4 }, () => ({ action: "press_key", key: "Tab" }));
    const list = approvalPreview(FORM_TOOL, { steps }).parts.find((p) => p.type === "plan");
    expect(list && "more" in list ? list.more : -1).toBe(4);
  });

  it("the real card draws the list under 'form:' and never the typed text", () => {
    render(<ApprovalCard approval={{ id: "ap1", callId: "c1", tool: FORM_TOOL, args: redacted }} />);
    const preview = screen.getByTestId("approval-preview");
    expect(preview.getAttribute("data-preview")).toBe("form");
    const block = within(preview).getByTestId("approval-plan");
    expect(block.getAttribute("data-list")).toBe("form");
    expect(block.querySelector("p")!.textContent).toBe("form: 3 steps");
    const list = within(preview).getByRole("list", { name: "The form, 3 steps" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      `type ${SECRET_LEN} characters into 'Email'`,
      "click 'Remember me'",
      "press Enter on 'Email'",
    ]);
    const card = document.body.textContent ?? "";
    expect(card).not.toContain("REDACTED");
    expect(card).not.toContain("chars");
    // The card's own answers are unchanged: one Allow covers this list.
    expect(screen.getByRole("button", { name: "Allow once" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Deny" })).toBeTruthy();
  });

  it("the plan ask keeps its own word", () => {
    render(<ApprovalCard approval={{ id: "ap2", callId: "c2", tool: "approve_plan", args: { steps: ["a", "b"] } }} />);
    const block = screen.getByTestId("approval-plan");
    expect(block.getAttribute("data-list")).toBe("plan");
    expect(screen.getByRole("list", { name: "The plan, 2 steps" })).toBeTruthy();
  });

  it("is a browser tool, so the card offers the tab answer like the other page actions", () => {
    expect(BROWSER_TOOLS.has(FORM_TOOL)).toBe(true);
  });
});
