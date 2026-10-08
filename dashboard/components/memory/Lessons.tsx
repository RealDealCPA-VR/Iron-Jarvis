"use client";

// "What I've learned" scope of the unified /memory surface: the lessons Jarvis
// reads into chats and runs, plus the ImprovementEngine panel. Moved verbatim
// from the old app/lessons/page.tsx body, with one addition — the "Distill
// now" button (POST /lessons/compact).
//
// v1.314.0 (UX wave 2): the copy matches the daemon. Since v1.279.0 task
// REFLECTIONS are excluded from every prompt (learning/engine.py
// _PROMPT_EXCLUDED_SOURCES), yet this tab said every lesson was "quietly added
// to every future run" — two opposite claims on one page. Reflections now sit
// after the preferences under "Notes about past jobs", each saying it is not
// sent to the model. And the jargon went one click down: "weight 3 · user"
// became a priority word (the number in its title; the default "user" scope
// is not printed), the per-agent and per-lesson tables moved into a closed
// "Details" disclosure, and agents read by name ("Builder").

import { useState } from "react";
import {
  GraduationCap,
  Loader2,
  Star,
  MessageSquare,
  Brain,
  Sparkles,
  Trash2,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";
import { del, post, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import type { Lesson } from "@/lib/types";
import {
  Card,
  Badge,
  Empty,
  OfflineHint,
  SkeletonRows,
  ErrorNote,
  SuccessNote,
  LoaderInline,
  SectionLabel,
  type Tone,
} from "@/components/ui";
import { Reveal } from "@/components/motion";
import { timeAgo } from "@/lib/format";
import { agentLabel } from "@/components/workflow/agents";

/** v1.314.0: reflections are notes about finished jobs — the daemon keeps
 *  them out of every prompt (learning/engine.py _PROMPT_EXCLUDED_SOURCES). */
const isReflection = (source: string | undefined) => source === "reflection";

/** v1.314.0: the injection weight as a word (the number stays in a title).
 *  Lessons are read highest weight first, so a heavier one is likelier to be
 *  among those that fit. */
function priorityWord(weight: number): string {
  return weight >= 3 ? "high priority" : "normal priority";
}

/** v1.314.0: the scope in words. The default "user" scope (everywhere) is
 *  not printed; a project scope says it applies only there. */
function scopeWords(scope: string | undefined | null): string | null {
  if (!scope || scope === "user") return null;
  if (scope.startsWith("project:")) return `only in project ${scope.slice("project:".length)}`;
  return scope;
}

interface SourceMeta {
  label: string;
  tone: Tone;
  Icon: LucideIcon;
  blurb: string;
}

const SOURCE_META: Record<string, SourceMeta> = {
  preference: {
    label: "preference",
    tone: "amber",
    Icon: Star,
    blurb: "something you told me you prefer",
  },
  feedback: {
    label: "feedback",
    tone: "cyan",
    Icon: MessageSquare,
    blurb: "learned from a thumbs up/down",
  },
  reflection: {
    label: "reflection",
    tone: "violet",
    Icon: Brain,
    blurb: "noticed while reflecting on a session",
  },
};

function metaFor(source: string): SourceMeta {
  return (
    SOURCE_META[source] ?? {
      label: source || "lesson",
      tone: "slate",
      Icon: Sparkles,
      blurb: "",
    }
  );
}

/* -------------------------------------------------------------------------- */
/*  ImprovementEngine (GET /improvement + POST /improvement/reflect)           */
/* -------------------------------------------------------------------------- */

interface ImprovementLessonStat {
  lesson_id: string;
  text: string;
  source: string;
  base_weight: number;
  weight_bonus: number;
  effective_weight: number;
  applied_count: number;
  avg_score: number | null;
  success_rate: number | null;
}

interface ImprovementAgentStat {
  agent_type: string;
  sessions: number;
  avg_score: number | null;
  success_rate: number | null;
  /** Late-window minus early-window mean score: >0 improving, <0 regressing. */
  trend: number;
  recent_scores: number[];
}

interface ImprovementStats {
  lessons: ImprovementLessonStat[];
  agents: ImprovementAgentStat[];
  outcomes: { count: number; avg_score?: number; baseline?: number };
}

interface ReflectSuggestion {
  kind?: string;
  target?: string;
  suggestion?: string;
}

interface ReflectResult {
  reviewed: number;
  threshold: number;
  suggestions: ReflectSuggestion[];
  applied: boolean;
}

/** POST /lessons/compact — dedup always; distillation only with a real model. */
interface CompactResult {
  deduped: number;
  distilled: number;
  removed: number;
  /** Honest degradation note, e.g. "no real model connected — deterministic dedup only". */
  note?: string;
}

function fmtScore(v: number | null | undefined): string {
  return typeof v === "number" ? v.toFixed(2) : "—";
}

function fmtRate(v: number | null | undefined): string {
  return typeof v === "number" ? `${Math.round(v * 100)}%` : "—";
}

function TrendLabel({ value }: { value: number }) {
  if (!value) return <span className="text-zinc-500">flat</span>;
  return (
    <span className={value > 0 ? "text-emerald-300" : "text-rose-300"}>
      {value > 0 ? "+" : ""}
      {value.toFixed(2)}
    </span>
  );
}

export function Lessons() {
  const { data, error, loading, reload } = useApi<{ lessons: Lesson[] }>(
    "/lessons?limit=50",
  );

  const offline = error && error.status === 0;
  const lessons = data?.lessons ?? [];

  // The self-improvement engine: outcome stats per lesson/agent + trend.
  const improvement = useApi<ImprovementStats>("/improvement");
  const stats = improvement.data;
  const hasStats =
    !!stats &&
    (stats.outcomes.count > 0 || stats.lessons.length > 0 || stats.agents.length > 0);

  const [reflectBusy, setReflectBusy] = useState(false);
  const [reflectError, setReflectError] = useState<string | null>(null);
  const [reflectResult, setReflectResult] = useState<ReflectResult | null>(null);

  async function reflect() {
    setReflectBusy(true);
    setReflectError(null);
    setReflectResult(null);
    try {
      const r = await post<ReflectResult>("/improvement/reflect?limit=5");
      setReflectResult(r);
      // Reflection can mint/adjust lessons — refresh both surfaces.
      improvement.reload();
      reload();
    } catch (err) {
      // Honest failure: without a connected model this can 5xx — show the detail.
      setReflectError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setReflectBusy(false);
    }
  }

  // Compaction: collapse duplicate auto-captured notes; with a real model
  // connected, distill the remainder into a few short reusable lessons.
  const [compactBusy, setCompactBusy] = useState(false);
  const [compactError, setCompactError] = useState<string | null>(null);
  const [compactResult, setCompactResult] = useState<CompactResult | null>(null);

  async function distillNow() {
    setCompactBusy(true);
    setCompactError(null);
    setCompactResult(null);
    try {
      const r = await post<CompactResult>("/lessons/compact");
      setCompactResult(r);
      reload(); // the pile changed — refresh the list
    } catch (err) {
      // 422 = distillation failed (unusable model reply etc.) — show the detail.
      setCompactError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setCompactBusy(false);
    }
  }

  // The user curates what sticks: forget a lesson -> it stops shaping runs.
  const [deleting, setDeleting] = useState<string | null>(null);
  const [forgetError, setForgetError] = useState<string | null>(null);
  async function forget(id: string) {
    setDeleting(id);
    setForgetError(null);
    try {
      await del(`/lessons/${encodeURIComponent(id)}`);
      reload();
    } catch (err) {
      // v1.226.0: the list must reflect reality EITHER way (a 404 = already
      // gone — refresh drops it); a real daemon error (non-0) is shown, an
      // offline failure stays with the OfflineHint above.
      reload();
      if (!(err instanceof ApiError && err.status === 0)) {
        setForgetError(err instanceof ApiError ? err.message : String(err));
      }
    } finally {
      setDeleting(null);
    }
  }

  // v1.314.0: preferences/feedback first, then the reflections under
  // "Notes about past jobs" (the daemon sends them newest first, mixed).
  const kept = lessons.filter((l) => !isReflection(l.source));
  const notes = lessons.filter((l) => isReflection(l.source));

  function renderLesson(lesson: Lesson, i: number) {
    const m = metaFor(lesson.source);
    const Icon = m.Icon;
    const note = isReflection(lesson.source);
    const where = scopeWords(lesson.scope);
    return (
      <li
        key={lesson.id ?? `${lesson.source}/${i}`}
        className="group flex items-start gap-3.5 rounded-xl border border-white/[0.05] bg-white/[0.02] px-4 py-3.5 transition-colors hover:border-white/[0.1]"
      >
        <span
          className={`mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-xl border ${
            m.tone === "amber"
              ? "border-amber-500/25 bg-amber-500/10 text-amber-300"
              : m.tone === "cyan"
                ? "border-accent/30 bg-accent/10 text-accent-soft"
                : m.tone === "violet"
                  ? "border-violet-500/25 bg-violet-500/10 text-violet-300"
                  : "border-white/10 bg-white/[0.04] text-zinc-400"
          }`}
        >
          <Icon size={17} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[15px] leading-relaxed text-zinc-100">{lesson.text}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-zinc-500">
            <Badge value={m.label} tone={m.tone} />
            {note ? (
              <>
                <span className="text-zinc-600">·</span>
                <span title={`Weight ${lesson.weight} — unused: notes are never put in a prompt`}>
                  not sent to the model
                </span>
              </>
            ) : (
              <>
                <span className="text-zinc-600">·</span>
                <span
                  title={`Weight ${lesson.weight} — lessons are read highest weight first`}
                >
                  {priorityWord(lesson.weight)}
                </span>
              </>
            )}
            {where && (
              <>
                <span className="text-zinc-600">·</span>
                <span title={lesson.scope}>{where}</span>
              </>
            )}
            {lesson.created_at && (
              <>
                <span className="text-zinc-600">·</span>
                <span>{timeAgo(lesson.created_at)}</span>
              </>
            )}
          </div>
        </div>
        {lesson.id && (
          <button
            type="button"
            onClick={() => void forget(lesson.id as string)}
            disabled={deleting === lesson.id}
            title={
              note
                ? "Forget this lesson — removes this note about a past job"
                : "Forget this lesson — it stops shaping your chats and runs"
            }
            className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md text-zinc-600 opacity-0 transition-all hover:bg-rose-500/15 hover:text-rose-300 group-hover:opacity-100 disabled:opacity-50"
          >
            {deleting === lesson.id ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Trash2 size={13} />
            )}
          </button>
        )}
      </li>
    );
  }

  return (
    <>
      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <Reveal>
        <Card
          title={`Lessons${lessons.length ? ` · ${lessons.length}` : ""}`}
          icon={<GraduationCap size={15} />}
          right={
            <button
              type="button"
              onClick={distillNow}
              disabled={compactBusy}
              title="Collapse duplicate auto-captured notes and (with a real model connected) distill them into a few short reusable lessons."
              className="inline-flex items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/[0.08] px-2.5 py-1 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14] disabled:opacity-50"
            >
              {compactBusy ? (
                <LoaderInline label="Distilling…" />
              ) : (
                <>
                  <Sparkles size={13} /> Distill now
                </>
              )}
            </button>
          }
        >
          {(compactError || compactResult) && (
            <div className="mb-3 space-y-2">
              {compactError && <ErrorNote>{compactError}</ErrorNote>}
              {compactResult && (
                <SuccessNote>
                  Compacted — {compactResult.deduped} duplicate
                  {compactResult.deduped === 1 ? "" : "s"} merged,{" "}
                  {compactResult.distilled} distilled, {compactResult.removed}{" "}
                  removed.
                  {compactResult.note ? ` Note: ${compactResult.note}.` : ""}
                </SuccessNote>
              )}
            </div>
          )}
          {forgetError && (
            <div className="mb-3">
              <ErrorNote>Could not forget that lesson: {forgetError}</ErrorNote>
            </div>
          )}
          {loading && !data ? (
            <SkeletonRows rows={4} />
          ) : lessons.length === 0 ? (
            <Empty icon={<GraduationCap size={24} />}>
              Nothing yet — give a session a 👍/👎 and I&apos;ll start learning.
            </Empty>
          ) : (
            <>
              {/* Preferences and lessons first — what reaches the model. */}
              {kept.length > 0 && (
                <ul className="space-y-3">
                  {kept.map((lesson, i) => renderLesson(lesson, i))}
                </ul>
              )}
              {notes.length > 0 && (
                <div className={kept.length > 0 ? "mt-6" : ""}>
                  <SectionLabel>Notes about past jobs</SectionLabel>
                  <p className="mb-3 mt-1 text-[12px] leading-relaxed text-zinc-500">
                    What Jarvis noted after finishing a job. Kept for reference — not sent to the
                    model.
                  </p>
                  <ul className="space-y-3">{notes.map((lesson, i) => renderLesson(lesson, i))}</ul>
                </div>
              )}
            </>
          )}
        </Card>
      </Reveal>

      {/* Improvement engine: outcome stats + on-demand reflection */}
      <Reveal>
        <Card
          title="Improvement"
          icon={<TrendingUp size={15} />}
          right={
            <button
              type="button"
              onClick={reflect}
              disabled={reflectBusy}
              title="Run one model reflection over recent low-scoring sessions — it suggests improvements but applies nothing"
              className="inline-flex items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/[0.08] px-2.5 py-1 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14] disabled:opacity-50"
            >
              {reflectBusy ? (
                <LoaderInline label="Reflecting…" />
              ) : (
                <>
                  <Brain size={13} /> Reflect on recent sessions
                </>
              )}
            </button>
          }
        >
          <div className="space-y-4">
            {reflectError && <ErrorNote>{reflectError}</ErrorNote>}
            {reflectResult && (
              <div className="space-y-2">
                <SuccessNote>
                  Reviewed {reflectResult.reviewed} low-scoring session
                  {reflectResult.reviewed === 1 ? "" : "s"} (score ≤{" "}
                  {reflectResult.threshold}) — {reflectResult.suggestions.length}{" "}
                  suggestion{reflectResult.suggestions.length === 1 ? "" : "s"}.
                  Nothing was applied automatically.
                </SuccessNote>
                {reflectResult.suggestions.length > 0 && (
                  <ul className="space-y-2">
                    {reflectResult.suggestions.map((sug, i) => (
                      <li
                        key={i}
                        className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-2.5 text-sm text-zinc-300"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge value={sug.kind || "suggestion"} tone="violet" />
                          {sug.target && (
                            <span className="text-[11px] text-zinc-500">{sug.target}</span>
                          )}
                        </div>
                        {sug.suggestion && (
                          <p className="mt-1.5 leading-relaxed">{sug.suggestion}</p>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {improvement.loading && !improvement.data ? (
              <SkeletonRows rows={3} />
            ) : improvement.error && improvement.error.status !== 0 ? (
              // e.g. 503 "improvement engine unavailable" — degrade quietly.
              <p className="text-sm text-zinc-500">
                Improvement stats aren&apos;t available: {improvement.error.message}
              </p>
            ) : stats && hasStats ? (
              <>
                {/* Outcome summary — v1.314.0: plain words; the baseline and
                    the tables are one click down. */}
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs text-zinc-400">
                  <span>
                    <span className="font-semibold text-zinc-200">
                      {stats.outcomes.count}
                    </span>{" "}
                    rated task{stats.outcomes.count === 1 ? "" : "s"}
                  </span>
                  {typeof stats.outcomes.avg_score === "number" && (
                    <>
                      <span className="text-zinc-600">·</span>
                      <span>
                        average rating{" "}
                        <span className="font-semibold text-zinc-200">
                          {fmtScore(stats.outcomes.avg_score)}
                        </span>
                      </span>
                    </>
                  )}
                </div>

                {(stats.agents.length > 0 ||
                  stats.lessons.length > 0 ||
                  typeof stats.outcomes.baseline === "number") && (
                  <details className="group rounded-xl border border-white/[0.05] bg-white/[0.015] px-3 py-2.5">
                    <summary className="cursor-pointer select-none text-[12px] text-zinc-400 hover:text-zinc-200">
                      Details — by agent and by lesson
                    </summary>
                    <div className="mt-3 space-y-4">
                      {typeof stats.outcomes.baseline === "number" && (
                        <p className="text-xs text-zinc-500">
                          Average score {fmtScore(stats.outcomes.avg_score)} · baseline{" "}
                          {fmtScore(stats.outcomes.baseline)}
                        </p>
                      )}

                      {/* Per-agent outcome stats */}
                      {stats.agents.length > 0 && (
                        <div>
                          <div className="mb-2 text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                            By agent
                          </div>
                          <div className="space-y-1.5">
                            {stats.agents.map((a) => (
                              <div
                                key={a.agent_type}
                                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-white/[0.05] bg-white/[0.015] px-3 py-2 text-xs"
                              >
                                <span className="font-medium text-zinc-200" title={a.agent_type}>
                                  {agentLabel(a.agent_type)}
                                </span>
                                <span className="flex flex-wrap items-center gap-3 text-zinc-500">
                                  <span>
                                    {a.sessions} session{a.sessions === 1 ? "" : "s"}
                                  </span>
                                  <span>avg {fmtScore(a.avg_score)}</span>
                                  <span>success {fmtRate(a.success_rate)}</span>
                                  <span title="Late-window minus early-window mean score">
                                    trend <TrendLabel value={a.trend} />
                                  </span>
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Per-lesson weights + outcomes (most consequential first) */}
                      {stats.lessons.length > 0 && (
                        <div>
                          <div className="mb-2 text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                            Lesson weights
                            {stats.lessons.length > 6 ? " · top 6" : ""}
                          </div>
                          <div className="space-y-1.5">
                            {stats.lessons.slice(0, 6).map((l) => (
                              <div
                                key={l.lesson_id}
                                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-white/[0.05] bg-white/[0.015] px-3 py-2 text-xs"
                              >
                                <span
                                  className="min-w-0 flex-1 truncate text-zinc-300"
                                  title={l.text}
                                >
                                  {l.text}
                                </span>
                                <span className="flex shrink-0 flex-wrap items-center gap-3 text-zinc-500">
                                  <span title={`base ${l.base_weight} + earned ${l.weight_bonus}`}>
                                    weight{" "}
                                    <span className="font-semibold text-zinc-200">
                                      {l.effective_weight}
                                    </span>
                                    {l.weight_bonus !== 0 && (
                                      <span
                                        className={
                                          l.weight_bonus > 0 ? "text-emerald-300" : "text-rose-300"
                                        }
                                      >
                                        {" "}
                                        ({l.weight_bonus > 0 ? "+" : ""}
                                        {l.weight_bonus})
                                      </span>
                                    )}
                                  </span>
                                  {/* v1.314.0: a reflection is never put in a
                                      prompt (since v1.279.0), so a use count
                                      beside it would read as if it were. */}
                                  {isReflection(l.source) ? (
                                    <span>not sent to the model</span>
                                  ) : (
                                    <span>applied {l.applied_count}×</span>
                                  )}
                                  <span>avg {fmtScore(l.avg_score)}</span>
                                  <span>success {fmtRate(l.success_rate)}</span>
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </details>
                )}
              </>
            ) : (
              <Empty icon={<TrendingUp size={24} />}>
                No outcome data yet — finish (and rate) a few sessions and
                per-lesson / per-agent stats will show up here.
              </Empty>
            )}
          </div>
        </Card>
      </Reveal>

      {lessons.length > 0 && (
        <Reveal>
          <div className="flex items-center gap-2 px-1 text-xs text-zinc-600">
            <Sparkles size={13} className="text-accent-soft/60" />
            {/* v1.314.0: true to the daemon — the top lessons by weight are
                read in (not every one), and reflections never are. */}
            Preferences and lessons are read into your chats and runs, most important first, so I
            keep getting closer to how you like things done. Notes about past jobs are not sent to
            the model.
          </div>
        </Reveal>
      )}
    </>
  );
}
