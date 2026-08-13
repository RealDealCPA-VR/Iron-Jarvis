"use client";

/**
 * The TURN RECEIPT — a quiet one-line strip under an assistant reply that makes
 * the reply ACCOUNTABLE: who actually answered, whether that is what was asked
 * for, which tools ran, which armed tools the engine refused, and which files
 * this turn created.
 *
 * WHY IT EXISTS: a mock provider once answered a real chat with a fabricated
 * "Done. Wrote RESULT.md" and NOTHING in the UI disclosed it — the "answered
 * by X" chip suppressed itself on the default route. This strip renders SERVER
 * truth (the daemon's `route` object + the tool ledger), never client
 * inference, and the dishonesty cases are deliberately NOT hidden behind the
 * expand:
 *   - mock answered        → strongest wording, amber, always visible
 *   - failover / mismatch  → "answered by X — …", amber, always visible
 *   - denied tools         → "N blocked" count in the collapsed line
 * A turn with literally nothing to say (no route, no tools, no denials, no
 * files) renders NOTHING — zero-noise on trivial turns is a feature, not an
 * omission.
 *
 * WIRE CONTRACT (routes/chat.py "route" object, v1.165.0): `requested` is ""
 * — not undefined — on chat's default path, so the mismatch check must treat
 * empty as "didn't ask". The reason vocabulary is "explicit" | "default" |
 * "failover" | "prompted-tools" | "auto-tier" | "local-oracle" | "mock".
 * "prompted-tools" means the CHOSEN adapter kept the request via the fenced
 * scaffold — same provider served, so it stays quiet; a capability REROUTE is
 * labelled "failover" by the router itself and therefore warns here.
 */

import { useId, useState, type ReactNode } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Ban,
  ChevronDown,
  ChevronRight,
  FileText,
  Gauge,
  Loader2,
  Route as RouteIcon,
  Undo2,
  Wrench,
} from "lucide-react";

/** Server truth about who served the turn (daemon's routing decision). */
export interface TurnRoute {
  /** What the user/client asked for, when they asked at all. */
  requested?: string;
  /** The provider that actually produced the answer. */
  provider: string;
  model?: string;
  /** Router's own word for why: "default" | "explicit" | "failover" | … */
  reason?: string;
}

export interface TurnReceiptProps {
  /** May be absent on messages persisted before the route object existed. */
  route?: TurnRoute | null;
  /** Tools that actually executed this turn. */
  toolsUsed?: string[];
  /** Armed tools the engine refused to run. */
  deniedTools?: string[];
  /** ABSOLUTE paths of files this turn created or edited. */
  documents?: string[];
  usage?: { input_tokens?: number; output_tokens?: number } | null;
  /** 0..1 context pressure, when known. */
  contextPct?: number | null;
  /** Wired by the coordinator to the DocPreview rail. Receives the FULL path. */
  onOpenDocument?: (path: string) => void;
  /**
   * v1.168.0 — "Undo this write" under the receipt's file chip. The caller
   * joins the undo journal to each document's absolute path and returns the
   * match, or null/undefined when there is none — an UNMATCHED file shows no
   * undo at all (never a guess). A matched not-undoable row renders disabled
   * with the honest reason as its title. Requires `onUndo` too.
   */
  undoFor?: (path: string) => ReceiptUndoState | null | undefined;
  /**
   * Performs the undo (the caller owns the explicit confirm + POST + refresh).
   * A rejection is shown inline under the file row — a failed undo must never
   * look like it happened.
   */
  onUndo?: (actionId: string, path: string) => void | Promise<void>;
}

/** The journal row matched to one of this turn's documents. */
export interface ReceiptUndoState {
  actionId: string;
  undoable: boolean;
  /** Honest reason a matched row cannot be undone ("already undone"…). */
  reason?: string;
  kind?: string;
}

/**
 * Last path segment — handles BOTH separators, because the daemon reports
 * Windows paths with backslashes while workspace-relative ones use slashes.
 * Falls back to the raw string rather than fabricating a name.
 */
export function docBasename(path: string): string {
  const parts = path.split(/[/\\]/).filter((p) => p.length > 0);
  return parts.length > 0 ? parts[parts.length - 1] : path;
}

/**
 * The honesty check: the amber wording the collapsed line must carry, or null
 * when the turn was served as asked. Mock outranks everything — "no real model
 * ran" is the strongest claim and the exact fabrication case this component
 * exists for; it must win even when the router ALSO reports a failover.
 * A failover names who was asked for when that is known (`requested` is ""
 * on the default route — the router had nobody specific to fail over FROM
 * that the user would recognise). "default"/"explicit"/"prompted-tools"/
 * "auto-tier"/"local-oracle" with the requested provider serving is the
 * quiet path.
 */
export function routeWarning(route: TurnRoute | null | undefined): string | null {
  if (!route) return null;
  if (route.provider === "mock") return "mock answer — no real model ran";
  if (route.reason === "failover") {
    return route.requested && route.requested !== route.provider
      ? `answered by ${route.provider} — failover from ${route.requested}`
      : `answered by ${route.provider} — failover`;
  }
  if (route.requested && route.requested !== route.provider) {
    return `answered by ${route.provider} — asked for ${route.requested}`;
  }
  return null;
}

function count(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

/** A finite, non-negative number or null — the server sometimes persists
 *  gaps; "context NaN%" or "-3 in" is worse than saying nothing. */
function fin(n: number | null | undefined): number | null {
  return typeof n === "number" && Number.isFinite(n) && n >= 0 ? n : null;
}

/** Runtime-string, non-blank — server arrays can carry empty entries and the
 *  props cross a JSON boundary, so the types alone are not a guarantee. */
function names(xs: string[]): string[] {
  return xs.filter((x) => typeof x === "string" && x.trim().length > 0);
}

export function TurnReceipt({
  route,
  toolsUsed = [],
  deniedTools = [],
  documents = [],
  usage,
  contextPct,
  onOpenDocument,
  undoFor,
  onUndo,
}: TurnReceiptProps) {
  const [open, setOpen] = useState(false);
  const [undoingPath, setUndoingPath] = useState<string | null>(null);
  const [undoErr, setUndoErr] = useState<string | null>(null);
  const panelId = useId();

  /** Run the caller's undo; surface a rejection inline instead of swallowing
   *  it — a failed undo must never look like it happened. */
  async function runUndo(actionId: string, path: string) {
    if (!onUndo || undoingPath) return;
    setUndoingPath(path);
    setUndoErr(null);
    try {
      await onUndo(actionId, path);
    } catch (e) {
      setUndoErr(e instanceof Error ? e.message : String(e));
    } finally {
      setUndoingPath(null);
    }
  }

  const tools = names(toolsUsed);
  const denied = names(deniedTools);
  // Dedupe: a file both created and edited this turn is ONE file — "2 files"
  // for one path would be the kind of small lie this strip exists to end.
  const docs = Array.from(new Set(names(documents)));

  const warning = routeWarning(route);
  // A route object with no provider and nothing to warn about (degenerate
  // persisted shapes) carries no accountability fact — treat it as absent.
  const rt = route && (route.provider || warning) ? route : null;

  // Zero-noise guard: nothing to account for, render nothing at all. A route
  // carrying a WARNING always renders — the warning is the whole point.
  if (!rt && !tools.length && !denied.length && !docs.length) {
    return null;
  }

  const mismatch = !!rt?.requested && rt.requested !== rt.provider;
  const inTok = fin(usage?.input_tokens);
  const outTok = fin(usage?.output_tokens);
  const ctx = fin(contextPct);

  // The collapsed line, assembled as parts joined by "·". The warning chip is
  // its own styled element so it reads as a WARNING, not just another word.
  const parts: ReactNode[] = [];
  if (rt) {
    parts.push(
      warning ? (
        <span
          key="who"
          className="inline-flex min-w-0 items-center gap-1 rounded-full border border-amber-500/25 bg-amber-500/[0.06] px-1.5 py-px font-medium text-amber-300"
        >
          <AlertTriangle size={10} className="shrink-0" />
          {warning}
        </span>
      ) : (
        <span key="who" className="text-zinc-400">
          {rt.provider}
        </span>
      ),
    );
  }
  if (tools.length > 0) {
    parts.push(<span key="tools">{count(tools.length, "tool")}</span>);
  }
  if (denied.length > 0) {
    // A silent denial invisible until expand would repeat the original bug —
    // the count is on the line, in warning colour.
    parts.push(
      <span key="denied" className="inline-flex items-center gap-1 text-amber-300">
        <Ban size={10} className="shrink-0" />
        {denied.length} blocked
      </span>,
    );
  }
  if (docs.length > 0) {
    parts.push(<span key="docs">{count(docs.length, "file")}</span>);
  }

  return (
    <div className="ml-11 mt-1 text-[11px] text-zinc-500">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((v) => !v)}
        className="group inline-flex max-w-full flex-wrap items-center gap-x-1.5 gap-y-1 text-left transition-colors hover:text-zinc-300"
      >
        {open ? (
          <ChevronDown size={10} className="shrink-0" />
        ) : (
          <ChevronRight size={10} className="shrink-0" />
        )}
        {parts.map((p, i) => (
          <span key={i} className="inline-flex min-w-0 items-center gap-1.5">
            {i > 0 && <span aria-hidden="true">·</span>}
            {p}
          </span>
        ))}
      </button>

      {open && (
        <div
          id={panelId}
          className="mt-1.5 max-w-[560px] space-y-2 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5"
        >
          {rt && (
            <div className="flex items-start gap-2">
              <RouteIcon size={12} className="mt-0.5 shrink-0 text-zinc-500" />
              <div className="min-w-0 text-[11.5px] leading-relaxed">
                <span className={warning ? "text-amber-300" : "text-zinc-300"}>
                  {rt.provider}
                </span>
                {rt.model && (
                  <span className="text-zinc-500"> · {rt.model}</span>
                )}
                {mismatch && (
                  <span className="text-amber-300/90">
                    {" "}
                    — requested {rt.requested}
                  </span>
                )}
                {rt.reason === "auto-tier" ? (
                  // v1.169.0: auto-tier stays QUIET (it is the user's own
                  // configured automation, not a substitution — v1.165.0), but
                  // the judgment behind it must be REACHABLE: the reason links
                  // to the Connections page, where each local model's report
                  // card shows the quality stats the tiering keys off. The
                  // link lives in the expanded panel only — the collapsed line
                  // is inside the toggle button, where a nested anchor would
                  // be illegal DOM.
                  <span className="text-zinc-500">
                    {" "}
                    (
                    <Link
                      href="/connections"
                      title="Auto picked this model from its difficulty tiers and your local models' measured quality — see each model's report card on Connections"
                      className="underline decoration-zinc-700 underline-offset-2 transition-colors hover:text-zinc-300"
                    >
                      auto-tier
                    </Link>
                    )
                  </span>
                ) : (
                  rt.reason && (
                    <span className="text-zinc-500"> ({rt.reason})</span>
                  )
                )}
              </div>
            </div>
          )}

          {tools.length > 0 && (
            <div className="flex items-start gap-2">
              <Wrench size={12} className="mt-0.5 shrink-0 text-zinc-500" />
              <div className="flex min-w-0 flex-wrap gap-x-1.5 gap-y-1">
                {tools.map((t, i) => (
                  <code
                    key={`${t}-${i}`}
                    title={t}
                    className="max-w-full truncate rounded bg-white/[0.04] px-1.5 py-0.5 font-mono text-[11px] text-zinc-300"
                  >
                    {t}
                  </code>
                ))}
              </div>
            </div>
          )}

          {denied.length > 0 && (
            <div className="flex items-start gap-2">
              <Ban size={12} className="mt-0.5 shrink-0 text-amber-400" />
              <div className="flex min-w-0 flex-wrap gap-x-1.5 gap-y-1">
                {denied.map((t, i) => (
                  <code
                    key={`${t}-${i}`}
                    title={t}
                    className="max-w-full truncate rounded border border-amber-500/25 bg-amber-500/[0.06] px-1.5 py-0.5 font-mono text-[11px] text-amber-300"
                  >
                    blocked: {t}
                  </code>
                ))}
              </div>
            </div>
          )}

          {docs.length > 0 && (
            <div className="flex items-start gap-2">
              <FileText size={12} className="mt-0.5 shrink-0 text-zinc-500" />
              <div className="min-w-0 flex-1">
                <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1">
                  {docs.map((path) => {
                    // Undo renders ONLY for a chip the caller matched to a
                    // journal row by path (v1.168.0) — never a guess.
                    const undoState =
                      undoFor && onUndo ? (undoFor(path) ?? null) : null;
                    return (
                      <span
                        key={path}
                        className="inline-flex min-w-0 items-center gap-0.5"
                      >
                        <button
                          type="button"
                          title={path}
                          onClick={() => onOpenDocument?.(path)}
                          className="max-w-[16rem] truncate rounded bg-white/[0.04] px-1.5 py-0.5 font-mono text-[11px] text-zinc-300 transition-colors hover:bg-white/[0.08] hover:text-accent-soft"
                        >
                          {docBasename(path)}
                        </button>
                        {undoState && (
                          <button
                            type="button"
                            onClick={() =>
                              void runUndo(undoState.actionId, path)
                            }
                            disabled={
                              !undoState.undoable || undoingPath !== null
                            }
                            aria-label={`Undo the write to ${docBasename(path)}`}
                            title={
                              undoState.undoable
                                ? `Undo this write — revert ${docBasename(path)}`
                                : `Can't undo: ${undoState.reason ?? "not undoable"}`
                            }
                            className="inline-flex shrink-0 items-center gap-1 rounded px-1 py-0.5 text-[10.5px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-amber-300 disabled:opacity-40"
                          >
                            {undoingPath === path ? (
                              <Loader2 size={10} className="animate-spin" />
                            ) : (
                              <Undo2 size={10} />
                            )}
                            undo
                          </button>
                        )}
                      </span>
                    );
                  })}
                </div>
                {undoErr && (
                  <p className="mt-1 text-[10.5px] text-rose-300/90">
                    {undoErr}
                  </p>
                )}
              </div>
            </div>
          )}

          {(inTok != null || outTok != null || ctx != null) && (
            <div className="flex items-start gap-2">
              <Gauge size={12} className="mt-0.5 shrink-0 text-zinc-500" />
              <div className="min-w-0 text-[11.5px] text-zinc-500">
                {[
                  inTok != null ? `${inTok.toLocaleString()} in` : null,
                  outTok != null ? `${outTok.toLocaleString()} out` : null,
                  ctx != null ? `context ${Math.round(ctx * 100)}%` : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
