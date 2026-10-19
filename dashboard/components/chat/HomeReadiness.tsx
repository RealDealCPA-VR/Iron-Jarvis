"use client";

/**
 * The empty chat's quiet setup lines (v1.353.0, wave 3 — "a clean first five
 * minutes"). Plain text rows under the composer, never cards: the chat home
 * is the composer and nothing else is boxed.
 *
 *  - CHOSEN (G35): "Done — <label> answers you now. Ask anything below." The
 *    connect doors said "Done" inside themselves, and the doors unmount the
 *    moment /health follows the new default, so the confirmation lasted a
 *    blink and named the promoted API row ("Anthropic (anthropic · …)") for a
 *    press labelled "Claude (your Claude Code sign-in)". The page keeps the
 *    PRESSED words for the rest of the visit (the OnboardingWelcome
 *    PressOutcome latch) and this line says them.
 *  - MACHINE (G36): "This PC: 5 of 6 helpers ready — <first missing> ·
 *    Details". The machine checks lived only on Everything › Status, a tab a
 *    new user never opens. Counted the OnboardingWelcome way — every
 *    end-user row, failing = not ok — never from `doctor.ok` (which looks
 *    only at the required rows).
 *  - APPS (G36): once a real model answers, "Connect the apps you use —
 *    GitHub, Notion, Slack… " leading to the Directory, while the checklist's
 *    optional `connect_apps` step is still open.
 *
 * Fed by ONE read of GET /onboarding when the empty chat first shows (the
 * page owns it) — never polled, never added to the status-line poll.
 */

import Link from "next/link";

/** The developer-toolchain rows the end-user surfaces never count
 *  (OnboardingWelcome's belt to readiness.DEV_TOOLCHAIN_CHECKS' brace). */
const DEV_TOOLCHAIN = new Set(["python", "uv", "git", "node", "pnpm"]);

export interface HomeReadiness {
  /** End-user machine checks counted the OnboardingWelcome way. */
  ready: number;
  total: number;
  /** The first not-ready helper's plain label ("" when all are ready). */
  firstMissing: string;
  /** A REQUIRED helper is missing (not just an optional extra). */
  requiredMissing: boolean;
  /** Setup is unfinished: a brand-new install, a required step still open,
   *  or replies are still the demo. */
  settingUp: boolean;
  /** The optional "Connect the apps you use" step is present and not done. */
  appsOpen: boolean;
}

type Row = Record<string, unknown>;
const obj = (v: unknown): Row | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Row) : null);

/** Decode GET /onboarding into what the home lines need; null for junk (an
 *  older daemon, a failed read) so nothing renders rather than a guess. */
export function decodeHomeReadiness(raw: unknown): HomeReadiness | null {
  const r = obj(raw);
  if (!r) return null;
  const checks = Array.isArray(obj(r.doctor)?.checks) ? (obj(r.doctor)!.checks as unknown[]) : [];
  const rows = checks
    .map(obj)
    .filter((c): c is Row => c !== null && typeof c.name === "string" && !DEV_TOOLCHAIN.has(c.name));
  const failing = rows.filter((c) => c.ok !== true);
  const label = (c: Row) =>
    (typeof c.label === "string" && c.label.trim()) || String(c.name).replace(/_/g, " ");
  const steps = Array.isArray(r.checklist) ? r.checklist.map(obj).filter((s): s is Row => s !== null) : [];
  const apps = steps.find((s) => s.key === "connect_apps");
  const isMock = obj(r.model)?.is_mock === true;
  return {
    ready: rows.length - failing.length,
    total: rows.length,
    firstMissing: failing.length ? label(failing[0]) : "",
    requiredMissing: failing.some((c) => (typeof c.level === "string" ? c.level : "required") !== "recommended"),
    settingUp: r.first_run === true || r.next_step != null || isMock,
    appsOpen: Boolean(apps) && apps!.done !== true,
  };
}

/** Should the machine line show? While setup is unfinished (a new user wants
 *  to know the PC is ready), or whenever a REQUIRED helper is missing — an
 *  optional extra alone never nags a settled user on every new chat. */
export function showMachineLine(h: HomeReadiness | null): boolean {
  return Boolean(h && h.total > 0 && (h.settingUp || h.requiredMissing));
}

/** The machine-check sentence, e.g. "This PC: 5 of 6 helpers ready — Reading
 *  old .doc and .xls files". */
export function machineText(h: HomeReadiness): string {
  const head = `This PC: ${h.ready} of ${h.total} helper${h.total === 1 ? "" : "s"} ready`;
  return h.firstMissing ? `${head} — ${h.firstMissing}` : head;
}

/** "GitHub, Notion, Slack…" — the Directory's own first names when the page
 *  has them, else three well-known ones. */
export function appNames(names: string[]): string {
  const shown = names.filter((n) => n && n.trim()).slice(0, 3);
  return `${(shown.length ? shown : ["GitHub", "Notion", "Slack"]).join(", ")}…`;
}

const QUIET_LINK =
  "text-zinc-400 underline decoration-zinc-700 underline-offset-2 transition-colors hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 rounded-sm";

export function ChosenLine({ label }: { label: string }) {
  if (!label) return null;
  return (
    <p role="status" data-testid="home-chosen" className="text-center text-[13px] text-tone-success">
      Done — {label} answers you now. Ask anything below.
    </p>
  );
}

export function MachineLine({ h }: { h: HomeReadiness | null }) {
  if (!h || !showMachineLine(h)) return null;
  return (
    <p data-testid="home-machine" className="text-center text-[12px] text-zinc-500">
      {machineText(h)} ·{" "}
      <Link href="/everything#status" className={QUIET_LINK}>
        Details
      </Link>
    </p>
  );
}

export function AppsLine({ names }: { names: string[] }) {
  return (
    <p data-testid="home-apps" className="text-center text-[12px] text-zinc-500">
      <Link href="/marketplace" className={QUIET_LINK}>
        Connect the apps you use
      </Link>{" "}
      — {appNames(names)}
    </p>
  );
}
