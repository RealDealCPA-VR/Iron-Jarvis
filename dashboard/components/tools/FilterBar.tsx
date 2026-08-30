"use client";

/**
 * Find a tool the way people look for one (v1.216.0).
 *
 * From the review: "Ctrl+K search in the title bar is good. On this page it
 * should filter in place and support: Added / not added · Built-in vs
 * extension · Runtime ready vs blocked · Capability: files, web, git, memory,
 * system. Without filters, this page dies as soon as you have 30 plugins."
 *
 * The title bar's Ctrl+K is the app's global palette — it navigates, it does
 * not filter a grid — so this is a separate, in-page control and its
 * placeholder says which one it is ("Filter tools and extensions"), which is
 * the review's "if both exist, sync them; if only one, put helper text".
 *
 * EVERY FILTER IS A TOGGLE, not a select: the questions are independent
 * ("added" AND "writes files") and a row of chips shows the current answer
 * without being opened.
 */

import { Search, X } from "lucide-react";
import { CAPABILITY_CHIP, type Capability } from "./meta";

export type StatusFilter = "all" | "added" | "available";
export type KindFilter = "all" | "builtin" | "extension";

export interface ToolFilters {
  q: string;
  status: StatusFilter;
  kind: KindFilter;
  /** Empty = no capability filter. */
  caps: Capability[];
  /** Hide extensions whose runtime this build cannot promise is present. */
  readyOnly: boolean;
}

export const EMPTY_FILTERS: ToolFilters = {
  q: "",
  status: "all",
  kind: "all",
  caps: [],
  readyOnly: false,
};

export function filtersActive(f: ToolFilters): boolean {
  return (
    f.q.trim() !== "" ||
    f.status !== "all" ||
    f.kind !== "all" ||
    f.caps.length > 0 ||
    f.readyOnly
  );
}

function Chip({
  on,
  onClick,
  children,
  testId,
  title,
}: {
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
  testId?: string;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      title={title}
      data-testid={testId}
      className={`rounded-lg border px-2.5 py-1 text-[11.5px] font-medium transition-colors ${
        on
          ? "border-accent/40 bg-accent/[0.12] text-accent-soft"
          : "border-white/[0.07] bg-white/[0.02] text-zinc-400 hover:border-white/20 hover:text-zinc-200"
      }`}
    >
      {children}
    </button>
  );
}

const CAP_FILTERS: Capability[] = ["read", "write", "network", "browser", "system"];

export function FilterBar({
  value,
  onChange,
  counts,
}: {
  value: ToolFilters;
  onChange: (next: ToolFilters) => void;
  /** What the current filter leaves visible — a filter that hides everything
   *  must say so, not look like an empty catalog. */
  counts: { builtin: number; extension: number };
}) {
  const set = (patch: Partial<ToolFilters>) => onChange({ ...value, ...patch });
  const toggleCap = (c: Capability) =>
    set({
      caps: value.caps.includes(c)
        ? value.caps.filter((x) => x !== c)
        : [...value.caps, c],
    });

  return (
    <div className="space-y-2.5" data-testid="tool-filters">
      <div className="relative">
        <Search
          size={14}
          aria-hidden
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500"
        />
        <input
          value={value.q}
          onChange={(e) => set({ q: e.target.value })}
          aria-label="Filter tools and extensions"
          placeholder="Filter tools and extensions…"
          data-testid="tool-search"
          className="field w-full py-2 pl-9 pr-8 text-[13px]"
        />
        {value.q && (
          <button
            type="button"
            onClick={() => set({ q: "" })}
            aria-label="Clear the filter text"
            className="absolute right-2.5 top-1/2 grid h-5 w-5 -translate-y-1/2 place-items-center rounded text-zinc-500 hover:text-zinc-200"
          >
            <X size={13} />
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <Chip
          on={value.status === "added"}
          onClick={() => set({ status: value.status === "added" ? "all" : "added" })}
          testId="filter-added"
        >
          Enabled
        </Chip>
        <Chip
          on={value.status === "available"}
          onClick={() =>
            set({ status: value.status === "available" ? "all" : "available" })
          }
          testId="filter-available"
        >
          Not added
        </Chip>
        <span className="mx-1 h-4 w-px bg-white/10" aria-hidden />
        <Chip
          on={value.kind === "builtin"}
          onClick={() => set({ kind: value.kind === "builtin" ? "all" : "builtin" })}
          testId="filter-builtin"
        >
          Built-in
        </Chip>
        <Chip
          on={value.kind === "extension"}
          onClick={() =>
            set({ kind: value.kind === "extension" ? "all" : "extension" })
          }
          testId="filter-extension"
        >
          Extensions
        </Chip>
        <span className="mx-1 h-4 w-px bg-white/10" aria-hidden />
        <Chip
          on={value.readyOnly}
          onClick={() => set({ readyOnly: !value.readyOnly })}
          title="Hide extensions that need a runtime you may not have"
          testId="filter-ready"
        >
          No runtime needed
        </Chip>
        {CAP_FILTERS.map((c) => (
          <Chip
            key={c}
            on={value.caps.includes(c)}
            onClick={() => toggleCap(c)}
            testId={`filter-cap-${c}`}
          >
            {CAPABILITY_CHIP[c]}
          </Chip>
        ))}
        {filtersActive(value) && (
          <button
            type="button"
            onClick={() => onChange(EMPTY_FILTERS)}
            data-testid="filter-clear"
            className="ml-1 rounded-lg px-2 py-1 text-[11.5px] text-zinc-500 underline-offset-2 hover:text-zinc-200 hover:underline"
          >
            Clear
          </button>
        )}
        <span className="ml-auto shrink-0 text-[11.5px] tabular-nums text-zinc-500">
          {counts.builtin} built-in · {counts.extension} extension
          {counts.extension === 1 ? "" : "s"}
        </span>
      </div>
    </div>
  );
}
