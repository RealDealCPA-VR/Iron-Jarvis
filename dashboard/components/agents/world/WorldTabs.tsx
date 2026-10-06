"use client";

/**
 * A world's right panel (v1.304.0): Board · Waiting on you · Completed · New
 * task. Only the OPEN tab is mounted — the Board polls /sessions every 4 s
 * and the New-task form reads the project detail, and neither has any
 * business doing that behind a closed tab.
 *
 * Opens on "Waiting on you" when something is waiting (that is why the user
 * came), otherwise on "Completed".
 */

import { useState } from "react";
import { WorldBoard } from "./WorldBoard";
import { WaitingList } from "./WaitingList";
import { CompletedList } from "./CompletedList";
import { NewTaskPanel } from "./NewTaskPanel";
import type { CompletedItem, WaitingItem, WorldMember } from "@/lib/agentWorlds";

export type WorldTab = "board" | "waiting" | "completed" | "new";

const TABS: Array<{ id: WorldTab; label: string }> = [
  { id: "board", label: "Board" },
  { id: "waiting", label: "Waiting on you" },
  { id: "completed", label: "Completed" },
  { id: "new", label: "New task" },
];

export function WorldTabs({
  projectId,
  waiting,
  waitingCount,
  completed,
  team,
  onTabChange,
}: {
  projectId: string;
  waiting: WaitingItem[];
  /** The badge — the daemon's count, never below the rows actually listed. */
  waitingCount: number;
  completed: CompletedItem[];
  team: WorldMember[];
  /** The panel widens for the Board; the parent owns the width. */
  onTabChange?: (tab: WorldTab) => void;
}) {
  const [tab, setTab] = useState<WorldTab>(() => (waitingCount > 0 ? "waiting" : "completed"));
  function pick(t: WorldTab) {
    setTab(t);
    onTabChange?.(t);
  }
  return (
    <section data-testid="world-tabs" data-tab={tab} className="card-surface flex min-h-0 flex-col">
      <div
        role="tablist"
        aria-label="Project world"
        className="flex shrink-0 gap-1 overflow-x-auto border-b hairline px-2 pt-2"
      >
        {TABS.map((t) => {
          const active = t.id === tab;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`world-tab-${t.id}`}
              data-testid={`world-tab-${t.id}`}
              aria-selected={active}
              aria-controls={`world-panel-${t.id}`}
              onClick={() => pick(t.id)}
              className={`flex shrink-0 items-center gap-1.5 rounded-t-lg border-b-2 px-2.5 py-1.5 text-[13px] font-medium transition-colors ${
                active
                  ? "border-accent text-zinc-100"
                  : "border-transparent text-zinc-500 hover:text-zinc-200"
              }`}
            >
              {t.label}
              {t.id === "waiting" && waitingCount > 0 && (
                <span
                  data-testid="world-waiting-badge"
                  className="rounded-full border border-amber-400/40 bg-amber-400/15 px-1.5 text-[11px] font-semibold text-amber-300"
                  aria-label={`${waitingCount} waiting on you`}
                >
                  {waitingCount}
                </span>
              )}
            </button>
          );
        })}
      </div>
      <div
        role="tabpanel"
        id={`world-panel-${tab}`}
        aria-labelledby={`world-tab-${tab}`}
        className="min-h-0 flex-1 overflow-y-auto p-3"
      >
        {tab === "board" && <WorldBoard projectId={projectId} />}
        {tab === "waiting" && <WaitingList projectId={projectId} items={waiting} />}
        {tab === "completed" && <CompletedList projectId={projectId} items={completed} />}
        {tab === "new" && <NewTaskPanel projectId={projectId} team={team} />}
      </div>
    </section>
  );
}

export default WorldTabs;
