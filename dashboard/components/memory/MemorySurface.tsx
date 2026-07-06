"use client";

// The ONE memory surface. Three previously separate pages (/memory, /lessons,
// /ltm) render here as labeled scopes so users never have to guess where a
// fact lives. The active scope comes from `?scope=` (working | lessons |
// longterm); the old /lessons and /ltm routes stay alive as thin wrappers
// that preselect their scope. A List ⇄ Graph toggle swaps the scoped lists
// for the all-scopes memory graph (`?view=graph`, persisted in localStorage).

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  BrainCircuit,
  GraduationCap,
  Database,
  List as ListIcon,
  Waypoints,
  type LucideIcon,
} from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { WorkingMemory } from "./WorkingMemory";
import { Lessons } from "./Lessons";
import { LongTerm } from "./LongTerm";
import MemoryGraph from "./MemoryGraph";

export type MemoryScope = "working" | "lessons" | "longterm";
export type MemoryView = "list" | "graph";

interface ScopeDef {
  id: MemoryScope;
  label: string;
  Icon: LucideIcon;
  /** One line: WHAT lives in this scope and WHEN it's used. */
  blurb: string;
}

const SCOPES: ScopeDef[] = [
  {
    id: "working",
    label: "Working",
    Icon: BrainCircuit,
    blurb:
      "Short-lived session, project, and user key-values that agents read mid-run.",
  },
  {
    id: "lessons",
    label: "What I've learned",
    Icon: GraduationCap,
    blurb: "Distilled lessons that get injected into every future run.",
  },
  {
    id: "longterm",
    label: "Long-term",
    Icon: Database,
    blurb:
      "The durable knowledge base — markdown brain, your vault, Notion, or cloud — that agents search on demand.",
  },
];

function isScope(v: string | null): v is MemoryScope {
  return v === "working" || v === "lessons" || v === "longterm";
}

function isView(v: string | null): v is MemoryView {
  return v === "list" || v === "graph";
}

/** localStorage key for the last-chosen view (list | graph). */
const VIEW_KEY = "ironjarvis.memory.view";

const VIEWS: { id: MemoryView; label: string; Icon: LucideIcon }[] = [
  { id: "list", label: "List", Icon: ListIcon },
  { id: "graph", label: "Graph", Icon: Waypoints },
];

/**
 * Public entry point used by /memory, /lessons, and /ltm. The inner component
 * reads `useSearchParams`, which would force the consuming page out of static
 * prerendering unless it sits inside a Suspense boundary — so we own that
 * boundary here (same pattern as NewSessionForm).
 */
export function MemorySurface({
  initialScope = "working",
}: {
  initialScope?: MemoryScope;
}) {
  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Memory"
          subtitle="Everything Iron Jarvis remembers — working notes, learned lessons, and the long-term knowledge base, in one place."
        />
      </Reveal>
      <Suspense fallback={null}>
        <ScopedMemory initialScope={initialScope} />
      </Suspense>
    </PageShell>
  );
}

function ScopedMemory({ initialScope }: { initialScope: MemoryScope }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  // `?scope=` wins (deep links like /memory?scope=longterm); otherwise the
  // route's preselected scope (/lessons -> lessons, /ltm -> longterm).
  const param = searchParams.get("scope");
  const scope: MemoryScope = isScope(param) ? param : initialScope;
  const active = SCOPES.find((s) => s.id === scope) ?? SCOPES[0];

  // View resolution: `?view=` wins (shareable deep links), then the persisted
  // localStorage choice, then List. This component only renders client-side
  // (useSearchParams inside Suspense), so reading localStorage in the lazy
  // initializer is safe — no SSR/hydration mismatch.
  const viewParam = searchParams.get("view");
  const [storedView, setStoredView] = useState<MemoryView | null>(() => {
    if (typeof window === "undefined") return null;
    try {
      const v = window.localStorage.getItem(VIEW_KEY);
      return isView(v) ? v : null;
    } catch {
      return null;
    }
  });
  const view: MemoryView = isView(viewParam) ? viewParam : (storedView ?? "list");

  function switchTo(next: MemoryScope) {
    if (next === scope) return;
    // Shallow-ish client swap: same surface, new query param. Landing on
    // /memory even from the /lessons and /ltm wrappers keeps the URL canonical.
    // Preserve an explicit `?view=` so scope changes never flip the view.
    const v = searchParams.get("view");
    router.replace(`/memory?scope=${next}${isView(v) ? `&view=${v}` : ""}`, {
      scroll: false,
    });
  }

  function switchView(next: MemoryView) {
    if (next === view) return;
    setStoredView(next);
    try {
      window.localStorage.setItem(VIEW_KEY, next);
    } catch {
      /* ignore */
    }
    router.replace(`/memory?scope=${scope}&view=${next}`, { scroll: false });
  }

  return (
    <>
      <Reveal>
        <div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            {view === "list" ? (
              <div
                role="tablist"
                aria-label="Memory scope"
                className="inline-flex max-w-full flex-wrap items-center gap-1 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-1"
              >
                {SCOPES.map((s) => {
                  const selected = s.id === scope;
                  return (
                    <button
                      key={s.id}
                      type="button"
                      role="tab"
                      aria-selected={selected}
                      onClick={() => switchTo(s.id)}
                      title={s.blurb}
                      className={`inline-flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-[13px] font-medium transition-colors ${
                        selected
                          ? "border border-accent/30 bg-accent/[0.12] text-accent-soft"
                          : "border border-transparent text-zinc-400 hover:bg-white/[0.05] hover:text-zinc-200"
                      }`}
                    >
                      <s.Icon size={14} />
                      {s.label}
                    </button>
                  );
                })}
              </div>
            ) : (
              // The graph spans ALL scopes, so the scope tabs hide in graph
              // mode (cleaner than a row of disabled tabs); this chip says why.
              <div className="inline-flex items-center gap-2 rounded-2xl border border-white/[0.07] bg-white/[0.02] px-3 py-2 text-[13px] text-zinc-400">
                <Waypoints size={14} className="shrink-0 text-accent-soft/80" />
                Graph spans all scopes — lessons, working memory, and long-term
                notes together.
              </div>
            )}

            <div
              role="group"
              aria-label="Memory view"
              className="inline-flex items-center gap-1 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-1"
            >
              {VIEWS.map(({ id, label, Icon }) => {
                const selected = id === view;
                return (
                  <button
                    key={id}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => switchView(id)}
                    className={`inline-flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-[13px] font-medium transition-colors ${
                      selected
                        ? "border border-accent/30 bg-accent/[0.12] text-accent-soft"
                        : "border border-transparent text-zinc-400 hover:bg-white/[0.05] hover:text-zinc-200"
                    }`}
                  >
                    <Icon size={14} />
                    {label}
                  </button>
                );
              })}
            </div>
          </div>
          <p className="mt-2 px-1 text-xs text-zinc-500">
            {view === "graph"
              ? "Every remembered item as a node — dashed links are computed similarity, solid cyan links are ones you drew."
              : active.blurb}
          </p>
        </div>
      </Reveal>

      {view === "graph" ? (
        <MemoryGraph />
      ) : scope === "working" ? (
        <WorkingMemory />
      ) : scope === "lessons" ? (
        <Lessons />
      ) : (
        <LongTerm />
      )}
    </>
  );
}
