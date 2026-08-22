import { describe, it, expect } from "vitest";
import { CHAT_EXAMPLES, pickExamples } from "@/components/chat/examples";

// v1.198.0 — empty-state example chips extracted from chat/page.tsx into a
// curated, rotating module. These tests pin the contract: the anchor prompt
// always leads, picks rotate without repeats, and the curated list keeps
// showcasing the real differentiators (offline PII redaction, keyless web
// search) instead of regressing to generic filler.

describe("CHAT_EXAMPLES curation", () => {
  it("has at least 8 curated prompts", () => {
    expect(CHAT_EXAMPLES.length).toBeGreaterThanOrEqual(8);
  });

  it("keeps the differentiator prompts (redaction + web search)", () => {
    expect(CHAT_EXAMPLES).toContain("Redact the personal info in a document");
    expect(CHAT_EXAMPLES).toContain(
      "Search the web for today's IRS mileage rate",
    );
  });

  it("anchors on the honest first question", () => {
    expect(CHAT_EXAMPLES[0]).toBe("What can you do?");
  });

  it("keeps the SSR slice contract: the prerendered slice(0, 4) and any pick lead with the same anchor", () => {
    // chat/page.tsx initializes with CHAT_EXAMPLES.slice(0, 4) (deterministic,
    // hydration-safe on the prerendered route) and swaps to pickExamples()
    // after mount — both must start with the anchor or the first chip visibly
    // changes on hydration.
    const ssrInitial = CHAT_EXAMPLES.slice(0, 4);
    expect(ssrInitial).toHaveLength(4);
    expect(ssrInitial[0]).toBe("What can you do?");
    expect(ssrInitial[0]).toBe(pickExamples()[0]);
  });

  it("has no duplicate entries", () => {
    expect(new Set(CHAT_EXAMPLES).size).toBe(CHAT_EXAMPLES.length);
  });
});

describe("pickExamples", () => {
  it("always returns the anchor first", () => {
    for (let i = 0; i < 100; i++) {
      expect(pickExamples()[0]).toBe("What can you do?");
    }
  });

  it("defaults to 4 prompts (1 anchor + 3 rotating)", () => {
    expect(pickExamples()).toHaveLength(4);
  });

  it("honors an explicit count", () => {
    expect(pickExamples(2)).toHaveLength(2);
    expect(pickExamples(CHAT_EXAMPLES.length)).toHaveLength(
      CHAT_EXAMPLES.length,
    );
  });

  it("never repeats a prompt within one pick, across many runs", () => {
    for (let i = 0; i < 200; i++) {
      const picked = pickExamples();
      expect(new Set(picked).size).toBe(picked.length);
    }
  });

  it("only ever returns members of CHAT_EXAMPLES", () => {
    for (let i = 0; i < 200; i++) {
      for (const prompt of pickExamples()) {
        expect(CHAT_EXAMPLES).toContain(prompt);
      }
    }
  });

  it("actually rotates: many runs surface more than the anchor's fixed trio", () => {
    const seen = new Set<string>();
    for (let i = 0; i < 500; i++) {
      for (const prompt of pickExamples()) seen.add(prompt);
    }
    // With 7 rotating candidates and 500 draws of 3, missing any candidate
    // is astronomically unlikely — this pins that rotation is real.
    expect(seen.size).toBe(CHAT_EXAMPLES.length);
  });
});
