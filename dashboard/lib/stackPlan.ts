/**
 * "Sort this stack" (idea 31, phase 31-a): the plan card's wire shape, its
 * WHITELIST decoder and the pure edits the card makes (move a page, rename,
 * pick a folder). The daemon's twin is documents/stack.py (`card_of`, the
 * safety step and the write): it checks every edit again before anything is
 * written, so nothing here is trusted to keep a page.
 *
 * The card rides the reply's `config_cards` (kind "stack"), decoded by
 * lib/configCards, drawn by components/chat/StackPlanCard.
 */

export interface StackDoc {
  pages: number[];
  name: string;
  /** Inside the chat's folder, as "a/b"; "" is the chat's folder itself. */
  folder: string;
  /** Blank pages kept with this document (flagged, never dropped). */
  blank: number[];
  /** Pages the plan could not place: "Not sure (pages 41-42).pdf". */
  unsure: boolean;
}

export interface StackOffer {
  provider: string;
  model: string | null;
  /** The model the offer names: "Read 4 more pages with Claude?" */
  name: string;
  /** How many pages one press sends (the daemon's READ_MORE_MAX bound):
   *  "Read 20 of 60 more pages with Claude?" when more wait. */
  pages: number;
}

export interface StackCard {
  kind: "stack";
  plan_id: string;
  /** The scan (absolute), opened beside the chat at a page. */
  source: string;
  source_name: string;
  /** The chat's folder (absolute): documents go in folders inside it. */
  root: string;
  page_count: number;
  documents: StackDoc[];
  /** Pages this PC could not read; offered, never sent unasked. */
  unread: number[];
  read_here: number;
  read_by_model: number;
  /** The model the user sent the unread pages to, by name ("" when none). */
  read_by: string;
  /** A page's first words (secret-masked), for the strip's pointer text. */
  snippets: Record<string, string>;
  offer: StackOffer | null;
  applied: boolean;
  /** Set on the message by the card: what Confirm wrote. */
  written?: { path: string; pages: number; name: string; folder: string }[];
  undone?: boolean;
  /** What the Undo said, when it said more than "removed". */
  undo_note?: string;
}

const PLAN_ID_RX = /^stk_[0-9a-f]{16}$/;
const MAX_PAGES = 300;

const str = (v: unknown, cap = 400): string => (typeof v === "string" ? v.slice(0, cap) : "");

function pageList(v: unknown, total: number): number[] {
  if (!Array.isArray(v)) return [];
  const out: number[] = [];
  for (const n of v.slice(0, MAX_PAGES)) {
    if (typeof n === "number" && Number.isInteger(n) && n >= 1 && n <= total && !out.includes(n)) out.push(n);
  }
  return out.sort((a, b) => a - b);
}

/** Keep a well-formed plan card; anything else is dropped (null). */
export function decodeStackCard(raw: unknown): StackCard | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (r.kind !== "stack" || !PLAN_ID_RX.test(str(r.plan_id))) return null;
  const total = typeof r.page_count === "number" && Number.isInteger(r.page_count)
    ? Math.max(0, Math.min(MAX_PAGES, r.page_count))
    : 0;
  if (!total) return null;
  const docs: StackDoc[] = [];
  for (const d of Array.isArray(r.documents) ? r.documents.slice(0, MAX_PAGES) : []) {
    if (!d || typeof d !== "object") continue;
    const x = d as Record<string, unknown>;
    const pages = pageList(x.pages, total);
    if (!pages.length || !str(x.name)) continue;
    docs.push({
      pages,
      name: str(x.name, 120),
      folder: str(x.folder, 200),
      blank: pageList(x.blank, total),
      unsure: x.unsure === true,
    });
  }
  if (!docs.length) return null;
  const snippets: Record<string, string> = {};
  if (r.snippets && typeof r.snippets === "object") {
    for (const [k, v] of Object.entries(r.snippets as Record<string, unknown>).slice(0, MAX_PAGES)) {
      if (/^\d{1,3}$/.test(k) && typeof v === "string") snippets[k] = v.slice(0, 90);
    }
  }
  const o = r.offer && typeof r.offer === "object" ? (r.offer as Record<string, unknown>) : null;
  const unread = pageList(r.unread, total);
  const perPress = o && typeof o.pages === "number" && Number.isInteger(o.pages) && o.pages >= 1
    ? Math.min(o.pages, unread.length)
    : unread.length;
  const offer = o && str(o.provider) && str(o.name)
    ? { provider: str(o.provider, 80), model: str(o.model, 120) || null, name: str(o.name, 80), pages: perPress }
    : null;
  const written = Array.isArray(r.written)
    ? r.written.slice(0, MAX_PAGES).flatMap((w) => {
        if (!w || typeof w !== "object") return [];
        const x = w as Record<string, unknown>;
        return str(x.path, 1000)
          ? [{ path: str(x.path, 1000), pages: typeof x.pages === "number" ? x.pages : 0, name: str(x.name, 200), folder: str(x.folder, 200) }]
          : [];
      })
    : undefined;
  return {
    kind: "stack",
    plan_id: str(r.plan_id),
    source: str(r.source, 1000),
    source_name: str(r.source_name, 200),
    root: str(r.root, 1000),
    page_count: total,
    documents: docs,
    unread,
    read_here: typeof r.read_here === "number" ? r.read_here : 0,
    read_by_model: typeof r.read_by_model === "number" ? r.read_by_model : 0,
    read_by: str(r.read_by, 80),
    snippets,
    offer,
    applied: r.applied === true,
    ...(written?.length ? { written } : {}),
    ...(r.undone === true ? { undone: true } : {}),
    ...(str(r.undo_note) ? { undo_note: str(r.undo_note, 300) } : {}),
  };
}

/** "page 7", "pages 41-42", "pages 1-2, 5" — documents/stack.span_words. */
export function spanWords(pages: number[]): string {
  const runs: number[][] = [];
  for (const n of [...pages].sort((a, b) => a - b)) {
    const last = runs[runs.length - 1];
    if (last && n === last[last.length - 1] + 1) last.push(n);
    else runs.push([n]);
  }
  const bits = runs.map((r) => (r.length > 1 ? `${r[0]}-${r[r.length - 1]}` : `${r[0]}`));
  return `${pages.length > 1 ? "pages" : "page"} ${bits.join(", ")}`;
}

/** Move one page into another document. A document left with no page goes
 *  (its pages all moved away); the page is never lost. */
export function movePage(docs: StackDoc[], page: number, to: number): StackDoc[] {
  if (to < 0 || to >= docs.length || docs[to].pages.includes(page)) return docs;
  if (!docs.some((d) => d.pages.includes(page))) return docs;
  return docs
    .map((d, i) => {
      if (i === to) return { ...d, pages: [...d.pages, page].sort((a, b) => a - b) };
      if (d.pages.includes(page)) {
        return { ...d, pages: d.pages.filter((n) => n !== page), blank: d.blank.filter((n) => n !== page) };
      }
      return d;
    })
    .filter((d) => d.pages.length > 0);
}

/** Every page in exactly one document: the check Confirm is held to. */
export function everyPageOnce(docs: StackDoc[], total: number): boolean {
  const seen = new Set<number>();
  for (const d of docs) {
    for (const n of d.pages) {
      if (seen.has(n)) return false;
      seen.add(n);
    }
  }
  return seen.size === total;
}

/** A name the user typed, as the file will be called (".pdf" added). */
export function fileName(raw: string): string {
  const stem = raw.trim().replace(/\.pdf$/i, "").replace(/[<>:"/\\|?*\u0000-\u001f]/g, " ").replace(/\s+/g, " ").trim();
  return stem ? `${stem}.pdf` : "";
}

/** A folder the picker returned (absolute), as a folder inside `root`
 *  ("a/b", "" for `root` itself), or null when it is outside it. */
export function folderInside(root: string, picked: string): string | null {
  const norm = (p: string) => p.replace(/[\\/]+/g, "/").replace(/\/+$/, "");
  const r = norm(root);
  const p = norm(picked);
  if (!r || !p) return null;
  const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();
  if (same(p, r)) return "";
  if (p.length > r.length && same(p.slice(0, r.length + 1), `${r}/`)) {
    const rel = p.slice(r.length + 1);
    return rel.split("/").some((s) => s === ".." || s === "") ? null : rel;
  }
  return null;
}

/** What Confirm sends: the documents as the user left them. */
export function applyBody(docs: StackDoc[]): { documents: { pages: number[]; name: string; folder: string; unsure: boolean }[] } {
  return { documents: docs.map((d) => ({ pages: d.pages, name: d.name, folder: d.folder, unsure: d.unsure })) };
}

/** The card's one line under its heading. */
export function stackSummary(card: StackCard): string {
  const n = card.documents.length;
  const bits = [`${n} document${n === 1 ? "" : "s"} from ${card.page_count} page${card.page_count === 1 ? "" : "s"}`];
  if (card.read_here) bits.push(`${card.read_here} read on this PC`);
  if (card.read_by_model) bits.push(`${card.read_by_model} read by ${card.read_by || "a model"}`);
  return bits.join(" · ");
}

/** "Read 4 more pages with Claude?" (D-31.2's words); "Read 20 of 60 more
 *  pages with Claude?" when one press sends only some of them. */
export function offerWords(card: StackCard): string {
  const n = card.unread.length;
  if (!card.offer || !n) return "";
  const per = Math.max(1, Math.min(card.offer.pages || n, n));
  return per < n
    ? `Read ${per} of ${n} more pages with ${card.offer.name}?`
    : `Read ${n} more page${n === 1 ? "" : "s"} with ${card.offer.name}?`;
}

/** The chat a card's press belongs to, read AT the press: a new chat's
 *  first reply is named only after its first save, and Local only can be
 *  switched on after the plan was made. */
export interface StackScope {
  threadId: string;
  localOnly: boolean;
}

/** What "Read N more pages" sends: the model the card named, and the chat's
 *  Local only setting (the daemon ORs it with the plan's and the saved
 *  chat's, so leaving it out never switches it off). */
export function readMoreBody(card: StackCard, scope?: StackScope | null): { provider: string; thread_id: string; local_only: boolean } {
  return {
    provider: card.offer?.provider ?? "",
    thread_id: scope?.threadId ?? "",
    local_only: scope?.localOnly === true,
  };
}

/** The user's moves and new names, when the documents differ from the
 *  plan's (a second plan after a read replaces them, and says so). */
export function editedSince(docs: StackDoc[], plan: StackDoc[]): boolean {
  if (docs.length !== plan.length) return true;
  return docs.some((d, i) => {
    const p = plan[i];
    return d.name !== p.name || d.folder !== p.folder || d.pages.join(",") !== p.pages.join(",");
  });
}
