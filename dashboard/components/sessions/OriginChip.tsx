"use client";

/**
 * OriginChip — who started this session (v1.168.0).
 *
 * `Session.origin` has been indexed and serialized since TX-01 precisely to
 * answer "did I start this, or did it start itself?" — and until this chip it
 * was rendered nowhere (the honest answer required opening SQLite). Kinds are
 * the prefix before the first ":" (e.g. `schedule:nightly-brief` → schedule);
 * the suffix is shown as the label detail when present. Since v1.314.0 the
 * chip READS in plain words (`originLabel`) and keeps the raw value in its
 * title and `data-origin`.
 *
 * An absent origin renders NOTHING: historically most user-started lanes pass
 * no origin, so an explicit "user" chip would be a guess, not a fact.
 */

// Kinds read through the shared TONE TOKENS (v1.313.0) instead of a private
// palette, so each chip is pale on the dark Marks (as before) and a deep,
// readable ink on Daylight / Liquid Glass. Workflow keeps fuchsia (no token
// means "workflow"); the light override block in globals.css inks it.
const KIND_STYLES: Record<string, string> = {
  schedule: "border-tone-info/30 bg-tone-info/10 text-tone-info",
  comm: "border-tone-success/30 bg-tone-success/10 text-tone-success",
  job: "border-accent/40 bg-accent/10 text-accent-soft",
  autonomy: "border-tone-warn/30 bg-tone-warn/10 text-tone-warn",
  reflex: "border-tone-violet/30 bg-tone-violet/10 text-tone-violet",
  workflow: "border-fuchsia-400/30 bg-fuchsia-400/10 text-fuchsia-300",
  self_dev: "border-tone-danger/30 bg-tone-danger/10 text-tone-danger",
  "memory-review": "border-zinc-400/30 bg-zinc-400/10 text-zinc-300",
  continuation: "border-zinc-400/30 bg-zinc-400/10 text-zinc-300",
  rerun: "border-zinc-400/30 bg-zinc-400/10 text-zinc-300",
};

const FALLBACK_STYLE = "border-zinc-400/30 bg-zinc-400/10 text-zinc-300";

export function originKind(origin: string): string {
  const i = origin.indexOf(":");
  return (i >= 0 ? origin.slice(0, i) : origin).trim();
}

/** v1.314.0 (UX wave 2): whole origins that have their own words. */
const EXACT_WORDS: Record<string, string> = {
  "job:mission": "Mission",
  "job:mission-member": "Mission teammate",
  "job:agents": "Agents page",
  self_dev: "Self-development",
  "memory-review": "Memory review",
  continuation: "Follow-up",
  rerun: "Re-run",
  autonomy: "Autonomy",
  api: "API",
  manual: "Started by you",
};

/** Kinds whose suffix is a NAME worth showing ("Schedule · nightly-brief").
 *  Kinds whose suffix is an internal id (assignment, project, goal, workflow
 *  run ids) show the kind word alone — the id stays in the title. */
const KIND_WORDS: Record<string, { word: string; showName: boolean }> = {
  schedule: { word: "Schedule", showName: true },
  reflex: { word: "Reflex", showName: true },
  comm: { word: "Message", showName: true },
  workflow: { word: "Workflow", showName: false },
  goal: { word: "Goal", showName: false },
  assignment: { word: "Queued job", showName: false },
  project: { word: "Project task", showName: false },
  job: { word: "Job", showName: true },
  sentinel: { word: "Sentinel", showName: true },
};

/**
 * The plain words for a session's origin (v1.314.0). The RAW origin is a
 * record of what started the run, so every surface keeps it in `title`; this
 * is only what the eye reads. An origin nobody has words for is shown as it
 * is — an invented label would be a guess, not a fact.
 */
export function originLabel(origin: string | null | undefined): string {
  const value = (origin || "").trim();
  if (!value) return "";
  if (EXACT_WORDS[value]) return EXACT_WORDS[value];
  const kind = originKind(value);
  const rule = KIND_WORDS[kind];
  if (!rule) return value;
  const name = value.slice(kind.length + 1).trim();
  return rule.showName && name ? `${rule.word} · ${name}` : rule.word;
}

export default function OriginChip({
  origin,
  className = "",
}: {
  origin?: string | null;
  className?: string;
}) {
  const value = (origin || "").trim();
  if (!value) return null;
  const kind = originKind(value);
  const style = KIND_STYLES[kind] ?? FALLBACK_STYLE;
  return (
    <span
      title={`Started by: ${value}`}
      data-testid="origin-chip"
      data-origin={value}
      className={`inline-flex max-w-[12rem] items-center truncate rounded-full border px-1.5 py-[1px] text-[10px] leading-4 ${style} ${className}`}
    >
      {originLabel(value)}
    </span>
  );
}
