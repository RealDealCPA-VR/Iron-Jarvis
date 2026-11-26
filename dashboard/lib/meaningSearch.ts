/**
 * SEARCH BY MEANING (next leaps 16-a, build-out plan R2.11, idea 16): what the
 * screens read from `GET /memory/meaning` (daemon `memory/meaning.view`) and
 * the presses they send. The daemon decides everything: whether the offer is
 * still open (never answered), which search models it found on this PC or
 * the owner's own network, and what each costs here, MEASURED. This module
 * only decodes that answer, as untrusted input (a model's name and a
 * server's label come from the servers: text only, cut), and names the
 * presses.
 *
 * Where it shows (the master plan's calm note): the OFFER is one suggestion
 * on Improvements (a plain inbox item, `inbox/collect.meaning_item`) and one
 * card under the reply whose by-meaning search matched words
 * (`MeaningSearchOffer`); File Search and Settings › Memory & you show a
 * plain settings row (`MeaningSearchRow`), never a second offer.
 */

import { post } from "./api";

export const MEANING_PATH = "/memory/meaning";
/** The setting's key (settings/schema): `?focus=` opens its section. */
export const MEANING_SETTING = "meaning_search";
export const MEANING_SETTINGS_HREF = "/settings?focus=meaning_search";
/** The model library (43-a), where a search model can be downloaded. */
export const LIBRARY_HREF = "/fleet?view=models";
/** Model servers, where a server the owner runs is added. */
export const SERVERS_HREF = "/fleet";

export const OFF_WORDS = "Search by meaning: off. It matches words for now.";
export const TURN_ON = "Turn it on";
export const NOT_NOW = "Not now";
export const USE_ANOTHER = "Use another model";
export const CHOOSE = "Choose a search model";
export const TURN_OFF = "Turn off";
export const USE_THIS = "Use this one";
export const IN_USE = "In use";
export const GET_A_MODEL = "Get a model";
export const OWN_SERVER = "Use a model server you run";
export const LOOKING = "Looking for search models on this PC and your network…";
export const LAST_RESORT = "Or, as a last resort, in a terminal:";
export const NOT_NOW_SAID = "Not now. Iron Jarvis won't offer it again; Settings › Memory & you can turn it on.";
export const DID_NOT_WORK = "That did not work. Try again.";
/** Re-reads while a look runs (only while the view is on screen). */
export const LOOK_REREAD_MS = 3000;
export const LOOK_REREADS_MAX = 10;

export type MeaningState = "off" | "offered" | "on" | "declined";

export interface MeaningCandidate {
  choice: string;
  model: string;
  server: string;
  whereWords: string;
  thisPc: boolean;
  costWords: string;
  /** null = not measured yet. */
  works: boolean | null;
}

export interface MeaningView {
  state: MeaningState;
  on: boolean;
  /** The choice in use ("" when off). */
  current: string;
  words: string;
  /** Never answered, and a search model answered its probe. */
  offer: boolean;
  best: MeaningCandidate | null;
  candidates: MeaningCandidate[];
  looking: boolean;
  noneFound: string;
  terminalLine: string;
}

function text(v: unknown, max = 400): string {
  return typeof v === "string" ? v.slice(0, max) : "";
}

function record(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function decodeCandidate(raw: unknown): MeaningCandidate | null {
  const r = record(raw);
  if (!r) return null;
  const choice = text(r.choice, 600);
  const model = text(r.model, 120).trim();
  if (!choice || !model) return null;
  return {
    choice,
    model,
    server: text(r.server, 120),
    whereWords: text(r.where_words, 160),
    thisPc: r.this_pc === true,
    costWords: text(r.cost_words, 300),
    works: r.works === true ? true : r.works === false ? false : null,
  };
}

const STATES: MeaningState[] = ["off", "offered", "on", "declined"];

/** The daemon's answer, or null when it is not one (an older daemon). */
export function decodeMeaning(raw: unknown): MeaningView | null {
  const r = record(raw);
  if (!r || typeof r.state !== "string" || !STATES.includes(r.state as MeaningState)) return null;
  const candidates = (Array.isArray(r.candidates) ? r.candidates : [])
    .map(decodeCandidate)
    .filter((c): c is MeaningCandidate => c !== null)
    .slice(0, 30);
  return {
    state: r.state as MeaningState,
    on: r.on === true,
    current: r.on === true ? text(r.value, 600) : "",
    words: text(r.words) || OFF_WORDS,
    offer: r.offer === true,
    best: decodeCandidate(r.best),
    candidates,
    looking: r.looking === true,
    noneFound: text(r.none_found),
    terminalLine: text(r.terminal_line, 80),
  };
}

/** Is the offer still open (never answered)? */
export function unanswered(view: MeaningView | null): boolean {
  return !!view && (view.state === "off" || view.state === "offered");
}

/** The note the route answered with, else "". */
export function noteOf(answer: unknown): string {
  return text(record(answer)?.note, 400);
}

export function chooseMeaning(choice: string): Promise<unknown> {
  return post<unknown>(`${MEANING_PATH}/choose`, { choice });
}

export function declineMeaning(): Promise<unknown> {
  return post<unknown>(`${MEANING_PATH}/decline`, {});
}

export function turnMeaningOff(): Promise<unknown> {
  return post<unknown>(`${MEANING_PATH}/off`, {});
}

/** The reply's file_search steps that carried the offer (their own frame,
 *  `chat_turn.tool_frame_data`), so the page saves `meaningOffer` on it. */
export function extractMeaningOffer(
  cards: readonly { name: string; status: string; ok?: boolean; meaningOffer?: boolean }[],
): boolean {
  return cards.some((c) => c.name === "file_search" && c.status === "done" && c.ok !== false && c.meaningOffer === true);
}
