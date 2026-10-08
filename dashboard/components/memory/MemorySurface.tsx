"use client";

// The ONE memory surface. Three previously separate pages (/memory, /lessons,
// /ltm) render here as labeled scopes so users never have to guess where a
// fact lives. The active scope comes from `?scope=` (working | lessons |
// longterm); the old /lessons and /ltm routes stay alive as thin wrappers
// that preselect their scope. A List ⇄ Graph toggle swaps the scoped lists
// for the all-scopes memory graph (`?view=graph`, persisted in localStorage).

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  BrainCircuit,
  Download,
  GraduationCap,
  Database,
  List as ListIcon,
  Waypoints,
  type LucideIcon,
} from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { WorkingMemory } from "./WorkingMemory";
import { KnowsAboutYou } from "./KnowsAboutYou";
import { Lessons } from "./Lessons";
import { LongTerm } from "./LongTerm";
import MemoryGraph from "./MemoryGraph";
import { RecallSearch } from "./RecallSearch";

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
    // v1.316.0: plain words — the old blurb's storage jargon read like a
    // database console to the person this page is for.
    blurb: "Scratch notes agents keep while a job is running.",
  },
  {
    id: "lessons",
    label: "What I've learned",
    Icon: GraduationCap,
    // v1.314.0: true to the daemon — reflections are kept out of every
    // prompt since v1.279.0 (learning/engine.py _PROMPT_EXCLUDED_SOURCES).
    blurb:
      "Preferences and lessons Jarvis reads into chats and runs. Task reflections are kept as notes about past jobs and are not sent to the model.",
  },
  {
    id: "longterm",
    label: "Long-term",
    Icon: Database,
    blurb:
      "Your knowledge bases — the built-in one plus any vault, Notion or drive you connect — that agents search when they need to.",
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
  // v1.316.0: the profile card above the tabs fills in AFTER its fetches, and
  // grows. A deep link's landing scroll made before that would end up too
  // high, so the scoped half re-lands once when the card has settled.
  const [aboveSettled, setAboveSettled] = useState(false);
  const onAboveSettled = useCallback(() => setAboveSettled(true), []);
  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Memory"
          subtitle="Everything Iron Jarvis remembers — working notes, learned lessons, and the long-term knowledge base, in one place."
        />
      </Reveal>
      {/* v1.279.0: the answer to "what do you know about me?" comes first —
          the scopes below are where a person goes to look something up. */}
      <KnowsAboutYou onSettled={onAboveSettled} />
      <Suspense fallback={null}>
        <ScopedMemory initialScope={initialScope} aboveSettled={aboveSettled} />
      </Suspense>
    </PageShell>
  );
}

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
  } catch {
    return false;
  }
}

function ScopedMemory({
  initialScope,
  aboveSettled,
}: {
  initialScope: MemoryScope;
  aboveSettled: boolean;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  // `?scope=` wins (deep links like /memory?scope=longterm); otherwise the
  // route's preselected scope (/lessons -> lessons, /ltm -> longterm).
  const param = searchParams.get("scope");
  const scope: MemoryScope = isScope(param) ? param : initialScope;
  const active = SCOPES.find((s) => s.id === scope) ?? SCOPES[0];

  // v1.316.0 — a deep link LANDS on the part it names. /ltm, /lessons, the
  // Train doorways and the palette's "lessons"/"long-term" all opened on the
  // same top as /memory (profile card + Recall), with the chosen tab ~900px
  // down, so the link looked like it did nothing. Decided ONCE, at mount:
  // a wrapper route (initialScope !== "working") or an explicit ?scope=.
  // A bare /memory opens at the top as before. A tab press also writes
  // ?scope=, which is why this is a ref read at mount and never re-derived.
  // A `?focus=<card>` link (the palette's "Add a memory base", "Import from
  // another AI") lands on that CARD via useFocusRef — the row never fights it.
  const scopesRef = useRef<HTMLDivElement>(null);
  const landing = useRef<"pending" | "landed" | "done">(
    (initialScope !== "working" || isScope(param)) && !searchParams.get("focus") ? "pending" : "done",
  );
  const land = useCallback(() => {
    scopesRef.current?.scrollIntoView?.({
      block: "start",
      behavior: prefersReducedMotion() ? "auto" : "smooth",
    });
  }, []);
  // First landing, as soon as the row exists.
  useEffect(() => {
    if (landing.current !== "pending") return;
    landing.current = "landed";
    land();
  }, [land]);
  // The person took over (scrolled, typed, tapped): never move them again.
  useEffect(() => {
    if (landing.current === "done") return;
    const stop = () => {
      landing.current = "done";
    };
    window.addEventListener("wheel", stop, { passive: true });
    window.addEventListener("touchstart", stop, { passive: true });
    window.addEventListener("keydown", stop);
    return () => {
      window.removeEventListener("wheel", stop);
      window.removeEventListener("touchstart", stop);
      window.removeEventListener("keydown", stop);
    };
  }, []);
  // One re-landing after the card above has filled in (or failed quietly).
  useEffect(() => {
    if (!aboveSettled || landing.current !== "landed") return;
    landing.current = "done";
    land();
  }, [aboveSettled, land]);

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
    // A tab press is the person steering: no later landing may move them.
    landing.current = "done";
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
      {/* Recall spans every store, so it sits ABOVE the scope tabs and shows in
          both list and graph views — "search everything" regardless of scope. */}
      <RecallSearch />

      <Reveal>
        <div>
          {/* v1.316.0: the scope row is the deep-link landing target
              (id + scroll-mt-14 clears the 40px title bar). The Import door
              moved INTO it, beside the view switch — it used to float alone
              between two cards. Imports (ChatGPT / Claude / Takeout) live on
              the Long-term tab; Simple mode hides the /ltm nav item, so this
              is the door from every other tab (v1.232.0). */}
          <div
            id="memory-scopes"
            ref={scopesRef}
            className="flex scroll-mt-14 flex-wrap items-center gap-3"
          >
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

            {view === "list" && scope !== "longterm" && (
              <Link
                href="/memory?scope=longterm"
                className="btn-ghost btn-sm ml-auto"
                data-testid="memory-import-link"
              >
                <Download size={13} aria-hidden className="shrink-0" />
                {/* Short on a phone so the door and List/Graph share a row;
                    the full words (the Handbook's) from sm up. */}
                <span className="sm:hidden">Import from another AI</span>
                <span className="hidden sm:inline">Import from ChatGPT/Claude/Takeout → Long-term memory</span>
              </Link>
            )}
            <div
              role="group"
              aria-label="Memory view"
              className={`inline-flex items-center gap-1 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-1 ${
                view === "list" && scope !== "longterm" ? "" : "ml-auto"
              }`}
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
