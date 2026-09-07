"use client";

/**
 * "Set up my browser" — the guided window (v1.240.0, Sidebar Ship 1).
 *
 * The user's words, which are the specification: "an HTML modal popup that
 * cleanly and beautifuly explains the bare minimum steps the user needs to
 * take".
 *
 * THE BARE MINIMUM IS FOUR THINGS, and three of them are presses nobody can
 * make on the user's behalf at any price:
 *
 *   1 · Open the folder.        (we hand over the real absolute path)
 *   2 · Load it in Chrome.      (Developer mode + Load unpacked — manual)
 *   3 · Pair.                   (the security boundary — manual, HERE)
 *   4 · Allow site access.      (one button, inside the add-on — manual)
 *
 * WHY PAIRING IS ITS OWN STEP, AND WHY IT IS THE MAIN PATH. The first cut of
 * this dialog armed a window the daemon used to auto-pair with, so it treated
 * "a browser is asking to pair" as a step already finished: `pending_pairing`
 * counted as loaded, the dialog jumped to "a new tab opens with one button",
 * and the real next action — Pair — was on the CARD, behind a full-screen
 * modal, unmentioned. Auto-pair is gone: a security review showed it minted a
 * real credential to any local process that forged an `Origin` header, with no
 * human press, and there is no cryptographic repair because the identity a
 * browser presents over a local socket is a header any program on the machine
 * can write. So a person confirming "yes, that is mine" IS the boundary, every
 * user lands on it every time, and the dialog puts the Pair button in front of
 * them — with the identity beside it, exactly as the card shows it, because
 * approving a credential for an anonymous caller is not a decision.
 *
 * WHY 2 AND 4 CANNOT BE AUTOMATED EITHER, since a surface that just says "do
 * this" without saying why reads as laziness. Load unpacked exists precisely so
 * that code outside Chrome cannot install code inside Chrome; the escape from
 * it is a Web Store listing, which needs a developer account and a review cycle
 * and is therefore a decision rather than a task (BROWSER-SIDEBAR-PLAN §8). And
 * Chrome requires the host-permission grant to originate from a user gesture
 * inside the add-on itself, so the daemon can open the tab holding the button
 * and can do nothing about the click.
 *
 * IT NEVER SWITCHES A CAPABILITY ON BEHIND THE USER. The first cut armed on
 * mount with `{access: "read_only"}`, which persists `browser_access` to
 * config.toml — so a user on Off who opened this dialog merely to READ what
 * setup involves left with the browser capability permanently enabled, and
 * Close did not revert it. The arm route's own docstring refuses to default
 * `access` for exactly that reason. Arming now sends NO access at all, the
 * level is a VISIBLE control in this dialog showing the value the daemon
 * currently holds, and it is written only when the user clicks a level —
 * through the same `PUT /settings` the card uses, so there is one writer.
 *
 * IT ADVANCES ITSELF. Nothing here has a "Next" button, because every
 * transition is a fact the daemon already reports: `GET /browser/status` is
 * polled by the card that owns this modal, the status is handed down as a
 * prop, and the steps read it. One poll, not two — a modal that opened its own
 * interval would double the traffic and could disagree with the card behind it
 * about what state the browser is in. The one LOCAL signal is step 1's copy:
 * taking the path is something only this component can observe.
 *
 * THE WINDOW IS A CONVENIENCE NOW, NOT A GATE. While it is armed the daemon
 * sends the host-permission directive on its own, so step 4 usually happens
 * without anyone pressing "Open that tab again". It is time-boxed and shown as
 * a clock; when it lapses NOTHING here stops working — every step is still
 * pressable, the sentence says so plainly, and Re-arm is right there. A
 * first-timer walking through Developer mode and a file picker will lapse it,
 * and a wizard that turned into an error page at that moment would be the
 * failure this paragraph exists to prevent.
 *
 * NO AGENT-FACING STRING REACHES THIS SURFACE. `/browser/request-host-permission`
 * answers 409 with "…Ask the user to open the Browser page in Iron Jarvis and
 * pair their browser, then retry." — remedy copy written for a TOOL, and it
 * tells the user, who is standing on that page, to go to that page. Failures
 * here are mapped to sentences written for the person reading them
 * (`humanFailure`), and the raw text is never rendered.
 *
 * VOCABULARY. "Extension" is this app's word for an MCP server, so nothing a
 * user reads here calls the add-on one. The literal `chrome://extensions` is
 * Chrome's name for its own page and is quoted as an address, not as a noun.
 *
 * LIGHT MARKS. Every amber utility below already carries a mark1 + mark8 rule
 * in the generated `light-amber-overrides` block in app/globals.css
 * (`text-amber-100/70`, `text-amber-100/80`, `text-amber-200`, `text-amber-300`,
 * `border-amber-500/25`, `bg-amber-500/10`). Nothing new is introduced, so
 * `__tests__/light-amber-v1233.test.tsx` stays green without an edit.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  CheckCircle2,
  ClipboardCopy,
  FolderOpen,
  Link2,
  Puzzle,
  RefreshCw,
  ShieldCheck,
  Timer,
} from "lucide-react";
import { Modal } from "@/components/Modal";
import { ErrorNote, LoaderInline, SectionLabel } from "@/components/ui";
import { post, put, ApiError } from "@/lib/api";
import type { BrowserStatus } from "@/lib/types";
import { copyPlain } from "@/components/settings/MaintenanceTools";

/** What `POST /browser/setup/arm` answers. */
interface ArmResult {
  armed: boolean;
  expires_in_s: number;
  addon_dir?: string;
}

/** The levels, in the card's words — one vocabulary for one setting. */
const ACCESS_LEVELS: { value: string; label: string; hint: string }[] = [
  { value: "off", label: "Off", hint: "Jarvis cannot see or touch your browser." },
  {
    value: "read_only",
    label: "Read only",
    hint: "Jarvis can see your open tabs, read the page you are looking at, and take a screenshot of it. Looking only.",
  },
  {
    value: "interactive",
    label: "Interactive",
    hint: "Jarvis can also click, type and navigate. Anything that changes a page is gated and asks you first.",
  },
];

function accessLabel(value: string): string {
  return ACCESS_LEVELS.find((l) => l.value === value)?.label ?? "Off";
}

/** The HTTP status behind a failure, or 0 when there is none to read. */
function statusOf(e: unknown): number {
  return e instanceof ApiError ? e.status : 0;
}

/**
 * A sentence for the PERSON, never the daemon's own words.
 *
 * The routes behind this dialog answer with remedy text written for an agent
 * ("Ask the user to open the Browser page in Iron Jarvis and pair their
 * browser, then retry"), which is both wrong here — the user IS on that page —
 * and in a voice no user should ever be shown. It is also not thrown away: the
 * card behind this dialog keeps rendering `last_error` from the daemon, and the
 * diagnostics page has the rest.
 */
function humanFailure(what: "arm" | "pair" | "grant" | "access", e: unknown): string {
  const status = statusOf(e);
  if (status === 0) {
    return "Iron Jarvis did not answer. Check it is still running, then try again.";
  }
  if (status === 401 || status === 403) {
    return "Iron Jarvis refused that. Reopen the app window and try again.";
  }
  switch (what) {
    case "arm":
      if (status === 404) {
        return "This copy of Iron Jarvis is older than the guided setup, so it cannot open a setup window. The steps below still work — restart Iron Jarvis to get the guided one.";
      }
      if (status === 503) {
        return "The browser bridge is not running in this copy of Iron Jarvis, so there is no setup window to open. The steps below still work.";
      }
      return "Iron Jarvis could not open a setup window just now. The steps below still work, and Re-arm tries again.";
    case "pair":
      if (status === 404 || status === 409) {
        return "That pairing request is no longer waiting — your browser may have reconnected. Give it a moment; when it asks again, this button comes back.";
      }
      return "Iron Jarvis could not complete the pairing just now. Try the button again in a moment.";
    case "grant":
      if (status === 409 || status === 503) {
        return "Your browser is not connected to Iron Jarvis right now, so there is nothing to ask. Finish the steps above first — the tab opens by itself once it is.";
      }
      return "Iron Jarvis could not ask your browser for site access just now. Try again in a moment.";
    case "access":
      if (status === 400) {
        return "Iron Jarvis would not accept that access level.";
      }
      return "Iron Jarvis could not change the access level just now. Try again in a moment.";
  }
}

/** m:ss, so a two-minute window reads as a clock and not as "117". */
function clock(total: number): string {
  const s = Math.max(0, Math.floor(total));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/* -------------------------------------------------------------------------- */
/*  The arc mark, mirrored from FirstRunWizard                                 */
/* -------------------------------------------------------------------------- */

/** The arc-reactor brand mark (FirstRunWizard's, sized down for a dialog
 *  header). Copied rather than imported: FirstRunWizard is a client wizard that
 *  pulls in the whole onboarding surface, and a dialog should not drag it in to
 *  draw a 32px logo. */
function ArcMark() {
  return (
    <span className="relative grid h-8 w-8 shrink-0 place-items-center">
      <span className="absolute inset-0 rounded-xl bg-accent/15 blur-[8px]" />
      <svg
        viewBox="0 0 24 24"
        aria-hidden="true"
        className="relative h-8 w-8 drop-shadow-[0_0_8px_rgb(var(--accent-rgb)/0.55)]"
        fill="none"
        stroke="currentColor"
      >
        <circle cx="12" cy="12" r="9.2" className="stroke-accent/30" strokeWidth="1.2" />
        <g className="stroke-accent">
          {Array.from({ length: 8 }).map((_, i) => {
            const a = (i * Math.PI) / 4;
            return (
              <line
                key={i}
                x1={12 + Math.cos(a) * 4.4}
                y1={12 + Math.sin(a) * 4.4}
                x2={12 + Math.cos(a) * 7.6}
                y2={12 + Math.sin(a) * 7.6}
                strokeWidth="1.1"
                strokeLinecap="round"
                opacity={0.7}
              />
            );
          })}
        </g>
        <circle cx="12" cy="12" r="3.4" className="fill-accent/20 stroke-accent" strokeWidth="1.3" />
        <circle cx="12" cy="12" r="1.2" className="fill-accent-soft" stroke="none" />
      </svg>
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/*  The folder, copyable                                                       */
/* -------------------------------------------------------------------------- */

/** The card's `FolderLine` shape, and for its reason: the next thing done with
 *  this string is a paste into Chrome's file picker, and a path retyped off a
 *  screen is a path mistyped. The copy REPORTS what happened, failure included
 *  — `copyPlain` prefers the Electron bridge and falls back to
 *  `navigator.clipboard`, which is permission-gated in Electron and simply
 *  absent over plain HTTP, and a Copy button that silently did nothing is the
 *  DraftCard lie (v1.161.0). The path stays on screen either way. */
function SetupFolder({
  folder,
  onCopied,
}: {
  folder: string;
  /** Called with the OUTCOME, not with "the button was pressed". A copy that
   *  found no clipboard has not put the path anywhere, and advancing the step
   *  on it would take the folder off the screen at the exact moment the user
   *  still needs to read it. */
  onCopied: (ok: boolean) => void;
}) {
  const [said, setSaid] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  async function copy() {
    const outcome = await copyPlain(folder);
    setSaid(outcome === "copied" ? "Copied" : "Nothing here can copy — read it above");
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setSaid(null), 2500);
    // Reported LAST, so the step advances only once the clipboard has actually
    // taken the path.
    onCopied(outcome === "copied");
  }

  return (
    <div data-testid="browser-setup-folder" className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <code
          data-testid="browser-setup-folder-value"
          className="min-w-0 break-all rounded-md bg-white/[0.04] px-2 py-1 font-mono text-[12px] text-zinc-200"
        >
          {folder}
        </code>
        <button
          type="button"
          data-testid="browser-setup-folder-copy"
          onClick={copy}
          title={`Copy ${folder}`}
          aria-label={`Copy ${folder}`}
          className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:border-accent/30 hover:text-zinc-200"
        >
          <ClipboardCopy size={12} /> Copy
        </button>
        {said && (
          <span data-testid="browser-setup-folder-copied" className="text-[11px] text-zinc-500">
            {said}
          </span>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  The stepper                                                                */
/* -------------------------------------------------------------------------- */

type StepNo = 1 | 2 | 3 | 4;

interface StepMeta {
  n: StepNo;
  label: string;
  done: boolean;
}

/** FirstRunWizard's stepper, reused so the two guided surfaces in this app read
 *  as one product: active is `border-accent/40 bg-accent/[0.08]`, a finished
 *  step is an emerald check, and the rail between them is a dim arrow. */
function Stepper({ steps, current }: { steps: StepMeta[]; current: number }) {
  return (
    <div data-testid="browser-setup-stepper" className="flex items-center gap-1.5">
      {steps.map((s, i) => {
        const active = current === s.n && !s.done;
        return (
          <div key={s.n} className="flex min-w-0 flex-1 items-center gap-1.5">
            <div
              data-testid={`browser-setup-step-${s.n}`}
              data-done={s.done ? "true" : "false"}
              data-active={active ? "true" : "false"}
              className={`flex min-w-0 flex-1 items-center gap-1.5 rounded-lg border px-2 py-1.5 transition-colors ${
                active
                  ? "border-accent/40 bg-accent/[0.08]"
                  : "border-white/[0.06] bg-white/[0.02]"
              }`}
            >
              {s.done ? (
                <CheckCircle2 size={15} className="shrink-0 text-emerald-400" aria-hidden="true" />
              ) : (
                <span
                  className={`grid h-[15px] w-[15px] shrink-0 place-items-center rounded-full border text-[9px] font-semibold ${
                    active ? "border-accent/50 text-accent-soft" : "border-white/20 text-zinc-500"
                  }`}
                >
                  {s.n}
                </span>
              )}
              <span
                className={`truncate text-[11px] font-medium ${
                  active ? "text-zinc-100" : s.done ? "text-zinc-400" : "text-zinc-500"
                }`}
              >
                {s.label}
              </span>
            </div>
            {i < steps.length - 1 && (
              <ArrowRight size={11} className="shrink-0 text-zinc-700" aria-hidden="true" />
            )}
          </div>
        );
      })}
    </div>
  );
}

/** A finished step collapses to one line: a check, its name, and nothing the
 *  user has to read again. The whole point of the dialog is that exactly one
 *  instruction is on screen at a time. */
function DoneRow({ n, label }: { n: number; label: string }) {
  return (
    <div
      data-testid={`browser-setup-done-${n}`}
      className="flex items-center gap-2 rounded-xl border border-white/[0.06] bg-white/[0.015] px-3 py-2"
    >
      <CheckCircle2 size={14} className="shrink-0 text-emerald-400" aria-hidden="true" />
      <span className="truncate text-[12.5px] text-zinc-400">{label}</span>
    </div>
  );
}

/** The one open instruction. A section in `EnableDialog`'s shape. */
function StepCard({
  n,
  icon,
  title,
  children,
}: {
  n: number;
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section
      data-testid={`browser-setup-open-${n}`}
      className="rounded-xl border border-white/[0.06] bg-white/[0.015] p-3"
    >
      <div className="mb-2 flex items-center gap-2">
        <span className="grid h-6 w-6 shrink-0 place-items-center rounded-lg border border-accent/40 bg-accent/[0.08] text-accent-soft">
          {icon}
        </span>
        <h3 className="text-[13px] font-semibold tracking-wide text-zinc-100">{title}</h3>
      </div>
      <div className="space-y-2.5">{children}</div>
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/*  The access level, SHOWN                                                    */
/* -------------------------------------------------------------------------- */

/**
 * What Jarvis is allowed to do with the browser — visible, current, and changed
 * only by a click on one of these buttons.
 *
 * THIS BLOCK IS A FIX, not a feature. Opening this dialog used to arm the setup
 * window with `access: "read_only"`, which the daemon writes to config.toml
 * through `PUT /settings`; Close did not undo it. Reading a dialog is not
 * consent to a capability, so the level now sits here, showing what the daemon
 * actually holds, and the user picks. `selected` comes from the polled status —
 * never a constant — because a hardcoded default shown as "your setting" is the
 * same lie in a quieter voice.
 */
function AccessChoice({
  selected,
  busy,
  onPick,
}: {
  selected: string;
  busy: boolean;
  onPick: (value: string) => void;
}) {
  const off = selected === "off";
  return (
    <div
      data-testid="browser-setup-access"
      data-selected={selected}
      className="rounded-xl border border-white/[0.08] bg-white/[0.02] px-3 py-2.5"
    >
      <SectionLabel>What Jarvis may do</SectionLabel>
      <div className="mt-2 flex flex-wrap gap-2">
        {ACCESS_LEVELS.map((lvl) => {
          const on = selected === lvl.value;
          return (
            <button
              key={lvl.value}
              type="button"
              data-testid={`browser-setup-access-${lvl.value}`}
              aria-pressed={on}
              disabled={busy}
              onClick={() => onPick(lvl.value)}
              className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-50 ${
                on
                  ? "border-accent/40 bg-accent/10 text-zinc-100"
                  : "border-white/10 text-zinc-400 hover:border-accent/30 hover:text-zinc-200"
              }`}
            >
              {lvl.label}
            </button>
          );
        })}
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-zinc-500">
        {ACCESS_LEVELS.find((l) => l.value === selected)?.hint ?? ACCESS_LEVELS[0].hint} Nothing
        changes this except you pressing one of these — opening this window changed nothing.
      </p>
      {off && (
        <p
          data-testid="browser-setup-access-off-note"
          className="mt-1.5 text-[11px] leading-relaxed text-amber-100/80"
        >
          <span className="font-semibold text-amber-200">This is currently Off.</span> You can walk
          through every step below, and Jarvis will still refuse every browser action until you pick
          a level here.
        </p>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  The modal                                                                  */
/* -------------------------------------------------------------------------- */

export function BrowserSetupModal({
  status,
  onClose,
  onChanged,
}: {
  /** The card's polled `GET /browser/status`. Handed down rather than fetched
   *  again, so the modal and the card behind it can never disagree. */
  status: BrowserStatus | null;
  onClose: () => void;
  /** Ask the card to refetch — after arming, after pairing, after an access
   *  change, and after the grant nudge. */
  onChanged?: () => void;
}) {
  const [arming, setArming] = useState(true);
  const [armError, setArmError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [left, setLeft] = useState(0);
  const [copied, setCopied] = useState(false);
  const [armedDir, setArmedDir] = useState("");
  const [busy, setBusy] = useState<"pair" | "grant" | "access" | null>(null);
  const [nudged, setNudged] = useState(false);
  const [finished, setFinished] = useState(false);
  const finishedRef = useRef(false);

  const changedRef = useRef(onChanged);
  changedRef.current = onChanged;
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  /* -- The window ---------------------------------------------------------- */

  /**
   * Open the setup window — and NOTHING ELSE.
   *
   * No `access` is sent. The route's own docstring says `None` means "leave
   * `browser_access` exactly as it is", and the client used to hardcode
   * `read_only` past it, so a dialog opened to be READ turned the capability on
   * for good. The level is the user's click in `AccessChoice`, not a side
   * effect of mounting a component.
   */
  const arm = useCallback(async () => {
    setArming(true);
    setArmError(null);
    try {
      const r = await post<ArmResult>("/browser/setup/arm", {});
      setArmedDir((r?.addon_dir ?? "").trim());
      // Set LAST: `left` is the signal every "the window is open" assertion
      // reads, so it must not be observable before the arm actually returned.
      setLeft(r?.armed ? Math.max(0, Math.floor(r.expires_in_s ?? 0)) : 0);
      changedRef.current?.();
    } catch (e) {
      setArmError(humanFailure("arm", e));
      setLeft(0);
    } finally {
      setArming(false);
    }
  }, []);

  useEffect(() => {
    void arm();
  }, [arm]);

  // One second at a time, and only while there is something to count. The
  // interval is not a poll — the daemon owns the deadline; this is the same
  // number displayed, so a clock that drifts a second is cosmetic and a clock
  // that keeps running after the window shut is a lie, which is why it stops.
  const running = left > 0;
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setLeft((s) => (s <= 1 ? 0 : s - 1)), 1000);
    return () => window.clearInterval(id);
  }, [running]);

  /** Closing gives the window back. A window left armed after the user walked
   *  away keeps the daemon's automatic site-access directive alive with nobody
   *  watching, so the disarm is fired on every exit path — the button, Escape
   *  and the backdrop all land here, because `Modal.onClose` is this. */
  const close = useCallback(() => {
    void post("/browser/setup/disarm", {}).catch(() => {
      /* Best effort: the window expires on its own timer regardless, and a
         dialog that refuses to shut because a teardown call failed is worse
         than a window that lapses two minutes later. */
    });
    closeRef.current();
  }, []);

  /* -- What the status says ------------------------------------------------ */

  const addonDir = ((status?.addon_dir ?? "") || armedDir).trim();
  const pending = status?.pending_pairing ?? null;
  // The add-on has reached the daemon at least once: it is connected, it is
  // paired from an earlier run, or it is on the wire asking to pair.
  const loaded = Boolean(status?.connected || status?.paired || pending);
  // PAIRED IS NOT "LOADED". A pending request is a browser ASKING; the credential
  // does not exist until the user presses Pair, and treating the ask as the answer
  // is what sent the old dialog to step 3 with nothing paired.
  const pairedNow = Boolean(status?.paired || status?.connected);
  const granted = Boolean(status?.connected && status?.host_permission);
  const access = (status?.access ?? "off").trim() || "off";

  const step1 = copied || loaded;
  const step2 = loaded;
  const step3 = pairedNow;
  const step4 = granted;
  const current: StepNo = !step1 ? 1 : !step2 ? 2 : !step3 ? 3 : 4;

  const steps: StepMeta[] = [
    { n: 1, label: "Open the folder", done: step1 },
    { n: 2, label: "Load it in Chrome", done: step2 },
    { n: 3, label: "Pair", done: step3 },
    { n: 4, label: "Allow site access", done: step4 },
  ];

  /* -- Finishing ----------------------------------------------------------- */

  // Green, then gone. The dialog says it worked for a beat rather than
  // vanishing, because a modal that disappears the instant a background poll
  // returns reads as a crash.
  useEffect(() => {
    if (!granted || finishedRef.current) return;
    // THE LATCH IS A REF. Depending on the `finished` STATE here would re-run
    // this effect the instant it set it, and the cleanup would clear the very
    // timeout that was about to close the dialog — a success state that never
    // goes away, which is the failure this comment is cheaper than.
    finishedRef.current = true;
    setFinished(true);
    const id = window.setTimeout(() => close(), 1600);
    return () => window.clearTimeout(id);
  }, [granted, close]);

  /* -- The three presses --------------------------------------------------- */

  /** Step 3. The card's `pair()`, in the dialog the user is actually looking
   *  at — same route, same body, same one-in-flight guard. */
  async function pair() {
    const requestId = pending?.request_id ?? "";
    if (!requestId || busy) return;
    setBusy("pair");
    setFailure(null);
    try {
      await post("/browser/pair", { request_id: requestId });
      changedRef.current?.();
    } catch (e) {
      setFailure(humanFailure("pair", e));
    } finally {
      setBusy(null);
    }
  }

  async function nudgeGrant() {
    if (busy) return;
    setBusy("grant");
    setFailure(null);
    try {
      await post("/browser/request-host-permission", {});
      changedRef.current?.();
      // Written LAST: "we asked" is only true once the daemon said so.
      setNudged(true);
    } catch (e) {
      setFailure(humanFailure("grant", e));
    } finally {
      setBusy(null);
    }
  }

  /** The ONE writer for `browser_access`, the same one the card uses. Only a
   *  click reaches it. */
  async function pickAccess(value: string) {
    if (busy || value === access) return;
    setBusy("access");
    setFailure(null);
    try {
      await put("/settings", { values: { browser_access: value } });
      changedRef.current?.();
    } catch (e) {
      setFailure(humanFailure("access", e));
    } finally {
      setBusy(null);
    }
  }

  const lapsed = !arming && left <= 0 && !granted;

  return (
    <Modal
      label="Set up your browser"
      onClose={close}
      className="w-full max-w-xl"
      testId="browser-setup-modal"
    >
      <header className="flex shrink-0 items-center gap-3 border-b hairline px-4 py-3">
        <ArcMark />
        <div className="min-w-0 flex-1">
          <h2 className="text-[14px] font-semibold tracking-wide text-zinc-100">
            Set up your browser
          </h2>
          <p className="mt-0.5 text-[11.5px] text-zinc-500">
            Four presses, and Iron Jarvis does the rest. Three of them are Chrome&apos;s to keep and
            one is yours to give.
          </p>
        </div>
      </header>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        <Stepper steps={steps} current={current} />

        {/* THE WINDOW, as a clock. */}
        {arming ? (
          <div
            data-testid="browser-setup-arming"
            className="rounded-xl border border-white/[0.06] bg-white/[0.015] px-3 py-2.5 text-[12px] text-zinc-400"
          >
            <LoaderInline label="Opening a setup window…" />
          </div>
        ) : lapsed ? (
          /* CALM, AND STILL USABLE. Developer mode plus a file picker takes a
             first-timer longer than two minutes, and the old copy met them with
             an amber "closed" panel that read like a failed install. Nothing in
             this dialog depends on the window: it only lets the daemon send the
             site-access directive for you. So this is a neutral line, it says
             what still works, and Re-arm is beside it. */
          <div
            data-testid="browser-setup-lapsed"
            className="flex flex-wrap items-center gap-2.5 rounded-xl border border-white/[0.08] bg-white/[0.02] px-3 py-2.5 text-[12px] leading-relaxed text-zinc-400"
          >
            <Timer size={14} className="shrink-0 text-zinc-500" aria-hidden="true" />
            <span className="min-w-0 flex-1">
              <span className="font-medium text-zinc-200">Take your time.</span> The setup window
              lasts a couple of minutes and this one has closed — every step below still works
              exactly the same. Re-open it whenever you are ready and the last step goes back to
              happening on its own.
            </span>
            <button
              type="button"
              data-testid="browser-setup-rearm"
              onClick={() => void arm()}
              className="btn-accent shrink-0 py-1 text-[11px]"
            >
              <RefreshCw size={12} /> Re-arm
            </button>
          </div>
        ) : (
          <div
            data-testid="browser-setup-window"
            className="flex items-center gap-2.5 rounded-xl border border-accent/25 bg-accent/[0.06] px-3 py-2.5 text-[12px] leading-relaxed text-zinc-300"
          >
            <Timer size={14} className="shrink-0 text-accent-soft" aria-hidden="true" />
            <span className="min-w-0 flex-1">
              For the next{" "}
              <span data-testid="browser-setup-countdown" className="font-mono text-zinc-100">
                {clock(left)}
              </span>{" "}
              Iron Jarvis asks your browser for site access by itself, so step four usually happens
              without you. Pairing is always your press.
            </span>
          </div>
        )}

        {armError && <ErrorNote>{armError}</ErrorNote>}
        {failure && <ErrorNote>{failure}</ErrorNote>}

        {/* Exactly one open instruction. Everything above it is a check. */}
        {finished ? (
          <div
            data-testid="browser-setup-success"
            className="flex items-center gap-2.5 rounded-xl border border-emerald-500/25 bg-emerald-500/10 px-3 py-3 text-[13px] text-emerald-200"
          >
            <CheckCircle2 size={16} className="shrink-0 text-emerald-400" aria-hidden="true" />
            <span>
              <span className="font-semibold">Your browser is connected.</span> Jarvis can see the
              page you are looking at, at the level you chose — {accessLabel(access)}.
            </span>
          </div>
        ) : (
          <div className="space-y-2.5">
            {step1 && (
              <DoneRow
                n={1}
                label={copied && !loaded ? "Folder copied to your clipboard" : "Folder found"}
              />
            )}
            {step2 && <DoneRow n={2} label="Add-on loaded in your browser" />}
            {step3 && <DoneRow n={3} label="Paired with Iron Jarvis" />}

            {current === 1 && (
              <StepCard n={1} icon={<FolderOpen size={13} />} title="Open this folder">
                {addonDir ? (
                  <>
                    <p className="text-[12.5px] leading-relaxed text-zinc-400">
                      This is the Iron Jarvis browser add-on on this machine, already built and
                      shipped with the app. Copy it — Chrome asks for it by path in the next step.
                    </p>
                    <SetupFolder
                      folder={addonDir}
                      onCopied={(ok) => {
                        if (ok) setCopied(true);
                      }}
                    />
                  </>
                ) : (
                  <p
                    data-testid="browser-setup-folder-missing"
                    className="text-[12.5px] leading-relaxed text-amber-100/80"
                  >
                    <span className="font-semibold text-amber-200">
                      Iron Jarvis could not find the add-on folder on this machine
                    </span>{" "}
                    — so there is no path to copy, and printing a folder name Chrome&apos;s picker
                    cannot resolve would only waste your time. Reinstalling the current release
                    puts it back. From a source checkout, use{" "}
                    <code className="font-mono text-zinc-300">extensions/chrome</code>, built once
                    first.
                  </p>
                )}
              </StepCard>
            )}

            {current === 2 && (
              <StepCard n={2} icon={<Puzzle size={13} />} title="Load it in Chrome">
                <p className="text-[12.5px] leading-relaxed text-zinc-400">
                  Open{" "}
                  <code className="rounded bg-white/[0.05] px-1 py-0.5 font-mono text-[11.5px] text-zinc-200">
                    chrome://extensions
                  </code>
                  , turn on <span className="font-medium text-zinc-200">Developer mode</span>, press{" "}
                  <span className="font-medium text-zinc-200">Load unpacked</span>, and paste the
                  folder you just copied.
                </p>
                <p
                  data-testid="browser-setup-why-manual"
                  className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2 text-[11.5px] leading-relaxed text-zinc-500"
                >
                  Iron Jarvis cannot do these two clicks for you. Chrome only installs add-ons
                  either from a Web Store listing or from a person pressing Load unpacked
                  themselves — that is the whole point of the setting. The Iron Jarvis browser
                  add-on has no Web Store listing yet, so it is the second route.
                </p>
                <p className="text-[11.5px] leading-relaxed text-zinc-500">
                  Come straight back here. The moment it starts, this window asks you to pair it.
                </p>
              </StepCard>
            )}

            {/* STEP 3 — THE ONE PRESS THAT IS THE SECURITY BOUNDARY.
                It used to live only on the card, behind this dialog, while the
                dialog told the user to look for a tab that had not opened. */}
            {current === 3 && (
              <StepCard n={3} icon={<Link2 size={13} />} title="Pair it">
                <p className="text-[12.5px] leading-relaxed text-zinc-400">
                  Your browser is asking to connect to Iron Jarvis. Press Pair if you just loaded
                  the Iron Jarvis browser add-on yourself.
                </p>
                {/* WHO is asking — the card's own identity line, in the card's
                    words. Approving a credential for an anonymous caller is not
                    a decision, so the id is on screen before the button is. */}
                <div
                  data-testid="browser-setup-pair-identity"
                  className="rounded-lg border border-amber-500/25 bg-amber-500/10 px-2.5 py-2 font-mono text-[11px] leading-relaxed text-amber-100/80"
                >
                  {pending?.extension_id
                    ? pending.extension_id === status?.expected_extension_id
                      ? `Identified as the Iron Jarvis browser add-on (${pending.extension_id})`
                      : `Unrecognised caller: ${pending.extension_id} — this is not the Iron Jarvis browser add-on`
                    : "This caller did not identify itself. Do not pair it unless you just loaded the add-on."}
                </div>
                <p
                  data-testid="browser-setup-why-pair"
                  className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2 text-[11.5px] leading-relaxed text-zinc-500"
                >
                  Iron Jarvis cannot press this one for you, and it never will. Anything running on
                  this computer can knock on the same door and claim to be your browser — the name
                  it gives is just text it typed. Nothing Iron Jarvis can check tells a real browser
                  from a program pretending to be one, so a person has to look at the name above and
                  say &ldquo;yes, that is mine&rdquo;. That press is the whole protection.
                </p>
                <button
                  type="button"
                  data-testid="browser-setup-pair"
                  onClick={pair}
                  disabled={busy !== null || !pending?.request_id}
                  className="btn-accent py-1.5 text-xs disabled:opacity-50"
                >
                  {busy === "pair" ? (
                    <LoaderInline label="Pairing…" />
                  ) : (
                    <>
                      <Link2 size={14} /> Pair
                    </>
                  )}
                </button>
                {!pending?.request_id && (
                  <p
                    data-testid="browser-setup-pair-waiting"
                    className="text-[11.5px] leading-relaxed text-zinc-500"
                  >
                    Nothing is asking to pair at this moment. Open the browser you loaded the add-on
                    into and this button wakes up.
                  </p>
                )}
              </StepCard>
            )}

            {current === 4 && (
              <StepCard n={4} icon={<ShieldCheck size={13} />} title="Allow site access">
                {status?.connected ? (
                  <>
                    <p className="text-[12.5px] leading-relaxed text-zinc-400">
                      A new tab opens in your browser with one button on it. Press it, and Jarvis
                      can read the page you are looking at. Until then it sees your tab list and
                      nothing on the pages.
                    </p>
                    <p
                      data-testid="browser-setup-why-gesture"
                      className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2 text-[11.5px] leading-relaxed text-zinc-500"
                    >
                      This click has to come from inside the add-on — Chrome requires the permission
                      prompt to be opened by you, in the browser, and refuses it to anything asking
                      from outside.
                    </p>
                    <div className="flex flex-wrap items-center gap-2.5">
                      <button
                        type="button"
                        data-testid="browser-setup-reopen-grant"
                        onClick={nudgeGrant}
                        disabled={busy !== null}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:border-accent/30 hover:text-zinc-200 disabled:opacity-50"
                      >
                        {busy === "grant" ? <LoaderInline label="Asking…" /> : <>Open that tab again</>}
                      </button>
                      {nudged && (
                        <span
                          data-testid="browser-setup-reopened"
                          className="text-[11px] text-zinc-500"
                        >
                          Asked your browser — look for the new tab.
                        </span>
                      )}
                    </div>
                  </>
                ) : (
                  /* Paired, and nothing on the wire. Asking a browser that is not
                     there would answer with the tool-facing 409 this dialog exists
                     to keep off the screen, so it is not offered. */
                  <p
                    data-testid="browser-setup-not-running"
                    className="text-[12.5px] leading-relaxed text-zinc-400"
                  >
                    This browser is paired, but it is not running right now. Open it — the add-on
                    reconnects on its own, and the site-access tab opens from there.
                  </p>
                )}
              </StepCard>
            )}
          </div>
        )}

        {/* The level, last: the steps are the job, this is the setting the job
            is done AT — and it is the thing this dialog must never change on
            its own. */}
        <AccessChoice selected={access} busy={busy !== null} onPick={(v) => void pickAccess(v)} />
      </div>

      <footer className="flex shrink-0 items-center justify-between gap-2 border-t hairline px-4 py-3">
        <span className="text-[11px] text-zinc-600">
          Closing this closes the setup window too. Nothing pairs behind your back.
        </span>
        <button
          type="button"
          data-testid="browser-setup-close"
          onClick={close}
          className="btn-ghost py-1.5 text-xs"
        >
          {granted ? "Done" : "Close"}
        </button>
      </footer>
    </Modal>
  );
}

export default BrowserSetupModal;
