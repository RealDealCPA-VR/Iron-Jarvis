"use client";

/**
 * v1.453.0: the summary Iron Jarvis wrote when a Build › Code session was
 * archived, shown at the top of that session (open from "Archived sessions"),
 * with Copy, Open file and Bring back. After the session is brought back the
 * summary stays, folded under one press.
 */

import { useEffect, useState } from "react";
import { Archive, ChevronDown, ChevronRight } from "lucide-react";
import { ApiError, get, post } from "@/lib/api";
import { Markdown } from "@/components/Markdown";
import { copyPlain } from "@/components/settings/MaintenanceTools";
import {
  ARCHIVED_NOTE,
  BRING_BACK,
  SUMMARY_COPIED,
  SUMMARY_COPY,
  SUMMARY_HEADING,
  SUMMARY_HEADING_TITLE,
  SUMMARY_OPEN,
  SUMMARY_READING,
  SUMMARY_SHOW,
  SUMMARY_UNREADABLE,
  decodeArchiveSummary,
  type ArchiveSummary,
  type ArchiveSummaryFacts,
} from "@/lib/buildArchive";

const SESSIONS = "/build/app/sessions";

function words(e: unknown, fallback: string): string {
  if (e instanceof ApiError && e.message) return e.message;
  return fallback;
}

export function ArchiveSummaryCard({
  sessionId,
  facts,
  archived,
  onBringBack,
}: {
  sessionId: string;
  facts: ArchiveSummaryFacts | null;
  archived: boolean;
  onBringBack?: () => void;
}) {
  const [open, setOpen] = useState(archived);
  const [summary, setSummary] = useState<ArchiveSummary | null>(null);
  const [state, setState] = useState<"idle" | "reading" | "error">("idle");
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => setOpen(archived), [archived, sessionId]);

  // Read once it is shown (and again when a newer archive wrote it).
  const at = facts?.at ?? "";
  const has = !!facts;
  useEffect(() => {
    if (!has || !open) return;
    let alive = true;
    setState("reading");
    get<unknown>(`${SESSIONS}/${encodeURIComponent(sessionId)}/summary`)
      .then((raw) => {
        if (!alive) return;
        const got = decodeArchiveSummary(raw);
        setSummary(got);
        setState(got ? "idle" : "error");
      })
      .catch(() => {
        if (alive) setState("error");
      });
    return () => {
      alive = false;
    };
  }, [has, open, sessionId, at]);

  if (!facts && !archived) return null;

  const bringBack = onBringBack ? (
    <button
      type="button"
      data-testid="code-archive-bring-back"
      onClick={onBringBack}
      className="rounded-lg px-2 py-0.5 text-[12px] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100"
    >
      {BRING_BACK}
    </button>
  ) : null;

  if (!facts) {
    return (
      <div data-testid="code-archived-note" className="mb-4 flex items-center gap-2 text-[12px] text-zinc-500">
        <Archive size={12} aria-hidden="true" />
        <span className="flex-1">{ARCHIVED_NOTE}</span>
        {bringBack}
      </div>
    );
  }

  const copy = async () => {
    if (!summary) return;
    const r = await copyPlain(summary.text);
    setNote(r === "copied" ? SUMMARY_COPIED : "Copying is not available here.");
  };
  const openFile = async () => {
    setNote(null);
    try {
      await post(`${SESSIONS}/${encodeURIComponent(sessionId)}/summary/open`);
    } catch (e) {
      setNote(words(e, "The summary file could not be opened."));
    }
  };

  return (
    <section data-testid="code-archive-summary" className="mb-5 rounded-xl bg-white/[0.03] px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          aria-expanded={open}
          data-testid="code-archive-summary-toggle"
          title={SUMMARY_HEADING_TITLE}
          onClick={() => setOpen((v) => !v)}
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left text-[13px] text-zinc-300"
        >
          {open ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
          <Archive size={12} aria-hidden="true" />
          <span className="truncate">{archived || open ? SUMMARY_HEADING : SUMMARY_SHOW}</span>
        </button>
        {open && summary ? (
          <>
            <button
              type="button"
              data-testid="code-archive-summary-copy"
              onClick={() => void copy()}
              className="rounded-lg px-2 py-0.5 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100"
            >
              {SUMMARY_COPY}
            </button>
            <button
              type="button"
              data-testid="code-archive-summary-open"
              title={summary.path}
              onClick={() => void openFile()}
              className="rounded-lg px-2 py-0.5 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100"
            >
              {SUMMARY_OPEN}
            </button>
          </>
        ) : null}
        {archived ? bringBack : null}
      </div>
      {note ? (
        <p data-testid="code-archive-summary-note" role="status" className="mt-1 text-[12px] text-zinc-500">
          {note}
        </p>
      ) : null}
      {open ? (
        state === "reading" && !summary ? (
          <p className="mt-2 text-[12px] text-zinc-500">{SUMMARY_READING}</p>
        ) : state === "error" ? (
          <p data-testid="code-archive-summary-error" role="alert" className="mt-2 text-[12px] text-tone-danger">
            {SUMMARY_UNREADABLE}
          </p>
        ) : summary ? (
          <div data-testid="code-archive-summary-text" className="mt-2 text-[13px] leading-relaxed text-zinc-300">
            <Markdown content={summary.text} />
          </div>
        ) : null
      ) : null}
    </section>
  );
}
