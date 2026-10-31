"use client";

/**
 * THE CONTEXT DROP-DOWN (v1.415.0) — one component for the Session tab of
 * BOTH coding-session views (Chat › Code: HarnessSessionRail; Build › Code:
 * CodePanes). The user, beside a screenshot of Claude Code's own panel:
 * "72% used … Messages 337.7k … Memory files … Free space … 240k until
 * auto-compact … Compact session".
 *
 * It draws what it is handed (lib/contextBreakdown) and computes nothing but
 * each part's share of the window: the headline (percent, "of N tokens"),
 * a stacked bar whose every segment is also a labelled row with its tokens
 * and percent (colour is never the only cue), the parts sent beside the
 * conversation but not counted, the lists (instruction files, extension
 * tools, skills), the automatic summary's mark, and Compact. A reading with
 * no parts keeps the single bar it always had and its own words.
 *
 * A disclosure, open by default; closing it is remembered for this window
 * (sessionStorage). Calm: no box, tone tokens, whole-pixel sizes.
 */

import { useEffect, useId, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { get, put } from "@/lib/api";
import { formatTokens } from "@/lib/format";
import {
  AUTO_COMPACT_CHOICES,
  AUTO_COMPACT_HELP,
  COMPACTION_SETTING,
  autoCompactChoice,
  settingFor,
  partPercent,
  swatchClass,
  untilSummaryWords,
  usedOfWindow,
  type ContextList,
  type ContextReading,
} from "@/lib/contextBreakdown";

const OPEN_KEY = "ij.session.context.open";
const QUIET = "text-[12px] text-zinc-500";
const PRESS =
  "shrink-0 rounded-md px-1.5 py-0.5 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:opacity-50";

function readOpen(): boolean {
  try {
    return window.sessionStorage.getItem(OPEN_KEY) !== "0";
  } catch {
    return true;
  }
}

function writeOpen(open: boolean): void {
  try {
    window.sessionStorage.setItem(OPEN_KEY, open ? "1" : "0");
  } catch {
    /* this visit only */
  }
}

export interface CompactPress {
  onPress: () => void;
  /** Why it cannot be pressed now ("" = it can). */
  blocked: string;
  busy?: boolean;
}

export function ContextBreakdown({
  prefix,
  reading,
  warn,
  emptyWords,
  sourceLine,
  note,
  bars = true,
  autoCompactControl,
  compact,
  compactAbsent,
  children,
}: {
  /** Test-id prefix ("session-rail" | "code-session"). */
  prefix: string;
  reading: ContextReading | null;
  /** The warning tone (else: from 80 % up). */
  warn?: boolean;
  /** Said when there is no reading at all. */
  emptyWords: string;
  /** Where the numbers came from and when. */
  sourceLine?: string;
  /** e.g. "Pi says only how full the conversation is, not what fills it." */
  note?: string;
  bars?: boolean;
  /** The automatic summary's mark, as a choice (Chat › Code only). */
  autoCompactControl?: ReactNode;
  compact?: CompactPress | null;
  /** Why there is no Compact here (said instead of the button). */
  compactAbsent?: string;
  children?: ReactNode;
}) {
  const [open, setOpen] = useState(true);
  const bodyId = useId();
  useEffect(() => setOpen(readOpen()), []);
  const toggle = () => {
    setOpen((v) => {
      writeOpen(!v);
      return !v;
    });
  };
  const hot = warn ?? (reading ? reading.percent >= 80 : false);
  const used = reading?.parts.filter((p) => p.kind !== "free") ?? [];
  const hasParts = !!reading && reading.parts.length > 0;
  const until = reading ? untilSummaryWords(reading) : null;

  return (
    <section data-testid={`session-rail-context`} data-prefix={prefix} className="space-y-1.5">
      <h3>
        <button
          type="button"
          data-testid={`${prefix}-context-toggle`}
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={toggle}
          className="flex items-center gap-1 rounded-md text-[12px] font-medium text-zinc-400 hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
        >
          {open ? <ChevronDown size={13} aria-hidden /> : <ChevronRight size={13} aria-hidden />}
          Context
          {!open && reading ? <span className="font-normal text-zinc-500"> · {reading.percent}% used</span> : null}
        </button>
      </h3>
      <div id={bodyId} hidden={!open} className="space-y-2">
        {children}
        {!reading ? (
          emptyWords ? (
            <p data-testid={`${prefix}-context-none`} className={QUIET}>
              {emptyWords}
            </p>
          ) : null
        ) : (
          <>
            <div className="flex items-baseline justify-between gap-2 text-[13px]">
              <span data-testid={`${prefix}-context-percent`} className={hot ? "text-tone-warn" : "text-zinc-200"}>
                {reading.percent}% used
              </span>
              <span data-testid={`${prefix}-context-window`} className={QUIET} title={usedOfWindow(reading)}>
                {hasParts ? usedOfWindow(reading) : `of ${formatTokens(reading.window)} tokens`}
              </span>
            </div>
            {bars && hasParts ? (
              <div
                data-testid={`${prefix}-context-stack`}
                role="img"
                aria-label={`${reading.percent}% used: ${used.map((p) => `${p.label} ${partPercent(p.tokens, reading.window)}`).join(", ")}`}
                className="flex h-2 overflow-hidden rounded-full bg-white/[0.08]"
              >
                {used.map((p) => (
                  <div
                    key={p.key}
                    data-part={p.key}
                    title={`${p.label}: ${formatTokens(p.tokens)}`}
                    className={`h-full ${swatchClass(p.key, p.kind)}`}
                    style={{ width: `${Math.max(0, Math.min(100, (p.tokens / reading.window) * 100))}%` }}
                  />
                ))}
              </div>
            ) : bars ? (
              <div className="h-1 overflow-hidden rounded-full bg-white/[0.08]" aria-hidden>
                <div
                  data-testid={`${prefix}-context-bar`}
                  className={`h-full rounded-full ${hot ? "bg-tone-warn" : "bg-zinc-400"}`}
                  style={{ width: `${Math.max(0, Math.min(100, reading.percent))}%` }}
                />
              </div>
            ) : null}
            {autoCompactControl}
            {hasParts ? (
              <ul data-testid={`${prefix}-context-parts`} className="space-y-1">
                {reading.parts.map((p) => (
                  <li key={p.key} data-part={p.key} className="flex items-center gap-2 text-[12px]">
                    <span aria-hidden className={`h-2.5 w-2.5 shrink-0 rounded-sm ${swatchClass(p.key, p.kind)}`} />
                    <span className="min-w-0 flex-1 truncate text-zinc-300">{p.label}</span>
                    <span className="tabular-nums text-zinc-400">{formatTokens(p.tokens)}</span>
                    <span className="w-12 text-right tabular-nums text-zinc-500">{partPercent(p.tokens, reading.window)}</span>
                  </li>
                ))}
              </ul>
            ) : null}
            {reading.extra.length ? (
              <div data-testid={`${prefix}-context-extra`} className="space-y-1">
                <p className={QUIET}>Sent with each message, not counted above:</p>
                <ul className="space-y-1">
                  {reading.extra.map((e) => (
                    <li key={e.key} className="flex items-center gap-2 text-[12px]">
                      <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-sm border border-white/20" />
                      <span className="min-w-0 flex-1 truncate text-zinc-400">{e.label}</span>
                      <span className="tabular-nums text-zinc-500">{formatTokens(e.tokens)}</span>
                      <span className="w-12 text-right text-zinc-600">—</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {reading.lists.map((l) => (
              <ListFold key={l.key} prefix={prefix} list={l} />
            ))}
            {note ? (
              <p data-testid={`${prefix}-context-note`} className={QUIET}>
                {note}
              </p>
            ) : null}
            {sourceLine ? <p className={QUIET}>{sourceLine}</p> : null}
          </>
        )}
        {reading && (until || compact || compactAbsent) ? (
          <div className="flex items-center gap-2 pt-0.5">
            <p data-testid={`${prefix}-context-until`} className="min-w-0 flex-1 text-[12px] text-zinc-400">
              {until ?? ""}
            </p>
            {compact ? (
              <button
                type="button"
                data-testid={`${prefix}-context-compact`}
                disabled={!!compact.blocked || compact.busy}
                title={compact.blocked || undefined}
                onClick={compact.onPress}
                className={PRESS}
              >
                {compact.busy ? <Loader2 size={12} className="mr-1 inline animate-spin" aria-hidden /> : null}
                Compact session
              </button>
            ) : null}
          </div>
        ) : null}
        {reading && compact?.blocked ? (
          <p data-testid={`${prefix}-context-compact-why`} className={QUIET}>
            {compact.blocked}
          </p>
        ) : null}
        {reading && !compact && compactAbsent ? (
          <p data-testid={`${prefix}-context-compact-absent`} className={QUIET}>
            {compactAbsent}
          </p>
        ) : null}
      </div>
    </section>
  );
}

/**
 * "Summarize earlier messages at: Auto" — Chat › Code's real setting
 * (settings/schema `context_compaction.auto_at`, read live by every turn),
 * read when the drop-down mounts and saved through the one settings writer
 * (`PUT /settings/values`), with only its mark changed. Nothing is drawn when
 * the setting cannot be read.
 */
export function AutoCompactChoice({ prefix, onSaved }: { prefix: string; onSaved?: () => void }) {
  const [setting, setSetting] = useState<unknown>(undefined);
  const [choice, setChoice] = useState("auto");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  useEffect(() => {
    let live = true;
    get<{ values?: Record<string, unknown> }>("/settings/values")
      .then((r) => {
        if (!live) return;
        const v = r?.values?.[COMPACTION_SETTING] ?? null;
        setSetting(v);
        setChoice(autoCompactChoice(v));
      })
      .catch(() => {
        if (live) setSetting(undefined);
      });
    return () => {
      live = false;
    };
  }, []);
  if (setting === undefined) return null;
  const known = AUTO_COMPACT_CHOICES.some((c) => c.value === choice);
  async function save(next: string) {
    const prev = choice;
    setChoice(next);
    setSaving(true);
    setError(null);
    const value = settingFor(setting, next);
    try {
      await put("/settings/values", { values: { [COMPACTION_SETTING]: value } });
      setSetting(value);
      onSaved?.();
    } catch (e) {
      setChoice(prev);
      setError(e instanceof Error && e.message ? e.message : "The choice was not saved.");
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="space-y-0.5">
      <div className="flex items-center gap-2 text-[12px]">
        <label htmlFor={id} className="min-w-0 flex-1 text-zinc-400">
          Summarize earlier messages at
        </label>
        <select
          id={id}
          data-testid={`${prefix}-context-auto`}
          value={choice}
          disabled={saving}
          onChange={(e) => void save(e.target.value)}
          className="rounded-md bg-white/[0.06] px-1.5 py-0.5 text-[12px] text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
        >
          {AUTO_COMPACT_CHOICES.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
          {!known ? <option value={choice}>{choice}%</option> : null}
        </select>
      </div>
      <p className={QUIET}>{AUTO_COMPACT_HELP}</p>
      {error ? (
        <p role="alert" className="text-[12px] text-tone-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function ListFold({ prefix, list }: { prefix: string; list: ContextList }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div data-testid={`${prefix}-context-list-${list.key}`}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1 rounded-md text-left text-[12px] text-zinc-400 hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
      >
        {open ? <ChevronDown size={12} aria-hidden /> : <ChevronRight size={12} aria-hidden />}
        <span className="flex-1">{list.label}</span>
        <span className="tabular-nums text-zinc-500">{list.count}</span>
      </button>
      <ul id={id} hidden={!open} className="mt-1 space-y-0.5 pl-4">
        {list.items.map((it, i) => (
          <li key={`${it.name}-${i}`} className="flex items-center gap-2 text-[12px]" title={it.path || undefined}>
            <span className="min-w-0 flex-1 truncate text-zinc-300">{it.name}</span>
            {it.detail ? <span className="shrink-0 text-zinc-500">{it.detail}</span> : null}
            {it.tokens !== null ? <span className="shrink-0 tabular-nums text-zinc-500">{formatTokens(it.tokens)}</span> : null}
          </li>
        ))}
        {list.count > list.items.length ? (
          <li className={QUIET}>and {list.count - list.items.length} more</li>
        ) : null}
      </ul>
    </div>
  );
}
