"use client";

// Blackboard panel (v1.166.0, B3) — the department's shared scratchpad.
//
// Sibling sub-agents coordinate through `blackboard_post` / `blackboard_message`
// tools; the store is keyed by the ROOT session id (`GET /blackboard/{id}`,
// routes/system.py) and until now was invisible to the user — agents were
// passing notes the UI never showed. Records come pre-shaped by the daemon's
// `_to_view`: {id, author, kind: "note"|"message", to_agent, text, created_at}.
//
// Renders NOTHING when the board is empty: most sessions never delegate, and an
// always-on empty "Blackboard" card would advertise machinery that isn't in
// play. Polls ~5s while the session is active so notes land as they are posted.
//
// KNOWN LIMIT (review-flagged, plan-compliant): the plan pins this panel to
// `GET /blackboard/{sessionId}` and the store is keyed by the ROOT session id,
// so on a CHILD session's page (reached via a TeamTree link) the team's board
// renders nothing even while siblings are passing notes — they are visible on
// the ROOT session's page only. Resolving a child's root cannot be done from
// this pair's territory: the run→session parent chain lives in the DB, the
// team endpoint only walks DOWNWARD, and guessing a root id client-side risks
// showing the WRONG team's notes. Backlogged for the coordinator: either grow
// the blackboard endpoint to resolve a child id to its root board, or accept
// root-page-only visibility.
//
// v1.193.0 — NAMES, NOT RUN IDS. The board became name-addressed this release
// (`blackboard/tools.py`: an agent posts to "researcher" and an unknown name is
// REFUSED with the addressable list), and `_to_view` — which THIS endpoint
// serves verbatim — now carries `author_name` / `to_name` alongside the run
// ids. The panel was rendering `shortId(run_id)` for both, i.e. the one
// identity nobody in the conversation uses. It shows the name when there is
// one and falls back to the clipped id otherwise; the id stays in the `title`,
// so nothing is lost.
//
// STILL POLLING, ON PURPOSE. Checked `core/events.py` in this tree: v1.193.0
// added DELEGATION_STARTED / DELEGATION_COMPLETED and nothing else touching
// this board — there is NO blackboard event, and `blackboard/tools.py`
// publishes none. So the 5s poll stays exactly as it was. A delegation event is
// NOT a substitute: it fires when a coordinator hands out work, which is not a
// claim that anyone wrote on the board, and subscribing to it here would light
// this panel up for something that did not touch it. Reported upward instead —
// the fix is a backend `blackboard.posted` event, after which this poll becomes
// the fallback floor rather than the only signal.

import { useEffect, useState } from "react";
import { StickyNote, MessageSquare } from "lucide-react";
import { get } from "@/lib/api";
import { Card } from "@/components/ui";
import { clockTime, shortId } from "@/lib/format";

export interface BlackboardRecordView {
  id: string;
  author: string;
  kind: string; // "note" | "message"
  to_agent: string | null;
  text: string;
  created_at: string;
  /** v1.193.0 additive (blackboard/tools.py::_to_view, served by
   *  GET /blackboard/{id}): the ROSTER-STYLE name the same teammate is
   *  addressable by — "builder", "custom:tax-reader", "remote:hermes". The
   *  board is NAME-ADDRESSED now, so this is the identity the agents
   *  themselves used. Empty/absent on every row written before the columns
   *  landed, and on any row the store could not name. */
  author_name?: string | null;
  to_name?: string | null;
}

export interface BlackboardResponse {
  board_id: string;
  records: BlackboardRecordView[];
}

/** Who a row is FROM / TO, in the vocabulary the team itself uses.
 *
 * v1.193.0: the board is name-addressed — an agent posts to "researcher", and
 * the daemon's own text renderer (`blackboard/tools.py::_render`) prefers the
 * name for exactly the reason the UI should: `author`/`to_agent` are
 * agent_run_ids, precise and unreadable. Falls back to the clipped run id, so
 * legacy rows (and rows the store could not name) read exactly as they did
 * before. NEVER invents a name — an unnamed row says the id, not "agent". */
function who(name: string | null | undefined, runId: string | null): string {
  const named = (name ?? "").trim();
  return named || shortId(runId);
}

export function BlackboardPanel({
  sessionId,
  active,
}: {
  sessionId: string;
  active: boolean;
}) {
  const [records, setRecords] = useState<BlackboardRecordView[]>([]);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await get<BlackboardResponse>(`/blackboard/${sessionId}`);
        if (alive) setRecords(res.records ?? []);
      } catch {
        /* optional panel — absent beats wrong */
      }
    };
    void load();
    if (!active) {
      return () => {
        alive = false;
      };
    }
    const timer = setInterval(() => void load(), 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [sessionId, active]);

  if (records.length === 0) return null;

  return (
    <Card
      title={`Blackboard · ${records.length}`}
      icon={<StickyNote size={15} />}
      right={
        <span className="text-[11px] text-zinc-500">shared between the team</span>
      }
    >
      <div className="max-h-72 space-y-1.5 overflow-y-auto">
        {records.map((r) => (
          <div
            key={r.id}
            className="rounded-lg border border-white/[0.04] bg-white/[0.015] px-2.5 py-1.5"
          >
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              {r.kind === "message" ? (
                <span className="inline-flex items-center gap-1 text-violet-300">
                  <MessageSquare size={11} /> message
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 text-accent-soft/80">
                  <StickyNote size={11} /> note
                </span>
              )}
              <span className="font-mono text-zinc-500" title={r.author}>
                {who(r.author_name, r.author)}
              </span>
              {r.kind === "message" && (r.to_agent || r.to_name) && (
                <span
                  className="font-mono text-zinc-500"
                  title={r.to_agent ?? undefined}
                >
                  → {who(r.to_name, r.to_agent)}
                </span>
              )}
              <span className="ml-auto text-zinc-600">{clockTime(r.created_at)}</span>
            </div>
            <div className="mt-1 whitespace-pre-wrap text-sm text-zinc-300">
              {r.text}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}
