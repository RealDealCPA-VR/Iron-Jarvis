"use client";

import { useState } from "react";
import { ChevronRight, Activity } from "lucide-react";
import { motion } from "framer-motion";
import { useApi } from "@/lib/useApi";
import type { Trace } from "@/lib/types";
import { Card, Spinner, Empty } from "./ui";
import { clockTime } from "@/lib/format";

export function TracesPanel({ sessionId }: { sessionId: string }) {
  const { data, error, loading } = useApi<{ traces: Trace[] }>(`/sessions/${sessionId}/traces`);
  const [open, setOpen] = useState<number | null>(null);
  const traces = data?.traces ?? [];

  return (
    <Card title={`Traces · ${traces.length}`} icon={<Activity size={15} />}>
      {loading && !data ? (
        <Spinner />
      ) : error ? (
        <Empty>Traces unavailable.</Empty>
      ) : traces.length === 0 ? (
        <Empty icon={<Activity size={22} />}>No traces.</Empty>
      ) : (
        <ul className="space-y-1 font-mono text-xs">
          {traces.map((t, i) => (
            <li
              key={i}
              className="overflow-hidden rounded-lg border border-white/[0.05] bg-white/[0.02]"
            >
              <button
                onClick={() => setOpen(open === i ? null : i)}
                className="flex w-full items-center gap-3 px-2.5 py-2 text-left transition-colors hover:bg-white/[0.04]"
              >
                <span className="tabular-nums text-zinc-600">{clockTime(t.ts)}</span>
                <span className="text-violet-300">{t.type}</span>
                <ChevronRight
                  size={14}
                  className={`ml-auto text-zinc-600 transition-transform ${
                    open === i ? "rotate-90" : ""
                  }`}
                />
              </button>
              {open === i && (
                <motion.pre
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  className="overflow-auto border-t border-white/[0.06] px-3 py-2 text-zinc-400"
                >
                  {JSON.stringify(t.payload, null, 2)}
                </motion.pre>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
