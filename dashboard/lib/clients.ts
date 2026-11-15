/**
 * Clients (v1.499.0, vision plan leap 3, Phase 3): a person or business the
 * owner works for, known by its folders and confirmed by the owner.
 *
 * The daemon reads (`GET /clients`, `GET /clients/{id}`, `GET /clients/of`)
 * and decides; the dashboard shows what it was handed and presses the
 * client's own routes. Everything from the wire is decoded here through a
 * whitelist: an unknown field is dropped, a row whose id is not a client id
 * is dropped (an id is the only thing a path is ever built from, never a
 * name), and a link that leaves the app is dropped. Every word the client
 * screens show lives here too, so a page and a test read one place.
 *
 * No imports from lib/api (many tests mock that module whole).
 */

import { normalizeIso, timeAgo } from "./format";
import { couldnt, serverSentence, type FailureLike } from "./loadFailed";
import { countWords, decodeEntries, type WorkItem } from "./workRecord";

/* ------------------------------------------------------------------ ids */

/** A client's id (`core.ids.new_id("client")`); a name never matches it. */
export const CLIENT_ID_RE = /^client_[0-9a-f]{12}$/;
/** A client preference's id (`core.ids.new_id("cpref")`). */
export const CLIENT_PREF_ID_RE = /^cpref_[0-9a-f]{12}$/;
/** A Project's id, only to build its link (letters, digits, `_`, `-`). */
const PROJECT_ID_RE = /^[A-Za-z0-9_-]{1,80}$/;

export function isClientId(v: unknown): v is string {
  return typeof v === "string" && CLIENT_ID_RE.test(v);
}

export function isClientPrefId(v: unknown): v is string {
  return typeof v === "string" && CLIENT_PREF_ID_RE.test(v);
}

/* ---------------------------------------------------------------- paths */

export const CLIENTS_HREF = "/clients";
/** Where the waiting names are answered (the bulk review). */
export const WAITING_HREF = "/clients#waiting";
export const CLIENTS_PATH = "/clients";
export const CONFIRM_MANY_PATH = "/clients/confirm-many";
export const NOTICE_PATH = "/clients/notice";

/** A client's page, built only from a real client id; anything else opens
 *  the Clients page. */
export function clientHref(id: unknown): string {
  return isClientId(id) ? `/clients/${id}` : CLIENTS_HREF;
}

/** The id as a path segment. Refuses anything that is not a client id, so a
 *  press can never be sent to another address. */
function seg(id: string): string {
  if (!isClientId(id)) throw new Error(NOT_ON_RECORD);
  return id;
}

export type ClientVerb = "confirm" | "decline" | "ask-again" | "restore";

export function clientPath(id: string): string {
  return `/clients/${seg(id)}`;
}

export function clientVerbPath(id: string, verb: ClientVerb): string {
  return `/clients/${seg(id)}/${verb}`;
}

export function clientFoldersPath(id: string): string {
  return `/clients/${seg(id)}/folders`;
}

export function clientRemoveFolderPath(id: string): string {
  return `/clients/${seg(id)}/folders/remove`;
}

export function clientPrefsPath(id: string): string {
  return `/clients/${seg(id)}/preferences`;
}

export function clientPrefPath(id: string, pid: string): string {
  if (!isClientPrefId(pid)) throw new Error(PREF_NOT_ON_RECORD);
  return `/clients/${seg(id)}/preferences/${pid}`;
}

export function clientOfPath(folder: string): string {
  return `/clients/of?folder=${encodeURIComponent(folder)}`;
}

/* -------------------------------------------------------------- helpers */

function str(v: unknown, max = 400): string {
  return typeof v === "string" ? v.slice(0, max) : "";
}

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? Math.floor(v) : 0;
}

function rec(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function list(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function strings(v: unknown, maxItems: number, maxLen = 400): string[] {
  return list(v)
    .filter((s): s is string => typeof s === "string" && s.trim().length > 0)
    .slice(0, maxItems)
    .map((s) => s.trim().slice(0, maxLen));
}

/** A number, or the length of a list (a count the daemon may send either way). */
function countOf(v: unknown): number {
  return Array.isArray(v) ? v.length : num(v);
}

/** True for a tab, a newline or any other control character. */
function hasControl(s: string): boolean {
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c < 32 || c === 127) return true;
  }
  return false;
}

/** An address inside the app only: one leading slash, never `//host`, a
 *  scheme, a backslash or a control character (a browser reads a backslash
 *  as a slash and drops a tab or a newline, so such an address could leave
 *  the app). "" for anything else. */
export function appLink(v: unknown): string {
  const s = str(v, 400);
  if (!s.startsWith("/") || s.startsWith("//") || s.includes("\\") || hasControl(s)) return "";
  return s;
}

/* ------------------------------------------------------------- decoders */

export interface ClientRef {
  id: string;
  name: string;
}

export function decodeClientRef(raw: unknown): ClientRef | null {
  const r = rec(raw);
  if (!r || !isClientId(r.id)) return null;
  const name = str(r.name, 80).trim();
  return name ? { id: r.id, name } : null;
}

/** `GET /clients/of?folder=` → `{client: {id, name} | null}`. */
export function decodeClientOf(raw: unknown): ClientRef | null {
  return decodeClientRef(rec(raw)?.client);
}

export type ClientHow = "noticed" | "added" | "";

function howOf(v: unknown): ClientHow {
  return v === "noticed" || v === "added" ? v : "";
}

export interface ClientFolderName {
  folder: string;
  name: string;
}

export interface ClientSummary {
  id: string;
  name: string;
  how: ClientHow;
  folders: ClientFolderName[];
  workCount: number;
  lastAt: string | null;
  preferencesCount: number;
}

export interface WaitingName {
  id: string;
  name: string;
  rule: string;
  about: string;
  evidence: string[];
  folders: number;
  work: number;
  /** Found under a Clients-type folder: the review starts it ticked. */
  preTicked: boolean;
  createdAt: string | null;
}

export interface DeclinedName {
  id: string;
  name: string;
  decidedAt: string | null;
  /** It was a client once: the press is "Make it a client again". */
  wasClient: boolean;
}

export interface ClientsList {
  clients: ClientSummary[];
  waiting: WaitingName[];
  declined: DeclinedName[];
  total: number;
  /** "Notice clients in your folders" is on (absent = on). */
  noticing: boolean;
}

function folderNames(v: unknown): ClientFolderName[] {
  const out: ClientFolderName[] = [];
  for (const f of list(v)) {
    const r = rec(f);
    const folder = r ? str(r.folder, 600) : "";
    if (folder) out.push({ folder, name: str(r?.name, 200) || folder });
  }
  return out;
}

/**
 * `GET /clients`. Null for anything that is not that answer (an older
 * Iron Jarvis, a mock that hands every path the same object), so a section
 * draws nothing rather than "no clients".
 */
export function decodeClientsList(raw: unknown): ClientsList | null {
  const r = rec(raw);
  if (!r || !Array.isArray(r.clients)) return null;
  const clients: ClientSummary[] = [];
  for (const c of r.clients) {
    const o = rec(c);
    if (!o || !isClientId(o.id)) continue;
    const name = str(o.name, 80).trim();
    if (!name) continue;
    clients.push({
      id: o.id,
      name,
      how: howOf(o.how),
      folders: folderNames(o.folders),
      workCount: num(o.work_count),
      lastAt: str(o.last_at, 40) || null,
      preferencesCount: num(o.preferences_count),
    });
  }
  const waiting: WaitingName[] = [];
  for (const w of list(r.waiting)) {
    const o = rec(w);
    if (!o || !isClientId(o.id)) continue;
    const name = str(o.name, 80).trim();
    if (!name) continue;
    waiting.push({
      id: o.id,
      name,
      rule: str(o.rule, 40),
      about: str(o.about, 200),
      evidence: strings(o.evidence, 4),
      folders: countOf(o.folders),
      work: num(o.work),
      preTicked: o.pre_ticked === true,
      createdAt: str(o.created_at, 40) || null,
    });
  }
  const declined: DeclinedName[] = [];
  for (const d of list(r.declined)) {
    const o = rec(d);
    if (!o || !isClientId(o.id)) continue;
    const name = str(o.name, 80).trim();
    if (!name) continue;
    declined.push({ id: o.id, name, decidedAt: str(o.decided_at, 40) || null, wasClient: o.was_client === true });
  }
  return {
    clients,
    waiting,
    declined,
    total: Math.max(num(r.total), clients.length),
    noticing: r.noticing !== false,
  };
}

export type ClientStatus = "proposed" | "confirmed" | "declined" | "";

export interface ClientHeader {
  id: string;
  name: string;
  status: ClientStatus;
  how: ClientHow;
  aliases: string[];
  createdAt: string | null;
  confirmedAt: string | null;
  /** Why Iron Jarvis asked: the evidence lines. */
  why: string[];
}

export interface ClientFolder {
  folder: string;
  name: string;
  how: "found" | "added" | "";
  count: number;
  lastAt: string | null;
  forgotten: boolean;
}

export interface ClientFile {
  name: string;
  path: string;
  how: string;
  folderName: string;
  entryId: string;
  title: string;
  link: string;
  at: string | null;
}

export interface ClientDecision {
  text: string;
  label: "decided" | "open";
  entryId: string;
  title: string;
  link: string;
  writtenAt: string | null;
  /** False when the conversation changed after the recap was written. */
  current: boolean;
}

export interface SeenIn {
  entryId: string;
  title: string;
  link: string;
}

export interface PersonNamed {
  name: string;
  how: "title" | "file" | "email";
  seenIn: SeenIn[];
}

export interface PrefEvidence {
  quote: string;
  threadId: string;
  where: string;
  at: string | null;
  link: string;
}

export interface ClientPreference {
  id: string;
  text: string;
  origin: "said" | "typed" | "";
  localOnly: boolean;
  evidence: PrefEvidence[];
  createdAt: string | null;
}

export interface NamedElsewhere {
  entryId: string;
  title: string;
  link: string;
  folderName: string;
}

export interface ClientProject {
  id: string;
  name: string;
  root: string;
  /** The Project's own page (built from its id). */
  href: string;
}

export interface ClientPageData {
  client: ClientHeader;
  folders: ClientFolder[];
  entries: WorkItem[];
  entriesTotal: number;
  files: ClientFile[];
  filesTotal: number;
  decisions: ClientDecision[];
  people: PersonNamed[];
  preferences: ClientPreference[];
  namedElsewhere: NamedElsewhere[];
  projects: ClientProject[];
  /** Exactly what a cloud chat in this client's folders is told. */
  told: string;
  /** The folder that preview was built for ("" with no preview). */
  toldFor: string;
}

function seenIn(v: unknown, max: number): SeenIn[] {
  const out: SeenIn[] = [];
  for (const s of list(v)) {
    const o = rec(s);
    const entryId = o ? str(o.entry_id, 200) : "";
    const title = o ? str(o.title, 300).trim() : "";
    if (!entryId || !title) continue;
    out.push({ entryId, title, link: appLink(o?.link) });
    if (out.length >= max) break;
  }
  return out;
}

/** `GET /clients/{id}`. Null when the answer names no client. Every claim
 *  that names no record (a decision, a file or a person with nothing it was
 *  seen in) is dropped: the page shows only what it can point to. */
export function decodeClientPage(raw: unknown): ClientPageData | null {
  const r = rec(raw);
  const c = rec(r?.client);
  if (!r || !c || !isClientId(c.id)) return null;
  const name = str(c.name, 80).trim();
  if (!name) return null;
  const status: ClientStatus =
    c.status === "proposed" || c.status === "confirmed" || c.status === "declined" ? c.status : "";
  const client: ClientHeader = {
    id: c.id,
    name,
    status,
    how: howOf(c.how),
    aliases: strings(c.aliases, 6, 80),
    createdAt: str(c.created_at, 40) || null,
    confirmedAt: str(c.confirmed_at, 40) || null,
    why: strings(c.why, 8),
  };

  const folders: ClientFolder[] = [];
  for (const f of list(r.folders)) {
    const o = rec(f);
    const folder = o ? str(o.folder, 600) : "";
    if (!folder) continue;
    folders.push({
      folder,
      name: str(o?.name, 200) || folder,
      how: o?.how === "found" || o?.how === "added" ? o.how : "",
      count: num(o?.count),
      lastAt: str(o?.last_at, 40) || null,
      forgotten: o?.forgotten === true,
    });
  }

  const { entries } = decodeEntries({ entries: list(r.entries) });

  const files: ClientFile[] = [];
  for (const f of list(r.files)) {
    const o = rec(f);
    const fname = o ? str(o.name, 300) : "";
    const entryId = o ? str(o.entry_id, 200) : "";
    if (!o || !fname || !entryId) continue;
    files.push({
      name: fname,
      path: str(o.path, 600),
      how: str(o.how, 20),
      folderName: str(o.folder_name, 200),
      entryId,
      title: str(o.title, 300).trim() || "(no title)",
      link: appLink(o.link),
      at: str(o.at, 40) || null,
    });
  }

  const decisions: ClientDecision[] = [];
  for (const d of list(r.decisions)) {
    const o = rec(d);
    if (!o) continue;
    const text = str(o.text, 400).trim();
    const entryId = str(o.entry_id, 200);
    const title = str(o.title, 300).trim();
    if (!text || !entryId || !title || (o.label !== "decided" && o.label !== "open")) continue;
    decisions.push({
      text,
      label: o.label,
      entryId,
      title,
      link: appLink(o.link),
      writtenAt: str(o.written_at, 40) || null,
      current: o.current === true,
    });
  }

  const people: PersonNamed[] = [];
  for (const p of list(r.people)) {
    const o = rec(p);
    if (!o) continue;
    const pname = str(o.name, 120).trim();
    const how = o.how === "title" || o.how === "file" || o.how === "email" ? o.how : null;
    const seen = seenIn(o.seen_in, 3);
    if (!pname || !how || seen.length === 0) continue;
    people.push({ name: pname, how, seenIn: seen });
  }

  const preferences: ClientPreference[] = [];
  for (const p of list(r.preferences)) {
    const o = rec(p);
    if (!o || !isClientPrefId(o.id)) continue;
    const text = str(o.text, 280).trim();
    if (!text) continue;
    const evidence: PrefEvidence[] = [];
    for (const e of list(o.evidence)) {
      const eo = rec(e);
      if (!eo) continue;
      evidence.push({
        quote: str(eo.quote, 160).trim(),
        threadId: str(eo.thread_id, 200),
        where: str(eo.where, 200),
        at: str(eo.at, 40) || null,
        link: appLink(eo.link),
      });
      if (evidence.length >= 3) break;
    }
    preferences.push({
      id: o.id,
      text,
      origin: o.origin === "said" || o.origin === "typed" ? o.origin : "",
      localOnly: o.local_only === true,
      evidence,
      createdAt: str(o.created_at, 40) || null,
    });
  }

  const namedElsewhere: NamedElsewhere[] = [];
  for (const n of list(r.named_elsewhere)) {
    const o = rec(n);
    const entryId = o ? str(o.entry_id, 200) : "";
    const title = o ? str(o.title, 300).trim() : "";
    if (!entryId || !title) continue;
    namedElsewhere.push({ entryId, title, link: appLink(o?.link), folderName: str(o?.folder_name, 200) });
  }

  const projects: ClientProject[] = [];
  for (const p of list(r.projects)) {
    const o = rec(p);
    const id = o ? str(o.id, 80) : "";
    if (!PROJECT_ID_RE.test(id)) continue;
    projects.push({
      id,
      name: str(o?.name, 200).trim() || "(no name)",
      root: str(o?.root, 600),
      href: `/projects/${encodeURIComponent(id)}`,
    });
  }

  return {
    client,
    folders,
    entries,
    entriesTotal: Math.max(num(r.entries_total), entries.length),
    files,
    filesTotal: Math.max(num(r.files_total), files.length),
    decisions,
    people,
    preferences,
    namedElsewhere,
    projects,
    told: str(r.told, 4000),
    toldFor: str(r.told_for, 1000),
  };
}

/** The answer of a confirm / create / restore: `{ok, client:{id,name}, note}`. */
export function decodeAnswer(raw: unknown): { client: ClientRef | null; note: string } {
  const r = rec(raw);
  return { client: decodeClientRef(r?.client), note: str(r?.note, 400).trim() };
}

/** `POST /clients/confirm-many` → `{confirmed: [...], skipped: [{id, why}]}`.
 *  A confirmed entry may be an id or `{id, name}`. */
export function decodeConfirmMany(raw: unknown): { confirmed: string[]; skipped: { id: string; why: string }[] } {
  const r = rec(raw);
  const confirmed: string[] = [];
  for (const c of list(r?.confirmed)) {
    const id = typeof c === "string" ? c : rec(c)?.id;
    if (isClientId(id)) confirmed.push(id);
  }
  const skipped: { id: string; why: string }[] = [];
  for (const s of list(r?.skipped)) {
    const o = rec(s);
    if (o && isClientId(o.id)) skipped.push({ id: o.id, why: str(o.why, 300).trim() });
  }
  return { confirmed, skipped };
}

/** How many new names "Look again now" found, when the answer says. */
export function decodeNotice(raw: unknown): number | null {
  if (typeof raw === "number" && Number.isFinite(raw) && raw >= 0) return Math.floor(raw);
  const r = rec(raw);
  if (!r) return null;
  for (const k of ["noticed", "minted", "added", "new"]) {
    const v = r[k];
    if (typeof v === "number" && Number.isFinite(v) && v >= 0) return Math.floor(v);
  }
  return null;
}

/* ----------------------------------------------------- the chat question */

/** The owner's answer, kept on the message so a reopened chat shows it. */
export type ClientAnswerState = "yes" | "no" | "gone";

export interface ChatClientQuestion {
  id: string;
  name: string;
  folders: number;
  work: number;
  state?: ClientAnswerState;
}

/**
 * The reply's `client_question` (`{id, name, folders, work} | null`): an
 * object with a client id and a name survives; null, absent or anything
 * malformed is null (the line draws nothing rather than a broken ask). A
 * saved message carries the answer back through the same decoder.
 */
export function decodeClientQuestion(raw: unknown): ChatClientQuestion | null {
  const r = rec(raw);
  if (!r || !isClientId(r.id)) return null;
  const name = str(r.name, 80).trim();
  if (!name) return null;
  const out: ChatClientQuestion = { id: r.id, name, folders: countOf(r.folders), work: num(r.work) };
  if (r.state === "yes" || r.state === "no" || r.state === "gone") out.state = r.state;
  return out;
}

/** The save hazard the preference line has too: a turn that began before
 *  the owner answered ends with a history still holding the open question.
 *  Every save runs through this with the answers given in this window. */
export function applySettledQuestions<M extends { clientQuestion?: ChatClientQuestion }>(
  msgs: M[],
  settled: ReadonlyMap<string, ChatClientQuestion>,
): M[] {
  if (settled.size === 0) return msgs;
  let changed = false;
  const out = msgs.map((m) => {
    const q = m.clientQuestion;
    if (!q) return m;
    const done = settled.get(q.id);
    if (!done || done.state === q.state) return m;
    changed = true;
    return { ...m, clientQuestion: done };
  });
  return changed ? out : msgs;
}

/* ---------------------------------------------------------------- words */

/**
 * The possessive, by ONE table pinned on both sides (the daemon's
 * `workmap/clients/names.possessive`): a name already possessive stays
 * ("Hartley's"), a name ending in s takes an apostrophe ("Hartleys'"),
 * any other takes 's ("Smith's").
 */
export const POSSESSIVE_TABLE: readonly (readonly [string, string])[] = [
  ["Hartley's", "Hartley's"],
  ["Hartleys", "Hartleys'"],
  ["Smith", "Smith's"],
  ["Smith Dental", "Smith Dental's"],
  ["Weiss", "Weiss'"],
  ["", ""],
];

export function possessive(name: string): string {
  const n = (name ?? "").trim();
  if (!n) return "";
  if (/['’]s$/i.test(n) || /['’]$/.test(n)) return n;
  if (/s$/i.test(n)) return `${n}'`;
  return `${n}'s`;
}

export const CLIENTS_TITLE = "Clients";
export const CLIENTS_ABOUT =
  "People and businesses you do work for. Iron Jarvis keeps each client's folders, work and preferences together, and tells a chat about a client only when the chat is in that client's folders.";
export const YOUR_CLIENTS = "Your clients";
export const YES_A_CLIENT = "Yes, a client";
export const NOT_A_CLIENT = "Not a client";
export const NO = "No";
export const THESE_ARE_CLIENTS = "These are clients";
export const ASK_AGAIN = "Ask again";
export const MAKE_IT_A_CLIENT_AGAIN = "Make it a client again";
export const LOOK_AGAIN_NOW = "Look again now";
export const ADD_A_CLIENT = "Add a client";
export const MAKE_THIS_A_CLIENT = "Make this a client";
export const DECIDED = "Decided";
export const STILL_OPEN = "Still open";
export const PEOPLE_NAMED = "People named";
export const NAMED_ELSEWHERE = "Named in other work";
export const NOT_ON_RECORD = "That client is not on record.";
export const PREF_NOT_ON_RECORD = "That preference is not on record.";
export const LOOK_AT_THEM = "Look at them";

export const REVIEW_HEADING = "Names Iron Jarvis noticed in your folders";
export const REVIEW_ABOUT =
  "Tick the names that are clients and press These are clients. Names found under a Clients folder start ticked. A name you leave unticked keeps waiting; it is not turned down.";
export const REVIEW_EMPTY = "No names are waiting. When a name keeps coming back in your folders, it is listed here.";
export const NOTICING_OFF =
  "Iron Jarvis is not looking for new names: Notice clients in your folders is off in Settings › Memory & you.";
export const NOT_CLIENTS_HEADING = "Not clients";
export const CLIENTS_EMPTY = "No clients yet. Answer a name above, or add one with its folder.";
export const MEMORY_EMPTY =
  "No clients yet. When a name keeps coming back in your folders, Iron Jarvis asks you here. You can also make any folder below a client.";
export const ADD_ABOUT =
  "A client is known by its folders. Give the folder that holds only this client's work; the name is taken from the folder when you leave it empty.";
export const ADD_NAME_LABEL = "Name (optional)";
export const ADD_FOLDER_LABEL = "Folder";
export const ADD_FOLDER = "Add a folder";
export const RENAME = "Rename";
export const SAVE = "Save";
export const CANCEL = "Cancel";
export const CHANGE = "Change";
export const FORGET = "Forget";
export const FORGET_CONFIRM = "Forget it?";
export const NOT_A_CLIENT_CONFIRM = "Not a client?";
export const LOCAL_ONLY_TAG = "Local only";
export const PREF_PLACEHOLDER = "e.g. Uses cash basis";
export const WHY_ASKED = "Why Iron Jarvis asked:";
export const DECISIONS_NOTE =
  "Each comes from a recap checked against its conversation for numbers, quotes, file names, paths and e-mail addresses. Names in a recap are not checked.";
export const PEOPLE_NOTE =
  "Found in your own titles and file names, and e-mail addresses in checked recaps. Iron Jarvis does not check who they are.";
export const PROJECTS_HEADING = "Projects in these folders";
export const WORK_HEADING = "Work";
export const FILES_HEADING = "Files";
export const FOLDERS_HEADING = "Folders";
export const NOTHING_TOLD = "Nothing is told: it is not a client now.";

/** The sentence under "What a chat in X's folders is told" when nothing is:
 *  by status, never "not a client" for a client (the independent review). */
export function nothingToldWords(name: string, status: ClientStatus, folders: number): string {
  if (status !== "confirmed") return NOTHING_TOLD;
  if (folders === 0) return `${name} has no folder yet, so no chat is told about ${name}.`;
  return `Nothing is told about ${name} now.`;
}

/** Which folder the preview of what is told was built for. */
export function toldForWords(folder: string): string {
  return `A chat in ${folder} is told:`;
}

export function isAClient(name: string): string {
  return `Is ${name} a client?`;
}

export function foldersWords(n: number): string {
  return n === 1 ? "1 folder" : `${n} folders`;
}

export function preferencesWords(n: number): string {
  return n === 1 ? "1 preference" : `${n} preferences`;
}

/** The one line under a reply: "Iron Jarvis has seen Hartleys in 2 folders
 *  and 9 pieces of work. Is Hartleys a client?" */
export function questionLine(q: Pick<ChatClientQuestion, "name" | "folders" | "work">): string {
  const seen =
    q.folders > 0 && q.work > 0
      ? `Iron Jarvis has seen ${q.name} in ${foldersWords(q.folders)} and ${countWords(q.work)}. `
      : q.folders > 0
        ? `Iron Jarvis has seen ${q.name} in ${foldersWords(q.folders)}. `
        : "";
  return `${seen}${isAClient(q.name)}`;
}

export function afterYes(name: string): string {
  return `${name} is a client now.`;
}

export function afterNo(name: string): string {
  return `Iron Jarvis won't ask about ${name} again.`;
}

export function pageWords(name: string): string {
  return `${possessive(name)} page`;
}

export function alreadyAnswered(name: string): string {
  return `${name} was already answered.`;
}

export function whatWants(name: string): string {
  return `What ${possessive(name)} wants`;
}

export function keepFor(name: string): string {
  return `Keep for ${name}`;
}

export function toldHeading(name: string): string {
  return `What a chat in ${possessive(name)} folders is told`;
}

export function prefsIntro(name: string): string {
  const own = possessive(name);
  return `Told to chats, Chat › Code, agent runs and terminal help in ${own} folders, and nowhere else. Build › Code tools are not told yet.`;
}

export function removeFrom(name: string): string {
  return `Remove from ${name}`;
}

export function forgottenFolderWords(name: string): string {
  return `Forgotten: Iron Jarvis keeps no record here. It is still ${possessive(name)} folder, so other clients' work stays out of it.`;
}

export function notAClientAbout(name: string): string {
  return `Chats in its folders stop being told about ${name}. Its folders and preferences are kept, unused. You can make it a client again.`;
}

export function chipWords(name: string): string {
  return `Client: ${name}`;
}

/** "Iron Jarvis noticed 14 names in your folders that look like clients." */
export function waitingLine(n: number): string {
  return n === 1
    ? "Iron Jarvis noticed 1 name in your folders that looks like a client."
    : `Iron Jarvis noticed ${n} names in your folders that look like clients.`;
}

/** "3 folders · 24 pieces of work · last 2d ago · 2 preferences" */
export function clientRowLine(c: Pick<ClientSummary, "folders" | "workCount" | "lastAt" | "preferencesCount">): string {
  const parts = [foldersWords(c.folders.length), countWords(c.workCount)];
  if (c.lastAt) parts.push(`last ${timeAgo(c.lastAt)}`);
  parts.push(preferencesWords(c.preferencesCount));
  return parts.join(" · ");
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function dateOf(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(normalizeIso(iso));
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "14 Nov 2026" ("" when there is no date). */
export function dayMonthYear(iso: string | null | undefined): string {
  const d = dateOf(iso);
  return d ? `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}` : "";
}

/** "14 Nov" ("" when there is no date). */
export function dayMonth(iso: string | null | undefined): string {
  const d = dateOf(iso);
  return d ? `${d.getDate()} ${MONTHS[d.getMonth()]}` : "";
}

/** "A client since 14 Nov 2026 (you said yes)" / "(you added it)". */
export function sinceLine(h: Pick<ClientHeader, "how" | "confirmedAt" | "createdAt" | "status">): string {
  if (h.status === "proposed") return "Waiting for your answer.";
  if (h.status === "declined") return "Not a client: you said no.";
  const when = dayMonthYear(h.confirmedAt ?? h.createdAt);
  const how = h.how === "added" ? "you added it" : "you said yes";
  return when ? `A client since ${when} (${how})` : `A client (${how})`;
}

/** "Also written: Hartley's, Hartley" ("" when there is no other spelling). */
export function alsoWritten(aliases: string[]): string {
  return aliases.length ? `Also written: ${aliases.join(", ")}` : "";
}

/** "Named Hartleys" for a folder found by its name, "Added by you" for one
 *  the owner gave. */
export function folderHowWords(how: ClientFolder["how"], name: string): string {
  if (how === "added") return "Added by you";
  if (how === "found") return `Named ${name}`;
  return "";
}

/** A file's verb, in the work record's words ("" when unknown). */
export function fileHowWords(how: string): string {
  return how === "made" || how === "changed" || how === "wrote" ? how : "";
}

/** Where a preference came from: "You typed this on 14 Nov" / "Said in a
 *  chat in Hartleys 2026: “Use cash basis”". */
export function prefEvidenceLine(p: Pick<ClientPreference, "origin" | "evidence" | "createdAt">): string {
  if (p.origin === "typed") {
    const when = dayMonth(p.createdAt);
    return when ? `You typed this on ${when}` : "You typed this";
  }
  const e = p.evidence[0];
  if (p.origin === "said" && e) {
    const where = e.where ? ` in ${e.where}` : "";
    return e.quote ? `Said in a chat${where}: “${e.quote}”` : `Said in a chat${where}`;
  }
  return "";
}

/** "From the recap of “March reconciliation”, 2d ago". */
export function decisionSource(d: Pick<ClientDecision, "title" | "writtenAt">): string {
  return `From the recap of “${d.title}”${d.writtenAt ? `, ${timeAgo(d.writtenAt)}` : ""}`;
}

export function olderWords(n: number): string {
  return n > 0 ? `and ${n} older` : "";
}

export function moreWords(n: number): string {
  return n > 0 ? `and ${n} more` : "";
}

/** What the bulk review says after one press. */
export function confirmedWords(n: number): string {
  if (n <= 0) return "Nothing was changed.";
  return n === 1 ? "1 name is a client now." : `${n} names are clients now.`;
}

export function lookedAgainWords(n: number | null): string {
  if (n === null) return "Looked again.";
  if (n === 0) return "Looked again. No new names.";
  return n === 1 ? "Looked again. 1 new name is waiting." : `Looked again. ${n} new names are waiting.`;
}

/** The sentence a refused or failed press shows: the route's own words for
 *  a refusal (a 4xx is written for the owner), else the plain failure line
 *  ("Couldn't save your answer. Try again."). */
export function pressFailed(error: FailureLike, action: string): string {
  return serverSentence(error) ? (error.message ?? "").trim() : couldnt(action, error);
}

/* ------------------------------------------- which folders may be offered */

/** The names a Clients-type folder may have (owner decision 9), folded. */
export const CLIENT_PARENT_PHRASES: ReadonlySet<string> = new Set([
  "clients",
  "client",
  "customers",
  "customer",
  "client files",
  "client folders",
  "customer files",
  "tax clients",
]);

/** Folders that hold work for many people, never one client's own. */
const WIDE_FOLDERS: ReadonlySet<string> = new Set([
  "downloads",
  "desktop",
  "documents",
  "my documents",
  "onedrive",
  "dropbox",
  "users",
  "home",
  "new folder",
]);

function fold(s: string): string {
  return s
    .toLowerCase()
    .replace(/['’]/g, "")
    .replace(/[&\-_.]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Whether a folder row may offer "Make this a client": never the top of a
 * drive or a share, a Clients-type folder, or a folder that holds work for
 * many people (Downloads, Desktop, Documents). The daemon's folder rules
 * decide; this only keeps the press off rows it would refuse anyway.
 */
export function mayMakeClient(folder: string): boolean {
  const parts = folder.split(/[\\/]+/).filter(Boolean);
  if (parts.length === 0) return false;
  const unc = /^[\\/]{2}/.test(folder);
  if (unc && parts.length <= 2) return false;
  if (!unc && parts.length === 1 && /^[A-Za-z]:$/.test(parts[0])) return false;
  const last = fold(parts[parts.length - 1]);
  if (!last || CLIENT_PARENT_PHRASES.has(last) || WIDE_FOLDERS.has(last)) return false;
  return true;
}
