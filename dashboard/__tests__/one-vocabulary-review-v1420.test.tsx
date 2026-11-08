/**
 * v1.420.0 review of the one-vocabulary lane (fix plan C1.8, C5.1, C5.2,
 * C6.7). Each case was red on the lane's first cut.
 *
 *  1. Every word the approval chip SHOWS for a level (its toolbar word AND
 *     its menu name) finds that level when the user types it to chat
 *     ("set approvals to Ask for commands"). The chat's settings tools
 *     (settings/tools._coerce) accept a value, a schema option label, or a
 *     word in `_RETIRED_OPTION_WORDS` — and the toolbar words "Ask every
 *     time" and the new "Ask for commands" were none of the three, so the
 *     words on the user's own screen were refused.
 *  2. The Python scans of the daemon's sentences parse ONLY the plain
 *     `{ word: "…", re: /…/i, use:` rows of retiredWords.ts; the words the
 *     daemon still says ("worktree", "no safe inverse") must stay out of
 *     that shape (RETIRED_ON_SCREEN's `onScreen(…)` calls), or the daemon
 *     scans go red on other lanes' files.
 *  3. The empty Session board said a card lands in the lane for "running,
 *     waiting for your review, done, or failed" — a stopped run has its own
 *     lane now.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { PERMISSION_LEVELS, PERMISSION_MODES } from "@/lib/permissionLevels";
import { LANES } from "@/lib/kanban";
import { RETIRED_ON_SCREEN, readableCopy } from "./helpers/retiredWords";

const DASH = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(DASH, rel), "utf-8").replace(/\r\n/g, "\n");

/** What chat's config tools accept for `device.approval_mode`, read from the
 *  two Python files the matcher reads (lower-case word -> mode). */
function chatAccepts(): Map<string, string> {
  const out = new Map<string, string>();
  const schema = read("../src/iron_jarvis/settings/schema.py");
  const at = schema.indexOf('S("device.approval_mode"');
  const opts = schema.slice(at, schema.indexOf(")),", schema.indexOf("options=", at)) + 3);
  for (const m of opts.matchAll(/\("(\w+)", "([^"]+)"\)/g)) {
    out.set(m[1].toLowerCase(), m[1]);
    out.set(m[2].toLowerCase(), m[1]);
  }
  const tools = read("../src/iron_jarvis/settings/tools.py");
  const start = tools.indexOf('"device.approval_mode": {', tools.indexOf("_RETIRED_OPTION_WORDS"));
  const block = tools.slice(start, tools.indexOf("},", start));
  for (const m of block.matchAll(/^\s*"([^"]+)": "(\w+)",/gm)) out.set(m[1], m[2]);
  return out;
}

describe("every word the chip shows finds its level in chat (review 1)", () => {
  it.each(PERMISSION_MODES.map((m) => [m] as const))("%s: its menu name and its toolbar word", (mode) => {
    const accepts = chatAccepts();
    const level = PERMISSION_LEVELS[mode];
    for (const word of [level.label, level.chip ?? level.label]) {
      expect(accepts.get(word.toLowerCase()), `${mode}: "${word}"`).toBe(mode);
    }
  });

  it("the older names keep their level", () => {
    const accepts = chatAccepts();
    expect(accepts.get("ask first")).toBe("approve_for_me");
    expect(accepts.get("ask when risky")).toBe("approve_for_me");
    expect(accepts.get("auto")).toBe("auto");
    expect(accepts.get("ask before everything")).toBe("always_ask");
  });
});

describe("the daemon's scans never read the on-screen-only words (review 2)", () => {
  it("retiredWords.ts rows in the Python parser's shape leave them out", () => {
    const src = read("__tests__/helpers/retiredWords.ts");
    // tests/test_vocabulary_beyond_dashboard_v13580.py::_retired, verbatim.
    const parsed = [...src.matchAll(/\{ word: "([^"]+)", re: \/(.+?)\/i, use:/g)].map((m) => m[1]);
    expect(parsed.length).toBeGreaterThan(10);
    for (const r of RETIRED_ON_SCREEN) expect(parsed, r.word).not.toContain(r.word);
    // Moved on purpose (v1.420.0, lane P): the Connections headings joined
    // the on-screen list once their last reader was reworded.
    expect(RETIRED_ON_SCREEN.map((r) => r.word)).toEqual([
      "worktree",
      "no safe inverse",
      "Subscription & local providers",
      "Rescan local CLIs",
      "No OAuth client configured",
      "Open Channels",
      // v1.455.0 (Wave E, moved on purpose): three more on-screen words.
      "tool server",
      "reflex",
      "Build session",
    ]);
  });
});

describe("the empty Session board names the Stopped lane (review 3)", () => {
  it("its sentence names every lane a card can land in", () => {
    const shown = readableCopy("components/sessions/SessionBoard.tsx").map((c) => c.text);
    const line = shown.find((t) => /becomes a card here/.test(t)) ?? "";
    expect(line).not.toBe("");
    expect(LANES.map((l) => l.id)).toContain("stopped");
    expect(line).toMatch(/stopped/i);
  });
});
