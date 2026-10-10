"use client";

import {
  cloneElement,
  isValidElement,
  useEffect,
  useId,
  useState,
  type ButtonHTMLAttributes,
  type ReactElement,
  type ReactNode,
} from "react";
import Link from "next/link";
import { DESKTOP_OFFLINE_HINT, isDesktopShell } from "@/lib/desktopShell";
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
      {/* Header + body padding tightened 20px -> 16px (v1.99.0): this is a tool
          driven daily, not a marketing page, and the looser padding cost roughly
          a row of content per card without buying legibility. The vertical
          rhythm BETWEEN cards (PageShell's space-y-6) is deliberately left
          alone — Overview and Creative use that breathing room. */}
      {(title || right) && (
        <header className="flex flex-wrap items-center justify-between gap-3 border-b hairline px-4 py-3">
          <h2 className="flex min-w-0 items-center gap-2 text-[13px] font-semibold tracking-wide text-zinc-200">
            {/* Neutral, not accent (v1.99.0). This icon renders in EVERY card
                header in the app, so tinting it accent-soft spent the brand
                colour on decoration hundreds of times over — leaving nothing to
                distinguish the things accent should mean: the active nav item,
                the primary action, live state. Quiet here, loud where it counts. */}
            {icon && <span className="text-zinc-500">{icon}</span>}
            {title}
          </h2>
          {right}
        </header>
      )}
      <div className={pad ? "p-4" : ""}>{children}</div>
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
        <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-400">
          {label}
        </div>
        {/* Neutral for the same reason as the Card header (v1.99.0) — every
            stat tile carried the accent, so it stopped signalling anything. */}
        {icon && <span className="text-zinc-500">{icon}</span>}
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

/* The status scale reads through the TONE TOKENS (v1.313.0): pale on the dark
   Marks exactly as before, deep ink on Daylight and Liquid Glass, where the old
   raw emerald/rose/violet tints measured under 2:1 and a "Failed" pill read as
   blank. Tints stay at /10 so the ink keeps >= 4.5:1 on its own pill. Running
   (cyan) stays the accent and idle (slate) stays neutral. */
const TONE_BADGE: Record<Tone, string> = {
  green: "bg-tone-success/10 text-tone-success border-tone-success/25",
  amber: "bg-tone-warn/10 text-tone-warn border-tone-warn/25",
  red: "bg-tone-danger/10 text-tone-danger border-tone-danger/25",
  cyan: "bg-accent/10 text-accent-soft border-accent/30",
  violet: "bg-tone-violet/10 text-tone-violet border-tone-violet/25",
  slate: "bg-zinc-500/10 text-zinc-300 border-zinc-500/25",
};

const TONE_DOT: Record<Tone, string> = {
  green: "bg-tone-success shadow-[0_0_8px_2px_rgb(var(--tone-success)/0.45)]",
  amber: "bg-tone-warn shadow-[0_0_8px_2px_rgb(var(--tone-warn)/0.45)]",
  red: "bg-tone-danger shadow-[0_0_8px_2px_rgb(var(--tone-danger)/0.45)]",
  cyan: "bg-accent shadow-[0_0_8px_2px_rgb(var(--accent-rgb)/0.55)]",
  violet: "bg-tone-violet shadow-[0_0_8px_2px_rgb(var(--tone-violet)/0.45)]",
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
  cancelled: "red",
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

/** The calm look's dot: the tone token alone, no glow. */
const CALM_DOT: Record<Tone, string> = {
  green: "bg-tone-success",
  amber: "bg-tone-warn",
  red: "bg-tone-danger",
  cyan: "bg-accent",
  violet: "bg-tone-violet",
  slate: "bg-zinc-500",
};

/** `default` is the bordered status pill most pages use; `calm` (v1.330.0) is
 *  the quiet chip of the calm pages (Settings > Connections): no border, no
 *  fill, neutral words, and only the small dot carries the tone token. */
export type BadgeVariant = "default" | "calm";

/** The calm Badge's shell, exported so a test can hold it to the same rules
 *  as the endpoint rows' quiet chip. */
export const CALM_BADGE =
  "inline-flex shrink-0 items-center gap-1.5 rounded-full px-1.5 py-0.5 text-[11px] font-medium leading-none text-zinc-400";

/** `keepCase` (v1.232.0): skip the CSS `capitalize` for a chip that carries a
 *  sentence, not a status word — "Completed · needs you" rendered as
 *  "Completed · Needs You". Every other Badge is unchanged. */
export function Badge({
  value,
  tone,
  keepCase = false,
  variant = "default",
  title,
  className = "",
  "data-testid": testId,
}: {
  value: string;
  tone?: Tone;
  keepCase?: boolean;
  variant?: BadgeVariant;
  /** v1.330.0: a tooltip, extra classes and a test id pass through, so a
   *  page's own status pill can be a Badge (Connections' StatusPill). Left
   *  out, the default pill renders exactly as before. */
  title?: string;
  className?: string;
  "data-testid"?: string;
}) {
  const t = tone ?? statusTone(value);
  if (variant === "calm") {
    return (
      <span
        data-badge-variant="calm"
        data-tone={t}
        data-testid={testId}
        title={title}
        className={`${CALM_BADGE} ${keepCase ? "" : "capitalize"} ${className}`.replace(/\s+/g, " ").trim()}
      >
        <span aria-hidden="true" className={`h-1.5 w-1.5 shrink-0 rounded-full ${CALM_DOT[t]}`} />
        {value}
      </span>
    );
  }
  return (
    <span
      data-testid={testId}
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${keepCase ? "" : "capitalize"} ${TONE_BADGE[t]}${className ? ` ${className}` : ""}`}
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
          ? "bg-tone-success shadow-[0_0_8px_2px_rgb(var(--tone-success)/0.45)] animate-pulse-glow"
          : "bg-zinc-600"
      }`}
    />
  );
}

export function StatusIcon({ status, size = 14 }: { status?: string; size?: number }) {
  const v = (status ?? "").toLowerCase();
  if (["completed", "ok", "succeeded", "success"].includes(v))
    return <CircleCheck size={size} className="text-tone-success" />;
  if (["failed", "error", "rejected", "denied"].includes(v))
    return <CircleX size={size} className="text-tone-danger" />;
  // Warn, not accent (v1.99.0): this is a SEMANTIC scale and the other two
  // rungs are already semantic (success / danger). Borrowing the brand colour
  // for one rung made status read as branding and branding read as status.
  if (["active", "running", "pending"].includes(v))
    return <Clock size={size} className="text-tone-warn" />;
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

/**
 * Inline spinner + label for use inside buttons.
 *
 * REDUCED MOTION (v1.185.0). `animate-spin-slow` is stopped under
 * `prefers-reduced-motion: reduce` by one rule in `globals.css` — every
 * spinner in the app, not this component alone, because the class is used
 * directly in a dozen places and a per-component guard is a rule the next
 * component forgets. Every other animation in the app already guards it; this
 * family was the exception.
 *
 * WHICH LEAVES A STILL CIRCLE, so the state has to be carried by something
 * other than the motion. `role="status"` announces it either way, and a
 * caller that passes no visible label still gets one for assistive tech —
 * a frozen icon with no text is indistinguishable from an idle one, which is
 * a worse failure than the spin it replaced.
 */
export function LoaderInline({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2" role="status">
      <LoaderCircle size={14} className="animate-spin-slow" aria-hidden="true" />
      {label ?? <span className="sr-only">Working…</span>}
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

/**
 * v1.316.0 (UX wave 4, form-labels-not-associated): a form label that is
 * really tied to its control. The one child (an input, select or textarea)
 * gets an id from `useId` unless it already carries one, and the label's
 * `htmlFor` points at it — so clicking the words focuses the control and a
 * screen reader reads the label. Repeated forms never collide. A control's
 * own `aria-label` (tests use getByLabelText on some) is left as it is.
 */
export function Field({
  label,
  hint,
  children,
  className = "",
}: {
  label: ReactNode;
  /** One quiet line under the control. */
  hint?: ReactNode;
  children: ReactElement<{ id?: string }>;
  className?: string;
}) {
  const auto = useId();
  const own = isValidElement(children) ? children.props.id : undefined;
  const id = own || auto;
  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
        {label}
      </label>
      {isValidElement(children) && !own ? cloneElement(children, { id }) : children}
      {hint && <p className="mt-1 text-[11px] text-zinc-500">{hint}</p>}
    </div>
  );
}

/** v1.314.0: an empty state's way forward — a link, or a press that opens the
 *  page's OWN form (never a second copy of it). Existing `{label, href}`
 *  callers are unchanged. */
export type EmptyAction =
  | { label: string; href: string; onClick?: never; disabled?: never }
  | {
      label: string;
      onClick: () => void;
      href?: never;
      /** Mirror the page's own busy state (the header button's), so a quick
       *  double-press cannot start the same thing twice. */
      disabled?: boolean;
    };

const EMPTY_ACTION_CLASS =
  "inline-flex items-center gap-1.5 rounded-xl border border-accent/30 bg-accent/[0.08] px-3 py-1.5 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14]";

/**
 * The one empty state. Three parts, each optional but the message (v1.314.0,
 * UX wave 2): `title` says what this place is, `children` why it matters in
 * one sentence, `action` the next step. `examples` are short illustrations
 * ("When an email from my bank arrives, summarise it") — words, not presses;
 * `secondary` is a quiet link for the reader who wants the other door.
 */
export function Empty({
  children,
  icon,
  title,
  action,
  secondary,
  examples,
}: {
  children: ReactNode;
  icon?: ReactNode;
  /** What this place is, in plain words. */
  title?: ReactNode;
  /** The next step: a link, or a press that opens the page's own form. */
  action?: EmptyAction;
  /** A quieter second door (a link), e.g. "Most people want Reflexes". */
  secondary?: { label: string; href: string };
  /** Short illustrations of what goes here. */
  examples?: string[];
}) {
  return (
    <div data-testid="empty-state" className="flex flex-col items-center justify-center gap-3 py-10 text-center">
      {icon && <div className="text-zinc-600">{icon}</div>}
      {title && <div className="text-sm font-medium text-zinc-200">{title}</div>}
      <div className="max-w-sm text-sm text-zinc-500">{children}</div>
      {examples && examples.length > 0 && (
        <ul data-testid="empty-examples" className="max-w-sm space-y-1 text-left text-xs text-zinc-500">
          {examples.map((e) => (
            <li key={e} className="flex gap-1.5">
              <span aria-hidden className="text-zinc-600">•</span>
              <span>{e}</span>
            </li>
          ))}
        </ul>
      )}
      {action &&
        (action.href !== undefined ? (
          <Link href={action.href} className={EMPTY_ACTION_CLASS}>
            {action.label} <ArrowRight size={13} />
          </Link>
        ) : (
          <button
            type="button"
            onClick={action.onClick}
            disabled={action.disabled}
            className={`${EMPTY_ACTION_CLASS} disabled:cursor-not-allowed disabled:opacity-50`}
          >
            {action.label} <ArrowRight size={13} />
          </button>
        ))}
      {secondary && (
        <Link href={secondary.href} className="text-xs text-zinc-500 underline-offset-2 hover:text-zinc-300 hover:underline">
          {secondary.label}
        </Link>
      )}
    </div>
  );
}

/** A small warn-tone chip marking sessions that ran on the built-in offline
 *  model. The words and the title are the disclosure; the tone (v1.313.0)
 *  only makes sure it can be READ on every Mark. */
export function MockChip({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border border-tone-warn/25 bg-tone-warn/10 px-2 py-0.5 text-[10px] font-medium text-tone-warn ${className}`}
      title="Ran on the built-in demo model — scripted replies, no real AI (offline mock)"
    >
      <MoonStar size={10} /> demo model
    </span>
  );
}

export function OfflineHint({ detail }: { detail?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="notice-warn flex items-start gap-3 rounded-2xl border px-4 py-3.5"
    >
      <ServerCrash size={18} className="notice-warn-icon mt-0.5 shrink-0" aria-hidden="true" />
      <div className="text-sm">
        <div className="notice-warn-title font-semibold">Daemon offline or unreachable.</div>
        <div className="notice-warn-body mt-1">
          {/* v1.226.0: inside the desktop shell the daemon is supervised by
              Electron — the CLI line is wrong advice there. */}
          {isDesktopShell() ? (
            DESKTOP_OFFLINE_HINT
          ) : (
            <>
              Start it with{" "}
              <code className="notice-warn-code rounded px-1.5 py-0.5 font-mono text-xs">
                uv run ironjarvis serve --port 8787 --root .
              </code>
            </>
          )}
          {detail ? ` — ${detail}` : null}
        </div>
      </div>
    </div>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-2.5 rounded-xl border border-tone-danger/25 bg-tone-danger/[0.07] px-3 py-2.5 text-sm text-tone-danger"
    >
      <TriangleAlert size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

/**
 * v1.226.0: the honest companion to `Empty`. A page that reads only
 * `error.status === 0` renders "No X yet." on a 500 — a false empty state.
 * Render this in the empty-state branch when `error` is a REAL daemon error
 * (non-0); it renders nothing for the offline case (the OfflineHint owns that)
 * so it is safe to place in front of `Empty` unconditionally.
 */
export function DataError({
  error,
  what = "this",
}: {
  error: { status: number; message: string } | null | undefined;
  /** Short noun for the copy: "Could not load {what}: …". */
  what?: string;
}) {
  if (!error || error.status === 0) return null;
  return (
    <ErrorNote>
      Could not load {what}: {error.message}
    </ErrorNote>
  );
}

export function SuccessNote({ children }: { children: ReactNode }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-start gap-2.5 rounded-xl border border-tone-success/25 bg-tone-success/[0.07] px-3 py-2.5 text-sm text-tone-success"
    >
      <CircleCheck size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-400">
      {children}
    </div>
  );
}

/** `default` is the hairline-bordered button most pages use; `calm`
 *  (v1.330.0) is a quiet ghost for the calm pages: no border, it fills on
 *  hover, and the armed step reads in tone-danger. Same button, same two
 *  presses, same 3 s timeout. */
export type ConfirmVariant = "default" | "calm";

/** The calm ConfirmButton's look, one string per state (exported for the
 *  source guard and the tests). 28 px tall at least, so a finger can hit it. */
export const CALM_CONFIRM = {
  base: "inline-flex min-h-7 items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:opacity-50",
  idle: "text-zinc-400 hover:bg-white/[0.06] hover:text-tone-danger",
  armed: "bg-tone-danger/10 text-tone-danger hover:bg-tone-danger/15",
} as const;

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
  variant = "default",
}: {
  onConfirm: () => void | Promise<void>;
  label?: string;
  confirmLabel?: string;
  className?: string;
  title?: string;
  variant?: ConfirmVariant;
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
  if (variant === "calm") {
    return (
      <button
        type="button"
        onClick={onClick}
        disabled={busy}
        title={title}
        data-confirm-variant="calm"
        data-armed={armed ? "true" : "false"}
        className={`${CALM_CONFIRM.base} ${armed ? CALM_CONFIRM.armed : CALM_CONFIRM.idle} ${className}`.trim()}
      >
        {busy ? <LoaderInline label={confirmLabel} /> : armed ? confirmLabel : label}
      </button>
    );
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-50 ${
        armed
          ? "border-tone-danger/50 bg-tone-danger/[0.12] text-tone-danger"
          : "border-white/10 text-zinc-400 hover:border-tone-danger/30 hover:text-tone-danger"
      } ${className}`}
    >
      {busy ? <LoaderInline label={confirmLabel} /> : armed ? confirmLabel : label}
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/*  Code chip + Button (v1.313.0, U1-3)                                        */
/* -------------------------------------------------------------------------- */

/**
 * An inline code chip: a command, a URL, a setting name. One theme-true class
 * (`.code-inline` in globals.css) instead of hand-rolled `bg-black/40` chips.
 * Black is the one neutral the themes do not remap, so on Daylight those read
 * as dark text on a muddy grey slab.
 */
export function Code({ children, className = "" }: { children?: ReactNode; className?: string }) {
  return <code className={`code-inline ${className}`.trim()}>{children}</code>;
}

export type ButtonVariant = "primary" | "secondary" | "soft" | "danger";
export type ButtonSize = "sm" | "md";

const BUTTON_VARIANT: Record<ButtonVariant, string> = {
  primary: "btn-accent",
  secondary: "btn-ghost",
  soft: "btn-soft",
  danger: "btn-danger",
};

/**
 * The one button for NEW code (v1.313.0). `variant` picks the look and `size`
 * the height: page-header actions are `md`, card and row actions `sm`, and a
 * view keeps at most one `primary`. Every other prop (onClick, disabled,
 * aria-*, title) passes straight through to the <button>. It defaults to
 * `type="button"`, because a bare <button> inside a <form> submits it. A
 * destructive action that needs two presses still uses `ConfirmButton`;
 * `danger` here is only the look.
 */
export function Button({
  variant = "secondary",
  size = "md",
  type = "button",
  className = "",
  ...rest
}: {
  variant?: ButtonVariant;
  size?: ButtonSize;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type={type}
      className={`${BUTTON_VARIANT[variant]} btn-${size} ${className}`.trim()}
      {...rest}
    />
  );
}
