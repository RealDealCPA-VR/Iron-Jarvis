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
 *  - MACHINE (G36): the machine checks lived only on Everything › Status, a
 *    tab a new user never opens. v1.449.0 (readiness Wave C, item 26): the
 *    count is of REQUIRED helpers only — every end-user check on a normal PC
 *    is optional, so "This PC: 5 of 6 helpers ready — Reading old .doc and
 *    .xls files" made a PC without Microsoft Word read broken on its first
 *    screen. Optional misses fold into one phrase ("1 optional extra not set
 *    up"), with the Status page's Details for the names.
 *  - NEXT (item 26): once a real model answers and the checklist still has a
 *    step open, ONE line says what to try next, in the step's own words —
 *    the checklist lives on Everything › Status, which newcomers do not
 *    open, so nothing on the home said what to do after the first answer.
 *  - APPS (G36): once a real model answers, "Connect the apps you use —
 *    GitHub, Notion, Slack… " leading to the Directory, while the checklist's
 *    optional `connect_apps` step is still open.
 *
 * Fed by ONE read of GET /onboarding when the empty chat first shows (the
 * page owns it) — never polled, never added to the status-line poll.
 */

import Link from "next/link";
import { ImportToolsOffer } from "@/components/onboarding/ImportToolsOffer";

/** The developer-toolchain rows the end-user surfaces never count
 *  (OnboardingWelcome's belt to readiness.DEV_TOOLCHAIN_CHECKS' brace). */
const DEV_TOOLCHAIN = new Set(["python", "uv", "git", "node", "pnpm"]);

export interface HomeReadiness {
  /** End-user machine checks counted the OnboardingWelcome way (every row). */
  ready: number;
  total: number;
  /** The first not-ready helper's plain label ("" when all are ready). */
  firstMissing: string;
  /** v1.449.0: the REQUIRED rows only — what the machine line counts. */
  requiredReady: number;
  requiredTotal: number;
  /** The first REQUIRED helper that is not ready ("" when none). */
  firstRequiredMissing: string;
  /** v1.449.0: the optional (recommended) helpers that are not ready. */
  optionalMissing: string[];
  /** A REQUIRED helper is missing (not just an optional extra). */
  requiredMissing: boolean;
  /** Setup is unfinished: a brand-new install, a required step still open,
   *  or replies are still the demo. */
  settingUp: boolean;
  /** Replies are still the demo model's. */
  isMock: boolean;
  /** v1.449.0: the checklist's next REQUIRED step still open (its key and
   *  title), or null when the checklist is done or unknown. */
  nextStep: { key: string; title: string } | null;
  /** The optional "Connect the apps you use" step is present and not done. */
  appsOpen: boolean;
}

type Row = Record<string, unknown>;
const obj = (v: unknown): Row | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Row) : null);

const isRequired = (c: Row) => (typeof c.level === "string" ? c.level : "required") !== "recommended";

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
  const required = rows.filter(isRequired);
  const requiredFailing = failing.filter(isRequired);
  const steps = Array.isArray(r.checklist) ? r.checklist.map(obj).filter((s): s is Row => s !== null) : [];
  const apps = steps.find((s) => s.key === "connect_apps");
  const isMock = obj(r.model)?.is_mock === true;
  const next = obj(r.next_step);
  return {
    ready: rows.length - failing.length,
    total: rows.length,
    firstMissing: failing.length ? label(failing[0]) : "",
    requiredReady: required.length - requiredFailing.length,
    requiredTotal: required.length,
    firstRequiredMissing: requiredFailing.length ? label(requiredFailing[0]) : "",
    optionalMissing: failing.filter((c) => !isRequired(c)).map(label),
    requiredMissing: requiredFailing.length > 0,
    settingUp: r.first_run === true || r.next_step != null || isMock,
    isMock,
    nextStep:
      next && typeof next.key === "string" && next.key
        ? { key: next.key, title: typeof next.title === "string" ? next.title : "" }
        : null,
    appsOpen: Boolean(apps) && apps!.done !== true,
  };
}

/** Should the machine line show? While setup is unfinished (a new user wants
 *  to know the PC is ready), or whenever a REQUIRED helper is missing — an
 *  optional extra alone never nags a settled user on every new chat. */
export function showMachineLine(h: HomeReadiness | null): boolean {
  return Boolean(h && h.total > 0 && (h.settingUp || h.requiredMissing));
}

/** The machine-check sentence (v1.449.0): REQUIRED helpers counted ("This
 *  PC: 2 of 2 helpers ready"; "This PC is ready" when nothing is required),
 *  the first required miss named, and every optional miss folded into one
 *  phrase ("· 1 optional extra not set up"). */
export function machineText(h: HomeReadiness): string {
  let head: string;
  if (h.requiredTotal > 0) {
    head = `This PC: ${h.requiredReady} of ${h.requiredTotal} helper${h.requiredTotal === 1 ? "" : "s"} ready`;
    if (h.firstRequiredMissing) head += ` — ${h.firstRequiredMissing}`;
  } else {
    head = "This PC is ready";
  }
  const n = h.optionalMissing.length;
  if (n) head += ` · ${n} optional extra${n === 1 ? "" : "s"} not set up`;
  return head;
}

/** v1.449.0 (item 26): what the checklist's open step asks for, said for
 *  the chat home — in the step's own words, with what to do RIGHT HERE. The
 *  connect step is the doors' (no line); an unknown step reads its title. */
export const NEXT_STEP_WORDS: Readonly<Record<string, string>> = {
  first_session: "Give it a task — ask for something in the box below.",
  work_with_document: "Work with a document — drop a PDF or spreadsheet into the box and ask for a summary.",
  teach_style: "Teach it your style — say “From now on, keep answers short”, or rate a reply with 👍 or 👎.",
};

/** The next-step sentence, or "" when there is nothing to say: no open
 *  step, replies still the demo (the doors are the next step), or the step
 *  is connecting a model. */
export function nextStepText(h: HomeReadiness | null): string {
  if (!h || !h.nextStep || h.isMock || h.nextStep.key === "connect_ai") return "";
  const words = NEXT_STEP_WORDS[h.nextStep.key] ?? h.nextStep.title.trim();
  return words ? `Next: ${words}` : "";
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
      Done — {label} answers you now. Type your question in the box.
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

/** v1.449.0 (item 26): one quiet line after the first real answer. */
export function NextStepLine({ h }: { h: HomeReadiness | null }) {
  const text = nextStepText(h);
  if (!text) return null;
  return (
    <p data-testid="home-next-step" className="text-center text-[12px] text-zinc-500">
      {text}{" "}
      <Link href="/everything#status" className={QUIET_LINK}>
        All steps
      </Link>
    </p>
  );
}

/** v1.455.0 (readiness Wave H item 29): with `importOffer`, the line also
 *  offers the tools the user's OTHER apps already use (the checklist's own
 *  `ImportToolsOffer`, read once, silent when nothing new is found). */
export function AppsLine({ names, importOffer = false }: { names: string[]; importOffer?: boolean }) {
  return (
    <>
      <p data-testid="home-apps" className="text-center text-[12px] text-zinc-500">
        <Link href="/marketplace" className={QUIET_LINK}>
          Connect the apps you use
        </Link>{" "}
        — {appNames(names)}
      </p>
      {importOffer && <ImportToolsOffer testPrefix="home-apps-import" centered />}
    </>
  );
}
