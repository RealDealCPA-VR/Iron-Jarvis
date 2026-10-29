"use client";

/**
 * THE VIEW SWITCH — one quiet box of plain text tabs with an icon each, the
 * open one in ink (v1.409.0: lifted out of the chat page's Chat | Code
 * switch, components/chat/ChatCodeSurface, so Build's Code | Terminal switch
 * is the SAME component and the two can never drift apart).
 *
 * A real tab list: each tab is a `role="tab"` button naming its panel,
 * arrow keys move to the next tab and choose it. The caller owns the value
 * and what choosing does; the switch only draws and reports. Every id and
 * test id is `<prefix>-…`, so the chat page keeps its exact ids
 * (`chat-view-switch`, `chat-view-<value>`, `chat-view-tab-<value>`,
 * `chat-view-panel-<value>`).
 */

import type { ReactNode } from "react";

export interface ViewSwitchOption<V extends string> {
  value: V;
  label: string;
  icon: ReactNode;
}

export function ViewSwitch<V extends string>({
  views,
  value,
  onChange,
  label,
  prefix,
}: {
  views: readonly ViewSwitchOption<V>[];
  value: V;
  onChange: (next: V) => void;
  /** The tab list's accessible name ("Chat or code"). */
  label: string;
  /** The ids' and test ids' prefix ("chat-view", "build-view"). */
  prefix: string;
}) {
  return (
    <div
      role="tablist"
      aria-label={label}
      data-testid={`${prefix}-switch`}
      className="flex items-center gap-1 rounded-lg bg-white/[0.04] p-0.5"
    >
      {views.map((v, i) => {
        const selected = value === v.value;
        return (
          <button
            key={v.value}
            type="button"
            role="tab"
            id={`${prefix}-tab-${v.value}`}
            aria-selected={selected}
            aria-controls={`${prefix}-panel-${v.value}`}
            tabIndex={selected ? 0 : -1}
            data-testid={`${prefix}-${v.value}`}
            onClick={() => onChange(v.value)}
            onKeyDown={(e) => {
              if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
              e.preventDefault();
              const step = e.key === "ArrowRight" ? 1 : -1;
              const next = views[(i + step + views.length) % views.length].value;
              onChange(next);
              document.querySelector<HTMLButtonElement>(`[data-testid="${prefix}-${next}"]`)?.focus();
            }}
            className={`inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 ${
              selected ? "bg-white/[0.08] font-medium text-zinc-100" : "text-zinc-500 hover:text-zinc-200"
            }`}
          >
            {v.icon}
            {v.label}
          </button>
        );
      })}
    </div>
  );
}

export default ViewSwitch;
