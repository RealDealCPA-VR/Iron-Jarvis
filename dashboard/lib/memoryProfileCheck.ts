/**
 * CHECK A MEMORY PROFILE (v1.408.0): does the profile's primary memory base
 * answer, can it be searched, does its operating guide load, can it save?
 *
 * Pure module, no React, NO import from lib/api (about 70 test files mock that
 * module wholesale). Whitelisting decoders: a malformed answer reads as
 * nothing, never a crash.
 *
 * The daemon contract:
 *   POST /brain/profiles/{id}/check
 *     {steps: [{step: "reach"|"search"|"guide"|"save", ok: boolean|null, words}]}
 *   POST /brain/profiles/{id}/test-save   {ok: boolean, words} | 409/400 {detail}
 *   404 on either = an older Iron Jarvis: the buttons are not drawn.
 */

export const CHECK_STEPS = ["reach", "search", "guide", "save"] as const;
export type CheckStepId = (typeof CHECK_STEPS)[number];

export interface CheckStep {
  step: CheckStepId;
  /** true passed, false a problem, null not checked. */
  ok: boolean | null;
  words: string;
}

export interface TestSave {
  ok: boolean;
  words: string;
}

export const CHECK_STATE_WORDS = { pass: "passed", fail: "problem", skip: "not checked" } as const;

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);

export function decodeCheck(raw: unknown): CheckStep[] | null {
  if (!isObj(raw) || !Array.isArray(raw.steps)) return null;
  return raw.steps
    .map((s): CheckStep | null => {
      if (!isObj(s) || typeof s.step !== "string" || !(CHECK_STEPS as readonly string[]).includes(s.step)) return null;
      const words = typeof s.words === "string" ? s.words.trim() : "";
      if (!words) return null;
      const ok = s.ok === true ? true : s.ok === false ? false : null;
      return { step: s.step as CheckStepId, ok, words };
    })
    .filter((s): s is CheckStep => s !== null);
}

export function decodeTestSave(raw: unknown): TestSave | null {
  if (!isObj(raw) || typeof raw.ok !== "boolean") return null;
  const words = typeof raw.words === "string" ? raw.words.trim() : "";
  return { ok: raw.ok, words: words || (raw.ok ? "The test note was saved." : "The test note was not saved.") };
}

/** The tone a step's line reads in. */
export function stepTone(ok: boolean | null): "ok" | "warn" | "neutral" {
  return ok === true ? "ok" : ok === false ? "warn" : "neutral";
}
