/**
 * v1.439.0 review — the Markdown export's two rules the builder's pins left
 * open: a message Iron Jarvis sent BY ITSELF to carry a reply on (after a
 * restart, a reset, an account move) is not the user's and is left out; a
 * note the user typed while the tool worked IS theirs and is kept.
 */

import { describe, expect, it } from "vitest";
import { conversationMarkdown, type CodeEvent } from "@/lib/buildApp";

const at = "2026-11-06T09:00:00Z";

describe("export as Markdown: whose words are in the file", () => {
  it("leaves out a message Iron Jarvis sent by itself to carry a reply on", () => {
    const events = [
      { type: "user", at, text: "Fix the build", files: [] },
      { type: "text_done", at, id: "m1", text: "Working on it." },
      {
        type: "user",
        at,
        text: "Carry on with the reply the restart cut off.",
        files: [],
        carry: { why: "restart", fromAccount: "", toAccount: "", accountName: "", group: "" },
      },
      { type: "text_done", at, id: "m2", text: "Done." },
    ] as unknown as CodeEvent[];
    const md = conversationMarkdown("Fix", events, "Claude Code");
    expect(md).toContain("Fix the build");
    expect(md).toContain("Done.");
    expect(md).not.toContain("Carry on with the reply");
  });

  it("keeps a note the user typed while the tool worked", () => {
    const events = [
      { type: "user", at, text: "Fix the build", files: [] },
      { type: "steer", at, id: "n1", state: "read", text: "use the new API", files: [], how: "" },
      { type: "text_done", at, id: "m1", text: "Done." },
    ] as unknown as CodeEvent[];
    const md = conversationMarkdown("Fix", events, "Claude Code");
    expect(md).toContain("> Note: use the new API");
  });
});
