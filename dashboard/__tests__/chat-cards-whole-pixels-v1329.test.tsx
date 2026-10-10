/**
 * v1.329.0 — calm chat wave 5, S3: the other chat cards use whole pixels and
 * theme tokens.
 *
 * The re-audit after wave 4 found half-pixel text sizes left in the cards
 * that are not part of the calm surfaces (DocPreview 14, RunResultCard 10,
 * GoalContractCard 9, WorkflowDraftCard 9, DraftCard 6, BatchSuggestCard 6,
 * EmailComposeDialog 4, CompactionCard 3, PreferenceSuggestion 3,
 * ArtifactsRail 2, ShareChatDialog 1) and literal hues (text-amber-300,
 * text-rose-300, bg-rose-500/[0.06] …) that only read on Daylight through the
 * generated light overrides, or not at all. The calm surfaces use the scale
 * 11 / 12 / 13 / 14 px and the tone tokens (text-tone-warn, bg-tone-danger/10
 * …), which every theme re-inks for itself.
 *
 * This file reads the SOURCE of every chat component and fails when one gains
 * a half-pixel text size, a literal Tailwind hue, or an arbitrary colour
 * value again. The allowlist below is short and each entry says why; an entry
 * that no longer matches anything fails too, so the list cannot rot.
 */

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const CHAT_DIR = path.join(__dirname, "..", "components", "chat");

/** Files OUTSIDE components/chat that a chat card renders classes from, keyed
 *  by the name the scan reports. Wave 6 (H4): WorkflowDraftCard draws its
 *  agent-type and step-kind chips from components/workflow/agents.ts
 *  (AGENT_META / KIND_META), so those class strings are chat-card classes
 *  too and the guard reads that file as well. */
const EXTRA: Record<string, string> = {
  "workflow/agents.ts": path.join(__dirname, "..", "components", "workflow", "agents.ts"),
};

const read = (name: string) =>
  readFileSync(EXTRA[name] ?? path.join(CHAT_DIR, name), "utf8").replace(/\r\n/g, "\n");

/** The cards this wave moved onto whole pixels and tone tokens. Each must
 *  exist and be scanned (anti-vacuity: a rename must not silently drop one). */
const CARDS = [
  "ArtifactsRail.tsx",
  "BatchSuggestCard.tsx",
  "CompactionCard.tsx",
  "DocPreview.tsx",
  "DraftCard.tsx",
  "EmailComposeDialog.tsx",
  "GoalContractCard.tsx",
  "PreferenceSuggestion.tsx",
  "PreflightNote.tsx",
  "RunResultCard.tsx",
  "SamplingCard.tsx",
  "ShareChatDialog.tsx",
  "WorkflowDraftCard.tsx",
];

/** Files this guard does not read yet, each with the reason. */
const NOT_SCANNED: Record<string, string> = {
  "TurnReceipt.tsx":
    "the page lane owns it this wave; its folded-row amber is tracked by that lane's own pins",
};

/** Exact class tokens a file may keep, with the reason. */
const ALLOW: Array<{ file: string; token: string; why: string }> = [
  {
    file: "DocPreview.tsx",
    token: "bg-[#50545a]",
    why: "the grey desk behind the paper page; the sandboxed Word preview paints the same grey in its own CSS (an iframe cannot read theme tokens), so both previews match",
  },
  {
    file: "DocPreview.tsx",
    token: "bg-[#3b3e44]",
    why: "the image viewer's neutral backdrop under the transparency checkerboard; a themed surface would make transparent pixels read as the theme's colour",
  },
  {
    file: "DocPreview.tsx",
    token: "text-blue-700",
    why: "link colour inside the document page (Word's link blue on the sheet), part of the document's own look, not the app's",
  },
];

const HUES =
  "slate|gray|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose";
// `accent` is the form-control tint (a checkbox's `accent-cyan-400`): it is a
// colour like the rest and Daylight has no light rule for it either.
const PROPS =
  "text|bg|border|ring|from|via|to|fill|stroke|outline|decoration|divide|placeholder|shadow|caret|accent";

const HALF_PIXEL = /text-\[\d+\.5px\]/g;
const LITERAL_HUE = new RegExp(
  `(?<![\\w-])(?:${PROPS})(?:-[trblxyse])?-(?:${HUES})-\\d{2,3}(?:\\/(?:\\d{1,3}|\\[[0-9.]+\\]))?`,
  "g",
);
// A literal colour value (`bg-[#0a0c11]`, `text-[rgb(10_12_17)]`). A value
// that reads a theme variable (`accent-[rgb(var(--accent-rgb))]`, as
// ElicitationCard's checkbox does) follows the theme, so it is not flagged.
const ARBITRARY_COLOUR = new RegExp(
  `(?<![\\w-])(?:${PROPS})-\\[(?:#|(?:rgb|hsl)a?\\((?!\\s*var\\())[^\\]]*\\]`,
  "g",
);

function offenders(name: string, src: string): string[] {
  const allowed = ALLOW.filter((a) => a.file === name).map((a) => a.token);
  const hits: string[] = [];
  for (const re of [HALF_PIXEL, LITERAL_HUE, ARBITRARY_COLOUR]) {
    for (const m of src.matchAll(re)) {
      const tok = m[0];
      // An allowed token may carry an alpha suffix it was allowed without.
      if (allowed.some((a) => tok === a || tok.startsWith(`${a}/`))) continue;
      const line = src.slice(0, m.index ?? 0).split("\n").length;
      hits.push(`${name}:${line} ${tok}`);
    }
  }
  return hits;
}

const scanned = [
  ...readdirSync(CHAT_DIR)
    .filter((f) => /\.(tsx|ts)$/.test(f))
    .filter((f) => !(f in NOT_SCANNED))
    .sort(),
  ...Object.keys(EXTRA),
];

describe("chat cards: whole pixels and theme tokens only", () => {
  it("scans every chat component, including each card this wave moved", () => {
    for (const card of CARDS) expect(scanned, card).toContain(card);
    // Anti-vacuity: the folder really holds the chat components.
    expect(scanned.length).toBeGreaterThan(30);
  });

  it("also reads the workflow chip classes WorkflowDraftCard renders (agents.ts)", () => {
    expect(scanned).toContain("workflow/agents.ts");
    const src = read("workflow/agents.ts");
    // Anti-vacuity: this is the file that holds the chips, and they are real
    // class strings the patterns would see.
    expect(src).toContain("export const AGENT_META");
    expect(src).toContain("export const KIND_META");
    expect((src.match(/chip: "/g) ?? []).length).toBeGreaterThanOrEqual(9);
    expect(read("WorkflowDraftCard.tsx")).toMatch(/from "@\/components\/workflow\/agents"/);
  });

  it("no chat component has a half-pixel size, a literal hue or a colour value outside the allowlist", () => {
    const all = scanned.flatMap((f) => offenders(f, read(f)));
    expect(all, "use the 11/12/13/14 px scale and the tone-* tokens").toEqual([]);
  });

  it("every allowlist entry still matches something (a stale entry is removed, not kept)", () => {
    for (const a of ALLOW) {
      expect(read(a.file), `${a.file}: ${a.token} (${a.why})`).toContain(a.token);
      expect(a.why.length).toBeGreaterThan(20);
    }
  });

  it("the patterns catch what they claim and leave the tokens alone", () => {
    const sample = (s: string) =>
      [HALF_PIXEL, LITERAL_HUE, ARBITRARY_COLOUR].some((re) => [...s.matchAll(re)].length > 0);
    for (const bad of [
      'className="text-[11.5px]"',
      'className="text-[10.5px] text-zinc-500"',
      'className="text-amber-300"',
      'className="hover:bg-rose-500/10"',
      'className="border-emerald-500/[0.25]"',
      'className="bg-amber-400"',
      'className="text-sky-300/80"',
      'className="bg-[#0a0c11]"',
      'className="border-t-rose-400"',
      'className="accent-cyan-400"',
      'className="h-4 w-4 accent-emerald-500"',
      'className="text-[rgb(10_12_17)]"',
      'className="bg-[rgba(0,0,0,0.4)]"',
    ]) {
      expect(sample(bad), bad).toBe(true);
    }
    for (const good of [
      'className="text-[11px] text-tone-warn"',
      'className="bg-tone-danger/10 border-tone-danger/25"',
      'className="text-zinc-500 hover:bg-white/[0.06]"',
      'className="text-accent-soft border-accent/40"',
      'className="text-[11pt] leading-[1.55]"',
      'className="[&_h2]:text-[14.5pt]"',
      'className="accent-accent"',
      'className="h-4 w-4 accent-[rgb(var(--accent-rgb))]"',
      'className="bg-[rgb(var(--accent-rgb)/0.12)]"',
    ]) {
      expect(sample(good), good).toBe(false);
    }
  });
});
