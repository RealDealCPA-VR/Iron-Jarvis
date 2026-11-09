// v1.458.0: "Record a task" — the worker's half.
//
// The page reader records what the user does (content/recorder.ts) and sends
// each step here; this file keeps the list for the ONE tab being recorded, adds
// the pages the tab moves to, paints the "Recording" sign on every page of that
// tab, and hands the list to the sidebar on Stop. Nothing here goes to Iron
// Jarvis: the steps leave the browser only when the user saves the reviewed
// list from the sidebar (`record_save`).
//
// THE LIST LIVES IN `chrome.storage.session`, not in a variable: an MV3 worker
// is evicted when idle, and a task the user takes five minutes to do would lose
// its first steps when the worker slept between two clicks. Session storage
// dies with the browser, which is right for a recording nobody stopped.
//
// EVERY CHANGE GOES THROUGH ONE QUEUE. Steps arrive as separate messages, each
// handled by its own async call; read-modify-write without a queue would let
// two quick steps overwrite each other, and a Stop could read the list before
// the last typing step (flushed by the Stop itself) was added.
//
// ADDRESSES keep scheme, host and path only — never the query string or the
// fragment, where sign-in links carry their tokens. A page the tab reaches
// within a few seconds of a step is that step's RESULT ("arrive": checked on
// replay, never navigated to); any other page change is a "navigate" step
// (the user typed an address or used a bookmark).

import { RECORDING_MAX_STEPS } from "../protocol";

/** The `chrome.storage.session` key the running recording is kept under. */
export const RECORDING_KEY = "ij.recording";

/** A page change this soon after a step is that step's result. */
export const ARRIVE_AFTER_STEP_MS = 4000;

/** The sign's id inside the page; its style element is `${RECORDING_SIGN_ID}-style`. */
export const RECORDING_SIGN_ID = "ij-recording-sign";

/** One kept step: the page reader's, or a page the tab moved to. */
export interface KeptStep {
  kind: string;
  at: number;
  url?: string;
  key?: string;
  sensitive?: boolean;
  target?: Record<string, unknown>;
}

/** The running (or just-ended) recording. */
export interface Recording {
  tabId: number;
  startedAt: number;
  steps: KeptStep[];
  /** Steps left out because the list was full. */
  dropped: number;
  /** Why it ended by itself ("tab closed", "idle"), or "" while it runs. */
  ended: string;
}

/** `scheme://host/path` of an http(s) address, or "". */
export function pageUrl(href: unknown): string {
  try {
    const url = new URL(String(href ?? ""));
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return "";
    }
    return `${url.protocol}//${url.host}${url.pathname}`;
  } catch {
    return "";
  }
}

const STEP_KINDS: ReadonlySet<string> = new Set(["click", "type", "select", "key", "file"]);

/** A step from a page, kept only in the shape a step has (it is untrusted input). */
export function cleanStep(raw: unknown, now: number): KeptStep | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const r = raw as Record<string, unknown>;
  const kind = String(r["kind"] ?? "");
  if (!STEP_KINDS.has(kind)) {
    return null;
  }
  const out: KeptStep = { kind, at: now };
  if (typeof r["key"] === "string") {
    out.key = r["key"];
  }
  if (r["sensitive"] === true) {
    out.sensitive = true;
  }
  const t = r["target"];
  if (t && typeof t === "object") {
    const tt = t as Record<string, unknown>;
    const s = (k: string, n: number): string => String(tt[k] ?? "").slice(0, n);
    out.target = {
      role: s("role", 40),
      name: s("name", 200),
      text: s("text", 300),
      tag: s("tag", 30),
      input_type: s("input_type", 30),
      css: s("css", 300),
      unique_name: tt["unique_name"] === true,
    };
  }
  return out;
}

/** The page the recording last saw, or "". */
function lastUrl(rec: Recording): string {
  for (let i = rec.steps.length - 1; i >= 0; i -= 1) {
    const url = rec.steps[i]?.url;
    if (url) {
      return url;
    }
  }
  return "";
}

/** Add a step, unless the list is full. */
export function addStep(rec: Recording, step: KeptStep): Recording {
  if (rec.steps.length >= RECORDING_MAX_STEPS) {
    return { ...rec, dropped: rec.dropped + 1 };
  }
  return { ...rec, steps: [...rec.steps, step] };
}

/** The tab is now at `href`: a navigate or an arrive step, or nothing (same page). */
export function addPage(rec: Recording, href: unknown, now: number): Recording {
  const url = pageUrl(href);
  if (!url || url === lastUrl(rec)) {
    return rec;
  }
  const last = rec.steps[rec.steps.length - 1];
  const caused = Boolean(last && !last.url && now - last.at <= ARRIVE_AFTER_STEP_MS);
  return addStep(rec, { kind: caused ? "arrive" : "navigate", url, at: now });
}

/** A new recording in `tabId`, starting on the page it shows. */
export function newRecording(tabId: number, href: unknown, now: number): Recording {
  const rec: Recording = { tabId, startedAt: now, steps: [], dropped: 0, ended: "" };
  return addPage(rec, href, now);
}

// --- storage, through one queue ---------------------------------------------

let queue: Promise<unknown> = Promise.resolve();

/** Run `fn` after every change queued before it. */
export function inOrder<T>(fn: () => Promise<T>): Promise<T> {
  const next = queue.then(fn, fn);
  queue = next.catch(() => undefined);
  return next;
}

export async function readRecording(): Promise<Recording | null> {
  try {
    const got = await chrome.storage.session.get(RECORDING_KEY);
    const rec = got?.[RECORDING_KEY] as Recording | undefined;
    return rec && typeof rec.tabId === "number" && Array.isArray(rec.steps) ? rec : null;
  } catch {
    return null;
  }
}

export async function writeRecording(rec: Recording | null): Promise<void> {
  try {
    if (rec) {
      await chrome.storage.session.set({ [RECORDING_KEY]: rec });
    } else {
      await chrome.storage.session.remove(RECORDING_KEY);
    }
  } catch {
    // Storage unavailable: the recording is lost with the worker, and the
    // sidebar says it found nothing to review. Never a crash.
  }
}

// --- the sign on the page -----------------------------------------------------

/**
 * Runs INSIDE the page: a red edge and a small "Recording" pill, so the user
 * always knows this tab is being recorded. `pointer-events: none` and
 * aria-hidden; the words are CSS generated content, so a page read never sees
 * them. Self-contained (executeScript serialises it). Idempotent.
 */
function paintRecording(id: string): void {
  if (document.getElementById(id)) {
    return;
  }
  const root = document.documentElement || document.body;
  const style = document.createElement("style");
  style.id = id + "-style";
  style.textContent =
    "@keyframes " + id + "-blink{0%,100%{opacity:1}50%{opacity:.35}}" +
    "#" + id + "{position:fixed;inset:0;pointer-events:none;z-index:2147483647;" +
    "box-shadow:inset 0 0 0 3px rgba(239,68,68,.85)}" +
    "#" + id + "-pill{position:fixed;top:10px;right:12px;pointer-events:none;z-index:2147483647;" +
    "padding:4px 12px 4px 26px;border-radius:999px;font:600 12px/1.4 system-ui,sans-serif;" +
    "color:#fff;background:rgba(185,28,28,.95);box-shadow:0 4px 14px rgba(0,0,0,.25)}" +
    "#" + id + "-pill::before{content:\"\";position:absolute;left:10px;top:50%;width:8px;height:8px;" +
    "margin-top:-4px;border-radius:999px;background:#fff;animation:" + id + "-blink 1.4s ease-in-out infinite}" +
    "#" + id + "-pill::after{content:\"Recording your steps · Stop in the Iron Jarvis sidebar\"}" +
    "@media (prefers-reduced-motion: reduce){#" + id + "-pill::before{animation:none}}";
  const edge = document.createElement("div");
  edge.id = id;
  edge.setAttribute("aria-hidden", "true");
  const pill = document.createElement("div");
  pill.id = id + "-pill";
  pill.setAttribute("aria-hidden", "true");
  root.appendChild(style);
  root.appendChild(edge);
  root.appendChild(pill);
}

/** Runs INSIDE the page: removes what `paintRecording` made, and nothing else. */
function clearRecording(id: string): void {
  for (const suffix of ["", "-pill", "-style"]) {
    document.getElementById(id + suffix)?.remove();
  }
}

/** The painters, for the worker and the tests. */
export const recordingPainters = { paintRecording, clearRecording };

export async function showSign(tabId: number): Promise<void> {
  try {
    await chrome.scripting.executeScript({ target: { tabId }, func: paintRecording, args: [RECORDING_SIGN_ID] });
  } catch {
    // A page closed to add-ons: no sign, and nothing is recorded there either.
  }
}

export async function hideSign(tabId: number): Promise<void> {
  try {
    await chrome.scripting.executeScript({ target: { tabId }, func: clearRecording, args: [RECORDING_SIGN_ID] });
  } catch {
    // The tab closed or navigated: the sign went with the page.
  }
}
