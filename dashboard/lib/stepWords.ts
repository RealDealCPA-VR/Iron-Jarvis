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
  // v1.352.0 (G22): the built-in browser tools, as one surface.
  browser: {
    done: "Used your browser",
    doing: "Using your browser",
    doneOn: "Used your browser",
    doingOn: "Using your browser",
    count: (n) => `used your browser${times(n)}`,
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
  "change",
  "make",
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
  return { text: target, file: kind === "read" || kind === "make" || kind === "change" || kind === "folder" };
}

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
  const w = STEP_WORDS[kind];
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
    hint: subject ? null : stepHint(kind, name),
  };
}

/** The phrase as one line of text: "Read harbor.xlsx", "Read · read_file",
 *  "gmail: search messages", "Used your browser · click". */
export function stepPhraseText(p: StepPhrase): string {
  if (p.subject) return `${p.words} ${p.subject}`;
  return p.hint ? `${p.words} · ${p.hint}` : p.words;
}
