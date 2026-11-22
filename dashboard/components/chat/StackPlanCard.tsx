"use client";

/**
 * "Sort this stack" (idea 31, phase 31-a): the plan card under the reply.
 *
 *   Sort scan.pdf                    8 documents from 60 pages · 4 read on this PC
 *   ┌ 2026-03 Bank statement.pdf ┐  Folder: Bank statements  [Choose…]
 *   │ [1][2][3][4][5][6][7][8]    │  (drag a page to another document, or press
 *   └─────────────────────────────┘   it, then "Move page 7 here")
 *   ⚠ Not sure (pages 41-42).pdf     the pages the plan could not place
 *   Read 2 more pages with Claude?   (only when this PC could not read some)
 *   Nothing is written until you confirm. The scan stays as it is.   [Confirm]
 *
 * One main button (Confirm). Confirm posts the plan as the user left it to
 * POST /documents/stack/{id}/apply, which writes it through the tool
 * registry; the card then lists the files, each opens beside the chat, and
 * one Undo (POST /documents/stack/{id}/undo, the ordinary Undo of that call)
 * removes them all. Nothing here keeps a page safe on its own: the daemon
 * checks every page is in exactly one document before anything is written.
 * At 390 px every row stacks and the page strip wraps; nothing pans sideways.
 */

import { useState, type DragEvent } from "react";
import { Check, FolderOpen, Layers, RotateCcw, TriangleAlert } from "lucide-react";
import { get, post } from "@/lib/api";
import { Button } from "@/components/ui";
import { FilePickerModal } from "@/components/FilePickerModal";
import { openPlace } from "@/lib/sourceLink";
import {
  applyBody,
  decodeStackCard,
  editedSince,
  everyPageOnce,
  fileName,
  folderInside,
  movePage,
  offerWords,
  readMoreBody,
  spanWords,
  stackSummary,
  undoNote,
  type StackCard,
  type StackDoc,
  type StackScope,
} from "@/lib/stackPlan";

const DRAG_TYPE = "application/x-ij-stack-page";

function errorWords(err: unknown, fallback: string): string {
  const msg = (err as { message?: string })?.message;
  return typeof msg === "string" && msg.trim() ? msg : fallback;
}

function PageChip({
  page,
  blank,
  snippet,
  selected,
  onPress,
}: {
  page: number;
  blank: boolean;
  snippet: string;
  selected: boolean;
  onPress: () => void;
}) {
  return (
    <button
      type="button"
      draggable
      data-testid={`stack-page-${page}`}
      aria-pressed={selected}
      title={blank ? "Blank page, kept with this document" : snippet || `Page ${page}`}
      onDragStart={(e) => {
        e.dataTransfer.setData(DRAG_TYPE, String(page));
        e.dataTransfer.setData("text/plain", String(page));
        e.dataTransfer.effectAllowed = "move";
      }}
      onClick={onPress}
      className={`h-7 min-w-[2rem] rounded-md border px-1.5 text-[12px] tabular-nums transition-colors ${
        selected
          ? "border-accent bg-accent/[0.15] text-zinc-100"
          : blank
          ? "border-dashed border-white/15 text-zinc-500 hover:border-white/30"
          : "border-white/10 bg-white/[0.03] text-zinc-300 hover:border-white/25"
      }`}
    >
      {page}
    </button>
  );
}

export function StackPlanCard({
  card,
  onSettle,
  scope,
}: {
  card: StackCard;
  onSettle: (next: StackCard) => void;
  /** The chat this card is in, read at the press (its id and Local only). */
  scope?: () => StackScope;
}) {
  const [docs, setDocs] = useState<StackDoc[]>(card.documents);
  const [picked, setPicked] = useState<number | null>(null);
  const [pickerFor, setPickerFor] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  const pageOwner = (n: number) => docs.findIndex((d) => d.pages.includes(n));

  function moveTo(to: number, page = picked) {
    if (page == null) return;
    setDocs((cur) => movePage(cur, page, to));
    setPicked(null);
  }

  function onDrop(e: DragEvent<HTMLDivElement>, to: number) {
    e.preventDefault();
    const page = Number(e.dataTransfer.getData(DRAG_TYPE) || e.dataTransfer.getData("text/plain"));
    if (Number.isInteger(page) && page >= 1) moveTo(to, page);
  }

  function setDoc(i: number, patch: Partial<StackDoc>) {
    setDocs((cur) => cur.map((d, k) => (k === i ? { ...d, ...patch } : d)));
  }

  // confirmPlan, never a bare `confirm`: a call of `confirm()` reads as the
  // browser's own prompt, and settings-panel-words G68 pins that none exists.
  async function confirmPlan() {
    if (!everyPageOnce(docs, card.page_count)) {
      setError("Every page goes in one document. Move the pages back first.");
      return;
    }
    if (docs.some((d) => !fileName(d.name))) {
      setError("Every document needs a name.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const res = await post<{ files?: StackCard["written"]; card?: unknown }>(
        `/documents/stack/${encodeURIComponent(card.plan_id)}/apply`,
        applyBody(docs.map((d) => ({ ...d, name: fileName(d.name) }))),
        { timeoutMs: 180_000 },
      );
      const fresh = decodeStackCard(res.card) ?? card;
      onSettle({ ...fresh, written: res.files ?? [], undone: false });
    } catch (err) {
      // The wait may have run out (or the answer been lost) while the daemon
      // wrote the documents: the card as it is now says what really happened,
      // never "Nothing was changed" over files that are there.
      let fresh: StackCard | null = null;
      try {
        const now = await get<{ card?: unknown }>(`/documents/stack/${encodeURIComponent(card.plan_id)}`);
        fresh = decodeStackCard(now.card);
      } catch {
        fresh = null;
      }
      const refused = typeof (err as { status?: unknown })?.status === "number" && (err as { status: number }).status >= 400;
      if (fresh?.applied) {
        onSettle({ ...fresh, written: [], undone: false });
      } else if (fresh && refused) {
        setError(errorWords(err, "The documents were not written. Nothing was changed."));
      } else {
        setError("No answer came back, so the documents may still be being written. Open this chat again to see the plan as it is now.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function undo() {
    setBusy(true);
    setError("");
    try {
      const res = await post<{ output?: string; card?: unknown }>(
        `/documents/stack/${encodeURIComponent(card.plan_id)}/undo`,
        {},
      );
      const fresh = decodeStackCard(res.card) ?? card;
      const said = undoNote(res.output);
      onSettle({ ...fresh, written: card.written, undone: true, ...(said ? { undo_note: said } : {}) });
    } catch (err) {
      setError(errorWords(err, "Couldn't undo that. Try again, or use Activity."));
    } finally {
      setBusy(false);
    }
  }

  /** A second plan replaces the documents on the card: said, when the user
   *  had moved pages or renamed since the plan was shown. */
  function takePlan(fresh: StackCard, said: string[]) {
    if (editedSince(fresh.documents, card.documents)) {
      if (editedSince(docs, card.documents)) {
        said.push("The plan was made again with those pages, so your moves and new names were replaced.");
      }
      setDocs(fresh.documents);
    }
    onSettle(fresh);
  }

  async function readMore() {
    if (!card.offer) return;
    const wereUnread = card.unread.length;
    const sc = scope?.() ?? null;
    setBusy(true);
    setError("");
    setNote("");
    try {
      const res = await post<{ read?: number; changed?: boolean; stopped?: string; card?: unknown }>(
        `/documents/stack/${encodeURIComponent(card.plan_id)}/read-more`,
        readMoreBody(card, sc),
        { timeoutMs: 600_000 },
      );
      const fresh = decodeStackCard(res.card);
      const said: string[] = [];
      if (res.changed && fresh?.offer) {
        said.push(`Nothing was sent. The model that reads pictures is now ${fresh.offer.name}.`);
      }
      if (typeof res.stopped === "string" && res.stopped.trim()) said.push(res.stopped.trim());
      if (fresh) takePlan(fresh, said);
      setNote(said.join(" "));
    } catch (err) {
      // The daemon may have read (and kept) some pages before this failed or
      // the wait ran out: the card as it is now says what really happened.
      let fresh: StackCard | null = null;
      try {
        const q = sc ? `?thread_id=${encodeURIComponent(sc.threadId)}&local_only=${sc.localOnly ? "true" : "false"}` : "";
        const now = await get<{ card?: unknown }>(`/documents/stack/${encodeURIComponent(card.plan_id)}${q}`);
        fresh = decodeStackCard(now.card);
      } catch {
        fresh = null;
      }
      const read = fresh ? wereUnread - fresh.unread.length : 0;
      if (fresh && read > 0) {
        const said = [`${read === 1 ? "1 page was" : `${read} pages were`} read and kept before this stopped.`];
        takePlan(fresh, said);
        setNote(said.join(" "));
        setError(errorWords(err, "The rest were not read."));
      } else if (fresh) {
        setError(errorWords(err, "Those pages were not read. Nothing was changed on the plan."));
      } else {
        setError(errorWords(err, "Those pages may not have been read. Open this chat again to see the plan as it is now."));
      }
    } finally {
      setBusy(false);
    }
  }

  if (card.applied || card.undone) {
    const files = card.written ?? [];
    if (card.undone) {
      return (
        <div data-testid="stack-card-undone" className="min-w-0 text-[12px] text-zinc-500 [overflow-wrap:anywhere]">
          <RotateCcw size={11} className="mr-1 inline align-[-1px]" aria-hidden />
          {card.undo_note
            ? `Undone — ${card.undo_note}`
            : `Undone — ${files.length === 1 ? "the document was" : files.length ? `the ${files.length} documents were` : "the documents were"} removed. ${card.source_name} is as it was.`}
        </div>
      );
    }
    return (
      <div
        data-testid="stack-card-written"
        className="w-full max-w-2xl space-y-2 rounded-xl border border-white/[0.08] bg-white/[0.02] px-3 py-2 text-[13px]"
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="inline-flex min-w-0 items-center gap-1.5 font-medium text-tone-success [overflow-wrap:anywhere]">
            <Check size={13} className="shrink-0" aria-hidden />{" "}
            {files.length === 1 ? "Wrote 1 document" : files.length ? `Wrote ${files.length} documents` : "Wrote the documents"}{" "}
            from {card.source_name}
          </span>
          <button
            type="button"
            onClick={() => void undo()}
            disabled={busy}
            className="ml-auto rounded-lg border border-white/10 px-2.5 py-1 text-[12px] text-zinc-200 hover:border-white/20 disabled:opacity-50"
          >
            Undo
          </button>
        </div>
        {files.length > 0 && (
          <ul className="space-y-0.5">
            {files.map((f) => (
              <li key={f.path} className="min-w-0">
                <button
                  type="button"
                  onClick={() => openPlace(f.path, {})}
                  title={f.path}
                  className="max-w-full truncate text-left text-[12px] text-accent-soft hover:underline"
                >
                  {f.folder ? `${f.folder}/` : ""}
                  {f.name}
                </button>
                <span className="ml-1.5 text-[11px] text-zinc-500">
                  {f.pages} page{f.pages === 1 ? "" : "s"}
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="text-[11px] text-zinc-500 [overflow-wrap:anywhere]">{card.source_name} is unchanged, where it was.</p>
        {error && (
          <p role="alert" className="text-[12px] text-tone-danger">
            {error}
          </p>
        )}
      </div>
    );
  }

  const offer = offerWords(card);
  const unsure = docs.filter((d) => d.unsure);
  return (
    <div
      data-testid="stack-card"
      className="w-full max-w-2xl space-y-2.5 rounded-xl border border-white/[0.1] bg-white/[0.02] p-3 text-[13px]"
    >
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
        {/* A scanner's long name with no spaces wraps; it never widens the card. */}
        <span className="inline-flex min-w-0 max-w-full items-center gap-1.5 font-semibold text-zinc-100">
          <Layers size={14} className="shrink-0 text-accent-soft" aria-hidden />
          <span className="min-w-0 [overflow-wrap:anywhere]">Sort {card.source_name}</span>
        </span>
        <span className="text-[12px] text-zinc-500">{stackSummary({ ...card, documents: docs })}</span>
      </div>
      <ol className="space-y-2">
        {docs.map((d, i) => (
          <li key={`${d.pages[0]}-${i}`}>
            <div
              data-testid={`stack-doc-${i}`}
              onDragOver={(e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = "move";
              }}
              onDrop={(e) => onDrop(e, i)}
              className={`space-y-1.5 rounded-lg border p-2 ${
                d.unsure ? "border-tone-warn/40 bg-tone-warn/[0.04]" : "border-white/[0.07]"
              }`}
            >
              <div className="flex flex-wrap items-center gap-1.5">
                {d.unsure && <TriangleAlert size={12} className="text-tone-warn" aria-label="Not sure" />}
                <label className="sr-only" htmlFor={`stack-name-${card.plan_id}-${i}`}>
                  Name of the document with {spanWords(d.pages)}
                </label>
                <input
                  id={`stack-name-${card.plan_id}-${i}`}
                  value={d.name}
                  onChange={(e) => setDoc(i, { name: e.target.value })}
                  onBlur={(e) => setDoc(i, { name: fileName(e.target.value) || d.name })}
                  onKeyDown={(e) => e.stopPropagation()}
                  spellCheck={false}
                  className="field min-w-0 flex-1 basis-56 text-[13px]"
                />
                <span className="text-[11px] text-zinc-500">{spanWords(d.pages)}</span>
              </div>
              <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
                <span className="text-zinc-500">Folder</span>
                <label className="sr-only" htmlFor={`stack-folder-${card.plan_id}-${i}`}>
                  Folder of the document with {spanWords(d.pages)}
                </label>
                <input
                  id={`stack-folder-${card.plan_id}-${i}`}
                  value={d.folder}
                  placeholder="this chat's folder"
                  onChange={(e) => setDoc(i, { folder: e.target.value })}
                  onKeyDown={(e) => e.stopPropagation()}
                  spellCheck={false}
                  className="field min-w-0 flex-1 basis-40 text-[12px]"
                />
                {card.root && (
                  <button
                    type="button"
                    onClick={() => setPickerFor(i)}
                    className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-zinc-400 hover:text-zinc-100"
                  >
                    <FolderOpen size={12} aria-hidden /> Choose…
                  </button>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-1" aria-label={`Pages of ${d.name}`}>
                {d.pages.map((n) => (
                  <PageChip
                    key={n}
                    page={n}
                    blank={d.blank.includes(n)}
                    snippet={card.snippets[String(n)] ?? ""}
                    selected={picked === n}
                    onPress={() => setPicked((cur) => (cur === n ? null : n))}
                  />
                ))}
                {picked != null && pageOwner(picked) !== i && (
                  <button
                    type="button"
                    data-testid={`stack-move-here-${i}`}
                    onClick={() => moveTo(i)}
                    className="h-7 rounded-md px-1.5 text-[12px] text-accent-soft hover:bg-accent/[0.08]"
                  >
                    Move page {picked} here
                  </button>
                )}
                {picked != null && pageOwner(picked) === i && card.source && (
                  <button
                    type="button"
                    onClick={() => openPlace(card.source, { page: picked })}
                    className="h-7 rounded-md px-1.5 text-[12px] text-zinc-400 hover:text-zinc-100"
                  >
                    See page {picked}
                  </button>
                )}
              </div>
            </div>
          </li>
        ))}
      </ol>
      {unsure.length > 0 && (
        <p className="text-[12px] text-zinc-400">
          {unsure.length === 1 ? "The pages in “Not sure”" : "The pages in each “Not sure”"} could not be placed with
          certainty. Move them, or keep them as their own file.
        </p>
      )}
      {card.unread.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-zinc-400" data-testid="stack-unread">
          <span>
            {card.unread.length === 1 ? "1 page" : `${card.unread.length} pages`} ({spanWords(card.unread)}) could
            not be read on this PC.
          </span>
          {offer ? (
            <button
              type="button"
              data-testid="stack-read-more"
              onClick={() => void readMore()}
              disabled={busy}
              title={`Sends ${card.unread.length === 1 ? "that page" : "those pages"} to ${card.offer?.name} to be read`}
              className="rounded-md px-1.5 py-0.5 text-accent-soft hover:bg-accent/[0.08] disabled:opacity-50"
            >
              {offer}
            </button>
          ) : (
            <span className="text-zinc-500">No connected model can read pictures.</span>
          )}
        </div>
      )}
      {note && (
        <p role="status" className="text-[12px] text-zinc-400">
          {note}
        </p>
      )}
      {error && (
        <p role="alert" className="text-[12px] text-tone-danger">
          {error}
        </p>
      )}
      <div className="flex flex-wrap items-center justify-end gap-x-3 gap-y-2">
        <span className="mr-auto min-w-0 text-[11px] text-zinc-500 [overflow-wrap:anywhere]">
          Nothing is written until you confirm. {card.source_name} stays as it is.
        </span>
        <Button variant="primary" size="sm" onClick={() => void confirmPlan()} disabled={busy} data-testid="stack-confirm">
          Confirm
        </Button>
      </div>
      <FilePickerModal
        open={pickerFor != null}
        pickFolders
        start={card.root}
        title="Choose a folder inside this chat's folder"
        onClose={() => setPickerFor(null)}
        onPick={(abs) => {
          const at = pickerFor;
          setPickerFor(null);
          if (at == null) return;
          const rel = folderInside(card.root, abs);
          if (rel == null) {
            setError(`Pick a folder inside ${card.root}.`);
            return;
          }
          setError("");
          setDoc(at, { folder: rel });
        }}
      />
    </div>
  );
}
