"use client";

/**
 * The Simple menu's tabs (v1.318.0): above a page's title, the other pages of
 * the place it belongs to (lib/hubs.ts) — "Automations: Workflows ·
 * Schedules" — so the next page is one press away without opening the menu.
 *
 * Simple mode only: Advanced keeps today's layout exactly. Rendered by
 * PageHeader, so the full-height screens (Chat, Agents, Build), which have no
 * page header, are untouched — their layouts measure the window and a row
 * above them would push their composer off the bottom.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { HUBS, hubFor, onPage, tabLabel, visibleTabs } from "@/lib/hubs";
import { recordOpen } from "@/lib/appTiles";
import { useAdvancedMode } from "@/lib/uiMode";

export function HubTabs() {
  // The address is read after mount (each page mounts its own header, so a
  // navigation re-reads it): the row is Simple-only and so never part of the
  // server render anyway, and it keeps PageHeader free of router hooks that
  // ~70 page tests mock without.
  const [pathname, setPathname] = useState<string | null>(null);
  useEffect(() => setPathname(window.location.pathname), []);
  const [advanced] = useAdvancedMode();
  if (advanced || pathname === null) return null;
  const found = hubFor(pathname);
  if (!found) return null;
  const tabs = visibleTabs(found.hub, pathname);
  if (tabs.length < 2) return null;
  const Icon = found.hub.icon;
  return (
    <nav
      aria-label={`${found.hub.label} pages`}
      data-testid="hub-tabs"
      data-hub={found.hub.key}
      className="-mb-2 flex min-w-0 items-center gap-1 overflow-x-auto pb-1 text-[13px] [scrollbar-width:none]"
    >
      <span className="mr-1 inline-flex shrink-0 items-center gap-1.5 text-zinc-500">
        <Icon size={14} aria-hidden />
        {found.hub.label}
      </span>
      {tabs.map((tab) => {
        const on = onPage(pathname, tab.href) && tab.href === found.tab.href;
        return (
          <Link
            key={tab.href}
            href={tab.href}
            onClick={() => recordOpen(tab.href)}
            aria-current={on ? "page" : undefined}
            className={`shrink-0 rounded-lg px-2.5 py-1 transition-colors ${
              on
                ? "bg-accent/[0.1] font-medium text-accent-soft"
                : "text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-100"
            }`}
          >
            {tabLabel(tab.href)}
          </Link>
        );
      })}
    </nav>
  );
}

export { HUBS };
