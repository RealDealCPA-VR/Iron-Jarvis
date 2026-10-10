/**
 * v1.329.0 (calm chat W8 J2) — the "@" menu's chat rows use the chat lists'
 * same-title rule (lib/sameTitleRows), through lib/chatRefsRows.
 *
 * The closing audit (shots/fa2day__atup__phone.png): on a phone, same-titled
 * chats in the @ menu showed a clipped "Yesterday · 8:48…" AND an age "3h",
 * the double label W6 H1 removed from both sidebar lists. Now every row draws
 * ONE quiet part: a shared title gets one short time or day in place of the
 * age; a title of its own gets the age, as the lists do.
 *
 * The page's rendering (twin vs age test ids, tooltips, keys) is pinned in
 * chat-at-files-v1329 and chat-at-menu-fit-v1328; this file pins the rule.
 */

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { chatRefRowParts } from "@/lib/chatRefsRows";
import { sameTitleLabels, sameTitleTooltip } from "@/lib/sameTitleRows";
import { formatAge } from "@/lib/threadStatus";

const NOW = new Date(2026, 9, 9, 23, 0).getTime();
const iso = (d: Date) => d.toISOString();
const at = (day: number, h: number, m: number, s = 0) => iso(new Date(2026, 9, day, h, m, s));

const read = (rel: string) => readFileSync(join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");

describe("the @ menu's chat rows: one quiet part, the lists' rule", () => {
  it("the audit's phone case: four same-titled chats from yesterday read as times, never a day + time + age", () => {
    const rows = [
      { id: "a", title: "Check what changed in the repo", updatedAt: at(8, 20, 48, 5) },
      { id: "b", title: "Check what changed in the repo", updatedAt: at(8, 20, 47, 40) },
      { id: "c", title: "Check what changed in the repo", updatedAt: at(8, 18, 11, 46) },
      { id: "d", title: "check what changed in the repo ", updatedAt: at(8, 18, 11, 2) },
    ];
    const out = chatRefRowParts(rows, NOW);
    expect([...out.values()].map((p) => `${p.kind}:${p.text}`)).toEqual([
      "twin:8:48 PM",
      "twin:8:47 PM",
      "twin:6:11 PM",
      "twin:6:11 PM",
    ]);
    // Never the old long part: no "·", no seconds, no age beside it.
    for (const p of out.values()) {
      expect(p.text).not.toContain("·");
      expect(p.text).not.toMatch(/\d+:\d+:\d+/);
    }
    // The exact time (with seconds) rides the tooltip.
    expect(out.get("a")?.tooltip).toBe("Yesterday · 8:48:05 PM");
  });

  it("matches the lists' labels exactly (one rule, not a copy)", () => {
    const rows = [
      { id: "a", title: "Same", updatedAt: at(9, 9, 5) },
      { id: "b", title: "same", updatedAt: at(9, 14, 30) },
      { id: "c", title: "Same", updatedAt: at(8, 9, 0) },
      { id: "d", title: "Twin", updatedAt: at(9, 8, 0) },
      { id: "e", title: "Twin", updatedAt: iso(new Date(2026, 9, 2, 8, 0)) },
    ];
    const lists = sameTitleLabels(
      rows.map((r) => ({ id: r.id, title: r.title, updated_at: r.updatedAt })),
      NOW,
    );
    const menu = chatRefRowParts(rows, NOW);
    for (const r of rows) {
      expect(menu.get(r.id)).toEqual({
        kind: "twin",
        text: lists.get(r.id),
        tooltip: sameTitleTooltip(r.updatedAt, NOW),
      });
    }
    expect(menu.get("a")?.text).toBe("9:05 AM");
    expect(menu.get("c")?.text).toBe("Yesterday");
    expect(menu.get("e")?.text).toBe("Oct 2");
  });

  it("a title of its own shows the list's age; no usable time draws nothing", () => {
    const rows = [
      { id: "a", title: "Lease options", updatedAt: iso(new Date(NOW - 12 * 60_000)) },
      { id: "b", title: "Menu prices", updatedAt: iso(new Date(NOW - 3 * 3_600_000)) },
      { id: "c", title: "Payroll", updatedAt: null },
      { id: "d", title: "Payroll notes", updatedAt: "not a time" },
    ];
    const out = chatRefRowParts(rows, NOW);
    expect(out.get("a")).toEqual({ kind: "age", text: "12m", tooltip: "" });
    expect(out.get("b")).toEqual({ kind: "age", text: formatAge(rows[1].updatedAt, NOW), tooltip: "" });
    expect(out.get("b")?.text).toBe("3h");
    expect(out.has("c")).toBe(false);
    expect(out.has("d")).toBe(false);
  });

  it("same title is judged over the rows the menu draws: a narrowed twin alone shows its age", () => {
    const both = [
      { id: "a", title: "Check what changed", updatedAt: iso(new Date(NOW - 20 * 60_000)) },
      { id: "b", title: "Check what changed", updatedAt: iso(new Date(NOW - 40 * 60_000)) },
    ];
    expect(chatRefRowParts(both, NOW).get("a")?.kind).toBe("twin");
    expect(chatRefRowParts([both[0]], NOW).get("a")).toEqual({ kind: "age", text: "20m", tooltip: "" });
  });

  it("an untitled chat shares its title with another untitled chat", () => {
    const rows = [
      { id: "a", title: "Untitled chat", updatedAt: at(9, 10, 0) },
      { id: "b", title: "Untitled chat", updatedAt: at(9, 11, 0) },
    ];
    const out = chatRefRowParts(rows, NOW);
    expect(out.get("a")?.text).toBe("10:00 AM");
    expect(out.get("b")?.text).toBe("11:00 AM");
  });
});

describe("the page draws ONE part per chat row (source)", () => {
  const page = read("app/chat/page.tsx");
  const start = page.indexOf('data-testid="at-menu-chats"');
  const end = page.indexOf("</div>", page.indexOf("chatMatches.map", start));
  const block = page.slice(start, end);

  it("reads the rule from lib/chatRefsRows, with no project or day part drawn beside it", () => {
    expect(start).toBeGreaterThan(0);
    expect(page).toContain('import { chatRefRowParts } from "@/lib/chatRefsRows";');
    expect(page).toMatch(/chatRefRowParts\(chatMatches,/);
    expect(block).toContain('part.kind === "twin" ? "chat-ref-twin" : "chat-ref-age"');
    expect(block).not.toContain("chat-ref-where");
    expect(block).not.toContain("formatAge(");
    // Exactly one <time> per row.
    expect(block.match(/<time\b/g) ?? []).toHaveLength(1);
  });
});
