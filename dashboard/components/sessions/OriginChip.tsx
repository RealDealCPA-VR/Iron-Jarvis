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

const KIND_STYLES: Record<string, string> = {
  schedule: "border-sky-400/30 bg-sky-400/10 text-sky-300",
  comm: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
  job: "border-accent/40 bg-accent/10 text-accent-soft",
  autonomy: "border-amber-400/30 bg-amber-400/10 text-amber-300",
  reflex: "border-violet-400/30 bg-violet-400/10 text-violet-300",
  workflow: "border-fuchsia-400/30 bg-fuchsia-400/10 text-fuchsia-300",
  self_dev: "border-rose-400/30 bg-rose-400/10 text-rose-300",
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
