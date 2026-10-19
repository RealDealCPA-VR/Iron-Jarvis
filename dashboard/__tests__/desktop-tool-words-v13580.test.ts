/**
 * v1.358.0 lane 8E — the desktop's closed-window ask toast and the approval
 * card say ONE thing for one ask.
 *
 * desktop/main.js runs in Electron's main process and cannot import this
 * TypeScript, so it keeps a copy of the card's table. The copy is held equal
 * from both sides through ONE fixture of tool ids and their words
 * (fixtures/desktop-tool-words-v13580.json): this file feeds every id to the
 * REAL `toolWords` here; tests/test_desktop_words_v13580.py feeds the same ids
 * to the desktop's real functions under node and reads both tables entry for
 * entry. Before this, `edit_file` read "use edit file" in the toast beside the
 * card's "change a file", and an app's tool read "use mcp  github  create
 * issue".
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { BROWSER_TOOLS, toolWords } from "@/lib/toolWords";

type Row = { tool: string; kind: "table" | "browser" | "app" | "unknown" | "ahead"; card: string | null; desktop: string };

const rows: Row[] = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "desktop-tool-words-v13580.json"), "utf-8"),
).rows;

describe("one set of words for an ask, card and desktop toast (v1.358.0)", () => {
  it("the card says the fixture's words for every tool it describes", () => {
    for (const row of rows.filter((r) => r.kind !== "ahead")) {
      expect(toolWords(row.tool), row.tool).toBe(row.card);
    }
  });

  // v1.358.0 (integrate, on purpose): the card gained the document writers
  // and the media maker, so no tool is described on the desktop only — the
  // "ahead" rows became ordinary table rows with the same words on both.
  it("no tool is described on the desktop only: the document writers and media read the same words", () => {
    expect(rows.filter((r) => r.kind === "ahead")).toEqual([]);
    for (const tool of ["write_document", "excel_edit", "generate_media"]) {
      const row = rows.find((r) => r.tool === tool);
      expect(row?.kind, tool).toBe("table");
      expect(toolWords(tool), tool).toBe(row?.desktop);
    }
  });

  it("covers all fourteen built-in browser tools and every kind of id", () => {
    const browsers = rows.filter((r) => r.kind === "browser").map((r) => r.tool);
    expect(new Set(browsers)).toEqual(new Set(BROWSER_TOOLS));
    expect(new Set(rows.map((r) => r.kind))).toEqual(new Set(["table", "browser", "app", "unknown"]));
  });

  it("anti-vacuity: an id nobody described has no words on the card (the desktop names it as it is)", () => {
    const unknown = rows.filter((r) => r.kind === "unknown");
    expect(unknown.length).toBeGreaterThan(2);
    for (const row of unknown) {
      expect(toolWords(row.tool)).toBeNull();
      expect(row.desktop.startsWith("use ")).toBe(true);
    }
  });
});
