"use client";

// The ONE memory surface. Three previously separate pages (/memory, /lessons,
// /ltm) render here as labeled scopes so users never have to guess where a
// fact lives. The active scope comes from `?scope=` (working | lessons |
// longterm); the old /lessons and /ltm routes stay alive as thin wrappers
// that preselect their scope.

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { BrainCircuit, GraduationCap, Database, type LucideIcon } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { WorkingMemory } from "./WorkingMemory";
import { Lessons } from "./Lessons";
import { LongTerm } from "./LongTerm";

export type MemoryScope = "working" | "lessons" | "longterm";

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

  function switchTo(next: MemoryScope) {
    if (next === scope) return;
    // Shallow-ish client swap: same surface, new query param. Landing on
    // /memory even from the /lessons and /ltm wrappers keeps the URL canonical.
    router.replace(`/memory?scope=${next}`, { scroll: false });
  }

  return (
    <>
      <Reveal>
        <div>
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
          <p className="mt-2 px-1 text-xs text-zinc-500">{active.blurb}</p>
        </div>
      </Reveal>

      {scope === "working" ? (
        <WorkingMemory />
      ) : scope === "lessons" ? (
        <Lessons />
      ) : (
        <LongTerm />
      )}
    </>
  );
}
