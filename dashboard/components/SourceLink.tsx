"use client";

import { useState, type ReactNode } from "react";
import { get } from "@/lib/api";
import {
  atOf,
  FILE_CHANGED,
  FILE_GONE,
  NO_RECORD_WORDS,
  openPlace,
  PLACE_FAILED,
  placeWords,
  readFromWords,
  SHOW_PLACE_TITLE,
  type SourceHref,
  type SourceMark,
} from "@/lib/sourceLink";

/**
 * A figure in a reply, drawn as a press that shows where it was read
 * (v1.547.0, idea 30-a, lib/sourceLink.ts). It looks like a link because it
 * reads as one; it is a button because it opens no page.
 *
 * - A figure with a record: GET /sources/<ids>, then the file opens beside
 *   the chat at that page, line or cell (over the chat on a phone). Several
 *   places: a short chooser, one quiet button per place. Where nothing opens
 *   the file (a page without the preview), the place is said in words.
 * - A figure with no record (D-30.1): a faint dotted line; its press says
 *   so. Nothing is fetched.
 *
 * The status line is always mounted so a screen reader hears the words.
 */
export function SourceLink({ target, children }: { target: SourceHref; children?: ReactNode }) {
  const [busy, setBusy] = useState(false);
  const [words, setWords] = useState("");
  const [choices, setChoices] = useState<SourceMark[]>([]);

  function show(mark: SourceMark) {
    const at = atOf(mark);
    const note = !mark.present ? FILE_GONE : mark.changed ? FILE_CHANGED : "";
    const opened = mark.present && openPlace(mark.path, at);
    setChoices([]);
    if (opened) {
      setWords(note);
      return;
    }
    const where = readFromWords(at, mark.name);
    setWords([where, mark.context ? `“${mark.context}”` : "", note].filter(Boolean).join(" "));
  }

  async function press() {
    if (target.none) {
      setWords((w) => (w ? "" : NO_RECORD_WORDS));
      return;
    }
    if (busy) return;
    setBusy(true);
    setWords("");
    try {
      const res = await get<{ marks: SourceMark[] }>(`/sources/${target.ids.join(",")}`);
      const marks = res.marks ?? [];
      if (marks.length === 1) show(marks[0]);
      else if (marks.length > 1) setChoices(marks);
      else setWords(NO_RECORD_WORDS);
    } catch (e) {
      setWords(e instanceof Error && e.message ? e.message : PLACE_FAILED);
    } finally {
      setBusy(false);
    }
  }

  if (target.none) {
    return (
      <>
        <button
          type="button"
          onClick={press}
          data-testid="source-link-none"
          aria-expanded={words ? true : false}
          className="inline text-inherit underline decoration-zinc-500/60 decoration-dotted underline-offset-2 hover:decoration-zinc-300"
        >
          {children}
        </button>
        <span role="status" data-testid="source-link-note" className={words ? "ml-1 text-xs text-zinc-400" : "sr-only"}>
          {words}
        </span>
      </>
    );
  }

  return (
    <>
      <button
        type="button"
        onClick={() => void press()}
        title={SHOW_PLACE_TITLE}
        data-testid="source-link"
        aria-busy={busy}
        className="inline text-accent-soft underline decoration-accent/40 underline-offset-2 transition-colors hover:decoration-accent"
      >
        {children}
      </button>
      {choices.length > 1 && (
        <span data-testid="source-link-choices" className="ml-1 inline-flex flex-wrap gap-x-2 text-xs text-zinc-400">
          <span>In {choices.length} places:</span>
          {choices.map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() => show(m)}
              className="text-accent-soft underline decoration-accent/30 underline-offset-2 hover:decoration-accent"
            >
              {m.name}, {placeWords(atOf(m))}
            </button>
          ))}
        </span>
      )}
      <span role="status" data-testid="source-link-note" className={words ? "ml-1 text-xs text-zinc-400" : "sr-only"}>
        {words}
      </span>
    </>
  );
}
