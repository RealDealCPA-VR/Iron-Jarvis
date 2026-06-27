"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import {
  CircleCheck,
  CircleX,
  Clock,
  CircleDot,
  LoaderCircle,
  TriangleAlert,
  ServerCrash,
  ArrowRight,
  MoonStar,
} from "lucide-react";

/* -------------------------------------------------------------------------- */
/*  Card                                                                       */
/* -------------------------------------------------------------------------- */

export function Card({
  title,
  icon,
  right,
  children,
  className = "",
  hover = false,
  pad = true,
}: {
  title?: ReactNode;
  icon?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  hover?: boolean;
  pad?: boolean;
}) {
  return (
    <section
      className={`card-surface transition-all duration-300 ${
        hover ? "hover:-translate-y-0.5 hover:shadow-card-hover" : ""
      } ${className}`}
    >
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 border-b hairline px-5 py-3.5">
          <h2 className="flex items-center gap-2 text-[13px] font-semibold tracking-wide text-zinc-200">
            {icon && <span className="text-accent-soft/80">{icon}</span>}
            {title}
          </h2>
          {right}
        </header>
      )}
      <div className={pad ? "p-5" : ""}>{children}</div>
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/*  Stat tile                                                                  */
/* -------------------------------------------------------------------------- */

export function Stat({
  label,
  value,
  sub,
  icon,
  accent = false,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  icon?: ReactNode;
  accent?: boolean;
}) {
  return (
    <div className="card-surface group relative overflow-hidden px-5 py-4 transition-all duration-300 hover:-translate-y-0.5 hover:shadow-card-hover">
      <div
        className={`pointer-events-none absolute -right-6 -top-8 h-24 w-24 rounded-full blur-2xl transition-opacity duration-300 ${
          accent
            ? "bg-accent/20 opacity-100"
            : "bg-accent/10 opacity-0 group-hover:opacity-100"
        }`}
      />
      <div className="flex items-center justify-between">
        <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-500">
          {label}
        </div>
        {icon && <span className="text-accent-soft/70">{icon}</span>}
      </div>
      <div className="mt-2 text-3xl font-semibold tracking-tight text-zinc-50">
        {value}
      </div>
      {sub && <div className="mt-1 text-xs text-zinc-500">{sub}</div>}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Status helpers                                                             */
/* -------------------------------------------------------------------------- */

export type Tone = "green" | "amber" | "red" | "cyan" | "slate" | "violet";

const TONE_BADGE: Record<Tone, string> = {
  green: "bg-emerald-500/10 text-emerald-300 border-emerald-500/25",
  amber: "bg-amber-500/10 text-amber-300 border-amber-500/25",
  red: "bg-rose-500/10 text-rose-300 border-rose-500/25",
  cyan: "bg-accent/10 text-accent-soft border-accent/30",
  violet: "bg-violet-500/10 text-violet-300 border-violet-500/25",
  slate: "bg-zinc-500/10 text-zinc-300 border-zinc-500/25",
};

const TONE_DOT: Record<Tone, string> = {
  green: "bg-emerald-400 shadow-[0_0_8px_2px_rgba(52,211,153,0.5)]",
  amber: "bg-amber-400 shadow-[0_0_8px_2px_rgba(251,191,36,0.5)]",
  red: "bg-rose-400 shadow-[0_0_8px_2px_rgba(251,113,133,0.5)]",
  cyan: "bg-accent shadow-[0_0_8px_2px_rgba(34,211,238,0.55)]",
  violet: "bg-violet-400 shadow-[0_0_8px_2px_rgba(167,139,250,0.5)]",
  slate: "bg-zinc-500",
};

const STATUS_TONE: Record<string, Tone> = {
  ok: "green",
  completed: "green",
  succeeded: "green",
  success: "green",
  allow: "green",
  low: "green",
  "logged in": "green",
  active: "cyan",
  running: "cyan",
  pending: "amber",
  created: "slate",
  ask: "amber",
  medium: "amber",
  "in review": "amber",
  review: "amber",
  failed: "red",
  error: "red",
  rejected: "red",
  denied: "red",
  deny: "red",
  high: "red",
  "logged out": "slate",
  idle: "slate",
  tool: "violet",
};

export function statusTone(value: string | null | undefined): Tone {
  if (!value) return "slate";
  return STATUS_TONE[value.toLowerCase()] ?? "slate";
}

/** Whether a status represents in-flight work (gets a live pulse). */
function isLive(value: string | null | undefined): boolean {
  const v = (value ?? "").toLowerCase();
  return v === "active" || v === "running" || v === "pending";
}

export function Badge({ value, tone }: { value: string; tone?: Tone }) {
  const t = tone ?? statusTone(value);
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium capitalize ${TONE_BADGE[t]}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${TONE_DOT[t]}`} />
      {value}
    </span>
  );
}

/** A status dot, optionally pulsing for live states. */
export function StatusDot({
  status,
  className = "",
}: {
  status?: string;
  className?: string;
}) {
  const t = statusTone(status);
  return (
    <span
      className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${TONE_DOT[t]} ${
        isLive(status) ? "animate-pulse-glow" : ""
      } ${className}`}
    />
  );
}

/** Simple on/off connectivity dot. */
export function Dot({ on }: { on: boolean }) {
  return (
    <span
      className={`inline-block h-2.5 w-2.5 rounded-full ${
        on
          ? "bg-emerald-400 shadow-[0_0_8px_2px_rgba(52,211,153,0.5)] animate-pulse-glow"
          : "bg-zinc-600"
      }`}
    />
  );
}

export function StatusIcon({ status, size = 14 }: { status?: string; size?: number }) {
  const v = (status ?? "").toLowerCase();
  if (["completed", "ok", "succeeded", "success"].includes(v))
    return <CircleCheck size={size} className="text-emerald-400" />;
  if (["failed", "error", "rejected", "denied"].includes(v))
    return <CircleX size={size} className="text-rose-400" />;
  if (["active", "running", "pending"].includes(v))
    return <Clock size={size} className="text-accent-soft" />;
  return <CircleDot size={size} className="text-zinc-500" />;
}

/* -------------------------------------------------------------------------- */
/*  Loading / empty / error states                                            */
/* -------------------------------------------------------------------------- */

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2.5 py-6 text-sm text-zinc-500">
      <LoaderCircle size={16} className="animate-spin-slow text-accent-soft" />
      {label}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton ${className}`} />;
}

/** Inline spinner + label for use inside buttons. */
export function LoaderInline({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <LoaderCircle size={14} className="animate-spin-slow" />
      {label}
    </span>
  );
}

/** A stack of skeleton lines for list/table loading states. */
export function SkeletonRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2.5">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}

export function Empty({
  children,
  icon,
  action,
}: {
  children: ReactNode;
  icon?: ReactNode;
  /** Optional call-to-action link shown beneath the message. */
  action?: { label: string; href: string };
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-10 text-center">
      {icon && <div className="text-zinc-600">{icon}</div>}
      <div className="max-w-sm text-sm text-zinc-500">{children}</div>
      {action && (
        <Link
          href={action.href}
          className="inline-flex items-center gap-1.5 rounded-xl border border-accent/30 bg-accent/[0.08] px-3 py-1.5 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14]"
        >
          {action.label} <ArrowRight size={13} />
        </Link>
      )}
    </div>
  );
}

/** A small amber chip marking sessions that ran on the built-in offline model. */
export function MockChip({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border border-amber-500/25 bg-amber-500/[0.1] px-2 py-0.5 text-[10px] font-medium text-amber-300 ${className}`}
      title="Ran on the built-in offline mock model"
    >
      <MoonStar size={10} /> offline mock
    </span>
  );
}

export function OfflineHint({ detail }: { detail?: string }) {
  return (
    <div className="flex items-start gap-3 rounded-2xl border border-amber-500/25 bg-amber-500/[0.07] px-4 py-3.5">
      <ServerCrash size={18} className="mt-0.5 shrink-0 text-amber-300" />
      <div className="text-sm text-amber-100/90">
        <div className="font-semibold text-amber-200">Daemon offline or unreachable.</div>
        <div className="mt-1 text-amber-100/60">
          Start it with{" "}
          <code className="rounded bg-black/40 px-1.5 py-0.5 font-mono text-xs text-amber-100/90">
            uv run ironjarvis serve --port 8787 --root .
          </code>
          {detail ? ` — ${detail}` : null}
        </div>
      </div>
    </div>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-2.5 rounded-xl border border-rose-500/25 bg-rose-500/[0.07] px-3 py-2.5 text-sm text-rose-200">
      <TriangleAlert size={16} className="mt-0.5 shrink-0" />
      <span>{children}</span>
    </div>
  );
}

export function SuccessNote({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-2.5 rounded-xl border border-emerald-500/25 bg-emerald-500/[0.07] px-3 py-2.5 text-sm text-emerald-200">
      <CircleCheck size={16} className="mt-0.5 shrink-0" />
      <span>{children}</span>
    </div>
  );
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-500">
      {children}
    </div>
  );
}

/**
 * Two-step destructive button: the first click arms it ("Confirm?"), a second
 * click within 3s runs the action. Prevents accidental irreversible deletes
 * (secrets are write-only and unrecoverable).
 */
export function ConfirmButton({
  onConfirm,
  label = "Delete",
  confirmLabel = "Confirm?",
  className = "",
  title,
}: {
  onConfirm: () => void | Promise<void>;
  label?: string;
  confirmLabel?: string;
  className?: string;
  title?: string;
}) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 3000);
    return () => clearTimeout(t);
  }, [armed]);
  const onClick = async () => {
    if (!armed) {
      setArmed(true);
      return;
    }
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
      setArmed(false);
    }
  };
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-50 ${
        armed
          ? "border-rose-500/50 bg-rose-500/15 text-rose-200"
          : "border-white/10 text-zinc-400 hover:border-rose-500/30 hover:text-rose-300"
      } ${className}`}
    >
      {busy ? <LoaderInline label={confirmLabel} /> : armed ? confirmLabel : label}
    </button>
  );
}
