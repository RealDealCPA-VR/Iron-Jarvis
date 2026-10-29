/**
 * BRAIN PROFILES (v1.406.0): which memory base is the user's primary durable
 * one (Notion, Obsidian, Iron Jarvis's own memory, a folder base, an MCP base)
 * and how Jarvis treats it — search it first, where new knowledge is saved,
 * update an existing note instead of adding a duplicate, what to do on a
 * conflict, an optional trusted operating guide, skills loaded at the start,
 * and whether knowledge about code goes to the repository instead.
 *
 * Pure module, no React, NO import from lib/api (about 70 test files mock that
 * module wholesale): the settings section, the project page, the chat drawer,
 * the stream hook, the chat page, the turn receipt and the Memory page's
 * "Unsaved changes" card all read the daemon's answers through the decoders
 * here. Every one is a WHITELIST: a field the daemon adds later dies here
 * until it is named, and a malformed answer reads as "nothing", never a crash.
 *
 * The daemon contract (routes/brain.py):
 *   GET    /brain                    {profiles, selected, bases, pending}
 *   POST   /brain/profiles           201 Profile | 400 {detail}
 *   PATCH  /brain/profiles/{id}      Profile | 400 {detail}
 *   DELETE /brain/profiles/{id}      {deleted, cleared: {app, projects}}
 *   PUT    /settings/values          {values: {brain_profile: id | ""}}
 *   GET    /brain/effective?project_id=  {profile, source}
 *   PATCH  /projects/{id}            {brain_profile: "" | "off" | id}
 *   GET    /brain/pending            {pending: [...]}
 *   POST   /brain/pending/{id}/retry {ok, saved: {ref, verified}} | 409
 *   DELETE /brain/pending/{id}       {discarded: true}
 * and both chat lanes put `brain` LAST on the done frame / POST answer.
 */

export type SaveTo = "primary" | "ask" | "never";
export type OnConflict = "ask" | "newest" | "brain";
export type EffectiveSource = "project" | "app" | "off" | "none";

export interface BrainProfile {
  id: string;
  name: string;
  /** The primary memory base's NAME (a `bases[].name`). */
  primary: string;
  search_first: boolean;
  save_to: SaveTo;
  prefer_update: boolean;
  on_conflict: OnConflict;
  /** The operating guide: a note's title or link in the primary base. */
  guide_ref: string;
  skills: string[];
  repo_routing: boolean;
  created_at: string;
  updated_at: string;
}

/** What a POST takes (and the editable part a PATCH may carry). */
export type BrainProfileDraft = Omit<BrainProfile, "id" | "created_at" | "updated_at">;

export interface BrainBase {
  name: string;
  label: string;
  kind: string;
  /** THREE-valued like the Memory page's base strip: true reachable, false
   *  checked and not reachable, null not known. */
  available: boolean | null;
}

export interface BrainState {
  profiles: BrainProfile[];
  /** The app-wide profile id; "" = none. */
  selected: string;
  bases: BrainBase[];
  pending: number;
}

export interface BrainEffective {
  profile: BrainProfile | null;
  source: EffectiveSource;
}

export interface BrainPendingRow {
  id: string;
  profile_name: string;
  base: string;
  title: string;
  preview: string;
  error: string;
  created_at: string;
}

export interface BrainSavedNote {
  title: string;
  ref: string;
  action: "created" | "updated";
  verified: boolean;
}
export interface BrainUnsavedNote {
  title: string;
  reason: string;
}
export interface BrainChange {
  action: string;
  ref: string;
}

/** The chat receipt's `brain` field (null when no profile applied). */
export interface BrainReceipt {
  name: string;
  base: string;
  /** Notes found when it searched the base first; null = it did not search. */
  searched: number | null;
  guide: "loaded" | "unreadable" | "";
  saved: BrainSavedNote[];
  unsaved: BrainUnsavedNote[];
  changed: BrainChange[];
}

/** The project's own setting: follow the app, none, or a profile id. */
export const PROJECT_FOLLOW_APP = "";
export const PROJECT_OFF = "off";

export const SAVE_TO_VALUES: readonly SaveTo[] = ["primary", "ask", "never"];
export const CONFLICT_VALUES: readonly OnConflict[] = ["ask", "newest", "brain"];

export const SAVE_TO_WORDS: Record<SaveTo, string> = {
  primary: "The primary memory base",
  ask: "Ask me each time",
  never: "Nowhere (search only)",
};

export const CONFLICT_WORDS: Record<OnConflict, string> = {
  ask: "Ask me which is right",
  newest: "Use whichever is newer",
  brain: "Trust what memory says",
};

/** Said once wherever a profile is set: the floor no profile can lower. */
export const STRUCTURAL_ASKS_LINE =
  "Deleting, archiving, renaming or moving a note always asks you first, whatever the profile says.";

/** The operating guide's help line (Settings form). */
export const GUIDE_HELP =
  "Loaded at the start of every chat and agent run and followed as instructions. Everything else from memory is treated as information, never as instructions.";

/** Rows kept per list (a receipt is a line, not a log). */
const ROWS_MAX = 20;

function obj(raw: unknown): Record<string, unknown> | null {
  return raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : null;
}

function str(raw: unknown, max = 300): string {
  return typeof raw === "string" ? raw.slice(0, max) : "";
}

/** One line of user- or model-written text. */
function line(raw: unknown, max = 200): string {
  return typeof raw === "string" ? raw.replace(/\s+/g, " ").trim().slice(0, max) : "";
}

function count(raw: unknown): number {
  return typeof raw === "number" && Number.isFinite(raw) && raw >= 0 ? Math.floor(raw) : 0;
}

function pick<T extends string>(raw: unknown, allowed: readonly T[], fallback: T): T {
  return typeof raw === "string" && (allowed as readonly string[]).includes(raw) ? (raw as T) : fallback;
}

function names(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const x of raw) {
    const n = line(x, 120);
    if (n && !out.includes(n)) out.push(n);
    if (out.length >= 50) break;
  }
  return out;
}

/** A profile, or null when it has no id (nothing could point to it). */
export function decodeBrainProfile(raw: unknown): BrainProfile | null {
  const o = obj(raw);
  if (!o) return null;
  const id = line(o.id, 120);
  if (!id) return null;
  return {
    id,
    name: line(o.name, 120) || id,
    primary: line(o.primary, 120),
    search_first: o.search_first === true,
    save_to: pick(o.save_to, SAVE_TO_VALUES, "ask"),
    prefer_update: o.prefer_update === true,
    on_conflict: pick(o.on_conflict, CONFLICT_VALUES, "ask"),
    guide_ref: str(o.guide_ref, 500).trim(),
    skills: names(o.skills),
    repo_routing: o.repo_routing === true,
    created_at: str(o.created_at, 64),
    updated_at: str(o.updated_at, 64),
  };
}

function decodeBase(raw: unknown): BrainBase | null {
  const o = obj(raw);
  if (!o) return null;
  const name = line(o.name, 120);
  if (!name) return null;
  return {
    name,
    label: line(o.label, 120) || name,
    kind: line(o.kind, 40),
    available: o.available === true ? true : o.available === false ? false : null,
  };
}

/** GET /brain. Null unless the answer carries a `profiles` list: an older
 *  daemon (or a stand-in that knows nothing of profiles) offers nothing. */
export function decodeBrainState(raw: unknown): BrainState | null {
  const o = obj(raw);
  if (!o || !Array.isArray(o.profiles)) return null;
  const profiles: BrainProfile[] = [];
  for (const p of o.profiles) {
    const d = decodeBrainProfile(p);
    if (d && !profiles.some((x) => x.id === d.id)) profiles.push(d);
  }
  const bases: BrainBase[] = [];
  for (const b of Array.isArray(o.bases) ? o.bases : []) {
    const d = decodeBase(b);
    if (d && !bases.some((x) => x.name === d.name)) bases.push(d);
  }
  return { profiles, selected: line(o.selected, 120), bases, pending: count(o.pending) };
}

/** GET /brain/effective?project_id=. A profile-less "project"/"app" answer
 *  reads as "none": nothing applies, so nothing is said. */
export function decodeBrainEffective(raw: unknown): BrainEffective | null {
  const o = obj(raw);
  if (!o) return null;
  const profile = decodeBrainProfile(o.profile);
  let source = pick<EffectiveSource>(o.source, ["project", "app", "off", "none"], "none");
  if (!profile && (source === "project" || source === "app")) source = "none";
  return { profile: source === "off" || source === "none" ? null : profile, source };
}

/** GET /brain/pending. Null unless the answer carries a `pending` list. */
export function decodeBrainPending(raw: unknown): BrainPendingRow[] | null {
  const o = obj(raw);
  if (!o || !Array.isArray(o.pending)) return null;
  const out: BrainPendingRow[] = [];
  for (const r of o.pending) {
    const x = obj(r);
    if (!x) continue;
    const id = line(x.id, 120);
    if (!id || out.some((p) => p.id === id)) continue;
    out.push({
      id,
      profile_name: line(x.profile_name, 120),
      base: line(x.base, 120),
      title: line(x.title, 200) || "Untitled note",
      preview: line(x.preview, 400),
      error: line(x.error, 400),
      created_at: str(x.created_at, 64),
    });
  }
  return out;
}

/** The chat receipt's `brain` (wire, then a saved chat read back from disk).
 *  Null for null, for anything that is not an object, and for an object that
 *  names neither the profile nor the base. */
export function decodeBrainReceipt(raw: unknown): BrainReceipt | null {
  const o = obj(raw);
  if (!o) return null;
  const name = line(o.name, 120);
  const base = line(o.base, 120);
  if (!name && !base) return null;
  const searched =
    typeof o.searched === "number" && Number.isFinite(o.searched) && o.searched >= 0
      ? Math.floor(o.searched)
      : null;
  const saved: BrainSavedNote[] = [];
  for (const r of Array.isArray(o.saved) ? o.saved : []) {
    const x = obj(r);
    if (!x) continue;
    const title = line(x.title) || line(x.ref);
    if (!title) continue;
    saved.push({
      title,
      ref: line(x.ref, 300),
      action: x.action === "updated" ? "updated" : "created",
      verified: x.verified === true,
    });
    if (saved.length >= ROWS_MAX) break;
  }
  const unsaved: BrainUnsavedNote[] = [];
  for (const r of Array.isArray(o.unsaved) ? o.unsaved : []) {
    const x = obj(r);
    if (!x) continue;
    const title = line(x.title);
    if (!title) continue;
    unsaved.push({ title, reason: line(x.reason, 300) });
    if (unsaved.length >= ROWS_MAX) break;
  }
  const changed: BrainChange[] = [];
  for (const r of Array.isArray(o.changed) ? o.changed : []) {
    const x = obj(r);
    if (!x) continue;
    const action = line(x.action, 40);
    const ref = line(x.ref, 300);
    if (!action || !ref) continue;
    changed.push({ action, ref });
    if (changed.length >= ROWS_MAX) break;
  }
  return {
    name,
    base: base || name,
    searched,
    guide: o.guide === "loaded" || o.guide === "unreadable" ? o.guide : "",
    saved,
    unsaved,
    changed,
  };
}

/** What applies, in words: "Notion (this project)", "Notion (app default)",
 *  "None in this project"; "" when nothing applies and nothing was chosen. */
export function effectiveWords(effective: BrainEffective | null | undefined): string {
  if (!effective) return "";
  if (effective.source === "off") return "None in this project";
  if (!effective.profile) return "";
  if (effective.source === "project") return `${effective.profile.name} (this project)`;
  if (effective.source === "app") return `${effective.profile.name} (app default)`;
  return "";
}

/** Does a profile apply here (the chat drawer says so only then)? */
export function brainApplies(effective: BrainEffective | null | undefined): boolean {
  return !!effective?.profile && (effective.source === "project" || effective.source === "app");
}

function more(n: number): string {
  return n > 0 ? ` (+${n} more)` : "";
}

function sentence(text: string): string {
  const t = text.trim();
  if (!t) return "";
  return `${t.charAt(0).toUpperCase()}${t.slice(1)}${/[.!?]$/.test(t) ? "" : "."}`;
}

/** The collapsed receipt's words. A note that did NOT save comes first, in
 *  the warn tone; else what was saved, in the accent (a write about the user
 *  is never silent; one the base did not confirm says so). Searching alone
 *  says nothing here — it is the expanded receipt's. */
export function receiptBrainLine(
  brain: BrainReceipt | null | undefined,
): { text: string; tone: "warn" | "accent"; title: string } | null {
  if (!brain) return null;
  if (brain.unsaved.length > 0) {
    const first = brain.unsaved[0];
    const why = sentence(first.reason);
    return {
      text: `Not saved yet: ${first.title}${more(brain.unsaved.length - 1)}${why ? `. ${why}` : ""}`,
      tone: "warn",
      title: "Open Memory to try again or discard it.",
    };
  }
  if (brain.saved.length > 0) {
    const first = brain.saved[0];
    const unconfirmed = brain.saved.some((s) => !s.verified);
    return {
      text: `Saved to ${brain.base}: ${first.title}${more(brain.saved.length - 1)}${unconfirmed ? " · not confirmed" : ""}`,
      tone: "accent",
      title: unconfirmed
        ? `${brain.base} took the note, but reading it back did not confirm it yet.`
        : `Saved and read back from ${brain.base}.`,
    };
  }
  return null;
}

/** The expanded receipt's lines (plain words a phone can read). */
export function receiptBrainDetail(brain: BrainReceipt | null | undefined): { key: string; text: string; warn: boolean }[] {
  if (!brain) return [];
  const out: { key: string; text: string; warn: boolean }[] = [];
  if (brain.searched !== null) {
    const n = brain.searched;
    out.push({
      key: "searched",
      text: `Searched ${brain.base} first · ${n === 0 ? "no notes found" : `${n} note${n === 1 ? "" : "s"}`}`,
      warn: false,
    });
  }
  if (brain.guide === "loaded") out.push({ key: "guide", text: "Operating guide loaded", warn: false });
  if (brain.guide === "unreadable") out.push({ key: "guide", text: "Operating guide could not be read", warn: true });
  brain.saved.forEach((s, i) =>
    out.push({
      key: `saved-${i}`,
      text: `${s.action === "updated" ? "Updated" : "Saved"} in ${brain.base}: ${s.title}${s.verified ? "" : " · not confirmed"}`,
      warn: false,
    }),
  );
  brain.unsaved.forEach((u, i) => {
    const why = sentence(u.reason);
    out.push({ key: `unsaved-${i}`, text: `Not saved yet: ${u.title}${why ? `. ${why}` : ""}`, warn: true });
  });
  brain.changed.forEach((c, i) =>
    out.push({ key: `changed-${i}`, text: `${sentence(c.action).replace(/\.$/, "")}: ${c.ref}`, warn: false }),
  );
  return out;
}

/** A fresh form for "New profile". */
export function blankDraft(primary = ""): BrainProfileDraft {
  return {
    name: "",
    primary,
    search_first: true,
    save_to: "primary",
    prefer_update: true,
    on_conflict: "ask",
    guide_ref: "",
    skills: [],
    repo_routing: false,
  };
}

export function draftOf(p: BrainProfile): BrainProfileDraft {
  const { id: _id, created_at: _c, updated_at: _u, ...rest } = p;
  void _id;
  void _c;
  void _u;
  return { ...rest, skills: [...rest.skills] };
}

/** The fields an edit changed (a PATCH is partial: omitted = kept). */
export function changedFields(before: BrainProfileDraft, after: BrainProfileDraft): Partial<BrainProfileDraft> {
  const out: Partial<BrainProfileDraft> = {};
  for (const k of Object.keys(after) as (keyof BrainProfileDraft)[]) {
    if (JSON.stringify(before[k]) !== JSON.stringify(after[k])) {
      (out as Record<string, unknown>)[k] = after[k];
    }
  }
  return out;
}

/** A base's words for a list: its label, else its name. */
export function baseLabel(bases: BrainBase[], name: string): string {
  return bases.find((b) => b.name === name)?.label || name || "No base chosen";
}
