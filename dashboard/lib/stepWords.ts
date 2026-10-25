/**
 * Calm chat wave 5 G2 (v1.329.0): ONE way of describing a tool step.
 *
 * The folded "Worked for …" line (components/chat/WorkLine) and the expanded
 * receipt under the same reply (components/chat/TurnReceipt) used to say the
 * same steps in two vocabularies: "Read sales-harbor-st.xlsx" above, a
 * monospace "read_file · 0.4 s" below. Both now read their words from this
 * table, so they can never drift apart again.
 *
 * Plain words only for the tools lib/workTarget KNOWS by exact name; any
 * other tool reads "Ran <its id>" (the app does not describe what it cannot
 * vouch for). A step saved before targets existed keeps the older shape:
 * the words, then the tool's own id as a quiet hint ("Read · read_file").
 *
 * v1.352.0 (G22): a connected app's step and a browser step read in words.
 * An app's tool (`mcp__<pack>__<tool>`) is "<pack>: <its tool's words>"
 * ("gmail: search messages") and is counted by its pack ("used gmail"), not
 * as "ran 1 tool"; a built-in browser tool is "Used your browser · click",
 * counted "used your browser". Both are read straight off the tool's own id
 * (lib/toolWords), never invented; the id itself stays in the hover title.
 *
 * v1.358.0 (wave 8, lane 8C): a call to a saved web service (`rest_call`)
 * reads "Called crm" and counts "called a web service" (the `service` kind,
 * lib/workTarget), where it read "Ran rest_call" and "ran 1 tool".
 *
 * Icons stay in WorkLine (this file is plain data, importable anywhere).
 */

import { toolKind, type WorkKind } from "@/lib/workTarget";
import { appNameWords, appToolNameWords, appToolParts, browserActionWords } from "@/lib/toolWords";

function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

function times(n: number): string {
  return n === 1 ? "" : ` ${n} times`;
}

export interface KindWords {
  /** Row title once the step is done. */
  done: string;
  /** Row title while it runs. */
  doing: string;
  /** The words BEFORE a target, once done and while running:
   *  "Searched the web for" + "pier 9 hours", "Read" + "harbor.xlsx". */
  doneOn: string;
  doingOn: string;
  /** The count in the folded line ("read 2 files"). */
  count: (n: number) => string;
}

export const STEP_WORDS: Record<WorkKind, KindWords> = {
  read: { done: "Read", doing: "Reading", doneOn: "Read", doingOn: "Reading", count: (n) => `read ${plural(n, "file")}` },
  make: { done: "Made", doing: "Making", doneOn: "Made", doingOn: "Making", count: (n) => `made ${plural(n, "file")}` },
  change: { done: "Changed", doing: "Changing", doneOn: "Changed", doingOn: "Changing", count: (n) => `changed ${plural(n, "file")}` },
  folder: { done: "Looked in", doing: "Looking in", doneOn: "Looked in", doingOn: "Looking in", count: (n) => `looked in ${plural(n, "folder")}` },
  search: { done: "Searched files", doing: "Searching files", doneOn: "Searched files for", doingOn: "Searching files for", count: (n) => `searched files${times(n)}` },
  web: { done: "Searched the web", doing: "Searching the web", doneOn: "Searched the web for", doingOn: "Searching the web for", count: (n) => `searched the web${times(n)}` },
  page: { done: "Read a web page", doing: "Reading a web page", doneOn: "Read a page on", doingOn: "Reading a page on", count: (n) => `read ${plural(n, "web page")}` },
  command: { done: "Ran", doing: "Running", doneOn: "Ran", doingOn: "Running", count: (n) => `ran ${plural(n, "command")}` },
  // v1.358.0 (wave 8, lane 8C): a call to one of the user's saved web
  // services (`rest_call`): "Called crm" with the service's name, "Called a
  // web service" without one; counted "called a web service", never "ran 1
  // tool". Several calls may reach different services, so the count does not
  // name one.
  service: {
    done: "Called a web service",
    doing: "Calling a web service",
    doneOn: "Called",
    doingOn: "Calling",
    count: (n) => (n === 1 ? "called a web service" : `called your web services ${n} times`),
  },
  // v1.352.0 (G22): the built-in browser tools, as one surface.
  browser: {
    done: "Used your browser",
    doing: "Using your browser",
    doneOn: "Used your browser",
    doingOn: "Using your browser",
    count: (n) => `used your browser${times(n)}`,
  },
  // v1.376.0 (IJ harness part 2, Phase 5): the agent's OWN browser
  // (harness/page_act). Each tool leads its row with its own verb
  // (SITE_WORDS below); these words are the count and the fallback.
  site: {
    done: "Used its browser",
    doing: "Using its browser",
    doneOn: "Used its browser on",
    doingOn: "Using its browser on",
    count: (n) => `used its browser${times(n)}`,
  },
  // v1.352.0 (G22): a connected app. The row and the count name the app
  // itself (appStepWords / stepCounts below); these words are only the
  // fallback for an app id that names no app, which toolKind never returns.
  app: { done: "Used an app", doing: "Using an app", doneOn: "Used", doingOn: "Using", count: (n) => `used an app${times(n)}` },
  tool: { done: "Ran", doing: "Running", doneOn: "Ran", doingOn: "Running", count: (n) => `ran ${plural(n, "tool")}` },
};

/** The order counts are said in: looking before making, tools last. A
 *  connected app sits just before the plain tools. */
export const KIND_ORDER: readonly WorkKind[] = [
  "read",
  "folder",
  "search",
  "page",
  "web",
  "browser",
  "site",
  "change",
  "make",
  "service",
  "command",
  "app",
  "tool",
];

/** v1.352.0 (G22): "used gmail", "used gmail 3 times" — an app's piece of
 *  the counted line, by its pack name. */
export function appCountWords(app: string, n: number): string {
  return `used ${app}${times(n)}`;
}

/**
 * v1.348.0: "read 2 files, ran 1 tool" pieces, in a fixed order, plus
 * "1 failed". ONE counter for both lines that sum up a turn's work: the
 * folded "Worked for 3.2 s · …" above a settled reply and the live
 * "Working for 12 s · …" while the turn runs (components/chat/WorkLine), so
 * the live words and the folded words can never drift apart.
 *
 * v1.351.0 (G07): a FAILED step is counted once, as failed, and never as
 * work done. One page read and seven failed reads used to say "read 8 web
 * pages, 7 failed"; it now says "read 1 web page, 7 failed".
 *
 * v1.352.0 (G22): an app's steps are counted per app, in the order the turn
 * first used each ("used gmail 2 times, used github"); browser steps are
 * "used your browser".
 */
export function stepCounts(steps: ReadonlyArray<{ name: string; ok?: boolean | null }>): string[] {
  const n = new Map<WorkKind, number>();
  const apps = new Map<string, number>();
  let failed = 0;
  for (const st of steps) {
    if (st.ok === false) {
      failed += 1;
      continue;
    }
    const k = toolKind(st.name);
    if (k === "app") {
      const parts = appToolParts(st.name);
      if (parts) {
        const app = appNameWords(parts.app);
        apps.set(app, (apps.get(app) ?? 0) + 1);
        continue;
      }
    }
    n.set(k, (n.get(k) ?? 0) + 1);
  }
  const out: string[] = [];
  for (const k of KIND_ORDER) {
    if (k === "app") {
      for (const [app, c] of apps) out.push(appCountWords(app, c));
    }
    const c = n.get(k);
    if (c) out.push(STEP_WORDS[k].count(c));
  }
  if (failed) out.push(failedWords(failed));
  return out;
}

/** v1.351.0 (G07): how many of a turn's steps failed (ok === false). */
export function failedSteps(steps: ReadonlyArray<{ ok?: boolean | null }>): number {
  let n = 0;
  for (const st of steps) if (st.ok === false) n += 1;
  return n;
}

/** The failure piece of the counted line: "7 failed". */
export function failedWords(n: number): string {
  return `${n} failed`;
}

/** The subject a row names after its words, and whether it is a file. A known
 *  tool names its safe target; any other tool names its own id ("Ran
 *  excel_query"). Null = no subject: the row keeps the older shape, words ·
 *  tool id (a step saved before targets existed).
 *
 *  v1.352.0 (G22): an app's step names its tool in words ("search messages",
 *  after the "gmail:" words); a browser step has no subject (its action is
 *  the quiet word after the dot — `stepHint`). */
export function rowSubject(
  kind: WorkKind,
  name: string,
  target: string | null,
): { text: string; file: boolean } | null {
  if (kind === "tool") return { text: name, file: false };
  if (kind === "app") {
    const parts = appToolParts(name);
    return { text: parts ? appToolNameWords(parts.tool) : name, file: false };
  }
  if (!target) return null;
  // v1.376.0: what a click or a typed entry in the agent's browser was aimed
  // at, in quotes — "Clicked "Add to cart"" (an address stays bare).
  if (kind === "site" && (name === "page_click" || name === "page_type")) {
    return { text: `"${target}"`, file: false };
  }
  return { text: target, file: kind === "read" || kind === "make" || kind === "change" || kind === "folder" };
}

/** v1.376.0 (IJ harness part 2, Phase 5): the agent's own browser, each tool
 *  by its own verb — with a subject ("Opened localhost:5173/cart",
 *  "Clicked "Add to cart"", "Typed into "Email"") or without ("Read the
 *  page", "Took a screenshot"). The daemon's twin is agents/mission.py
 *  _TOOL_WORDS. */
export const SITE_WORDS: Readonly<Record<string, { done: string; doing: string; doneOn: string; doingOn: string }>> = {
  page_open: { done: "Opened a page", doing: "Opening a page", doneOn: "Opened", doingOn: "Opening" },
  page_click: { done: "Clicked", doing: "Clicking", doneOn: "Clicked", doingOn: "Clicking" },
  page_type: { done: "Typed into a field", doing: "Typing into a field", doneOn: "Typed into", doingOn: "Typing into" },
  page_read: { done: "Read the page", doing: "Reading the page", doneOn: "Read", doingOn: "Reading" },
  page_screenshot: {
    done: "Took a screenshot",
    doing: "Taking a screenshot",
    doneOn: "Took a screenshot of",
    doingOn: "Taking a screenshot of",
  },
};

/**
 * The words that LEAD a row, done or running, with or without a subject.
 * v1.352.0 (G22): an app's row leads with its app, "gmail:", the same while
 * it runs (the spinner says it is running); every other kind reads its words
 * from STEP_WORDS exactly as before.
 */
export function kindWords(kind: WorkKind, name: string, opts: { subject: boolean; running: boolean }): string {
  if (kind === "app") {
    const parts = appToolParts(name);
    if (parts) return `${appNameWords(parts.app)}:`;
  }
  const w = (kind === "site" && Object.prototype.hasOwnProperty.call(SITE_WORDS, name) ? SITE_WORDS[name] : null) ??
    STEP_WORDS[kind];
  if (opts.subject) return opts.running ? w.doingOn : w.doneOn;
  return opts.running ? w.doing : w.done;
}

/**
 * The quiet word after the dot on a row with no subject: the tool's own id
 * for an older step ("Read · read_file"), and since v1.352.0 (G22) a browser
 * step's action ("Used your browser · click") — the browser's id stays in
 * the hover title.
 */
export function stepHint(kind: WorkKind, name: string): string {
  if (kind === "browser") return browserActionWords(name) ?? name;
  // v1.358.0 (lane 8C): a web-service call with no service named reads
  // "Called a web service" alone; its tool id stays in the hover title.
  if (kind === "service") return "";
  // v1.376.0: the agent's browser's own verbs say it all ("Read the page").
  if (kind === "site") return "";
  return name;
}

/** A FINISHED step in words: what was done ("Read"), to what
 *  ("sales-harbor-st.xlsx", or null), whether that is a file, and the quiet
 *  word after the dot when the words alone do not name it (the "· read_file"
 *  of an older step, a browser step's "· click"; null when the subject
 *  already names it). */
export interface StepPhrase {
  kind: WorkKind;
  words: string;
  subject: string | null;
  file: boolean;
  hint: string | null;
}

export function stepPhrase(name: string, target?: string | null): StepPhrase {
  const kind = toolKind(name);
  const subject = rowSubject(kind, name, target ?? null);
  return {
    kind,
    words: kindWords(kind, name, { subject: !!subject, running: false }),
    subject: subject?.text ?? null,
    file: !!subject?.file,
    hint: subject ? null : stepHint(kind, name) || null,
  };
}

/** The phrase as one line of text: "Read harbor.xlsx", "Read · read_file",
 *  "gmail: search messages", "Used your browser · click". */
export function stepPhraseText(p: StepPhrase): string {
  if (p.subject) return `${p.words} ${p.subject}`;
  return p.hint ? `${p.words} · ${p.hint}` : p.words;
}

/**
 * IJ harness part 3, Phase 2: what a Build reply's FINISH CHECK did, as the
 * daemon kept it on the reply line (`harness/finish.saved_record`; decoded by
 * lib/buildSessions `decodeFinish`). `tests` is the check's own run, decided
 * by the command's own exit status; `after` the last test run the model made
 * after the check's note ("" when it made none).
 */
export interface FinishRecord {
  tests: "passed" | "failed" | "none" | "not_run";
  command: string;
  removed: number;
  kept: number;
  points: number;
  unmet: number;
  after: "" | "passed" | "failed";
  why?: string;
}

/** The finish check's ONE sentence on the work line: "Checked: tests passed,
 *  1 scratch file removed", "Checked: tests failed, then fixed". Built only
 *  from the record (never a claim the record does not make). */
export function finishWords(f: FinishRecord): string {
  const parts: string[] = [];
  if (f.tests === "passed") {
    parts.push(f.after === "failed" ? "tests passed, then failed" : "tests passed");
  } else if (f.tests === "failed") {
    parts.push(f.after === "passed" ? "tests failed, then fixed" : "tests failed");
  } else if (f.tests === "not_run") {
    parts.push(
      f.after === "passed"
        ? "tests passed after the check"
        : f.after === "failed"
          ? "tests failed after the check"
          : "tests not run",
    );
  } else {
    parts.push("no tests found");
  }
  if (f.removed > 0) parts.push(`${plural(f.removed, "scratch file")} removed`);
  if (f.kept > 0) parts.push(`${plural(f.kept, "new file")} kept`);
  if (f.unmet > 0) parts.push(`${plural(f.unmet, "point")} needed more work`);
  return `Checked: ${parts.join(", ")}`;
}

/** Does the finish line carry the warn mark? Only when the tests were last
 *  seen FAILING (never fixed after), the same theme token as a failed step. */
export function finishWarn(f: FinishRecord): boolean {
  if (f.after === "failed") return true;
  return f.tests === "failed" && f.after !== "passed";
}

/** The finish line's hover: the command it ran, and why the tests did not
 *  run when they did not. */
export function finishTitle(f: FinishRecord): string | undefined {
  const bits: string[] = [];
  if (f.command) bits.push(`Ran: ${f.command}`);
  if (f.tests === "not_run" && f.why) bits.push(`Not run: ${f.why}`);
  return bits.length ? bits.join("\n") : undefined;
}

/** Human wording for an agent run's phase (v1.149.0). An unknown phase falls
 *  through to its raw name rather than a generic label — a new phase the
 *  daemon adds should read oddly, not silently look like every other one.
 *
 *  v1.354.0 (G20): lifted out of the chat page so the run's own page says
 *  the same words as the chat's helper-agent row ("Planning the work…",
 *  never the raw "planning"). */
export const PHASE_LABEL: Readonly<Record<string, string>> = {
  planning: "Planning the work…",
  running: "Working…",
  verifying: "Checking its work…",
  assembling: "Writing up the result…",
};

export function phaseLabel(phase: string): string {
  return PHASE_LABEL[phase] ?? phase;
}
