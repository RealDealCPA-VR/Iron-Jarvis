"use client";

/**
 * OriginChip — who started this session (v1.168.0).
 *
 * `Session.origin` has been indexed and serialized since TX-01 precisely to
 * answer "did I start this, or did it start itself?" — and until this chip it
 * was rendered nowhere (the honest answer required opening SQLite). Kinds are
 * the prefix before the first ":" (e.g. `schedule:nightly-brief` → schedule);
 * the suffix is shown as the label detail when present.
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
      className={`inline-flex max-w-[12rem] items-center truncate rounded-full border px-1.5 py-[1px] font-mono text-[10px] leading-4 ${style} ${className}`}
    >
      {value}
    </span>
  );
}
