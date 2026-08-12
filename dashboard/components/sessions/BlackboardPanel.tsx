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
}

export interface BlackboardResponse {
  board_id: string;
  records: BlackboardRecordView[];
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
              <span className="font-mono text-zinc-500">{shortId(r.author)}</span>
              {r.kind === "message" && r.to_agent && (
                <span className="font-mono text-zinc-500">
                  → {shortId(r.to_agent)}
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
