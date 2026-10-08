"use client";

/**
 * EVERYTHING (calm UI redesign S6, AUDIT §4.2 / Q16).
 *
 * The one directory of every surface that is not in the sidebar's four items:
 * four columns (Work, Automations, Knowledge, System) plus Setup, each entry
 * with one plain line. It replaces the Simple hubs, the Advanced menu, the
 * Overview's tile grid and its admin drawer — and it is why any surface is two
 * clicks from home (sidebar → Everything → the surface), which T2 measures.
 *
 * The list comes from the surface manifest (lib/surfaces.ts), never a copy.
 * A pin (up to three, Q11) puts an entry under the sidebar's four items.
 */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Pin, PinOff, Search } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { recordOpen } from "@/lib/appTiles";
import {
  EVERYTHING_GROUPS,
  MAX_PINS,
  PINS_EVENT,
  readPins,
  surfacesIn,
  togglePin,
  type Surface,
} from "@/lib/surfaces";

function matches(s: Surface, q: string): boolean {
  if (!q) return true;
  const hay = [s.label, s.blurb, ...s.aliases].join(" ").toLowerCase();
  return q
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((w) => hay.includes(w));
}

export default function EverythingPage() {
  const [q, setQ] = useState("");
  const [pins, setPins] = useState<string[]>([]);
  const [pinNote, setPinNote] = useState("");

  useEffect(() => {
    const sync = () => setPins(readPins());
    sync();
    window.addEventListener(PINS_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(PINS_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const columns = useMemo(
    () =>
      EVERYTHING_GROUPS.map((g) => ({ ...g, items: surfacesIn(g.key).filter((s) => matches(s, q.trim())) })).filter(
        (g) => g.items.length > 0,
      ),
    [q],
  );

  function onPin(s: Surface) {
    if (!togglePin(s.href)) {
      setPinNote(`You can pin ${MAX_PINS}. Unpin one first.`);
      return;
    }
    setPinNote("");
  }

  return (
    <PageShell>
      <PageHeader title="Everything" subtitle="Every part of Iron Jarvis, grouped, each with one line about what it is for." />
      <Reveal>
        <div className="relative isolate max-w-md">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 z-[1] -translate-y-1/2 text-zinc-500" aria-hidden />
          <label htmlFor="everything-filter" className="sr-only">
            Filter
          </label>
          <input
            id="everything-filter"
            data-testid="everything-filter"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Filter…"
            className="field w-full pl-8"
          />
        </div>
        {pinNote && (
          <p role="status" className="mt-2 text-[12px] text-tone-warn">
            {pinNote}
          </p>
        )}
      </Reveal>
      <Reveal>
        {columns.length === 0 ? (
          <p className="text-[13px] text-zinc-400">Nothing matches “{q.trim()}”. Try Ctrl K — it also searches settings and chats.</p>
        ) : (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-6 sm:grid-cols-2 xl:grid-cols-5" data-testid="everything-grid">
            {columns.map((g) => (
              <section key={g.key} aria-labelledby={`everything-${g.key}`} data-testid={`everything-group-${g.key}`}>
                <h2 id={`everything-${g.key}`} className="text-[12px] font-semibold uppercase tracking-wide text-zinc-400">
                  {g.label}
                </h2>
                <p className="mb-2 text-[12px] text-zinc-500">{g.hint}</p>
                <ul className="space-y-1">
                  {g.items.map((s) => {
                    const Icon = s.icon;
                    const pinned = pins.includes(s.href);
                    return (
                      <li key={s.href} className="group/entry flex items-start gap-1">
                        <Link
                          href={s.href}
                          onClick={() => recordOpen(s.href)}
                          data-testid={`everything-link-${s.href.slice(1)}`}
                          className="flex min-w-0 flex-1 items-start gap-2.5 rounded-lg px-2 py-1.5 hover:bg-white/[0.04]"
                        >
                          <Icon size={15} className="mt-0.5 shrink-0 text-accent-soft" aria-hidden />
                          <span className="min-w-0">
                            <span className="block text-[13px] text-zinc-100">{s.label}</span>
                            <span className="block text-[12px] leading-snug text-zinc-500">{s.blurb}</span>
                          </span>
                        </Link>
                        <button
                          type="button"
                          onClick={() => onPin(s)}
                          aria-pressed={pinned}
                          aria-label={pinned ? `Unpin ${s.label} from the sidebar` : `Pin ${s.label} to the sidebar`}
                          title={pinned ? "Unpin from the sidebar" : "Pin to the sidebar"}
                          className={`mt-1 grid h-7 w-7 shrink-0 place-items-center rounded-md text-zinc-500 hover:text-zinc-200 ${
                            pinned ? "text-accent-soft" : "[@media(hover:hover)]:opacity-0 group-hover/entry:opacity-100 focus-visible:opacity-100"
                          }`}
                        >
                          {pinned ? <PinOff size={13} /> : <Pin size={13} />}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>
        )}
      </Reveal>
    </PageShell>
  );
}
