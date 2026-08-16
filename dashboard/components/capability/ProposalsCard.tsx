"use client";

// Capability requests (v1.178.0, P4) — the agent asked, the user decides.
//
// THE MEASURED FAILURE this card exists for: "rename all files in this folder"
// ran FOUR times and renamed nothing, because no rename tool existed — and the
// agent had no way to SAY so. It shelled out and wrote scripts to re-read PDFs
// it had already read. `capability/models.py` turned that missing sentence into
// a durable row; this is the only place it is ever answered.
//
// THE THREE THINGS THIS CARD MUST NOT GET WRONG:
//
//  1. APPROVAL CREATES. Filing changes nothing at all — the copy says so — but
//     Approve is the one click in the whole feature that makes something exist.
//     Wording it as "accept"/"acknowledge" would be a lie by softness.
//  2. APPROVAL IS NOT PERMISSION. An approved custom tool lands on
//     `custom:<name>`, which is absent from the permission table and therefore
//     resolves to `ask` (fail-closed), and the deny floor can never be raised.
//     The row shows the mode the agent WANTED next to what it will actually run
//     under, so "requested: allow" can never be misread as "will be allowed".
//  3. IT CANNOT PROMISE WHAT IT CANNOT DO. `can_apply` / `kind_note` / `blocked`
//     come from the LIVE platform on the GET, so an MCP or connection request —
//     which needs a command and credentials only the user has — says so BEFORE
//     the click instead of failing after it.
//
// Degrade rule (RosterStrip.tsx's, same reasoning): a daemon that predates the
// endpoint simply has no card. Not an error, not an empty box advertising a
// feature that isn't there.

import { useCallback, useEffect, useState } from "react";
import { Check, Inbox, Plug, RotateCw, ShieldAlert, Link2, Wrench, X } from "lucide-react";
import { ApiError, get, post } from "@/lib/api";
import { Card, ErrorNote, LoaderInline, SuccessNote } from "@/components/ui";
import { Reveal } from "@/components/motion";

/** One entry of GET /capability/proposals (`store.proposal_view`).
 *
 *  Typed HERE, not in lib/types.ts — that file is coordinator-owned this
 *  release. Everything except `id`/`name`/`status` is optional so a daemon that
 *  serves an older, thinner view renders what it has instead of throwing. */
export interface CapabilityProposal {
  id: string;
  /** "tool" | "mcp" | "connection". */
  kind?: string;
  /** The daemon's plain-language noun ("a custom tool"). */
  kind_label?: string;
  /** The capability as the agent named it. Shown verbatim. */
  name: string;
  /** WHY, in the agent's own sentences. Never summarized or rewritten here. */
  rationale?: string;
  /** Exactly what it would be ALLOWED to do — the sentence being approved. */
  scope?: string;
  /** The job in hand when the gap was hit. */
  task?: string;
  /** For a tool: the literal argv that would be registered. */
  command?: string[];
  /** The mode the agent WANTED. Recorded, never applied. */
  requested_permission?: string;
  /** The permission key it will actually run under ("custom:<name>"), or "". */
  runs_under?: string;
  status?: string;
  /** Can approval actually do this from here (tool yes, mcp/connection no)? */
  can_apply?: boolean;
  /** The honest one-liner behind `can_apply` — shown either way. */
  kind_note?: string;
  /** Non-empty = may NEVER be granted (deny floor, templated program). */
  blocked?: string;
  created_at?: string | null;
}

interface ProposalsPayload {
  proposals?: CapabilityProposal[];
  pending?: number;
  stats?: { pending?: number; approved?: number; rejected?: number; total?: number };
}

/** Icon per kind. Decorative — the kind badge next to it carries the words, so
 *  these are aria-hidden with no role (a11y rule: a graphic that repeats
 *  adjacent text must not be announced twice). */
function kindIcon(kind: string) {
  if (kind === "mcp") return <Plug size={12} aria-hidden="true" />;
  if (kind === "connection") return <Link2 size={12} aria-hidden="true" />;
  return <Wrench size={12} aria-hidden="true" />;
}

const KIND_PILL: Record<string, string> = {
  tool: "border-accent/30 bg-accent/[0.08] text-accent-soft",
  mcp: "border-violet-500/30 bg-violet-500/10 text-violet-300",
  connection: "border-zinc-500/30 bg-zinc-500/10 text-zinc-400",
};

/** argv as chips; {placeholder} tokens pop, matching the Tools page's own
 *  rendering of a custom tool's command. */
function ArgvChips({ argv }: { argv: string[] }) {
  return (
    <div className="flex flex-wrap gap-1">
      {argv.map((tok, i) => {
        const isPh = /^\{.+\}$/.test(tok);
        return (
          <span
            key={i}
            className={`rounded-md border px-1.5 py-0.5 font-mono text-[11px] ${
              isPh
                ? "border-accent/30 bg-accent/[0.08] text-accent-soft"
                : "border-white/[0.06] bg-white/[0.03] text-zinc-300"
            }`}
          >
            {tok}
          </span>
        );
      })}
    </div>
  );
}

/**
 * The review queue for capability requests, mounted on the Tools page.
 *
 * Renders NOTHING at all until the first answer lands, and nothing ever on a
 * daemon without the endpoint. An empty queue is a calm sentence, never an
 * error — "no agent has hit a wall" is the good outcome, not a fault.
 */
export function ProposalsCard() {
  const [payload, setPayload] = useState<ProposalsPayload | null>(null);
  // true = this daemon has no /capability/proposals -> render nothing at all.
  const [absent, setAbsent] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<{ id: string; action: "approve" | "reject" } | null>(
    null,
  );
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});

  const refresh = useCallback(async () => {
    try {
      setPayload(await get<ProposalsPayload>("/capability/proposals"));
      setAbsent(false);
      // CLEARED ON SUCCESS, like lib/useApi does (review fix). This card has no
      // polling and no Retry button, so an error left set outlived the failure
      // that caused it: one transient 500 on the post-decision re-read pinned a
      // red alert to a queue that was reading perfectly well, for as long as the
      // user stayed on /tools.
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && (err.status === 404 || err.status === 405)) {
        setAbsent(true);
        return;
      }
      // A live daemon that errors is worth saying out loud; a dead one is not
      // (status 0 = the app isn't running, which every page already reports
      // once at the top). MemoryReview's split, for the same reason.
      if (err instanceof ApiError && err.status !== 0) setError(err.message);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  /** POST approve|reject for one row, then re-read the queue.
   *
   *  The list is re-fetched rather than patched locally: approve can FAIL with
   *  the row left pending (a 409 for something approval cannot satisfy), so a
   *  local "remove the row" would show a request as handled that is still in
   *  the queue on the daemon. */
  async function decide(p: CapabilityProposal, action: "approve" | "reject") {
    if (busy) return;
    setBusy({ id: p.id, action });
    setNote(null);
    setRowErrors((e) => {
      const next = { ...e };
      delete next[p.id];
      return next;
    });
    try {
      const res = await post<{
        applied?: { created?: string; permission_key?: string; permission_mode?: string };
      }>(`/capability/proposals/${encodeURIComponent(p.id)}/${action}`);
      if (action === "approve") {
        // Report what the server said it CREATED and the mode it read back off
        // the live engine — not what this card forecast before the click. A
        // claim about permissions that was never verified is the exact kind of
        // reassurance this feature must not manufacture.
        const applied = res?.applied;
        const created = applied?.created || p.name;
        const key = applied?.permission_key || p.runs_under || "";
        const mode = applied?.permission_mode || "";
        setNote(
          key && mode
            ? `Created “${created}”. It runs as ${key} at “${mode}” — it still needs your say-so each time.`
            : `Created “${created}”.`,
        );
      } else {
        setNote("Request turned down. The agent won't ask for this one again.");
      }
      void refresh();
    } catch (err) {
      setRowErrors((e) => ({
        ...e,
        [p.id]: err instanceof ApiError ? err.message : String(err),
      }));
      // A refused approve leaves the row pending on purpose — re-read so the
      // card keeps showing it rather than quietly dropping a live request.
      void refresh();
    } finally {
      setBusy(null);
    }
  }

  // THE TWO SILENCES ARE DIFFERENT, and collapsing them into one
  // `if (!payload) return null` hid a real defect while this was being built:
  // `error` was being set for a live 5xx and could never render, because the
  // failed fetch left `payload` null and the whole card returned early. So:
  //   - `absent`      -> no card, ever (the daemon predates the endpoint);
  //   - no data, no error -> nothing YET (first load, or the app isn't running,
  //     which every page already reports once at the top);
  //   - a live error  -> the card exists and says what went wrong.
  if (absent) return null;
  if (!payload && !error) return null;

  const pending = (payload?.proposals ?? []).filter(
    (p) => Boolean(p) && (p.status ?? "pending") === "pending",
  );
  // Nothing loaded and something to report: the queue is UNKNOWN, not empty —
  // "Nothing to review" under an error message would be a claim the card
  // cannot make.
  const unknown = !payload && Boolean(error);

  return (
    // The Reveal lives HERE, not at the mount point: a hidden card must leave
    // no empty wrapper behind to double the page's space-y gap (RosterStrip).
    <Reveal>
      <Card
        title={
          pending.length > 0
            ? `Requests from your agents · ${pending.length}`
            : "Requests from your agents"
        }
        icon={<Inbox size={15} aria-hidden="true" />}
      >
        <p className="mb-3 text-[13px] leading-relaxed text-zinc-500">
          When a job would have gone better with a tool Iron Jarvis doesn&apos;t have,
          the agent asks for it here instead of working around it.{" "}
          <span className="text-zinc-300">
            Asking creates nothing — approving is what creates it.
          </span>{" "}
          Whatever you approve is still permission-gated: it asks you before every
          single use, and the tools on the deny floor can never be raised.
        </p>

        {note && (
          <div className="mb-3">
            <SuccessNote>{note}</SuccessNote>
          </div>
        )}
        {error && (
          <div className="mb-3 space-y-2">
            <ErrorNote>{error}</ErrorNote>
            {/* The card reads on mount and after a decision — it does not poll.
                So a failed read used to be TERMINAL: the queue stayed unknown
                until the user navigated away and back, which is indistinguishable
                from "no requests" to anyone who doesn't know the card polls
                nothing (review fix). */}
            <button
              type="button"
              onClick={() => void refresh()}
              className="btn-ghost py-1.5 text-xs"
            >
              <RotateCw size={13} aria-hidden="true" /> Try again
            </button>
          </div>
        )}

        {unknown ? null : pending.length === 0 ? (
          <p className="text-[13px] leading-relaxed text-zinc-600">
            Nothing to review. When an agent runs into something it has no tool for,
            the request lands here with its reason — you approve or turn it down.
          </p>
        ) : (
          <ul className="space-y-3">
            {pending.map((p) => {
              const kind = p.kind || "tool";
              const pill = KIND_PILL[kind] ?? KIND_PILL.connection;
              const blocked = (p.blocked || "").trim();
              // An ABSENT can_apply must not disable the only action on the
              // card: a thinner view than this release's is degraded-but-usable,
              // and the daemon answers an impossible approve with an honest 409
              // that lands on the row. Only an explicit `false` disables.
              const canApply = p.can_apply !== false && !blocked;
              const wanted = (p.requested_permission || "").trim();
              const rowBusy = busy?.id === p.id;
              // `decide` opens with `if (busy) return`, so while ANY row is
              // deciding every other row's click is swallowed. Disabling only
              // the busy row left the rest looking live and doing nothing —
              // which reads as "it worked" (review fix). One decision at a time
              // is deliberate: approve mutates the registry.
              const locked = busy !== null;
              const command = p.command ?? [];
              return (
                <li
                  key={p.id}
                  className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-3.5"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${pill}`}
                    >
                      {kindIcon(kind)}
                      {p.kind_label || kind}
                    </span>
                    <span className="font-mono text-[13px] font-semibold text-zinc-100">
                      {p.name}
                    </span>
                  </div>

                  {p.task && (
                    <p className="mt-1.5 text-[11.5px] leading-relaxed text-zinc-500">
                      Hit while: <span className="text-zinc-300">{p.task}</span>
                    </p>
                  )}

                  {/* The agent's OWN words. Shown verbatim — this is the text the
                      decision is made on, so nothing rewrites or trims it. */}
                  {p.rationale && (
                    <p className="mt-1.5 text-[13px] leading-relaxed text-zinc-300">
                      {p.rationale}
                    </p>
                  )}

                  {p.scope && (
                    <p className="mt-1.5 text-xs leading-relaxed text-zinc-400">
                      <span className="text-zinc-500">It would be allowed to:</span>{" "}
                      {p.scope}
                    </p>
                  )}

                  {command.length > 0 && (
                    <div className="mt-2">
                      <div className="mb-1 text-[10px] uppercase tracking-[0.12em] text-zinc-600">
                        Command it would run
                      </div>
                      <ArgvChips argv={command} />
                    </div>
                  )}

                  {/* The permission truth, side by side. `runs_under` is the key
                      the created tool answers to; its absence from the table
                      resolves to "ask", which is why this can say so flatly. */}
                  {p.runs_under && (
                    <p className="mt-2 text-[11.5px] leading-relaxed text-zinc-500">
                      If approved it runs as{" "}
                      <code className="rounded bg-black/40 px-1 py-0.5 font-mono text-[11px] text-accent-soft/90">
                        {p.runs_under}
                      </code>{" "}
                      and asks you every time.
                    </p>
                  )}
                  {wanted && wanted !== "ask" && (
                    <p className="mt-1 text-[11.5px] leading-relaxed text-amber-300/80">
                      The agent asked to run this at “{wanted}”. Approving does not
                      grant that — approval never writes a permission, so it still
                      asks.
                    </p>
                  )}

                  {p.kind_note && (
                    <p
                      className={`mt-2 text-[11.5px] leading-relaxed ${
                        canApply ? "text-zinc-600" : "text-amber-300/80"
                      }`}
                    >
                      {p.kind_note}
                    </p>
                  )}

                  {blocked && (
                    <p className="mt-2 flex items-start gap-1.5 text-[11.5px] leading-relaxed text-rose-300/90">
                      <ShieldAlert size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
                      <span>{blocked}</span>
                    </p>
                  )}

                  <div className="mt-2.5 flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={() => void decide(p, "approve")}
                      disabled={locked || !canApply}
                      aria-label={`Approve “${p.name}”`}
                      title={
                        blocked
                          ? "This can never be granted — see the reason above"
                          : canApply
                            ? `Create “${p.name}” now. It will still ask before every use.`
                            : "Iron Jarvis can't create this one for you — see the note above"
                      }
                      className="btn-accent py-1.5 text-xs disabled:opacity-50"
                    >
                      {rowBusy && busy?.action === "approve" ? (
                        <LoaderInline label="Creating…" />
                      ) : (
                        <>
                          <Check size={13} aria-hidden="true" /> Approve &amp; create
                        </>
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => void decide(p, "reject")}
                      disabled={locked}
                      aria-label={`Reject “${p.name}”`}
                      title="Turn this down. Nothing is created and it won't be asked again."
                      className="btn-ghost py-1.5 text-xs disabled:opacity-50"
                    >
                      {rowBusy && busy?.action === "reject" ? (
                        <LoaderInline label="Rejecting…" />
                      ) : (
                        <>
                          <X size={13} aria-hidden="true" /> Reject
                        </>
                      )}
                    </button>
                  </div>

                  {rowErrors[p.id] && (
                    <div className="mt-2">
                      <ErrorNote>{rowErrors[p.id]}</ErrorNote>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </Reveal>
  );
}
