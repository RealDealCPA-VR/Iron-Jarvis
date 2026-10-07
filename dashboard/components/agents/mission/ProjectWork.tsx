"use client";

// A PROJECT'S WORK on its mission screen (v1.308.0): Board / Waiting on you /
// Completed — the same three lists the project world had, now under the
// objective composer instead of beside a round table. Only the open tab is
// mounted (the Board polls), and the screen opens on "Waiting on you" when
// something is.

import { useState } from "react";
import type { CompletedItem, WaitingItem } from "@/lib/agentWorlds";
import { WorldBoard } from "@/components/agents/world/WorldBoard";
import { WaitingList } from "@/components/agents/world/WaitingList";
import { CompletedList } from "@/components/agents/world/CompletedList";

type Tab = "waiting" | "board" | "completed";

export function ProjectWork({
  projectId,
  waiting,
  completed,
}: {
  projectId: string;
  waiting: WaitingItem[];
  completed: CompletedItem[];
}) {
  const [tab, setTab] = useState<Tab>(waiting.length > 0 ? "waiting" : "board");
  const tabs: { key: Tab; label: string }[] = [
    { key: "board", label: "Board" },
    { key: "waiting", label: waiting.length ? `Waiting on you (${waiting.length})` : "Waiting on you" },
    { key: "completed", label: "Completed" },
  ];
  return (
    <section data-testid="mission-project-work" className="card-surface p-0">
      <div role="tablist" aria-label="Project work" className="flex items-center gap-1 border-b hairline px-4 pt-2">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            data-testid={`project-tab-${t.key}`}
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={`-mb-px rounded-t-lg border-b-2 px-3 py-1.5 text-[13px] transition-colors ${
              tab === t.key
                ? "border-accent font-semibold text-zinc-100"
                : "border-transparent text-zinc-400 hover:text-zinc-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="px-4 py-3">
        {tab === "board" && <WorldBoard projectId={projectId} />}
        {tab === "waiting" && <WaitingList projectId={projectId} items={waiting} />}
        {tab === "completed" && <CompletedList projectId={projectId} items={completed} />}
      </div>
    </section>
  );
}
