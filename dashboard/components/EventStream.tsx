"use client";

import { AnimatePresence, motion } from "framer-motion";
import { Radio } from "lucide-react";
import { useEvents } from "@/lib/useEvents";
import { clockTime } from "@/lib/format";
import { Card, Dot, Empty } from "./ui";

const TYPE_COLOR: Record<string, string> = {
  "session.created": "text-accent-soft",
  "session.completed": "text-emerald-300",
  "agent.started": "text-accent-soft",
  "agent.completed": "text-emerald-300",
  "agent.state_changed": "text-zinc-300",
  "tool.executed": "text-violet-300",
  "tool.denied": "text-rose-300",
  "artifact.generated": "text-amber-300",
  "memory.updated": "text-teal-300",
  "workflow.completed": "text-emerald-300",
  "review.requested": "text-amber-300",
  "provider.failed": "text-rose-300",
  "provider.downgraded": "text-amber-300",
  // Inbound surfaces: webhooks + two-way comm (received spawns a session;
  // rejected = unauthorized sender refused by the allowlist).
  "webhook.received": "text-sky-300",
  "comm.received": "text-cyan-300",
  "comm.rejected": "text-rose-300",
  // Automation: a cron schedule fired an event-kind task.
  "schedule.fired": "text-indigo-300",
  // A computer-use run reached a terminal status (completed/failed/blocked).
  "computeruse.run_finished": "text-fuchsia-300",
};

export function EventStream() {
  const { events, connected } = useEvents(80);

  return (
    <Card
      title="Live events"
      icon={<Radio size={15} />}
      right={
        <span className="flex items-center gap-2 text-xs text-zinc-500">
          <Dot on={connected} />
          {connected ? "streaming" : "disconnected"}
        </span>
      }
    >
      {events.length === 0 ? (
        <Empty icon={<Radio size={22} />}>
          {connected
            ? "Waiting for events — run a session to see live activity."
            : "No connection to /events yet."}
        </Empty>
      ) : (
        <ul className="max-h-[440px] space-y-0.5 overflow-y-auto pr-1 font-mono text-xs">
          <AnimatePresence initial={false}>
            {events.map((e) => (
              <motion.li
                key={e.id}
                layout
                initial={{ opacity: 0, x: -10, backgroundColor: "rgb(var(--accent-rgb)/0.10)" }}
                animate={{ opacity: 1, x: 0, backgroundColor: "rgb(var(--accent-rgb)/0)" }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
                className="flex items-baseline gap-3 rounded-lg px-2.5 py-1.5 hover:bg-white/[0.04]"
              >
                <span className="shrink-0 tabular-nums text-zinc-600">
                  {clockTime(e.ts)}
                </span>
                <span className={`shrink-0 font-medium ${TYPE_COLOR[e.type] || "text-zinc-300"}`}>
                  {e.type}
                </span>
                <span className="truncate text-zinc-500">
                  {e.session_id ? e.session_id.slice(0, 8) : ""}
                  {summarize(e.payload)}
                </span>
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      )}
    </Card>
  );
}

function summarize(payload: Record<string, unknown>): string {
  if (!payload) return "";
  // channel/sender: comm.* — slug: webhook.received — workflow: schedule.fired —
  // run_id: computeruse.run_finished. Long values (e.g. task) truncate in the row.
  const keys = [
    "tool",
    "name",
    "status",
    "state",
    "risk",
    "summary",
    "channel",
    "sender",
    "slug",
    "workflow",
    "run_id",
    "task",
  ];
  const parts: string[] = [];
  for (const k of keys) {
    if (payload[k] !== undefined && payload[k] !== null) {
      parts.push(`${k}=${String(payload[k])}`);
    }
  }
  return parts.length ? "  " + parts.join(" ") : "";
}
