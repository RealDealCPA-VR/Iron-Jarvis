"use client";

/**
 * v1.545.0 — Build › Code: one session goes on with another coding tool
 * (lib/buildAppSwitch). The three small pieces the screen draws for it:
 *
 * - `SwitchConfirm`: the ONE confirmation, drawn under the lists of the
 *   session's picker once another tool was pressed there — "Switch to
 *   Codex?", the daemon's own sentences one per line, and two presses. It
 *   takes the cursor when it appears; Tab moves between its presses, and
 *   Shift+Tab from the first of them (or ArrowUp) goes back to the picker's
 *   lists above it (`onLeave`), so a model or an effort of the tool asked
 *   about can still be picked by keyboard; Esc (the picker's own) cancels.
 *   A refusal of the first press is said HERE, in the
 *   daemon's own sentence, with one press (Close): nothing was changed, and
 *   the lists above stay open for another pick.
 * - `HandoffRow`: the summary a tool was given, folded under the message it
 *   rode. One press opens the EXACT text, as plain text (never Markdown or
 *   HTML: it is what a program was told, bars and all), with Copy.
 * - `HandoffWaiting`: one quiet line above the message box while a summary
 *   waits for the next message.
 */

import { useEffect, useId, useRef, useState } from "react";
import { Check, ChevronDown, ChevronRight, Copy, FileText } from "lucide-react";
import {
  HANDOFF_COPIED,
  HANDOFF_COPY,
  HANDOFF_COPY_FAILED,
  HANDOFF_ROW_TITLE,
  SWITCH_CANCEL,
  SWITCH_CHECKING,
  SWITCH_CLOSE,
  SWITCH_REARM_GUARD_MS,
  SWITCHING,
  handoffRowWords,
  handoffWaitingWords,
  switchHeading,
  switchPress,
  switchRefusedHeading,
  type HandoffPending,
  type SwitchPlan,
} from "@/lib/buildAppSwitch";

const QUIET =
  "inline-flex h-7 items-center rounded-lg px-2.5 text-[12px] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50";
const MAIN =
  "inline-flex h-7 items-center rounded-lg bg-accent px-2.5 text-[12px] font-medium text-ink-950 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50";

/** How long "Copied" stays under an opened summary. */
export const HANDOFF_COPIED_MS = 1500;

export function SwitchConfirm({
  label,
  plan,
  refused = "",
  changed = "",
  busy,
  onConfirm,
  onCancel,
  onLeave,
  now = Date.now,
}: {
  /** The tool asked about, by name. */
  label: string;
  /** The daemon's plan (null while it is read: nothing can be confirmed). */
  plan: SwitchPlan | null;
  /** The daemon refused the first press: its own sentence ("" = it did not). */
  refused?: string;
  /** The second press was refused because the switch would now do something
   *  else: the daemon's sentence, said above the NEW lines ("" = it was not). */
  changed?: string;
  /** The second press is out. */
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  /** Go back to the lists above (the picker's search box): Shift+Tab from the
   *  first press, or ArrowUp. Absent = Tab only moves between the presses. */
  onLeave?: () => void;
  /** The clock (a test's own). */
  now?: () => number;
}) {
  const box = useRef<HTMLElement | null>(null);
  const headingId = useId();
  // The confirmation takes the cursor when it appears (it is the answer to
  // the press that asked), and is brought into view (a short window's panel
  // scrolls inside itself).
  useEffect(() => {
    box.current?.focus({ preventScroll: true });
    box.current?.scrollIntoView?.({ block: "nearest" });
  }, []);
  // A plan read again because it CHANGED is new to the reader: the press that
  // switches does nothing for a moment after its lines are shown (a
  // double-click on the old plan's press must not confirm the new one).
  const armedFor = useRef<SwitchPlan | null>(null);
  const armedAt = useRef(0);
  if (changed && plan && armedFor.current !== plan) {
    armedFor.current = plan;
    armedAt.current = now();
  }
  const confirm = () => {
    if (changed && plan && now() - armedAt.current < SWITCH_REARM_GUARD_MS) return;
    onConfirm();
  };
  // …and again when it says the plan changed (the press that had the cursor
  // is waiting for the new lines).
  useEffect(() => {
    if (changed) box.current?.focus({ preventScroll: true });
  }, [changed]);
  return (
    <section
      ref={box}
      tabIndex={-1}
      role="group"
      aria-labelledby={headingId}
      data-testid="code-switch-ask"
      data-busy={busy ? "true" : undefined}
      className="rounded-lg outline-none focus-visible:ring-1 focus-visible:ring-accent/40"
      onKeyDown={(e) => {
        if (e.key === "ArrowUp" && onLeave) {
          // Back to the lists above (the tool asked about: its models).
          e.preventDefault();
          e.stopPropagation();
          onLeave();
          return;
        }
        if (e.key !== "Tab") return;
        // Tab never closes the panel from here (that would drop the question).
        e.preventDefault();
        e.stopPropagation();
        const list = Array.from(box.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])") ?? []);
        const at = list.indexOf(document.activeElement as HTMLButtonElement);
        // Shift+Tab from the first press (or from the confirmation itself):
        // back to the lists.
        if (e.shiftKey && at <= 0 && onLeave) {
          onLeave();
          return;
        }
        if (!list.length) return;
        const next = at < 0 ? (e.shiftKey ? list.length - 1 : 0) : (at + (e.shiftKey ? -1 : 1) + list.length) % list.length;
        list[next]?.focus();
      }}
    >
      <h3 id={headingId} data-testid="code-switch-heading" className="text-[13px] font-medium text-zinc-100">
        {refused ? switchRefusedHeading(label) : switchHeading(label)}
      </h3>
      {changed && !refused ? (
        <p role="status" data-testid="code-switch-changed" className="mt-1 break-words text-[12px] text-zinc-300">
          {changed}
        </p>
      ) : null}
      {refused ? (
        <p role="alert" data-testid="code-switch-refused" className="mt-1 break-words text-[12px] text-zinc-300">
          {refused}
        </p>
      ) : plan ? (
        <ul
          data-testid="code-switch-lines"
          aria-live="polite"
          className="mt-1 min-w-0 space-y-0.5 break-words text-[12px] text-zinc-400"
        >
          {plan.lines.map((line, i) => (
            <li key={`${i}:${line}`} data-testid="code-switch-line">
              {line}
            </li>
          ))}
        </ul>
      ) : (
        <p role="status" data-testid="code-switch-checking" className="mt-1 text-[12px] text-zinc-500">
          {SWITCH_CHECKING}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center justify-end gap-2">
        <button type="button" data-testid="code-switch-cancel" disabled={busy} onClick={onCancel} className={QUIET}>
          {refused ? SWITCH_CLOSE : SWITCH_CANCEL}
        </button>
        {refused ? null : (
          <button
            type="button"
            data-testid="code-switch-confirm"
            disabled={!plan || busy}
            onClick={confirm}
            className={MAIN}
          >
            {busy ? SWITCHING : switchPress(label)}
          </button>
        )}
      </div>
    </section>
  );
}

export function HandoffRow({
  toLabel,
  resumes,
  text,
  note,
}: {
  /** The tool it was given to, by name. */
  toLabel: string;
  resumes: boolean;
  /** EXACTLY what the tool was given. */
  text: string;
  /** A sentence of the daemon's to show under it ("" = none). */
  note: string;
}) {
  const [open, setOpen] = useState(false);
  const [said, setSaid] = useState<"" | "copied" | "failed">("");
  const timer = useRef<number | null>(null);
  const panelId = useId();
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );
  const copy = async () => {
    let outcome: "copied" | "unavailable" = "unavailable";
    try {
      const { copyPlain } = await import("@/components/settings/MaintenanceTools");
      outcome = await copyPlain(text);
    } catch {
      outcome = "unavailable";
    }
    setSaid(outcome === "copied" ? "copied" : "failed");
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setSaid(""), HANDOFF_COPIED_MS);
  };
  return (
    <div data-testid="code-handoff" data-open={open ? "true" : "false"} className="min-w-0 text-[12px] text-zinc-500">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={open ? panelId : undefined}
          data-testid="code-handoff-toggle"
          title={HANDOFF_ROW_TITLE}
          onClick={() => setOpen((v) => !v)}
          className="flex min-w-0 items-center gap-1.5 rounded-sm text-left transition-colors hover:text-zinc-300 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
        >
          {open ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
          <FileText size={12} aria-hidden="true" className="shrink-0" />
          <span className="min-w-0 truncate">{handoffRowWords(toLabel, resumes)}</span>
        </button>
        {open ? (
          <button
            type="button"
            data-testid="code-handoff-copy"
            onClick={() => void copy()}
            className="inline-flex items-center gap-1 rounded-lg px-2 py-0.5 text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
          >
            {said === "copied" ? <Check size={12} aria-hidden="true" className="text-tone-success" /> : <Copy size={12} aria-hidden="true" />}
            {HANDOFF_COPY}
          </button>
        ) : null}
        <span role="status" data-testid="code-handoff-copied" className="text-[11px]">
          {said === "copied" ? HANDOFF_COPIED : said === "failed" ? HANDOFF_COPY_FAILED : ""}
        </span>
      </div>
      {open ? (
        <div id={panelId} className="mt-1">
          {/* Plain text, as the tool got it: a text node, never parsed. */}
          <pre
            data-testid="code-handoff-text"
            tabIndex={0}
            className="max-h-96 select-text overflow-auto whitespace-pre-wrap break-words rounded-lg bg-white/[0.03] px-3 py-2 font-mono text-[12px] leading-5 text-zinc-300 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/40"
          >
            {text}
          </pre>
          {note ? (
            <p data-testid="code-handoff-note" className="mt-1 text-zinc-500">
              {note}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function HandoffWaiting({ pending }: { pending: HandoffPending | null }) {
  if (!pending) return null;
  return (
    <p role="status" data-testid="code-handoff-waiting" data-resumes={pending.resumes ? "true" : "false"} className="mb-2 px-1 text-[12px] text-zinc-500">
      {handoffWaitingWords(pending.toLabel, pending.resumes)}
    </p>
  );
}
