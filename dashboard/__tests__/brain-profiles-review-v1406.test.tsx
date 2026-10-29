/**
 * v1.406.0 review: a save the memory base answered but that could not be
 * read back is NOT queued on the Memory page (a second copy could duplicate
 * it), so the receipt must not send the user there. Only a save that was
 * really kept as an unsaved change (`kept: true`) says "Open Memory".
 */
import { describe, expect, it } from "vitest";

import { decodeBrainReceipt, receiptBrainLine } from "@/lib/brainProfiles";

const BASE = { name: "Work brain", base: "Notion", searched: 1, guide: "", saved: [], changed: [] };

describe("the receipt's unsaved line says where the note is", () => {
  it("a note kept on the Memory page points there", () => {
    const r = decodeBrainReceipt({
      ...BASE,
      unsaved: [{ title: "Q4 plan", reason: "Notion is unavailable", kept: true }],
    });
    expect(r?.unsaved[0].kept).toBe(true);
    expect(receiptBrainLine(r)?.title).toMatch(/Open Memory/);
  });

  it("a note the base answered for but could not show back never points to Memory", () => {
    const r = decodeBrainReceipt({
      ...BASE,
      unsaved: [{ title: "Q4 plan", reason: "could not be confirmed", kept: false }],
    });
    const line = receiptBrainLine(r);
    expect(line?.title).not.toMatch(/Open Memory/);
    expect(line?.title).toMatch(/Notion/);
  });

  it("an older daemon's unsaved row (no kept key) is not claimed to be on the Memory page", () => {
    const r = decodeBrainReceipt({ ...BASE, unsaved: [{ title: "Q4 plan", reason: "x" }] });
    expect(r?.unsaved[0].kept).toBe(false);
  });
});
