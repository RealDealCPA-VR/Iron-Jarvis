"use client";

/**
 * v1.290.0 — Safety checks: what the agents did that looks bad.
 *
 * The daemon scans the tool ledger (commands, file reads/writes, web requests)
 * against a packaged rule set (`GET /detections/findings`, `GET
 * /detections/rules`) and this card shows what matched. Nothing here BLOCKS
 * anything — it tells the user where to look, in plain words.
 *
 * Two rules from the v1.226.0 lesson apply: a 5xx is an ERROR (DataError),
 * never the "nothing found" empty state, and status 0 is the OfflineHint's
 * story. The empty state names what was actually checked (N tool calls
 * against M rules) so "no findings" is a statement, not a shrug.
 *
 * `FindingRow` is shared with the Other agents card (AgentHistory.tsx), so a
 * finding reads the same wherever it is shown.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { ChevronDown, ChevronRight, ShieldAlert, ShieldCheck } from "lucide-react";
import { useApi } from "@/lib/useApi";
import { timeAgo } from "@/lib/format";
import { Card, DataError, OfflineHint, SkeletonRows } from "@/components/ui";
import type {
  AgentEvent,
  DetectionFinding,
  DetectionFindingsResponse,
  DetectionRulesResponse,
} from "@/lib/types";

/* -------------------------------------------------------------------------- */
/*  Small shared pieces                                                        */
/* -------------------------------------------------------------------------- */

/** Distinct colour per severity — and the WORD, never colour alone. */
const SEVERITY_STYLE: Record<string, string> = {
  critical: "border-rose-500/40 bg-rose-500/15 text-rose-200",
  high: "border-orange-500/30 bg-orange-500/10 text-orange-300",
  medium: "border-amber-400/30 bg-amber-400/10 text-amber-200",
  low: "border-sky-400/30 bg-sky-400/10 text-sky-200",
};

export function SeverityChip({ severity }: { severity: string }) {
  const sev = (severity || "low").toLowerCase();
  return (
    <span
      data-testid="severity-chip"
      data-severity={sev}
      className={`inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
        SEVERITY_STYLE[sev] ?? "border-white/15 bg-white/[0.04] text-zinc-300"
      }`}
    >
      {sev}
    </span>
  );
}

/** Cut a string to `max` characters with an ellipsis (evidence is never a wall). */
export function clip(s: string | null | undefined, max = 160): string {
  const t = (s ?? "").replace(/\s+/g, " ").trim();
  return t.length > max ? `${t.slice(0, max - 1)}…` : t;
}

/** The one thing an event acted on: the command, else the path, else the URL. */
export function eventTarget(e: AgentEvent): string {
  return e.command || e.path || e.url || "";
}

/** Plain words for an event's action. */
export const ACTION_WORDS: Record<string, string> = {
  "command.executed": "Ran a command",
  "file.read": "Read a file",
  "file.write": "Wrote a file",
  "file.delete": "Deleted a file",
  "network.request": "Web request",
  "tool.called": "Used a tool",
  "approval.denied": "Was refused",
  "prompt.submitted": "Prompt",
  "response.completed": "Response",
};

export function actionWord(action: string): string {
  return ACTION_WORDS[action] ?? action;
}

const SOURCE_WORDS: Record<string, string> = {
  ironjarvis: "Iron Jarvis",
  "claude-code": "Claude Code",
  codex: "Codex",
};

export function sourceWord(source: string | undefined): string {
  return (source && SOURCE_WORDS[source]) || source || "unknown";
}

/** The orchestrator's Session id shape: `core/ids.new_id("session")` mints
 *  "session_" + 12 hex. Ledger rows also carry ids from MCP harness panes,
 *  workflow runs and review jobs (capability-review / memory-review), none of
 *  which has a /sessions page — so ONLY this shape earns that link. */
export const SESSION_ID_RE = /^session_[0-9a-f]{12}$/;

/** Where an Iron Jarvis finding's session lives in the app, or null when it
 *  has no page (a link to a route that 404s is worse than no link). */
export function findingLink(
  source: string | undefined,
  sessionId: string | undefined,
): { href: string; label: string } | null {
  if (source !== "ironjarvis" || !sessionId) return null;
  // A chat turn is reported as "chat:<turn id>" (v1.290.0): chat has no
  // /sessions page, so the link goes to Chat instead.
  if (sessionId === "chat" || sessionId.startsWith("chat:")) {
    return { href: "/chat", label: "Open Chat" };
  }
  if (SESSION_ID_RE.test(sessionId)) {
    return { href: `/sessions/${sessionId}`, label: "Open the session" };
  }
  return null;
}

/** The small "subagent" tag: this event ran inside a Claude Code subagent. */
export function SubagentTag({ name, testId }: { name: string; testId: string }) {
  return (
    <span
      data-testid={testId}
      title={`Run by the subagent ${name}`}
      className="shrink-0 rounded border border-white/10 px-1 text-[10px] text-zinc-500"
    >
      subagent
    </span>
  );
}

/** Evidence shown before "+ N more": enough to see the pattern, never a wall. */
const EVIDENCE_SHOWN = 8;

/** One finding: severity, title, reason, when, a session link (Iron Jarvis
 *  sessions with a page — see findingLink) and collapsible evidence. */
export function FindingRow({ finding }: { finding: DetectionFinding }) {
  const [open, setOpen] = useState(false);
  const evidence = finding.events ?? [];
  const when = finding.last_ts || finding.first_ts;
  const link = findingLink(finding.source, finding.session_id);
  return (
    <li
      data-testid="finding"
      data-rule={finding.rule_id}
      className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2.5"
    >
      <div className="flex flex-wrap items-center gap-2">
        <SeverityChip severity={finding.severity} />
        <span className="min-w-0 flex-1 text-sm font-medium text-zinc-100">
          {finding.title}
          {finding.count && finding.count > 1 ? (
            <span className="ml-1.5 text-[11px] font-normal text-zinc-500">
              × {finding.count}
            </span>
          ) : null}
        </span>
        <span className="shrink-0 text-[11px] text-zinc-500" title={when ?? undefined}>
          {when ? timeAgo(when) : "time unknown"}
        </span>
      </div>
      <p data-testid="finding-reason" className="mt-1 text-[12.5px] leading-relaxed text-zinc-400">
        {finding.reason}
      </p>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-zinc-500">
        <span>{sourceWord(finding.source)}</span>
        {link ? (
          <Link
            data-testid="finding-session-link"
            href={link.href}
            className="text-accent-soft underline-offset-2 hover:underline"
          >
            {link.label}
          </Link>
        ) : null}
        {evidence.length > 0 ? (
          <button
            type="button"
            data-testid="finding-evidence-toggle"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="inline-flex items-center gap-1 text-zinc-400 hover:text-zinc-200"
          >
            {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            Evidence ({evidence.length})
          </button>
        ) : null}
      </div>
      {open ? (
        <ul data-testid="finding-evidence" className="mt-2 space-y-1 border-t border-white/[0.06] pt-2">
          {evidence.slice(0, EVIDENCE_SHOWN).map((e, i) => {
            const target = eventTarget(e);
            return (
              <li
                key={`${e.ref ?? ""}-${i}`}
                data-testid="finding-evidence-item"
                className="flex min-w-0 items-baseline gap-2 text-[11.5px]"
              >
                <span className="shrink-0 text-zinc-500">{actionWord(e.action)}</span>
                {e.subagent ? (
                  <SubagentTag name={e.subagent} testId="finding-evidence-subagent" />
                ) : null}
                {target ? (
                  <code
                    title={target.length > 200 ? target.slice(0, 2000) : undefined}
                    className="min-w-0 truncate font-mono text-zinc-300"
                  >
                    {clip(target, 200)}
                  </code>
                ) : e.tool ? (
                  <code className="min-w-0 truncate font-mono text-zinc-400">{e.tool}</code>
                ) : null}
              </li>
            );
          })}
          {evidence.length > EVIDENCE_SHOWN ? (
            <li className="text-[11px] text-zinc-600">
              + {evidence.length - EVIDENCE_SHOWN} more
            </li>
          ) : null}
        </ul>
      ) : null}
    </li>
  );
}

/* -------------------------------------------------------------------------- */
/*  The Safety checks card                                                     */
/* -------------------------------------------------------------------------- */

const RANGES = [
  { hours: 24, label: "24h", words: "24 hours" },
  { hours: 168, label: "7 days", words: "7 days" },
] as const;

export function SafetyChecksCard() {
  const [hours, setHours] = useState<number>(24);
  const [rulesOpen, setRulesOpen] = useState(false);
  const findings = useApi<DetectionFindingsResponse>(`/detections/findings?hours=${hours}`);
  const rules = useApi<DetectionRulesResponse>("/detections/rules");
  const range = RANGES.find((r) => r.hours === hours) ?? RANGES[0];

  // /activity#safety (the bell's link): the card mounts after the page shell,
  // so nudge the browser's own hash scroll once it exists.
  useEffect(() => {
    if (typeof window === "undefined" || window.location.hash !== "#safety") return;
    document.getElementById("safety")?.scrollIntoView?.({ block: "start" });
  }, []);

  const err = findings.error;
  const list = findings.data?.findings ?? [];
  const ruleList = rules.data?.rules ?? [];
  const ruleCount = rules.data ? (rules.data.count ?? ruleList.length) : null;
  const scanned = findings.data?.events_scanned ?? 0;

  return (
    <div id="safety" className="scroll-mt-20">
      <Card
        title="Safety checks"
        icon={<ShieldAlert size={15} />}
        right={
          <div
            role="group"
            aria-label="Time range"
            className="inline-flex rounded-lg border border-white/10 p-0.5"
          >
            {RANGES.map((r) => (
              <button
                key={r.hours}
                type="button"
                data-testid={`safety-range-${r.hours}`}
                aria-pressed={hours === r.hours}
                onClick={() => setHours(r.hours)}
                className={`rounded-md px-2 py-0.5 text-[11px] font-medium transition-colors ${
                  hours === r.hours
                    ? "bg-white/[0.08] text-zinc-100"
                    : "text-zinc-500 hover:text-zinc-300"
                }`}
              >
                {r.label}
              </button>
            ))}
          </div>
        }
      >
        <p className="mb-3 text-[12.5px] leading-relaxed text-zinc-500">
          Iron Jarvis checks what its agents did — commands, files, web requests — against a set of
          safety rules. Nothing is blocked by this; it shows you what is worth a look.
        </p>

        {err ? (
          err.status === 0 ? (
            <OfflineHint />
          ) : (
            <div data-testid="safety-error">
              <DataError error={err} what="safety checks" />
            </div>
          )
        ) : findings.loading && !findings.data ? (
          <SkeletonRows rows={2} />
        ) : findings.data && list.length === 0 ? (
          <div
            data-testid="safety-empty"
            className="flex items-center gap-2.5 rounded-xl border border-emerald-500/20 bg-emerald-500/[0.05] px-3 py-2.5 text-sm text-zinc-300"
          >
            <ShieldCheck size={16} className="shrink-0 text-emerald-400" aria-hidden="true" />
            <span>
              No safety findings in the last {range.words} — {scanned.toLocaleString()} tool call
              {scanned === 1 ? "" : "s"} checked against{" "}
              {ruleCount === null ? "the safety rules" : `${ruleCount} rule${ruleCount === 1 ? "" : "s"}`}
              .
            </span>
          </div>
        ) : null}

        {!err && list.length > 0 ? (
          <>
            <p className="mb-2 text-[11px] text-zinc-500">
              {list.length} finding{list.length === 1 ? "" : "s"} in the last {range.words} —{" "}
              {scanned.toLocaleString()} tool call{scanned === 1 ? "" : "s"} checked.
            </p>
            <ul data-testid="safety-findings" className="space-y-2">
              {list.map((f, i) => (
                <FindingRow key={`${f.rule_id}-${f.session_id}-${i}`} finding={f} />
              ))}
            </ul>
          </>
        ) : null}

        <div className="mt-3 border-t border-white/[0.06] pt-2.5">
          <button
            type="button"
            data-testid="safety-rules-toggle"
            aria-expanded={rulesOpen}
            onClick={() => setRulesOpen((v) => !v)}
            className="inline-flex items-center gap-1 text-[12px] font-medium text-zinc-400 hover:text-zinc-200"
          >
            {rulesOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            Rules{ruleCount !== null ? ` (${ruleCount})` : ""}
          </button>
          {rulesOpen ? (
            rules.error ? (
              rules.error.status === 0 ? (
                <p className="mt-2 text-[12px] text-zinc-500">The daemon is not reachable.</p>
              ) : (
                <div className="mt-2">
                  <DataError error={rules.error} what="the rules" />
                </div>
              )
            ) : !rules.data ? (
              <div className="mt-2">
                <SkeletonRows rows={2} />
              </div>
            ) : (
              <ul data-testid="safety-rules" className="mt-2 space-y-1.5">
                {ruleList.map((r) => (
                  <li
                    key={r.id}
                    data-testid="safety-rule"
                    data-rule={r.id}
                    className="rounded-lg border border-white/[0.06] bg-white/[0.015] px-3 py-2"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <SeverityChip severity={r.severity} />
                      <span className="text-[12.5px] font-medium text-zinc-200">{r.title}</span>
                      {r.adapted_from ? (
                        <span
                          data-testid="safety-rule-adapted"
                          className="text-[10.5px] text-zinc-500"
                          title={r.adapted_from}
                        >
                          adapted from agent-beacon
                        </span>
                      ) : null}
                    </div>
                    {r.description ? (
                      <p className="mt-1 text-[11.5px] leading-relaxed text-zinc-500">
                        {r.description}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            )
          ) : null}
        </div>
      </Card>
    </div>
  );
}
