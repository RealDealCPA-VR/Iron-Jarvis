"use client";

/**
 * How hands-off Jarvis is (v1.402.0, the autonomy metrics) — the user's three
 * numbers on the Autonomy page: how often it stopped to ask (and how many of
 * those it could have done without), how often the person had to nudge it on,
 * and the share of tasks it finished with no help. The system's own nudges
 * (an approved plan that ran by itself, …) sit on one quiet line beneath.
 *
 * Wire: GET /autonomy/metrics → `eval/autonomy.py`'s answer (its docstring is
 * the definition). One read per visit, never polled. An OLDER DAEMON has no
 * route: a 404 renders NOTHING (the StandingGrants rule); no daemon at all
 * renders nothing either (the page's own offline note says it once).
 */

import { Gauge } from "lucide-react";
import { useApi } from "@/lib/useApi";
import { Card } from "@/components/ui";
import {
  askedWords,
  decodeMetrics,
  handsOffWords,
  nudgedWords,
  systemWords,
  truncatedWords,
} from "@/lib/autonomyMetrics";

export function HandsOffCard() {
  const res = useApi<unknown>("/autonomy/metrics?days=30");
  // An older daemon (404), no daemon (0), or a body that is not the metrics:
  // nothing. The route itself never fails (a read it cannot do answers zeros
  // with `error`, said below), so a card that only said "could not load" would
  // be a husk on every page the daemon cannot serve it.
  if (res.error) return null;
  if (!res.data) return null; // first load — no husk
  const m = decodeMetrics(res.data);
  if (!m) return null;
  const system = systemWords(m);
  const truncated = truncatedWords(m);
  return (
    <Card
      title="How hands-off Jarvis is"
      icon={<Gauge size={15} />}
      right={<span className="text-xs text-zinc-500">Last {m.days} days</span>}
    >
      <div data-testid="hands-off-card">
        {m.tasks === 0 ? (
          <p className="text-sm text-zinc-400" data-testid="hands-off-empty">
            No tasks in the last {m.days} days yet. The numbers start with your next one.
          </p>
        ) : (
          <ul className="space-y-2 text-sm text-zinc-200">
            <li data-testid="hands-off-pct">
              <span className="font-semibold tabular-nums text-tone-success">{handsOffWords(m)}</span>
              <span className="text-zinc-500">
                {" "}
                ({m.hands_off} of {m.tasks})
              </span>
            </li>
            <li data-testid="hands-off-asked" className="tabular-nums">
              {askedWords(m)}
            </li>
            <li data-testid="hands-off-nudged" className="tabular-nums">
              {nudgedWords(m)}
            </li>
          </ul>
        )}
        {system && (
          <p className="mt-3 text-xs text-zinc-500" data-testid="hands-off-system">
            {system}
          </p>
        )}
        {truncated && (
          <p className="mt-3 text-xs text-zinc-500" data-testid="hands-off-truncated">
            {truncated}
          </p>
        )}
        {m.error && (
          <p className="mt-3 text-xs text-zinc-500" data-testid="hands-off-error">
            {m.error}
          </p>
        )}
        <details className="mt-3 text-xs text-zinc-500" data-testid="hands-off-how">
          <summary className="cursor-pointer select-none text-zinc-400 hover:text-zinc-200">
            How this is counted
          </summary>
          <div className="mt-2 max-w-3xl space-y-1.5 leading-relaxed">
            <p>
              A task is one thing you asked for in a chat or a coding session (and what followed until
              your next request), or one run a schedule, a goal, an objective or a queued task started. It
              finished when its last reply ended without an error, a limit, a stop or failing tests.
            </p>
            <p>
              Asked counts every approval card and question put to you. It could have been avoided
              when you approved the same call again in one task, approved a tool you had already
              allowed for the chat, answered a question with “you decide”, or approved a plan without
              changing it. Safety checks (a commit, a push, a paid call, Local only) never count as
              avoidable, and neither does a “no”.
            </p>
            <p>
              A nudge is a short message only to move the work on (“continue”, “try again”, “still
              failing”, a “yes” to an offer), the same request sent again, a Try again or an edited
              question, or a Continue press.
              “With no help” means no answered card or question, no nudge and no note while it
              worked.
            </p>
          </div>
        </details>
      </div>
    </Card>
  );
}
