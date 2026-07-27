import { describe, expect, it } from "vitest";

import { slashTokenAt, spliceToken } from "../lib/slash";

/**
 * REPORTED: "slash commands only seem to work if they are the first thing typed
 * in the chat, but it should work if I have a prompt and then use some specific
 * skills within the prompt or message — that same thing should apply anywhere in
 * the chat I choose."
 */
describe("slashTokenAt — where the picker opens", () => {
  it("opens on a leading /, as it always did", () => {
    expect(slashTokenAt("/tax", 4)).toEqual({ start: 0, end: 4, query: "tax" });
  });

  it("opens MID-MESSAGE — the whole point of v1.105.0", () => {
    const text = "draft a client memo /tax";
    expect(slashTokenAt(text, text.length)).toEqual({
      start: 20,
      end: 24,
      query: "tax",
    });
  });

  it("opens on a bare / with no query yet", () => {
    expect(slashTokenAt("summarize this /", 16)?.query).toBe("");
  });

  it("opens after a newline, not just a space", () => {
    expect(slashTokenAt("line one\n/tax", 13)?.start).toBe(9);
  });

  it("matches case-insensitively", () => {
    expect(slashTokenAt("/TaxResearch", 12)?.query).toBe("taxresearch");
  });
});

describe("slashTokenAt — what must NOT flicker a dropdown", () => {
  // Each of these has a "/" that follows a non-space character. If any starts
  // matching, the picker pops open while someone types an ordinary sentence.
  it.each([
    ["a URL", "see http://example.com"],
    ["a Windows path", "open C:/Users/VR"],
    ["and/or", "approve and/or deny"],
    ["a ratio", "we run 24/7"],
    ["a date", "due 4/15"],
    ["a nested path", "/foo/bar"],
  ])("stays closed for %s", (_label, text) => {
    expect(slashTokenAt(text, text.length)).toBeNull();
  });

  it("stays closed on empty input", () => {
    expect(slashTokenAt("", 0)).toBeNull();
  });

  it("closes once the caret moves PAST the token", () => {
    const text = "hello /tax world";
    expect(slashTokenAt(text, 10)).not.toBeNull(); // caret just after "tax"
    expect(slashTokenAt(text, text.length)).toBeNull(); // caret after "world"
  });

  it("reads the token at the caret, not the last one in the text", () => {
    const text = "/alpha and /beta";
    expect(slashTokenAt(text, 6)?.query).toBe("alpha");
  });
});

describe("slashTokenAt — a stale caret can never throw or over-read", () => {
  it("clamps a caret past the end of the text", () => {
    // Sending clears the composer without moving the caret; the offset outruns
    // the string it indexes into.
    expect(slashTokenAt("", 40)).toBeNull();
    expect(slashTokenAt("/tax", 999)).toEqual({ start: 0, end: 4, query: "tax" });
  });

  it("clamps a negative caret", () => {
    expect(slashTokenAt("/tax", -3)).toBeNull();
  });
});

describe("spliceToken — picking must not eat the prompt", () => {
  it("removes ONLY the token and keeps the message", () => {
    const text = "draft a client memo /tax";
    expect(spliceToken(text, slashTokenAt(text, text.length))).toBe(
      "draft a client memo ",
    );
  });

  it("keeps text on BOTH sides of the token", () => {
    const text = "use /fin on this file";
    // Caret sits at the end of "/fin", which is how the picker is open at all.
    expect(spliceToken(text, slashTokenAt(text, 8))).toBe("use on this file");
  });

  it("does not leave a double space where a mid-sentence token was", () => {
    const text = "use /fin on this file";
    expect(spliceToken(text, slashTokenAt(text, 8))).not.toContain("  ");
  });

  it("keeps the trailing space when the token ended the message", () => {
    // The caret lands here and the user keeps typing — eating this space would
    // glue the next word onto the previous one.
    const text = "draft a memo /fin";
    expect(spliceToken(text, slashTokenAt(text, text.length))).toBe("draft a memo ");
  });

  it("empties a composer that held nothing but the token", () => {
    expect(spliceToken("/tax", slashTokenAt("/tax", 4))).toBe("");
  });
});
