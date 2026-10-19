/**
 * v1.356.0 (G75, product-readiness wave 6): the chat's own controls in plain
 * words, in ONE place.
 *
 * A new user met "context window", "Compact now", "compacted automatically at
 * the context ceiling", a "1M" chip, raw tool ids with "Disarm" tooltips and
 * a folder tooltip about "the files this chat's tools make". The controls do
 * exactly what they did; only their words changed, and the words live here so
 * the chat page, the compaction card, the model rows and the tests read the
 * same sentence.
 *
 * Dependency-light on purpose: the tool words read lib/toolWords and
 * lib/workTarget (both plain data), so this file is importable anywhere.
 */

import { appNameWords, appToolNameWords, appToolParts, toolWords } from "@/lib/toolWords";
import { toolKind, type WorkKind } from "@/lib/workTarget";

/* ------------------------------------------------------------- compaction */

/** The offer above the composer when a chat is getting long (the daemon's
 *  SUGGEST band). The press summarizes the start; nothing is deleted. */
export const COMPACTION_OFFER_WORDS =
  "This chat is getting long. Summarize the start to keep it fast? The full transcript is kept.";

/** The offer's one button (busy: "Summarizing…"). */
export const COMPACTION_OFFER_BUTTON = "Summarize";

/** "Not now"'s hover: what happens if the user does nothing. */
export const COMPACTION_NOT_NOW_TITLE =
  "If you do nothing, Jarvis summarizes the start by itself once the chat gets much longer.";

/** The compaction card's line for who started the summary. */
export function compactionTriggerWords(trigger: string | undefined): string | null {
  if (trigger === "manual") return "summarized at your request";
  if (trigger === "auto") return "summarized automatically because the chat got long";
  return null;
}

/* ------------------------------------------------------------- model rows */

/** The chip on a model that reads very long documents (a million-token
 *  window): "long docs", never "1M". */
export const LONG_DOCS_CHIP = "long docs";
export const LONG_DOCS_TITLE = "Reads very long documents and chats at once";

/* ------------------------------------------------------------ tools menu */

/** What a kind of built-in tool does, said before it runs (the tools menu
 *  lists tools that are ON for the chat, not steps that ran). Exact-name
 *  kinds only (lib/workTarget); "tool" is a tool the app cannot vouch for and
 *  has no words here. */
const KIND_DOES: Partial<Record<WorkKind, string>> = {
  read: "Read files",
  make: "Make files",
  change: "Change files",
  folder: "Look in folders",
  search: "Search in files",
  web: "Search the web",
  page: "Read web pages",
  command: "Run commands",
  browser: "Use your browser",
};

function capFirst(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

/**
 * A tool turned on for this chat, in words: what it does ("Save a file",
 * "Read files", "gmail: search messages"), never its raw id. An id the app
 * does not know answers null and the caller shows the id itself (the app
 * never invents a description). The exact id always stays in the row's
 * title: it is what the chat sends.
 */
export function armedToolWords(name: string): string | null {
  const id = typeof name === "string" ? name.trim() : "";
  if (!id) return null;
  // A connected app's tool: "<app>: <its own words>", the work line's shape.
  const app = appToolParts(id);
  if (app) return `${appNameWords(app.app)}: ${appToolNameWords(app.tool)}`;
  // The approval card's words first: they name what ONE tool does.
  const said = toolWords(id);
  if (said) return capFirst(said);
  const kind = toolKind(id);
  return KIND_DOES[kind] ?? null;
}

/** The Turn off button's accessible name for one armed tool. */
export function turnOffToolLabel(name: string): string {
  const words = armedToolWords(name);
  return words ? `Turn off ${words} (${name})` : `Turn off ${name}`;
}

/** The web switch's hover in the tools menu, the same words as the toolbar's
 *  Web chip. */
export function webSwitchTitle(on: boolean, room: boolean, max: number): string {
  if (on) return "Web research is on for this chat. Press to turn it off.";
  if (room) return "Turn on web research for this chat";
  return `All ${max} tool slots are in use. Turn one off first.`;
}

/* ---------------------------------------------------------------- folder */

/** The working folder's hover, picked or not. */
export function workingFolderTitle(folder: string | null | undefined): string {
  const f = (folder ?? "").trim();
  return f
    ? `Jarvis works on files in that folder: ${f}`
    : "Pick a folder. Jarvis works on files in that folder.";
}

/* ------------------------------------------------------ model server form */

/** The first-run key door's own model server fields: the address it answers
 *  on, and the name of the model to use there ("Base URL" and "Model" said
 *  nothing to a new user). */
export const MODEL_SERVER_ADDRESS = "Your model server's address";
export const MODEL_NAME_LABEL = "Model name";
