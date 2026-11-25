/**
 * "Hand it a recording" (idea 33, phase 33-a): a recording's card, its wire
 * shape and its WHITELIST decoder. The daemon's twin is
 * documents/recording_jobs.py (`card_of`): every word and number on the card
 * comes from the job's own record (the notes were checked by code against
 * what was heard), never from the model's reply.
 *
 * The card rides the reply's `config_cards` (kind "recording"), decoded by
 * lib/configCards, drawn by components/chat/RecordingCard. While the
 * recording is still being heard it reads `GET /documents/recording/{id}`
 * again, only while it is on screen (G7).
 */

/** What a chat takes as a recording (twin of documents/recording.py
 *  RECORDING_SUFFIXES, pinned equal): audio, and video for its sound. */
export const RECORDING_SUFFIXES: readonly string[] = [
  ".aac", ".avi", ".flac", ".m4a", ".m4v", ".mkv", ".mov", ".mp3", ".mp4", ".oga", ".ogg", ".opus", ".wav",
  ".weba", ".webm", ".wma", ".wmv",
];

export function isRecordingName(name: string): boolean {
  const m = /\.[^./\\]+$/.exec(name.toLowerCase());
  return !!m && RECORDING_SUFFIXES.includes(m[0]);
}

/** Where a dropped or picked file is on this PC, from the desktop app ("" in
 *  a browser, or for a file with no path). A recording too large to upload
 *  is then read where it is, never copied. */
export function desktopPathOf(file: File): string {
  try {
    const fn = (window as unknown as { ironjarvis?: { pathForFile?: (f: File) => unknown } }).ironjarvis?.pathForFile;
    const got = typeof fn === "function" ? fn(file) : "";
    return typeof got === "string" ? got : "";
  } catch {
    return "";
  }
}

export type RecordingStatus = "hearing" | "notes" | "ask" | "done" | "failed" | "undone";

export interface RecordingItem {
  /** The time it was said, as the transcript writes it: "12:31". */
  time: string;
  seconds: number;
  /** The item in one short sentence (secret-masked by the daemon). */
  note: string;
}

export interface RecordingOffer {
  provider: string;
  model: string | null;
  /** The model the notes would be written with: "Write the notes with Claude?" */
  name: string;
  /** How many words of transcript it would be sent. */
  words: number;
}

export type RecordingKind = "decisions" | "you_owe" | "they_owe";
export const RECORDING_KINDS: readonly RecordingKind[] = ["decisions", "you_owe", "they_owe"];

/** How each kind is headed on the card (twin of recording.KIND_WORDS). */
export const KIND_HEADINGS: Readonly<Record<RecordingKind, string>> = {
  decisions: "Decisions",
  you_owe: "Things you owe",
  they_owe: "Things they owe",
};

export interface RecordingCard {
  kind: "recording";
  recording_id: string;
  source_name: string;
  source: string;
  status: RecordingStatus;
  heard_s: number;
  total_s: number;
  /** "12 of 40 minutes". */
  progress_words: string;
  /** Who turns the speech into text: "the offline speech model on this PC". */
  who: string;
  on_this_pc: boolean;
  /** "Made on this PC by the small offline speech model. …" */
  made_words: string;
  /** Said when something chosen for voice was not used (off your network). */
  transcriber_note: string;
  /** The files (absolute), "" until written. */
  transcript: string;
  notes: string;
  words: number;
  length_words: string;
  cut_words: string;
  notes_by: string;
  counts: Record<RecordingKind | "dropped", number>;
  /** "5 decisions, 3 things you owe, 2 things they owe". */
  counts_words: string;
  /** "2 items were left out: their words are not in the recording near the time given." */
  dropped_words: string;
  who_owes_words: string;
  items: Record<RecordingKind, RecordingItem[]>;
  /** The yes the notes wait on (only while status is "ask"). */
  offer: RecordingOffer | null;
  /** Why there are no notes, in words. */
  notes_why: string;
  error: string;
}

const ID_RX = /^rec_[0-9a-f]{16}$/;
const STATUSES: readonly RecordingStatus[] = ["hearing", "notes", "ask", "done", "failed", "undone"];
const MAX_ITEMS = 30;

const str = (v: unknown, cap = 600): string => (typeof v === "string" ? v.slice(0, cap) : "");
const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : 0);

function items(v: unknown): RecordingItem[] {
  if (!Array.isArray(v)) return [];
  const out: RecordingItem[] = [];
  for (const raw of v.slice(0, MAX_ITEMS)) {
    if (!raw || typeof raw !== "object") continue;
    const r = raw as Record<string, unknown>;
    const time = str(r.time, 12);
    if (!/^\d{1,2}(?::\d{2}){1,2}$/.test(time)) continue;
    out.push({ time, seconds: num(r.seconds), note: str(r.note, 300) });
  }
  return out;
}

/** Keep a well-formed recording card; anything else is dropped (null). */
export function decodeRecordingCard(raw: unknown): RecordingCard | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (r.kind !== "recording" || !ID_RX.test(str(r.recording_id))) return null;
  const status = STATUSES.includes(r.status as RecordingStatus) ? (r.status as RecordingStatus) : "failed";
  const counts = (r.counts && typeof r.counts === "object" ? r.counts : {}) as Record<string, unknown>;
  const its = (r.items && typeof r.items === "object" ? r.items : {}) as Record<string, unknown>;
  const offerRaw = r.offer && typeof r.offer === "object" ? (r.offer as Record<string, unknown>) : null;
  const offer: RecordingOffer | null =
    offerRaw && status === "ask" && str(offerRaw.provider) && str(offerRaw.name)
      ? {
          provider: str(offerRaw.provider, 80),
          model: typeof offerRaw.model === "string" ? offerRaw.model.slice(0, 120) : null,
          name: str(offerRaw.name, 80),
          words: num(offerRaw.words),
        }
      : null;
  return {
    kind: "recording",
    recording_id: str(r.recording_id),
    source_name: str(r.source_name, 200),
    source: str(r.source, 1000),
    status,
    heard_s: num(r.heard_s),
    total_s: num(r.total_s),
    progress_words: str(r.progress_words, 60),
    who: str(r.who, 120),
    on_this_pc: r.on_this_pc === true,
    made_words: str(r.made_words, 300),
    transcriber_note: str(r.transcriber_note, 400),
    transcript: str(r.transcript, 1000),
    notes: str(r.notes, 1000),
    words: num(r.words),
    length_words: str(r.length_words, 40),
    cut_words: str(r.cut_words, 200),
    notes_by: str(r.notes_by, 80),
    counts: {
      decisions: num(counts.decisions),
      you_owe: num(counts.you_owe),
      they_owe: num(counts.they_owe),
      dropped: num(counts.dropped),
    },
    counts_words: str(r.counts_words, 200),
    dropped_words: str(r.dropped_words, 200),
    who_owes_words: str(r.who_owes_words, 200),
    items: { decisions: items(its.decisions), you_owe: items(its.you_owe), they_owe: items(its.they_owe) },
    offer,
    notes_why: str(r.notes_why, 400),
    error: str(r.error, 600),
  };
}

/** Still working: the card reads the record again (only while on screen). */
export function stillWorking(card: RecordingCard): boolean {
  return card.status === "hearing" || card.status === "notes";
}

/** The card's one running line, from the record. */
export function workingWords(card: RecordingCard): string {
  if (card.status === "notes") {
    return card.notes_by ? `Writing the notes with ${card.notes_by}…` : "Writing the notes…";
  }
  const where = card.on_this_pc ? "on this PC" : `with ${card.who || "the speech service"}`;
  return `Turning speech into text ${where}: ${card.progress_words}`;
}

/** How far it is, 0..1 (0 when the whole length is not known). */
export function progressShare(card: RecordingCard): number {
  if (!card.total_s) return 0;
  return Math.max(0, Math.min(1, card.heard_s / card.total_s));
}

/** The offer, in words: "Write the notes with Claude? The transcript (812
 *  words) is sent to it. Nothing has been sent yet." */
export function offerWords(offer: RecordingOffer): string {
  const words = offer.words ? ` (${offer.words.toLocaleString("en-US")} words)` : "";
  return `Write the notes with ${offer.name}? The transcript${words} is sent to it. Nothing has been sent yet.`;
}

/** The chat a press belongs to, read AT the press (lib/stackPlan's twin). */
export interface RecordingScope {
  threadId: string;
  localOnly: boolean;
}

/** What "Write the notes" sends: the model the card named and the chat's
 *  Local only (the daemon ORs it with the job's and the saved chat's). */
export function notesBody(
  card: RecordingCard,
  scope?: RecordingScope | null,
): { provider: string; model: string | null; thread_id: string; local_only: boolean } {
  return {
    provider: card.offer?.provider ?? "",
    model: card.offer?.model ?? null,
    thread_id: scope?.threadId ?? "",
    local_only: scope?.localOnly === true,
  };
}

/** A file's name from its path, for the card's links. */
export function baseName(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}
