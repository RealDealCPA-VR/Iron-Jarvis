"use client";

// v1.305.0 — the preferences in "What Jarvis knows about you", by status.
//
//   Kept             confirmed — read into every conversation; edit / forget
//   Suggested        proposed  — a correction the user repeated; keep / edit /
//                    not this, each with the user's own words as evidence
//   Never ask again  declined  — final until the user says otherwise ("Ask again")
//
// Plus ONE opt-in deeper look: "Look through my Claude Code and Codex
// sessions". Consent is PER PRESS and the sentence above the button says what
// is read before anything is — the user's typed messages only, on this PC.
// The button exists only when the history reader found those apps.
//
// Same voice as the card it lives in: 12.5px rows, the accent dot, zinc for
// everything quiet. Nothing here is a modal; every refusal is said on the row.
import { useState } from "react";
import { Loader2 } from "lucide-react";
import { del, patch, post } from "@/lib/api";
import {
  keptTextFrom,
  quotable,
  quoteMeta,
  scanAppsWord,
  scanResultLine,
  type PrefQuote,
  type PrefRow,
  type PreferencesView,
} from "@/lib/preferences";

/** The word for where a kept preference came from. */
export function originWord(row: Pick<PrefRow, "origin" | "source">): string {
  if (row.origin === "noticed") return "you kept a suggestion";
  if (row.origin === "said") return "you said so";
  switch (row.source) {
    case "preference":
      return "you said so";
    case "feedback":
      return "from your feedback";
    case "distilled":
      return "learned over time";
    case "user":
      return "you wrote it";
    default:
      return row.source || "";
  }
}

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

// v1.316.0: a finger-sized target. These were ~30x14px links of 11px zinc-500
// — on a phone the one control that makes Jarvis forget something was hard to
// see and harder to hit. `py-1` gives a 24px hit area; `-my-1` keeps it from
// pushing the sentence's line apart (the target overlaps the gap instead).
const ACTION =
  "-my-1 rounded px-1.5 py-1 text-xs text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-accent-soft disabled:opacity-40";
const LABEL = "text-[10.5px] font-semibold uppercase tracking-[0.12em] text-zinc-500";

function Quotes({ quotes }: { quotes: PrefQuote[] }) {
  if (!quotes.length) return null;
  return (
    <div className="mt-0.5 space-y-0.5">
      {quotes.map((q, i) => {
        const meta = quoteMeta(q);
        return (
          <p key={i} data-testid="prefs-quote" className="text-[11.5px] leading-relaxed text-zinc-500">
            <span className="text-zinc-400">&ldquo;{q.quote}&rdquo;</span>
            {meta && <> — {meta}</>}
          </p>
        );
      })}
    </div>
  );
}

/** An inline sentence field: Enter saves, Escape puts the row back. */
function EditField({
  initial,
  busy,
  label,
  onSave,
  onCancel,
}: {
  initial: string;
  busy: boolean;
  label: string;
  onSave: (text: string) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(initial);
  return (
    <span className="inline-flex w-full min-w-0 flex-wrap items-center gap-1">
      <input
        autoFocus
        data-testid="prefs-edit-input"
        aria-label={label}
        value={draft}
        maxLength={280}
        disabled={busy}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            if (draft.trim()) onSave(draft.trim());
          } else if (e.key === "Escape") {
            e.preventDefault();
            onCancel();
          }
        }}
        className="min-w-[14rem] flex-1 rounded border border-white/[0.08] bg-white/[0.03] px-1.5 py-0.5 text-[12.5px] text-zinc-200 outline-none focus:border-accent/40"
      />
      <button type="button" disabled={busy || !draft.trim()} onClick={() => onSave(draft.trim())} className={ACTION}>
        {busy && <Loader2 size={10} className="mr-0.5 inline animate-spin" />}
        Save
      </button>
      <button type="button" disabled={busy} onClick={onCancel} className={ACTION}>
        Cancel
      </button>
    </span>
  );
}

export function PreferenceSections({
  view,
  onChanged,
  apply,
}: {
  view: PreferencesView;
  /** Re-read `/memory/preferences` after a change landed. */
  onChanged: () => void;
  /** Apply a local change at once (the re-read confirms it). */
  apply: (fn: (v: PreferencesView) => PreferencesView) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [scanning, setScanning] = useState(false);
  const [scanLine, setScanLine] = useState<string | null>(null);
  const [scanErr, setScanErr] = useState<string | null>(null);

  async function act(id: string, run: () => Promise<unknown>, local: (v: PreferencesView, res: unknown) => PreferencesView) {
    if (busyId) return;
    setBusyId(id);
    setErrors((e) => {
      if (!(id in e)) return e;
      const next = { ...e };
      delete next[id];
      return next;
    });
    try {
      const res = await run();
      apply((v) => local(v, res));
      setEditing(null);
      onChanged();
    } catch (e) {
      setErrors((prev) => ({ ...prev, [id]: errText(e) }));
    } finally {
      setBusyId(null);
    }
  }

  const without = (rows: PrefRow[], id: string) => rows.filter((r) => r.id !== id);

  function keep(row: PrefRow, text?: string) {
    const edited = text !== undefined && text !== row.text ? text : undefined;
    void act(
      row.id,
      () => post(`/memory/preferences/${encodeURIComponent(row.id)}/keep`, edited ? { text: edited } : {}),
      (v, res) => ({
        ...v,
        suggested: without(v.suggested, row.id),
        kept: [
          ...without(v.kept, row.id),
          { ...row, status: "confirmed", text: keptTextFrom(res, edited ?? row.text) },
        ],
      }),
    );
  }
  function decline(row: PrefRow) {
    void act(
      row.id,
      () => post(`/memory/preferences/${encodeURIComponent(row.id)}/decline`, {}),
      (v) => ({
        ...v,
        suggested: without(v.suggested, row.id),
        never: [...without(v.never, row.id), { ...row, status: "declined" }],
      }),
    );
  }
  function askAgain(row: PrefRow) {
    void act(
      row.id,
      () => post(`/memory/preferences/${encodeURIComponent(row.id)}/ask-again`, {}),
      (v) => ({ ...v, never: without(v.never, row.id) }),
    );
  }
  function editKept(row: PrefRow, text: string) {
    if (text === row.text) {
      setEditing(null);
      return;
    }
    void act(
      row.id,
      () => patch(`/memory/preferences/${encodeURIComponent(row.id)}`, { text }),
      (v, res) => ({
        ...v,
        kept: v.kept.map((r) => (r.id === row.id ? { ...r, text: keptTextFrom(res, text) } : r)),
      }),
    );
  }
  function forget(row: PrefRow) {
    void act(
      row.id,
      () => del(`/memory/preferences/${encodeURIComponent(row.id)}`),
      (v) => ({ ...v, kept: without(v.kept, row.id) }),
    );
  }

  async function scan() {
    if (scanning) return;
    setScanning(true);
    setScanErr(null);
    setScanLine(null);
    try {
      const res = await post<unknown>("/memory/preferences/scan", { sources: view.scanSources });
      setScanLine(scanResultLine(res) ?? "Done.");
      onChanged();
    } catch (e) {
      setScanErr(errText(e));
    } finally {
      setScanning(false);
    }
  }

  const rowErr = (id: string) =>
    errors[id] ? (
      <p data-testid="prefs-error" className="mt-0.5 text-[11px] text-rose-300/90">
        {errors[id]}
      </p>
    ) : null;

  const apps = scanAppsWord(view.scanSources);

  return (
    <div className="space-y-3">
      {/* KEPT — read into every conversation. */}
      <div id="prefs-kept" data-testid="prefs-kept" className="space-y-1">
        {view.kept.length > 0 ? (
          <ul data-testid="knows-preferences" className="space-y-1">
            {view.kept.map((p) => {
              const busy = busyId === p.id;
              return (
                <li key={p.id} className="group/pref flex items-start gap-2 text-[12.5px] text-zinc-300">
                  <span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-accent/70" />
                  <div className="min-w-0 flex-1">
                    {editing === p.id ? (
                      <EditField
                        initial={p.text}
                        busy={busy}
                        label="Edit this preference"
                        onSave={(t) => editKept(p, t)}
                        onCancel={() => setEditing(null)}
                      />
                    ) : (
                      <span className="min-w-0">
                        {p.text}
                        {originWord(p) && (
                          <span className="ml-1.5 text-[10.5px] uppercase tracking-wide text-zinc-600">
                            {originWord(p)}
                          </span>
                        )}
                        <span className="ml-1.5 inline-flex items-center gap-0.5 opacity-60 transition-opacity group-hover/pref:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100">
                          <button
                            type="button"
                            data-testid="prefs-edit"
                            disabled={busy}
                            onClick={() => setEditing(p.id)}
                            className={ACTION}
                          >
                            Edit
                          </button>
                          <button
                            type="button"
                            data-testid="prefs-forget"
                            disabled={busy}
                            onClick={() => forget(p)}
                            title="Stop reading this into conversations"
                            className={ACTION}
                          >
                            {busy && <Loader2 size={10} className="mr-0.5 inline animate-spin" />}
                            Forget
                          </button>
                        </span>
                      </span>
                    )}
                    {rowErr(p.id)}
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <p data-testid="knows-onramp" className="text-[12.5px] leading-relaxed text-zinc-400">
            Jarvis has not learned a preference yet. Say it in chat the way you would to a
            colleague — <span className="text-zinc-300">&ldquo;From now on, keep answers short&rdquo;</span>,{" "}
            <span className="text-zinc-300">&ldquo;Always give me numbered steps&rdquo;</span> — and it is kept
            here and used in every conversation.
          </p>
        )}
      </div>

      {/* SUGGESTED — a correction the user repeated, waiting on a yes or no. */}
      {view.suggested.length > 0 && (
        <div id="prefs-suggested" data-testid="prefs-suggested" className="space-y-1.5">
          <p className={LABEL}>Suggested — you asked for these more than once</p>
          <ul className="space-y-2">
            {view.suggested.map((p) => {
              const busy = busyId === p.id;
              return (
                <li key={p.id} className="flex items-start gap-2 text-[12.5px] text-zinc-300">
                  <span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full border border-accent/60" />
                  <div className="min-w-0 flex-1">
                    {editing === p.id ? (
                      <EditField
                        initial={p.text}
                        busy={busy}
                        label="Your standing preference, in your words"
                        onSave={(t) => keep(p, t)}
                        onCancel={() => setEditing(null)}
                      />
                    ) : (
                      <span className="min-w-0">
                        {p.text}
                        <span className="ml-1.5 inline-flex items-center gap-0.5">
                          <button
                            type="button"
                            data-testid="prefs-keep"
                            disabled={busy}
                            onClick={() => keep(p)}
                            className={ACTION}
                          >
                            {busy && <Loader2 size={10} className="mr-0.5 inline animate-spin" />}
                            Keep
                          </button>
                          <span aria-hidden="true" className="text-zinc-600">·</span>
                          <button
                            type="button"
                            data-testid="prefs-edit"
                            disabled={busy}
                            onClick={() => setEditing(p.id)}
                            className={ACTION}
                          >
                            Edit
                          </button>
                          <span aria-hidden="true" className="text-zinc-600">·</span>
                          <button
                            type="button"
                            data-testid="prefs-decline"
                            disabled={busy}
                            onClick={() => decline(p)}
                            title="Never suggest this again"
                            className={ACTION}
                          >
                            Not this
                          </button>
                        </span>
                      </span>
                    )}
                    <Quotes quotes={p.evidence} />
                    {rowErr(p.id)}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* NEVER ASK AGAIN — declined is final until the user says otherwise. */}
      {view.never.length > 0 && (
        <div id="prefs-never" data-testid="prefs-never" className="space-y-1">
          <p className={LABEL}>Never ask again</p>
          <ul className="space-y-1">
            {view.never.map((p) => (
              <li key={p.id} className="flex items-start gap-2 text-[12.5px] text-zinc-500">
                <span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-zinc-700" />
                <div className="min-w-0 flex-1">
                  <span className="min-w-0">
                    &ldquo;{quotable(p.text)}&rdquo;
                    <button
                      type="button"
                      data-testid="prefs-ask-again"
                      disabled={busyId === p.id}
                      onClick={() => askAgain(p)}
                      title="Let Jarvis suggest this again if you ask for it again"
                      className={`ml-1.5 ${ACTION}`}
                    >
                      Ask again
                    </button>
                  </span>
                  {rowErr(p.id)}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* THE DEEPER LOOK — consent per press, only when those apps are here. */}
      {view.scanSources.length > 0 && (
        <div id="prefs-scan" data-testid="prefs-scan" className="space-y-1 border-t border-white/[0.05] pt-2.5">
          <p className="text-[11.5px] leading-relaxed text-zinc-500">
            Reads only the messages you typed in your newest {apps} sessions — never the replies or
            tool output — and suggests anything you asked for more than once. It stays on this PC.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              data-testid="prefs-scan-button"
              disabled={scanning}
              onClick={() => void scan()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/[0.08] bg-white/[0.02] px-2.5 py-1 text-[12px] text-zinc-300 transition-colors hover:border-accent/40 hover:text-accent-soft disabled:opacity-50"
            >
              {scanning && <Loader2 size={11} className="animate-spin" />}
              {scanning ? "Reading…" : `Look through my ${apps} sessions`}
            </button>
            {scanLine && (
              <span data-testid="prefs-scan-result" className="text-[11.5px] text-zinc-400">
                {scanLine}
              </span>
            )}
          </div>
          {scanErr && (
            <p data-testid="prefs-scan-error" className="text-[11px] text-rose-300/90">
              {scanErr}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
