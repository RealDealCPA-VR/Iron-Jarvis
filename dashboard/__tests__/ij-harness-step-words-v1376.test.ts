/**
 * v1.376.0 (IJ harness part 2, Phase 5) — a step in the agent's OWN browser
 * reads in plain words on the conversation's step rows (lib/stepWords,
 * lib/workTarget's "site" kind): "Opened localhost:5173/cart", "Clicked
 * "Add to cart"", "Typed into "Email"" (never what was typed), "Reading the
 * page" — and the folded line counts them as "used its browser", never
 * "ran 3 tools" and never the user's paired browser ("used your browser").
 */
import { describe, expect, it } from "vitest";
import { kindWords, stepCounts, stepPhrase, stepPhraseText } from "@/lib/stepWords";
import { stepTarget, toolKind } from "@/lib/workTarget";
import { toolStepLine } from "@/lib/runWords";
import { toolWords } from "@/lib/toolWords";

describe("the agent's browser on the step rows", () => {
  it("names the page by host and path, never the query string", () => {
    expect(stepTarget("page_open", { url: "http://localhost:5173/cart?token=abc#x" })).toBe("localhost:5173/cart");
    expect(stepTarget("page_open", { url: "localhost:5173/" })).toBe("localhost:5173");
    expect(stepTarget("page_screenshot", { url: "http://127.0.0.1:3000/a" })).toBe("127.0.0.1:3000/a");
    expect(stepTarget("page_open", { url: "javascript:alert(1)" })).toBeNull();
  });

  it("says each tool with its own verb", () => {
    expect(stepPhraseText(stepPhrase("page_open", "localhost:5173/cart"))).toBe("Opened localhost:5173/cart");
    expect(stepPhraseText(stepPhrase("page_click", stepTarget("page_click", { target: "Add to cart" })))).toBe(
      'Clicked "Add to cart"',
    );
    const typed = stepPhraseText(
      stepPhrase("page_type", stepTarget("page_type", { target: "Email", text: "a@b.test" })),
    );
    expect(typed).toBe('Typed into "Email"');
    expect(typed).not.toContain("a@b.test");
    expect(stepPhraseText(stepPhrase("page_read", stepTarget("page_read", {})))).toBe("Read the page");
    expect(stepPhraseText(stepPhrase("page_screenshot", null))).toBe("Took a screenshot");
    expect(kindWords("site", "page_read", { subject: false, running: true })).toBe("Reading the page");
    expect(kindWords("site", "page_open", { subject: true, running: true })).toBe("Opening");
  });

  it("counts them as its browser, not as tools and not as your browser", () => {
    expect(toolKind("page_click")).toBe("site");
    const steps = ["page_open", "page_click", "page_read"].map((name) => ({ name, ok: true }));
    expect(stepCounts(steps)).toEqual(["used its browser 3 times"]);
    expect(toolStepLine("page_click", true)).toBe("clicked on a page in its browser");
    expect(toolStepLine("page_type", false)).toBe("tried a page entry and it failed");
  });

  it("asks in the card's words", () => {
    expect(toolWords("page_open")).toBe("open a page on your computer or network in its browser");
    expect(toolWords("page_type")).toBe("type into a page in its browser");
  });
});
