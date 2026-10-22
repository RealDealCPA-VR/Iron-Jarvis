"use client";

/**
 * The EXPERIENCE LIBRARY's suggestions for a Build session's folder (v1.370.0,
 * IJ harness plan Phase 7): one quiet card each, with Accept and Decline.
 *
 * A LESSON, once accepted, is read by every later session in that folder. An
 * EDIT is a change to one of the folder's harness files
 * (`.ironjarvis/harness/…`); nothing is written into the folder until it is
 * accepted, and the daemon refuses in words when the file changed since.
 *
 * The suggestion is made in the background ONCE the session has been idle a
 * while after replies that changed files or ran commands (one request to the
 * session's model, never one per reply), so the library is read when the
 * session opens, when a reply ends (and once shortly after,
 * LIBRARY_REREAD_MS), and then only when the daemon says a suggestion is due
 * or being made (`nextLibraryRead`) — never polled forever.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Lightbulb } from "lucide-react";
import { diffLines } from "@/lib/diff";
import {
  LIBRARY_REREAD_MS,
  acceptLibraryItem,
  declineLibraryItem,
  fetchSessionLibrary,
  forgetLesson,
  nextLibraryRead,
  type LibraryItem,
} from "@/lib/buildLibrary";
import { MemoMarkdown } from "@/components/Markdown";
import { ConfirmButton } from "@/components/ui";

export const ACCEPT = "Accept";
export const DECLINE = "Decline";

function EditDiff({ item }: { item: LibraryItem }) {
  const lines = diffLines(item.before ? item.before.split("\n") : [], item.after.split("\n"));
  return (
    <pre
      data-testid="library-edit-diff"
      className="max-h-48 overflow-auto rounded-lg bg-white/[0.03] px-3 py-2 text-[12px] leading-relaxed"
    >
      {lines
        .filter((l, i, all) => !(i === all.length - 1 && l.kind === "same" && !l.text))
        .map((l, i) => (
          <div
            key={i}
            className={
              l.kind === "added" ? "text-tone-success" : l.kind === "removed" ? "text-tone-danger line-through" : "text-zinc-500"
            }
          >
            {l.kind === "added" ? "+ " : l.kind === "removed" ? "- " : "  "}
            {l.text}
          </div>
        ))}
    </pre>
  );
}

function LibraryCard({
  item,
  folderName,
  onDecided,
}: {
  item: LibraryItem;
  folderName: string;
  onDecided: (id: string, note: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function decide(accept: boolean) {
    setBusy(true);
    setError(null);
    try {
      const refused = accept ? await acceptLibraryItem(item.id) : await declineLibraryItem(item.id);
      if (refused) {
        setError(refused);
        return;
      }
      onDecided(
        item.id,
        !accept
          ? ""
          : item.kind === "lesson"
            ? `Kept. Later sessions in ${folderName || "this folder"} will read it.`
            : `Written to ${item.path}.`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const lesson = item.kind === "lesson";
  return (
    <div
      data-testid={`library-card-${item.id}`}
      data-kind={item.kind}
      className="space-y-2 rounded-2xl bg-white/[0.04] px-4 py-3"
    >
      <p className="flex items-center gap-1.5 text-[12px] text-zinc-400">
        <Lightbulb size={12} aria-hidden className="shrink-0 text-accent-soft" />
        {lesson
          ? `A lesson for later sessions in ${folderName || "this folder"}`
          : `A change to ${item.path}${item.newFile ? " (a new file)" : ""}`}
      </p>
      {lesson ? (
        <div className="text-[13px] leading-relaxed text-zinc-100">
          <MemoMarkdown content={item.text} />
        </div>
      ) : (
        <>
          {item.text ? <p className="text-[13px] text-zinc-100">{item.text}</p> : null}
          <EditDiff item={item} />
        </>
      )}
      <p className="text-[12px] text-zinc-500">
        {lesson
          ? "Checked against what this session really did. Nothing is kept until you accept it."
          : "Nothing is written into the folder until you accept it."}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-testid="library-accept"
          disabled={busy}
          onClick={() => void decide(true)}
          className="btn-accent text-xs"
        >
          {ACCEPT}
        </button>
        <button
          type="button"
          data-testid="library-decline"
          disabled={busy}
          onClick={() => void decide(false)}
          className="rounded-md px-2 py-1 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 disabled:opacity-40"
        >
          {DECLINE}
        </button>
      </div>
      {error ? (
        <p role="alert" className="text-[12px] text-tone-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** The lessons KEPT for the folder, folded: each can be forgotten (two presses). */
function KeptLessons({ lessons, onForgotten }: { lessons: LibraryItem[]; onForgotten: () => void }) {
  const [error, setError] = useState<string | null>(null);
  return (
    <details data-testid="library-kept" className="rounded-2xl px-1 text-[12px] text-zinc-400">
      <summary className="cursor-pointer select-none py-1 hover:text-zinc-200">
        Lessons kept for this folder ({lessons.length})
      </summary>
      <ul className="space-y-1.5 pb-1 pt-1">
        {lessons.map((l) => (
          <li key={l.id} data-testid={`library-kept-${l.id}`} className="flex items-start gap-2">
            <div className="min-w-0 flex-1 text-zinc-300">
              <MemoMarkdown content={l.text} />
            </div>
            <ConfirmButton
              variant="calm"
              label="Forget"
              confirmLabel="Forget it?"
              title="Later sessions in this folder will no longer read this lesson"
              onConfirm={async () => {
                setError(null);
                try {
                  const refused = await forgetLesson(l.id);
                  if (refused) setError(refused);
                  else onForgotten();
                } catch (e) {
                  setError(e instanceof Error ? e.message : String(e));
                }
              }}
            />
          </li>
        ))}
      </ul>
      {error ? (
        <p role="alert" className="pb-1 text-tone-danger">
          {error}
        </p>
      ) : null}
    </details>
  );
}

export function LibraryCards({ threadId, refreshKey }: { threadId: string; refreshKey: number }) {
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [kept, setKept] = useState<LibraryItem[]>([]);
  const [folderName, setFolderName] = useState("");
  const [note, setNote] = useState<string | null>(null);
  /** When to read again (ms from the last read), per the daemon's answer. */
  const [next, setNext] = useState<{ inMs: number; at: number } | null>(null);
  const liveRef = useRef(true);

  const read = useCallback(async () => {
    try {
      const v = await fetchSessionLibrary(threadId);
      if (!liveRef.current) return;
      setItems(v ? v.proposals : []);
      setKept(v ? v.lessons : []);
      if (v) setFolderName(v.folderName);
      const inMs = nextLibraryRead(v);
      setNext(inMs === null ? null : { inMs, at: Date.now() });
    } catch {
      /* no card rather than a wrong one */
    }
  }, [threadId]);

  useEffect(() => {
    liveRef.current = true;
    setItems([]);
    setKept([]);
    setNote(null);
    setNext(null);
    return () => {
      liveRef.current = false;
    };
  }, [threadId]);

  // On open, when a reply ends (and once shortly after: the daemon starts
  // the session's idle wait a moment after the reply's last frame).
  useEffect(() => {
    void read();
    if (refreshKey === 0) return;
    const timers = LIBRARY_REREAD_MS.map((ms) => setTimeout(() => void read(), ms));
    return () => timers.forEach(clearTimeout);
  }, [read, refreshKey]);

  // Then only when the daemon said a suggestion is due (its idle wait ends)
  // or being made — one read each time, never a fixed poll.
  useEffect(() => {
    if (!next) return;
    const timer = setTimeout(() => void read(), next.inMs);
    return () => clearTimeout(timer);
  }, [next, read]);

  if (!items.length && !note && !kept.length) return null;
  return (
    <div data-testid="library-cards" className="space-y-2">
      {kept.length ? <KeptLessons lessons={kept} onForgotten={() => void read()} /> : null}
      {items.map((item) => (
        <LibraryCard
          key={item.id}
          item={item}
          folderName={folderName}
          onDecided={(id, words) => {
            setItems((prev) => prev.filter((p) => p.id !== id));
            setNote(words || null);
            void read();
          }}
        />
      ))}
      {note ? (
        <p data-testid="library-note" role="status" className="text-[12px] text-zinc-400">
          {note}
        </p>
      ) : null}
    </div>
  );
}
