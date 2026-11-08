/**
 * v1.449.0 (plan 0h.5 item 18, Wave B "Did it work?, everywhere") — a status
 * word lives in ONE table, lib/statusWords. Five files had spelled their own:
 * lib/codingSessions ("Finished"), lib/mission ("Finished", "Could not
 * finish", "Waiting for your OK"), lib/kanban (a lane hint "Finished"),
 * lib/threadStatus ("Working on it", "Waiting on you") and lib/runningNow.
 * Each now reads `statusWords(...)` for its word and tone.
 *
 * The scan (the model-server-name-everywhere-v13600 pattern): read each of
 * those files, strip comments, and fail on a string literal that IS a status
 * word — the table's own words, or one of the retired spellings. A literal
 * that only contains such a word inside a longer sentence ("Nothing has
 * finished yet", "Stopped by you or by Emergency stop") is a description, not
 * a status, and is allowed.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { statusWords, toneInkClass } from "@/lib/statusWords";
import { codingStateWords } from "@/lib/codingSessions";
import { statusTone, statusWords as threadWords } from "@/lib/threadStatus";
import { missionHeadline, type MissionView } from "@/lib/mission";
import { LANES } from "@/lib/kanban";
import { endWords, liveWords, type AttemptView } from "@/lib/buildAttempts";

const ROOT = path.join(__dirname, "..");
const read = (...p: string[]) => readFileSync(path.join(ROOT, ...p), "utf8").replace(/\r\n/g, "\n");

/** The files that must read the table, never spell a status word. */
const FILES = [
  ["lib", "codingSessions.ts"],
  ["lib", "mission.ts"],
  ["lib", "kanban.ts"],
  ["lib", "threadStatus.ts"],
  ["lib", "runningNow.ts"],
  // (review) three more surfaces that spelled "Waiting for your OK",
  // "Finished" and "Working" of their own: the Try-on attempt rows, the
  // helper rows and the Build › Code session list.
  ["lib", "buildAttempts.ts"],
  ["components", "harness", "HarnessHelpers.tsx"],
  ["components", "buildCode", "BuildCodeView.tsx"],
] as const;

/** The table's own words plus every spelling these files used to have —
 *  compared as spelled (a status WORD is capitalised; a lower-case status KEY
 *  such as "completed" is data, not a word a person reads). */
const STATUS_WORDS = new Set(
  [
    ...["queued", "active", "completed", "failed", "cancelled", "working", "waiting_you", "waiting", "resuming", "cancelling", "interrupted", "completed_with_failures", "skipped", "pending"].map(
      (s) => statusWords(s).word,
    ),
    "Finished",
    "Could not finish",
    "Waiting for your OK",
    "Working on it",
    "Waiting on you",
    "Completed",
    "Running",
  ],
);

function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

function literals(src: string): string[] {
  const out: string[] = [];
  const re = /"((?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*)'|`((?:[^`\\]|\\.)*)`/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src))) out.push((m[1] ?? m[2] ?? m[3] ?? "").trim());
  return out;
}

describe("a status word is read from lib/statusWords, never spelled again", () => {
  for (const file of FILES) {
    it(`${file.join("/")} spells no status word of its own`, () => {
      const src = stripComments(read(...file));
      const found = literals(src).filter((s) => STATUS_WORDS.has(s));
      expect(found, `${file.join("/")} spells: ${found.join(", ")}`).toEqual([]);
    });
  }

  it("CONTROL: the scan sees a status word when a file spells one", () => {
    const planted = stripComments(`const a = "Finished"; // "Working on it"\n/* "Stopped" */ const b = 'Waiting on you'; const c = "Nothing has finished yet";`);
    expect(literals(planted).filter((s) => STATUS_WORDS.has(s))).toEqual(["Finished", "Waiting on you"]);
  });
});

describe("each reader hands back the table's word and tone", () => {
  it("a coding session's state is the table's row", () => {
    expect(codingStateWords({ state: "finished", outcome: "completed" })).toEqual(statusWords("completed"));
    expect(codingStateWords({ state: "finished", outcome: "failed" })).toEqual(statusWords("failed"));
    expect(codingStateWords({ state: "finished", outcome: "cancelled" })).toEqual(statusWords("cancelled"));
    expect(codingStateWords({ state: "working", outcome: "" })).toEqual(statusWords("working"));
    expect(codingStateWords({ state: "waiting", outcome: "" })).toEqual(statusWords("waiting"));
    expect(codingStateWords({ state: "idle", outcome: "" })).toEqual({ word: "No reply yet", tone: "slate" });
  });

  it("a chat's running / waiting words and tones are the table's", () => {
    expect(threadWords("running")).toBe(statusWords("working").word);
    expect(threadWords("waiting")).toBe(statusWords("waiting").word);
    expect(threadWords("idle")).toBe("");
    expect(statusTone("running")).toBe(statusWords("working").tone);
    expect(statusTone("waiting")).toBe(statusWords("waiting").tone);
    expect(statusTone("unread")).toBe("slate");
  });

  it("a mission's headline is the table's word for its verdict; the two bell headlines stay the bell's", () => {
    const v = (status: string, outcome: string | null, extra: Partial<MissionView> = {}): MissionView =>
      ({
        session: { status, outcome, interrupted: false },
        coordinator: { name: "Jarvis", status: "working", waiting_on: null, steps: 0 },
        members: [],
        progress: { done: 0, total: 0 },
        deliverable: { text: "", documents: [], worklist: null },
        ...extra,
      }) as unknown as MissionView;
    expect(missionHeadline(v("completed", "completed"))).toBe(statusWords("completed").word);
    expect(missionHeadline(v("failed", null))).toBe(statusWords("failed").word);
    expect(missionHeadline(v("cancelled", null))).toBe(statusWords("cancelled").word);
    expect(missionHeadline(v("queued", null))).toBe(`${statusWords("queued").word}, waiting for a free slot`);
    expect(
      missionHeadline(v("active", null, { coordinator: { name: "Jarvis", status: "waiting_you", waiting_on: { approval_id: "a", tool: "shell" }, steps: 0 } })),
    ).toBe(statusWords("waiting").word);
    expect(missionHeadline(v("completed", "needs_you"))).toBe("Finished, but something needs you");
  });

  it("the Session board's lanes are titled by the table and never hint a status word", () => {
    const done = LANES.find((l) => l.id === "completed")!;
    expect(done.title).toBe(statusWords("completed").word);
    expect(STATUS_WORDS.has(done.hint)).toBe(false);
    for (const l of LANES) expect(STATUS_WORDS.has(l.hint), `${l.id} hint "${l.hint}"`).toBe(false);
  });

  it("(review) a Try-on attempt row's words are the table's", () => {
    const a = (over: Partial<AttemptView>): AttemptView =>
      ({ state: "running", steps: 0, why: "", words: "", error: "", ...over }) as unknown as AttemptView;
    expect(liveWords(a({ state: "waiting" }))).toBe(statusWords("waiting").word);
    expect(liveWords(a({ state: "running", steps: 0 }))).toBe(statusWords("working").word);
    expect(liveWords(a({ state: "running", steps: 3 }))).toBe(`${statusWords("working").word} · 3 steps`);
    expect(endWords(a({ state: "done", why: "finished" }))).toBe(statusWords("completed").word);
    expect(endWords(a({ state: "failed", why: "failed" }))).toBe(statusWords("failed").word);
    expect(endWords(a({ state: "failed", why: "failed", error: "boom" }))).toBe(`${statusWords("failed").word}: boom`);
    expect(endWords(a({ state: "stopped", why: "stopped" }))).toBe(statusWords("cancelled").word);
    // The daemon's own words still win.
    expect(endWords(a({ state: "done", why: "finished", words: "All green" }))).toBe("All green");
  });

  it("toneInkClass maps every tone to a theme token (never a literal hue)", () => {
    for (const t of ["green", "red", "amber", "cyan", "violet", "slate"] as const) {
      const c = toneInkClass(t);
      expect(c).toMatch(/^text-(tone-|accent|zinc-)/);
      expect(c).not.toMatch(/#|rgb/);
    }
    expect(toneInkClass("green")).toBe("text-tone-success");
    expect(toneInkClass("red")).toBe("text-tone-danger");
    expect(toneInkClass("amber")).toBe("text-tone-warn");
  });
});
