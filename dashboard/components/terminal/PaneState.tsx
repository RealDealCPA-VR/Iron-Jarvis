"use client";

/**
 * What the agent in this pane is doing (v1.217.0).
 *
 * Build could already START a coding CLI in a pane and then went blind: the
 * app knew the process was alive and that bytes arrived, nothing more. So the
 * pane where the real work happens was the pane it could say least about, and
 * finding the one that stopped for a question meant opening each of them.
 *
 * The vocabulary is adapted from herdr, a terminal multiplexer built for
 * coding agents — "never hunt for the stuck one" — and it keeps herdr's two
 * honesty rules, which are already this app's own rules under other names:
 *
 *   `blocked`  an approval or question is on screen. It is waiting on YOU.
 *   `unknown`  we cannot classify it — and that NEVER renders as finished.
 *
 * The second is the roster's liveness rule restated ("null is NOT 'free' — it
 * is 'no claim'"), which is why `unknown` draws nothing at all rather than a
 * grey "idle": a pane we cannot read must not look like a pane that is ready.
 *
 * DELIBERATELY NOT COLOUR-ONLY. Every state that renders carries a word, the
 * same rule the tools page's status chips follow.
 */

import type { ReactNode } from "react";
import { AlertTriangle, Check, Loader2 } from "lucide-react";

export type PaneState = "working" | "blocked" | "idle" | "done" | "unknown";

export interface PaneActivity {
  id: string;
  name?: string | null;
  agent_cli?: string | null;
  state?: PaneState | null;
  state_line?: string | null;
  alive?: boolean;
}

/**
 * The client half of the `idle` / `done` split.
 *
 * The daemon reports the settled state as `idle` because whether the user has
 * LOOKED is a fact about this browser, not about the pane. Build already
 * tracks that (`unseenTermOutput`, v1.212.0), so the downgrade happens here —
 * one source of seen-ness, reused rather than reinvented on the server.
 */
/**
 * What a pane ROW says when the classifier had no answer (v1.218.0).
 *
 * `unknown` is deliberately silent on the pane header — a badge reading
 * "unknown" on every plain shell is noise, and on a pane we cannot read it
 * looks like an answer. But the RAIL lists every pane, and a list where half
 * the rows say nothing looks broken rather than honest. So `unknown` splits by
 * what we know about the pane's occupant, and neither half is a completion
 * claim:
 *
 *   `shell`    nothing launched an agent here and the scrollback shows none.
 *              A plain shell, stated as such.
 *   `unclear`  an agent IS here (the Launch catalog started it, or the
 *              scrollback names it) and we cannot read what it is doing.
 *              "We cannot tell" — never "ready", never "finished".
 *
 * The split is honest because `agent_cli` is not a guess: the daemon fills it
 * from what Launch started, falling back to sniffing the scrollback. With
 * neither, calling the pane a shell is a statement about evidence we have, not
 * a claim about work we cannot see.
 */
export type PaneDisplay = PaneState | "shell" | "unclear";

export function displayState(
  state: PaneState,
  cli?: string | null,
): PaneDisplay {
  if (state !== "unknown") return state;
  return cli ? "unclear" : "shell";
}

export function resolveState(
  raw: PaneState | null | undefined,
  unseen: boolean,
): PaneState {
  if (raw === "idle" && unseen) return "done";
  return raw ?? "unknown";
}

const LOOK: Record<
  Exclude<PaneDisplay, "unknown">,
  { label: string; cls: string; icon: ReactNode }
> = {
  working: {
    label: "working",
    cls: "border-accent/30 bg-accent/[0.08] text-accent-soft",
    icon: <Loader2 size={10} aria-hidden className="animate-spin-slow" />,
  },
  blocked: {
    // The one state that is about the USER. It is the loudest thing on the
    // pane for that reason, and it is the reason the feature exists.
    label: "needs you",
    cls: "border-amber-400/40 bg-amber-400/[0.12] text-amber-200",
    icon: <AlertTriangle size={10} aria-hidden />,
  },
  idle: {
    label: "ready",
    cls: "border-white/10 bg-white/[0.02] text-zinc-500",
    icon: null,
  },
  // The two quiet ones. Muted on purpose: they are the ABSENCE of a claim,
  // and dressing them like the states that mean something would spend the
  // user's attention on the panes that have nothing to report.
  shell: {
    label: "shell",
    cls: "border-white/[0.06] bg-white/[0.01] text-zinc-600",
    icon: null,
  },
  unclear: {
    label: "can't tell",
    cls: "border-white/[0.06] bg-white/[0.01] text-zinc-500",
    icon: null,
  },
  done: {
    label: "finished",
    cls: "border-emerald-500/30 bg-emerald-500/[0.08] text-emerald-300",
    icon: <Check size={10} aria-hidden />,
  },
};

export function PaneStateChip({
  state,
  cli,
  line,
  className = "",
}: {
  state: PaneDisplay;
  cli?: string | null;
  line?: string | null;
  className?: string;
}) {
  // `unknown` renders NOTHING, and so does `shell`: on the pane HEADER the
  // shell's own name sits immediately to the left, so a chip repeating "shell"
  // is the same word twice. `unclear` DOES render there — "an agent is in here
  // and I cannot read it" is information the header has no other way to give.
  // The rail (v1.218.0) shows all of them, because a list is a different job:
  // there, a row with nothing in it reads as broken.
  if (state === "unknown" || state === "shell") return null;
  const look = LOOK[state];
  return (
    <span
      data-testid={`pane-state-${state}`}
      title={
        (cli ? `${cli}: ` : "") +
        (state === "blocked"
          ? "waiting on an approval or a question"
          : state === "done"
            ? "finished while you were looking elsewhere"
            : state === "unclear"
              ? "an agent is here and its output does not say what it is doing — NOT a claim that it finished"
              : look.label) +
        (line ? `\n${line}` : "")
      }
      className={`inline-flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${look.cls} ${className}`}
    >
      {look.icon}
      {look.label}
    </span>
  );
}

/**
 * The canvas-level answer to "which one needs me?" — herdr's "never hunt for
 * the stuck one", made literal.
 *
 * Renders only when it has something to say. A count of zero is not a status,
 * and a strip that is always present becomes furniture.
 */
export function PaneStateSummary({
  panes,
  onFocus,
}: {
  panes: { id: string; state: PaneState; name?: string | null }[];
  onFocus: (id: string) => void;
}) {
  const blocked = panes.filter((p) => p.state === "blocked");
  const working = panes.filter((p) => p.state === "working").length;
  const done = panes.filter((p) => p.state === "done");
  if (blocked.length === 0 && working === 0 && done.length === 0) return null;
  return (
    <div
      data-testid="pane-summary"
      className="flex flex-wrap items-center gap-2 text-[11.5px]"
    >
      {blocked.length > 0 && (
        <span className="flex flex-wrap items-center gap-1.5">
          <AlertTriangle size={12} className="shrink-0 text-amber-300" aria-hidden />
          <span className="text-amber-200/90">
            {blocked.length} pane{blocked.length === 1 ? "" : "s"} need
            {blocked.length === 1 ? "s" : ""} you
          </span>
          {blocked.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => onFocus(p.id)}
              data-testid={`focus-blocked-${p.id}`}
              title="Bring this pane to the front"
              className="rounded-md border border-amber-400/30 bg-amber-400/[0.08] px-1.5 py-0.5 font-mono text-[10.5px] text-amber-200 transition-colors hover:bg-amber-400/[0.16]"
            >
              {p.name || p.id}
            </button>
          ))}
        </span>
      )}
      {working > 0 && (
        <span className="text-zinc-500">
          {blocked.length > 0 ? "· " : ""}
          {working} working
        </span>
      )}
      {done.length > 0 && (
        <span className="text-emerald-300/80">
          {blocked.length > 0 || working > 0 ? "· " : ""}
          {done.length} finished
        </span>
      )}
    </div>
  );
}

/**
 * The rail's per-row marker (v1.218.0). A dot, because a row already carries
 * the state in words beside it — this is the thing the eye finds when scanning
 * a column of ten panes, not the thing that says what the state IS.
 */
export function PaneDot({ state }: { state: PaneDisplay }) {
  const cls =
    state === "blocked"
      ? "bg-amber-400 animate-pulse"
      : state === "working"
        ? "bg-accent"
        : state === "done"
          ? "bg-emerald-400"
          : state === "idle"
            ? "bg-zinc-500"
            : "bg-zinc-700";
  return (
    <span
      aria-hidden
      data-testid={`pane-dot-${state}`}
      className={`h-1.5 w-1.5 shrink-0 rounded-full ${cls}`}
    />
  );
}

export function stateWord(state: PaneDisplay): string {
  return state === "unknown" ? "" : LOOK[state].label;
}
