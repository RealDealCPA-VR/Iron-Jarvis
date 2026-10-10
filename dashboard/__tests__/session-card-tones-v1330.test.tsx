/**
 * The board card is drawn on calm surfaces (the chat drawer's Board and the
 * /agents board), so its last literal hues, half-pixel chips and dash-aside
 * copy went in v1.330.0: Reject is tone-danger beside the tone-success
 * Approve, the waiting link is tone-warn, "Attached" is tone-success, the
 * agent and "Team of N" chips are 11px, and the Retry / revise toasts are
 * plain sentences.
 */
import { describe, expect, it } from "vitest";
import { asides, readSrc } from "./helpers/dashGuard";

const FILE = "components/kanban/SessionCard.tsx";

describe("SessionCard: tone tokens, whole pixels, plain copy (v1.330.0)", () => {
  const src = readSrc(FILE);

  it("uses no literal rose / amber / emerald hues", () => {
    expect(src.match(/\b(?:text|bg|border)-(?:rose|amber|emerald)-\d{3}/g) ?? []).toEqual([]);
  });

  it("draws no half-pixel text", () => {
    expect(src.match(/text-\[\d+\.\d+px\]/g) ?? []).toEqual([]);
  });

  it("Reject reads in tone-danger", () => {
    const i = src.indexOf("onReject?.();");
    expect(i).toBeGreaterThan(-1);
    expect(src.slice(i, i + 500)).toContain("text-tone-danger");
  });

  it("has no spaced em-dash asides in user-visible copy", () => {
    expect(asides(FILE)).toEqual([]);
  });

  it("the retry toast is plain", () => {
    expect(src).toContain('"Retry started. A fresh run is underway."');
  });
});
