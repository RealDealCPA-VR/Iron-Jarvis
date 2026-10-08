"use client";

/**
 * The Simple home (v1.318.0): what a person who is not technical sees first.
 *
 * The old Overview put ~30 controls in front of a new user — a reactor hero
 * with disk space and "problems today", thirty module tiles behind three
 * doors, a run-quality card, a shortcuts card — before the one thing they
 * came to do. In Simple mode (the default) the page is, top to bottom:
 *
 *   1. one plain status line (the same words the hero used);
 *   2. the "Ask Jarvis anything" box and the everyday starters — the SAME
 *      component the first-run strip shows (`AskAndStart`), and left out here
 *      while that strip is up so the page never offers two boxes;
 *   3. "Pick up where you left off": the last few chats, one press each;
 *   4. "Where to go": the six places of the Simple menu (lib/hubs.ts) as big
 *      tiles, each saying in one line what it is for;
 *   5. a quiet "Show all modules" for the full grid.
 *
 * Nothing is gone: Advanced shows the full Overview exactly as before, every
 * module is one press away from the menu and from Ctrl K.
 */

import Link from "next/link";
import { ArrowRight, LayoutGrid, MessageSquare } from "lucide-react";
import { AskAndStart } from "@/components/onboarding/FirstRunStrip";
import { Dot } from "@/components/ui";
import { HUBS } from "@/lib/hubs";
import { recordOpen } from "@/lib/appTiles";
import { useApi } from "@/lib/useApi";

interface ThreadRow {
  id: string;
  title?: string;
  updated_at?: string;
}

const RECENT = 3;

/** The places the home's tiles lead to: every hub but Home itself and Help
 *  (Help is in the menu and the title bar). */
const PLACES = HUBS.filter((h) => h.key !== "home" && h.key !== "help");

function ago(iso: string | undefined): string {
  if (!iso) return "";
  const t = Date.parse(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  if (Number.isNaN(t)) return "";
  const min = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  return d === 1 ? "yesterday" : `${d} days ago`;
}

export function HomeStart({
  statusLine,
  healthy,
  showAsk,
  onShowAll,
}: {
  statusLine: string;
  /** The status dot: on when nothing is failing. */
  healthy: boolean;
  /** False while the first-run strip (which carries the same box AND says
   *  what the status line would) is up. */
  showAsk: boolean;
  /** "All modules": reveals the full grid below. */
  onShowAll: () => void;
}) {
  const threads = useApi<{ threads: ThreadRow[] }>("/chat/threads");
  const recent = (threads.data?.threads ?? []).slice(0, RECENT);

  return (
    <section data-testid="home-start" aria-label="Home" className="space-y-5">
      {showAsk && (
        <div role="status" className="flex items-center gap-2 text-sm text-zinc-300">
          <Dot on={healthy} />
          <span>{statusLine}</span>
        </div>
      )}

      {showAsk && <AskAndStart />}

      {recent.length > 0 && (
        <div className="card-surface p-5" data-testid="home-recent">
          <div className="mb-3 text-[13px] font-semibold text-zinc-200">Pick up where you left off</div>
          <ul className="grid gap-2 sm:grid-cols-3">
            {recent.map((t) => (
              <li key={t.id}>
                <Link
                  href={`/chat?thread=${encodeURIComponent(t.id)}`}
                  className="group flex h-full items-start gap-2.5 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 transition-colors hover:border-accent/30 hover:bg-accent/[0.04]"
                >
                  <MessageSquare size={15} className="mt-0.5 shrink-0 text-accent-soft" aria-hidden />
                  <span className="min-w-0">
                    <span className="block truncate text-[13px] font-medium text-zinc-100">
                      {t.title?.trim() || "Untitled chat"}
                    </span>
                    {ago(t.updated_at) && (
                      <span className="block text-[12px] text-zinc-500">{ago(t.updated_at)}</span>
                    )}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div data-testid="home-places">
        <div className="mb-3 text-[13px] font-semibold text-zinc-200">Where to go</div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {PLACES.map((hub) => {
            const Icon = hub.icon;
            return (
              <Link
                key={hub.key}
                href={hub.href}
                data-hub={hub.key}
                onClick={() => recordOpen(hub.href)}
                className="group flex items-start gap-3 rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4 transition-all duration-300 hover:-translate-y-0.5 hover:border-accent/30 hover:bg-accent/[0.04]"
              >
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-accent/20 bg-accent/[0.08] text-accent-soft">
                  <Icon size={19} aria-hidden />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5 text-[15px] font-semibold text-zinc-100">
                    {hub.label}
                    <ArrowRight
                      size={14}
                      className="text-zinc-500 transition-transform group-hover:translate-x-0.5 group-hover:text-accent-soft"
                      aria-hidden
                    />
                  </span>
                  <span className="mt-0.5 block text-[13px] leading-snug text-zinc-400">{hub.blurb}</span>
                </span>
              </Link>
            );
          })}
          {/* The sixth tile: every module, for when the place is not enough. */}
          <button
            type="button"
            data-testid="show-all-modules"
            onClick={onShowAll}
            className="group flex items-start gap-3 rounded-2xl border border-dashed border-white/10 p-4 text-left transition-colors hover:border-accent/30 hover:bg-accent/[0.04]"
          >
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-white/10 text-zinc-400">
              <LayoutGrid size={19} aria-hidden />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[15px] font-semibold text-zinc-200">All modules</span>
              <span className="mt-0.5 block text-[13px] leading-snug text-zinc-500">
                Every tool in Iron Jarvis, by name. Ctrl K finds any of them too.
              </span>
            </span>
          </button>
        </div>
      </div>
    </section>
  );
}
