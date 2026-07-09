"use client";

// Unified "Recall" search — the Memory Fabric surfaced as ONE box that queries
// every memory store at once (files, notes, working memory, projects, lessons,
// past runs) and shows ranked, source-tagged results. It sits ABOVE the scope
// tabs on the Memory surface, so it reads as "search everything" no matter
// which scope is selected. Backed by GET /memory/recall.

import { useState } from "react";
import {
  Search,
  Sparkles,
  FileText,
  NotebookPen,
  BrainCircuit,
  Boxes,
  GraduationCap,
  History,
  type LucideIcon,
} from "lucide-react";
import { get, ApiError } from "@/lib/api";
import { Card, Empty, ErrorNote, OfflineHint, LoaderInline } from "@/components/ui";
import { Reveal } from "@/components/motion";
import { VoiceInput, appendDictation } from "@/components/VoiceInput";
import { pct } from "@/lib/format";

/** The federated stores the recall endpoint spans. */
type Source =
  | "files"
  | "notes"
  | "memory"
  | "knowledge"
  | "lessons"
  | "sessions";

const SOURCES: Source[] = [
  "files",
  "notes",
  "memory",
  "knowledge",
  "lessons",
  "sessions",
];

/** Per-source presentation: a friendly label, a distinct accent, an icon, and
 *  a singular/plural pair for the summary line. Each accent is picked to read
 *  at a glance while staying inside the arc-reactor-cyan dark palette. */
const SOURCE_META: Record<
  Source,
  { label: string; one: string; many: string; Icon: LucideIcon; pill: string; bar: string }
> = {
  files: {
    label: "File",
    one: "file",
    many: "files",
    Icon: FileText,
    pill: "border-accent/30 bg-accent/10 text-accent-soft",
    bar: "bg-accent/70",
  },
  notes: {
    label: "Note",
    one: "note",
    many: "notes",
    Icon: NotebookPen,
    pill: "border-violet-500/25 bg-violet-500/10 text-violet-300",
    bar: "bg-violet-400/70",
  },
  memory: {
    label: "Memory",
    one: "memory",
    many: "memories",
    Icon: BrainCircuit,
    pill: "border-emerald-500/25 bg-emerald-500/10 text-emerald-300",
    bar: "bg-emerald-400/70",
  },
  knowledge: {
    label: "Project",
    one: "project",
    many: "projects",
    Icon: Boxes,
    pill: "border-sky-500/25 bg-sky-500/10 text-sky-300",
    bar: "bg-sky-400/70",
  },
  lessons: {
    label: "Lesson",
    one: "lesson",
    many: "lessons",
    Icon: GraduationCap,
    pill: "border-amber-500/25 bg-amber-500/10 text-amber-300",
    bar: "bg-amber-400/70",
  },
  sessions: {
    label: "Past run",
    one: "past run",
    many: "past runs",
    Icon: History,
    pill: "border-zinc-500/25 bg-zinc-500/10 text-zinc-300",
    bar: "bg-zinc-400/70",
  },
};

function isSource(v: string): v is Source {
  return (SOURCES as string[]).includes(v);
}

interface RecallItem {
  source: Source;
  ref: string;
  title?: string;
  snippet?: string;
  score?: number;
  // Source-specific extras the endpoint may attach — surfaced when present.
  path?: string;
  line?: number;
  layer?: string;
  scope?: string;
  status?: string;
  kind?: string;
}

interface RecallResponse {
  results: RecallItem[];
  by_source: Partial<Record<Source, number>>;
  count: number;
  query: string;
}

/** "7 across 3 stores: 3 files · 1 lesson · 3 notes" */
function summaryLine(by: Partial<Record<Source, number>>, count: number): string {
  const parts = SOURCES.filter((s) => (by[s] ?? 0) > 0).map((s) => {
    const n = by[s] ?? 0;
    const meta = SOURCE_META[s];
    return `${n} ${n === 1 ? meta.one : meta.many}`;
  });
  const stores = parts.length;
  const head = `${count} ${count === 1 ? "result" : "results"} across ${stores} ${
    stores === 1 ? "store" : "stores"
  }`;
  return parts.length ? `${head}: ${parts.join(" · ")}` : head;
}

/** Clamp a 0..1 (or 0..100) score to a bar width percentage. */
function barWidth(score: number | undefined): number {
  if (score === undefined || Number.isNaN(score)) return 0;
  const n = score <= 1 ? score * 100 : score;
  return Math.max(0, Math.min(100, n));
}

export function RecallSearch() {
  const [q, setQ] = useState("");
  // The set of enabled stores. All on by default; toggling a chip narrows the
  // federation and passes the remaining set as the `sources` csv.
  const [enabled, setEnabled] = useState<Set<Source>>(() => new Set(SOURCES));
  const [data, setData] = useState<RecallResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);

  function toggleSource(s: Source) {
    setEnabled((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });
  }

  async function search(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim() || enabled.size === 0) return;
    setBusy(true);
    setError(null);
    setOffline(false);
    try {
      const params = new URLSearchParams({ q: q.trim(), k: "12" });
      // Only send `sources` when the user has narrowed the federation; sending
      // the full set is equivalent to omitting it (search everything).
      if (enabled.size < SOURCES.length) {
        params.set("sources", SOURCES.filter((s) => enabled.has(s)).join(","));
      }
      const res = await get<RecallResponse>(`/memory/recall?${params.toString()}`);
      // Guard against a store the UI doesn't know about — render only known ones.
      setData({
        ...res,
        results: (res.results ?? []).filter((r) => isSource(r.source)),
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) setOffline(true);
      else setError(err instanceof ApiError ? err.message : String(err));
      setData(null);
    } finally {
      setBusy(false);
    }
  }

  const results = data?.results ?? null;

  return (
    <Reveal>
      {offline && (
        <div className="mb-4">
          <OfflineHint />
        </div>
      )}
      <Card
        title="Recall"
        icon={<Sparkles size={15} />}
        right={
          <span className="hidden text-[11px] text-zinc-500 sm:inline">
            Search every memory store at once
          </span>
        }
      >
        <form onSubmit={search} className="flex flex-wrap items-end gap-3">
          <div className="min-w-[240px] flex-1">
            <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
              Recall anything
            </label>
            <div className="relative">
              <Search
                size={15}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-600"
              />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search files, notes, memory, projects, lessons & past runs… or dictate"
                aria-label="Recall search query"
                className="field pl-9 pr-12"
              />
              <div className="absolute right-1.5 top-1/2 -translate-y-1/2">
                <VoiceInput
                  size="sm"
                  onTranscript={(chunk) => setQ((p) => appendDictation(p, chunk))}
                />
              </div>
            </div>
          </div>
          <button
            type="submit"
            disabled={busy || !q.trim() || enabled.size === 0}
            className="btn-accent"
          >
            {busy ? <LoaderInline label="Recalling…" /> : "Recall"}
          </button>
        </form>

        {/* Store filter chips — narrow the federation. */}
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-[11px] uppercase tracking-[0.1em] text-zinc-500">
            Stores
          </span>
          {SOURCES.map((s) => {
            const meta = SOURCE_META[s];
            const on = enabled.has(s);
            return (
              <button
                key={s}
                type="button"
                aria-pressed={on}
                onClick={() => toggleSource(s)}
                className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition-colors ${
                  on
                    ? meta.pill
                    : "border-white/[0.06] bg-transparent text-zinc-600 hover:text-zinc-400"
                }`}
              >
                <meta.Icon size={11} />
                {meta.label}
              </button>
            );
          })}
        </div>

        {error && (
          <div className="mt-3">
            <ErrorNote>{error}</ErrorNote>
          </div>
        )}

        {/* Summary line */}
        {data && (
          <p className="mt-3 text-xs text-zinc-500">{summaryLine(data.by_source, data.count)}</p>
        )}

        {/* Results / states */}
        <div className="mt-3">
          {busy ? null : results === null ? (
            <Empty icon={<Sparkles size={22} />}>
              One search across everything Iron Jarvis remembers — files, notes,
              working memory, projects, lessons, and past runs.
            </Empty>
          ) : results.length === 0 ? (
            <Empty icon={<Search size={22} />}>Nothing in memory matches that yet.</Empty>
          ) : (
            <ul className="space-y-2.5">
              {results.map((r, i) => {
                const meta = SOURCE_META[r.source];
                const heading = r.title?.trim() || r.ref;
                const showRef = Boolean(r.title?.trim()) && Boolean(r.ref);
                return (
                  <li
                    key={`${r.source}/${r.ref}/${i}`}
                    className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-3.5 py-3"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span
                            className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium ${meta.pill}`}
                          >
                            <meta.Icon size={10} />
                            {meta.label}
                          </span>
                          <span className="truncate text-sm font-semibold text-zinc-100">
                            {heading}
                          </span>
                        </div>
                        {r.snippet && (
                          <p className="mt-1 line-clamp-3 text-sm text-zinc-400">{r.snippet}</p>
                        )}
                        {showRef && (
                          <div className="mt-1.5 truncate font-mono text-[11px] text-zinc-600">
                            {r.path ? `${r.path}${r.line ? `:${r.line}` : ""}` : r.ref}
                          </div>
                        )}
                      </div>
                      {r.score !== undefined && (
                        <div className="shrink-0 text-right">
                          <div className="font-mono text-[11px] text-accent-soft">{pct(r.score)}</div>
                          <div
                            className="mt-1 h-1 w-14 overflow-hidden rounded-full bg-white/[0.06]"
                            aria-hidden="true"
                          >
                            <div
                              className={`h-full rounded-full ${meta.bar}`}
                              style={{ width: `${barWidth(r.score)}%` }}
                            />
                          </div>
                        </div>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </Card>
    </Reveal>
  );
}
