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
 * Icons stay in WorkLine (this file is plain data, importable anywhere).
 */

import { toolKind, type WorkKind } from "@/lib/workTarget";

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
  tool: { done: "Ran", doing: "Running", doneOn: "Ran", doingOn: "Running", count: (n) => `ran ${plural(n, "tool")}` },
};

/** The subject a row names after its words, and whether it is a file. A known
 *  tool names its safe target; any other tool names its own id ("Ran
 *  excel_query"). Null = no subject: the row keeps the older shape, words ·
 *  tool id (a step saved before targets existed). */
export function rowSubject(
  kind: WorkKind,
  name: string,
  target: string | null,
): { text: string; file: boolean } | null {
  if (kind === "tool") return { text: name, file: false };
  if (!target) return null;
  return { text: target, file: kind === "read" || kind === "make" || kind === "change" || kind === "folder" };
}

/** A FINISHED step in words: what was done ("Read"), to what
 *  ("sales-harbor-st.xlsx", or null), whether that is a file, and the tool's
 *  own id when the words alone do not name it (the quiet "· read_file" of an
 *  older step; null when the subject already is the id). */
export interface StepPhrase {
  kind: WorkKind;
  words: string;
  subject: string | null;
  file: boolean;
  hint: string | null;
}

export function stepPhrase(name: string, target?: string | null): StepPhrase {
  const kind = toolKind(name);
  const w = STEP_WORDS[kind];
  const subject = rowSubject(kind, name, target ?? null);
  return {
    kind,
    words: subject ? w.doneOn : w.done,
    subject: subject?.text ?? null,
    file: !!subject?.file,
    hint: subject ? null : name,
  };
}

/** The phrase as one line of text: "Read harbor.xlsx", "Read · read_file". */
export function stepPhraseText(p: StepPhrase): string {
  if (p.subject) return `${p.words} ${p.subject}`;
  return p.hint ? `${p.words} · ${p.hint}` : p.words;
}
