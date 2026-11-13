import { describe, expect, it } from "vitest";
import { changeLines, stampKey, withNewerNotices, type CodeEvent } from "@/lib/buildApp";

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
  it("compares stamps as times: a whole-second stamp has no fraction", () => {
    // The daemon writes "…:07Z" when the microseconds are 0; as text that
    // sorts after "…:07.300000Z", so the later notice was dropped.
    const drawn = [ev({ type: "text_done", at: "2026-11-12T20:00:07Z", text: "done" })];
    const saved = [ev({ type: "notice", at: "2026-11-12T20:00:07.300000Z", text: "Model changed." })];
    expect(withNewerNotices(drawn, saved).map((e) => (e as { text?: string }).text)).toEqual(["done", "Model changed."]);
    // And the other way round: a whole-second notice is not newer than a later stamp.
    const later = [ev({ type: "text_done", at: "2026-11-12T20:00:07.300000Z", text: "done" })];
    expect(withNewerNotices(later, [ev({ type: "notice", at: "2026-11-12T20:00:07Z", text: "old" })])).toBe(later);
    expect(stampKey("2026-11-12T20:00:07Z")).toBe("2026-11-12T20:00:07.000000000Z");
    expect(stampKey("2026-11-12T20:00:07.3+00:00")).toBe("2026-11-12T20:00:07.300000000Z");
    expect(stampKey("x")).toBe("");
  });

  it("a notice stamped at the newest drawn time is added once, never twice", () => {
    const at = "2026-11-12T20:00:07.123000Z";
    const drawn = [ev({ type: "text_done", at, text: "done" })];
    const note = ev({ type: "notice", at, text: "Model changed." });
    // Same tick, not drawn yet: added.
    expect(withNewerNotices(drawn, [note]).length).toBe(2);
    // Already drawn (it came with the reply's stream): not added again.
    const both = [...drawn, note];
    expect(withNewerNotices(both, [note])).toBe(both);
  });
  it("counts only the daemon's change lines", () => {
    expect(
      changeLines([
        ev({ type: "notice", text: "Model changed: Sonnet 5.5 → Opus 5.5, from your next message." }),
        ev({ type: "notice", text: "Effort changed: High → Low." }),
        ev({ type: "notice", text: "Starting Claude Code…" }),
        ev({ type: "text_done", text: "Model changed: not a notice" }),
      ]),
    ).toBe(2);
  });
});
