import { describe, expect, it } from "vitest";
import { withNewerNotices, type CodeEvent } from "@/lib/buildApp";

const ev = (o: Record<string, unknown>) => o as unknown as CodeEvent;

describe("withNewerNotices (v1.481.1)", () => {
  it("adds only notices newer than everything drawn", () => {
    const drawn = [ev({ type: "user", at: "2026-11-11T10:00:00Z", text: "hi" }), ev({ type: "text", at: "2026-11-11T10:00:05Z", text: "h" })];
    const saved = [
      ev({ type: "notice", at: "2026-11-11T09:59:00Z", text: "old" }),
      ev({ type: "text_done", at: "2026-11-11T10:00:09Z", text: "hello" }),
      ev({ type: "notice", at: "2026-11-11T10:01:00Z", text: "Model changed: A → B." }),
    ];
    const out = withNewerNotices(drawn, saved);
    expect(out.slice(0, 2)).toEqual(drawn);
    expect(out.slice(2).map((e) => (e as { text?: string }).text)).toEqual(["Model changed: A → B."]);
    // Nothing newer: the very same array back.
    expect(withNewerNotices(drawn, saved.slice(0, 2))).toBe(drawn);
  });
});
