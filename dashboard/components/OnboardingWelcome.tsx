"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { m } from "framer-motion"; // v1.250.0 (S-08)
import {
  Sparkles,
  CheckCircle2,
  Circle,
  ArrowRight,
  X,
  Rocket,
  Wrench,
  TriangleAlert,
  ChevronRight,
  Bot,
} from "lucide-react";
import { useApi, type ApiState } from "@/lib/useApi";
import { ApiError } from "@/lib/api";
// W2-6 (track B): `chooseForAnswers` is `useModel` under a name the hooks
// lint rule does not mistake for a hook (it is called from a click handler).
import { candidateFor, chooseForAnswers, decodeOnboardingModel } from "@/lib/onboarding";
import { useDaemon } from "@/lib/daemon";
import type { DoctorCheck, Onboarding, OnboardingStep } from "@/lib/types";

const DISMISS_KEY = "ij_onboarding_dismissed";

/** The preference the "Teach it your style" press puts in Chat's box. It
 *  starts with "From now on" — the words that arm remember_preference. */
export const TEACH_EXAMPLE = "From now on, keep your answers short and to the point";

/** Map a checklist step to the page that completes it. */
const STEP_LINK: Record<string, { href: string; cta: string }> = {
  connect_ai: { href: "/connections", cta: "Connect a model" },
  // v1.197.0: the first task happens in CHAT — one surface that escalates
  // itself; a chat thread now counts as the first session, so don't send a
  // brand-new user to the empty Sessions list.
  first_session: { href: "/chat", cta: "Open Chat" },
  work_with_document: { href: "/documents", cta: "Open Documents" },
  // v1.197.0: the teach-style signal is CREATED in Chat — a stated
  // preference (the remember_preference tool writes the lesson) or, since
  // v1.320.0, a 👍 / 👎 under a reply. v1.320.0: the press opens Chat with an
  // example preference ALREADY TYPED (nothing is sent until the user does) —
  // a blank Chat left the user guessing what "teach" meant.
  // /memory?scope=lessons stays a dead end for a user with zero lessons.
  teach_style: { href: `/chat?ask=${encodeURIComponent(TEACH_EXAMPLE)}`, cta: "Teach it in Chat" },
  set_up_voice: { href: "/connections", cta: "Enable voice" },
};

function stepLink(step: OnboardingStep) {
  return STEP_LINK[step.key] ?? { href: "/sessions", cta: "Get started" };
}

/** v1.310.0 (W2-2): a check row may carry a plain `label` beside its mono name. */
type WelcomeCheck = DoctorCheck & { label?: string };

/** v1.310.0 (welcome-env-checks-dev-tools): the developer toolchain is the
 *  `ironjarvis doctor` CLI's business, never this card's — a packaged install
 *  needs none of it, and the uv row's fix was a remote-script paste. The
 *  daemon already drops these from /onboarding; this is the belt to that
 *  brace for a daemon that still sends them. */
const DEV_TOOLCHAIN = new Set(["python", "uv", "git", "node", "pnpm"]);

/** A failing REQUIRED row shows inline; anything else is an optional extra.
 *  A row with no level (a very old daemon) is treated as required — the
 *  card would rather show one row too many than hide a real problem. */
function isRecommended(c: WelcomeCheck): boolean {
  return (c.level ?? "required") === "recommended";
}

/** v1.310.0 (review): where a usable model's words go, from the daemon's own
 *  `local` flag on each W2-2 `usable` row. Track B's decoder keeps only
 *  provider + label, so the flag is read off the raw block here; an older
 *  daemon that sends no flag falls back to the providers that run on the
 *  user's own machine. */
const LOCAL_FALLBACK = new Set(["ollama", "custom"]);

function localFlags(raw: unknown): Map<string, boolean> {
  const out = new Map<string, boolean>();
  const rows = (raw as { usable?: unknown } | null | undefined)?.usable;
  if (!Array.isArray(rows)) return out;
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const { provider, local } = row as Record<string, unknown>;
    if (typeof provider === "string" && typeof local === "boolean") out.set(provider, local);
  }
  return out;
}

/** A label as it reads mid-sentence: "Your own model server" becomes "your
 *  own model server" ("…sent to your own model server…"); a name like
 *  "Claude" keeps its capital. */
function midSentence(label: string): string {
  return /^(Your|The|A|An)\b/.test(label) ? label[0].toLowerCase() + label.slice(1) : label;
}

/** v1.313.0 (first-run-connect-step-wrong-cta): where a CLOUD provider's
 *  words go, in lib/onboarding's own WHERE words ("goes to Anthropic") — the
 *  same words the chat empty state prints, so the two surfaces never say it
 *  two ways. `candidateFor` with no /health falls straight through to that
 *  table; "" when the table has no entry (a custom server). */
function whereWords(provider: string): string {
  return candidateFor(null, provider)?.where ?? "";
}

/** The sentence said BEFORE a press — the privacy decision the user is
 *  making. Cloud vs local is theirs to choose, so it is said for every row.
 *  v1.313.0: a cloud row names the COMPANY the words go to ("What you type
 *  goes to Anthropic.") instead of repeating the press's own label right
 *  under it; a row the WHERE table does not know keeps its old sentence. */
function whereItGoes(provider: string, label: string, local: boolean): string {
  if (!local) {
    const where = whereWords(provider);
    if (where) return `What you type ${where}.`;
    return `Your questions will be sent to ${midSentence(label)} to be answered.`;
  }
  if (provider === "ollama") return "It runs on this PC, so your questions stay here.";
  // v1.310.0 (review): a custom address can point at a HOSTED OpenAI-
  // compatible service, so this card does not promise "no AI company sees
  // them" — it says only what is known: the words go to the server the user
  // set up.
  return "It runs on the model server you set up, so your questions go only there.";
}

/** v1.310.0 (review): a CLI promoted to itself answers on its own sign-in and
 *  comes back as model "subscription" (settings.py's `_CLI_INHERITS` branch
 *  when the user also holds their own API key) — that is not a model id, and
 *  "Done — subscription now answers" was nonsense at the most important
 *  moment of the first five minutes. Only a real id is shown in the code
 *  font. */
const NOT_A_MODEL_ID = new Set(["", "subscription", "default"]);

function isRealModelId(model: string): boolean {
  return !NOT_A_MODEL_ID.has(model.trim().toLowerCase());
}

/** Who answers now, in the confirmation: the pressed offer's label, else a
 *  real model id, else plain words — never "subscription". */
function answeredBy(o: { label: string; model: string }): string {
  if (o.label) return o.label;
  return isRealModelId(o.model) ? o.model : "The model you chose";
}

/** What the "use this for answers" press came back with (kept locally so the
 *  sentence survives the /onboarding re-read that follows it). `label` is the
 *  PRESSED offer's own name, so the confirmation names what the user chose. */
type PressOutcome =
  | { kind: "promoted"; provider: string; model: string; label: string }
  | { kind: "declined"; reason: string }
  | { kind: "error"; message: string };

/**
 * The getting-started card.
 *
 * v1.310.0 (wave 2): the page may hand in its own `/onboarding` read (`state`)
 * so the Overview fetches it ONCE for both the first-run strip's gate and this
 * card; on its own (the wizard-less surfaces, tests) it reads it itself.
 */
export function OnboardingWelcome({ state }: { state?: ApiState<Onboarding> } = {}) {
  const own = useApi<Onboarding>(state ? null : "/onboarding");
  const api = state ?? own;
  const { data } = api;
  // The press refreshes the shared /health read so the topbar and every other
  // reader follow the new default at once (W2-6). Outside a DaemonProvider
  // this is a no-op.
  const { refresh: refreshHealth } = useDaemon();
  const [dismissed, setDismissed] = useState(true); // assume dismissed until we read storage
  const [extrasOpen, setExtrasOpen] = useState(false);
  // The provider whose press is in flight ("" = none). Every press is
  // disabled while one runs; only the pressed one says "Switching…".
  const [pressing, setPressing] = useState("");
  const [outcome, setOutcome] = useState<PressOutcome | null>(null);
  // v1.313.0: the connect_ai row's "Choose it for answers" brings the user to
  // the model choices instead of leaving for /connections.
  const modelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setDismissed(localStorage.getItem(DISMISS_KEY) === "1");
  }, []);

  function dismiss() {
    localStorage.setItem(DISMISS_KEY, "1");
    setDismissed(true);
  }
  function reopen() {
    localStorage.removeItem(DISMISS_KEY);
    setDismissed(false);
  }

  if (!data) return null;

  // v1.310.0 (W2-2): `model` is absent on an older daemon — then no card.
  const model = decodeOnboardingModel((data as unknown as { model?: unknown }).model);

  const failing = ((data.doctor?.checks ?? []) as WelcomeCheck[]).filter(
    (c) => !c.ok && !DEV_TOOLCHAIN.has(c.name),
  );
  const requiredFailing = failing.filter((c) => !isRecommended(c));
  const extras = failing.filter(isRecommended);
  // v1.310.0 (mock-default-trap-cli-ollama): the offline demo answering is
  // itself a reason to show the card, whatever the checklist says.
  const relevant =
    data.first_run || data.next_step != null || !data.doctor?.ok || !!model?.is_mock || !!outcome;

  // Everything is set up — nothing to nudge.
  if (!relevant) return null;

  // v1.310.0: the ONE explicit "use this for answers" press (W2-1 via W2-6).
  // The daemon promotes ONLY over the untouched demo default and says so in
  // a sentence when it does not; this card repeats that sentence verbatim and
  // never claims a switch the daemon did not make.
  async function pressForAnswers(provider: string, label: string) {
    if (pressing) return;
    setPressing(provider);
    try {
      const res = await chooseForAnswers(provider, refreshHealth);
      if (res?.promoted) {
        setOutcome({
          kind: "promoted",
          provider: res.promoted.provider,
          model: res.promoted.model,
          label,
        });
      } else {
        setOutcome({
          kind: "declined",
          reason: res?.reason || "Nothing changed — a model was already chosen for answers.",
        });
      }
      // The checklist's "Connect your AI" row and the card itself read the
      // new default from here.
      api.reload();
    } catch (err) {
      setOutcome({
        kind: "error",
        message: err instanceof ApiError || err instanceof Error ? err.message : String(err),
      });
    } finally {
      setPressing("");
    }
  }

  // v1.310.0 (review): EVERY usable model gets its own press. The daemon
  // orders the list CLIs first, so offering only the first hid a local
  // Ollama behind a cloud sign-in — the card made the cloud-vs-local choice
  // for the user by leaving the alternative out.
  const offers = model?.usable ?? [];
  const local = localFlags((data as unknown as { model?: unknown }).model);
  const isLocal = (provider: string) => local.get(provider) ?? LOCAL_FALLBACK.has(provider);
  const showModelCard = !!model?.is_mock || !!outcome;
  // The presses stay offered until one worked or the daemon said why it will
  // not (a 409 changed nothing, so try again).
  const pressesOnScreen =
    showModelCard && offers.length > 0 && outcome?.kind !== "promoted" && outcome?.kind !== "declined";

  /* v1.313.0 (first-run-connect-step-wrong-cta, overview-setup-says-it-twice):
     the daemon marks the connect_ai row "Choose it for answers" exactly when a
     real model is ready for one press (checklist.py). Its control then BRINGS
     the user to the presses above — scroll + focus — instead of sending them
     to /connections. It never presses for them: which model answers (cloud
     or this PC) stays the user's call. Any other state keeps STEP_LINK. */
  const onePressRow = (step: OnboardingStep) =>
    step.key === "connect_ai" && step.action === "Choose it for answers" && pressesOnScreen;

  function showChoices() {
    const card = modelRef.current;
    if (!card) return;
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.focus({ preventScroll: true });
  }

  /* v1.310.0 (mock-default-trap-cli-ollama): which model answers, said
     plainly. While the default is the offline demo every reply is a
     scripted sample — a user who is signed in to Claude or runs Ollama
     used to get those with every screen saying "connected". One press
     fixes it, and it is the USER's press: cloud-vs-local is their
     privacy decision, so nothing here switches on its own. */
  const modelCard = showModelCard ? (
    <div
      ref={modelRef}
      // Focusable only by script (the row's "Choose it for answers"), so the
      // choices are announced and ringed when the user is brought here.
      tabIndex={-1}
      data-testid="welcome-model"
      className="mt-5 scroll-mt-24 rounded-xl border border-accent/25 bg-ink-950/40 p-3.5 outline-none transition-shadow focus:ring-2 focus:ring-accent/40"
    >
      <div className="flex items-start gap-2.5">
        <Bot size={16} className="mt-0.5 shrink-0 text-accent-soft" />
        <div className="min-w-0 flex-1 text-sm">
          {outcome?.kind === "promoted" ? (
            // Named by what the user PRESSED; the model id rides along
            // only when it is one (never the bare word "subscription").
            <p role="status" className="text-zinc-200">
              Done — {answeredBy(outcome)} now answers your questions
              {isRealModelId(outcome.model) && outcome.label && (
                <>
                  {" "}
                  (<span className="font-mono text-accent-soft">{outcome.model}</span>)
                </>
              )}
              . Try asking something in Chat.
            </p>
          ) : outcome?.kind === "declined" ? (
            <p role="status" className="text-zinc-300">
              {outcome.reason}
            </p>
          ) : offers.length > 0 ? (
            <p className="text-zinc-200">
              Replies are a scripted demo right now — no model has been chosen to answer
              yet.{" "}
              {offers.length === 1
                ? `${offers[0].label} is ready to answer.`
                : "These are ready — choose the one that should answer:"}
            </p>
          ) : (
            <p className="text-zinc-200">
              Replies are a scripted demo until you connect a model — what you ask won’t
              get a real answer yet.{" "}
              <Link
                href="/connections"
                className="font-medium text-accent-soft underline-offset-2 hover:underline"
              >
                Connect a model
              </Link>
            </p>
          )}

          {outcome?.kind === "error" && (
            <p role="alert" className="mt-2 text-xs text-amber-200">
              {outcome.message}
            </p>
          )}

          {/* One press per usable model, each with where the words go
              said BEFORE the press. v1.313.0: every offer wears the SAME
              outlined style — a solid first offer read as a recommendation,
              and cloud vs local is the user's choice, not the list order's.
              The page's one solid button is the checklist row that leads
              here. */}
          {pressesOnScreen && (
            <ul className="mt-3 space-y-2.5">
              {offers.map((o) => (
                <li key={o.provider}>
                  <button
                    type="button"
                    disabled={!!pressing}
                    onClick={() => pressForAnswers(o.provider, o.label)}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-accent/35 bg-accent/[0.08] px-3 py-1.5 text-left text-xs font-medium text-accent-soft transition-colors hover:border-accent/50 hover:bg-accent/[0.14] disabled:opacity-60"
                  >
                    {pressing === o.provider ? "Switching…" : `Use ${o.label} for answers`}
                    {pressing !== o.provider && <ArrowRight size={13} />}
                  </button>
                  <p className="mt-1 text-xs text-zinc-500">
                    {whereItGoes(o.provider, o.label, isLocal(o.provider))}
                  </p>
                </li>
              ))}
            </ul>
          )}
          {/* v1.313.0 (review): with the one-press choices on screen the
              checklist row scrolls HERE instead of linking to Connections,
              so this quiet link keeps Connections one click from the
              Overview — for an API key, OpenCode, Grok, or anything the
              presses above don't offer. A text link, not a button: the
              page keeps its one solid primary. */}
          {pressesOnScreen && (
            <p className="mt-3 text-xs">
              <Link
                href="/connections"
                className="text-zinc-400 underline decoration-zinc-700 underline-offset-2 transition-colors hover:text-zinc-200"
              >
                Other ways to connect →
              </Link>
            </p>
          )}
        </div>
      </div>
    </div>
  ) : null;

  // Collapsed: a small "Setup" re-open affordance.
  if (dismissed) {
    const chip = (
      <button
        onClick={reopen}
        className="inline-flex items-center gap-2 rounded-xl border border-accent/25 bg-accent/[0.07] px-3 py-1.5 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.12]"
      >
        <Rocket size={13} /> Finish setup
        {data.next_step && (
          <span className="text-zinc-500">· {data.next_step.title}</span>
        )}
      </button>
    );
    // v1.310.0 (review): a dismissal stored by an EARLIER version survives
    // the upgrade, and the checklist is what the user put away — not the
    // one-press fix for a demo default with a real model ready. That stays
    // beside the chip (the press, or the answer it got) until it is spent.
    if ((model?.is_mock && offers.length > 0) || outcome) {
      return (
        <div>
          {chip}
          {modelCard}
        </div>
      );
    }
    return chip;
  }

  return (
    <m.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
      className="relative overflow-hidden rounded-2xl border border-accent/25 bg-accent/[0.04] shadow-glow-sm"
    >
      {/* glow flourish */}
      <div className="pointer-events-none absolute -right-10 -top-16 h-48 w-48 rounded-full bg-accent/10 blur-3xl" />

      <div className="relative p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 place-items-center rounded-xl border border-accent/30 bg-accent/[0.1]">
              <Sparkles size={20} className="text-accent-soft" />
            </span>
            <div>
              <h2 className="text-lg font-semibold tracking-tight text-zinc-50">
                {data.first_run ? "Welcome to Iron Jarvis" : "Finish setting up"}
              </h2>
              <p className="text-sm text-zinc-400">
                A few quick steps and Jarvis is ready to help with real work.
              </p>
            </div>
          </div>
          <button
            onClick={dismiss}
            title="Dismiss"
            aria-label="Dismiss"
            className="rounded-lg p-1 text-zinc-500 transition-colors hover:bg-white/[0.05] hover:text-zinc-300"
          >
            <X size={16} />
          </button>
        </div>

        {/* The model card (built above, so a dismissed card can still show it). */}
        {modelCard}

        {/* Checklist */}
        <ol className="mt-5 space-y-2">
          {(data.checklist ?? []).map((step) => {
            const isNext = data.next_step?.key === step.key;
            const link = stepLink(step);
            const leadsToChoices = onePressRow(step);
            // v1.313.0 (phone-checklist-rows-cramped): below sm the action
            // drops under the text (the text claims the row's width, the
            // control wraps and lines up under it), so a narrow screen never
            // squeezes the step into a six-line column beside its button.
            const ctaLayout = "ml-[30px] mt-0.5 sm:ml-0";
            const ctaTone = step.done
              ? "text-zinc-500 hover:bg-white/[0.05] hover:text-zinc-300"
              : isNext || leadsToChoices
                ? "bg-accent text-ink-950 shadow-glow-sm hover:bg-accent-soft"
                : "border border-white/10 text-zinc-300 hover:bg-white/[0.05]";
            const ctaClass = `${ctaLayout} inline-flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${ctaTone}`;
            return (
              <li
                key={step.key}
                className={`flex flex-wrap items-start gap-3 rounded-xl border px-3.5 py-3 transition-colors sm:flex-nowrap ${
                  isNext
                    ? "border-accent/30 bg-accent/[0.07]"
                    : "border-white/[0.05] bg-white/[0.02]"
                }`}
              >
                <span className="mt-0.5 shrink-0">
                  {step.done ? (
                    <CheckCircle2 size={18} className="text-emerald-400" />
                  ) : (
                    <Circle size={18} className={isNext ? "text-accent-soft" : "text-zinc-600"} />
                  )}
                </span>
                <div className="min-w-0 flex-1 basis-[calc(100%-30px)] sm:basis-0">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span
                      className={`text-sm font-medium ${
                        step.done ? "text-zinc-400 line-through decoration-zinc-600" : "text-zinc-100"
                      }`}
                    >
                      {step.title}
                    </span>
                    {isNext && (
                      <span className="rounded-full border border-accent/30 bg-accent/[0.1] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-accent-soft">
                        next
                      </span>
                    )}
                    {step.optional && !step.done && (
                      <span className="rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-500">
                        optional
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 text-xs leading-relaxed text-zinc-500">
                    {/* v1.313.0 (overview-setup-says-it-twice): with the
                        choices on screen the card above already says replies
                        are a demo, so the row points at them instead of
                        saying it a second time. The daemon's own sentence is
                        kept for every other state. */}
                    {leadsToChoices
                      ? "Your choices are just above — pick the one that should answer you."
                      : step.detail}
                  </p>
                </div>
                {/* Always clickable — a completed step still links to its page
                    (e.g. "Connect your AI" done -> open Connections to manage
                    it). A done row previously rendered NO control at all, which
                    read as a broken button. */}
                {leadsToChoices ? (
                  <button type="button" onClick={showChoices} className={ctaClass}>
                    {step.action} <ArrowRight size={13} className="-rotate-90" />
                  </button>
                ) : (
                  <Link href={link.href} className={ctaClass}>
                    {step.done ? "Open" : link.cta} <ArrowRight size={13} />
                  </Link>
                )}
              </li>
            );
          })}
        </ol>

        {/* v1.310.0 (welcome-env-checks-dev-tools): only a REQUIRED failure is
            worth amber on a new user's first screen — it is something that
            actually stops the app working, so it shows with its fix. */}
        {requiredFailing.length > 0 && (
          <div className="mt-5 rounded-xl border border-amber-500/20 bg-amber-500/[0.05] p-3.5">
            <div className="mb-2 flex items-center gap-2 text-xs font-medium text-amber-200">
              <Wrench size={13} /> Needs your attention
            </div>
            <ul className="space-y-2">
              {requiredFailing.map((c) => (
                <li key={c.name} className="flex items-start gap-2.5 text-xs">
                  <TriangleAlert size={13} className="mt-0.5 shrink-0 text-amber-300" />
                  <div className="min-w-0">
                    <span className="font-medium text-amber-100/90">{c.label || c.name}</span>
                    <span className="text-zinc-500"> — {c.detail}</span>
                    {c.fix && <div className="mt-0.5 text-zinc-500">{c.fix}</div>}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* ...and everything RECOMMENDED folds into one quiet line, named in
            plain words ("Reading old .doc files", not `antiword`). Nothing is
            broken when one of these is missing; a feature is just unavailable
            until it is added. The rows are not rendered while folded. */}
        {extras.length > 0 && (
          <div className="mt-4">
            <button
              type="button"
              aria-expanded={extrasOpen}
              onClick={() => setExtrasOpen((o) => !o)}
              className="inline-flex items-center gap-1.5 text-xs text-zinc-500 transition-colors hover:text-zinc-300"
            >
              <ChevronRight
                size={13}
                className={`transition-transform ${extrasOpen ? "rotate-90" : ""}`}
              />
              Optional extras ({extras.length})
            </button>
            {extrasOpen && (
              <ul className="mt-2 space-y-1.5 pl-5">
                {extras.map((c) => (
                  <li key={c.name} className="text-xs">
                    <span className="text-zinc-300">{c.label || c.name}</span>
                    <span className="text-zinc-500"> — {c.detail}</span>
                    {c.fix && <div className="mt-0.5 text-zinc-500">{c.fix}</div>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </m.div>
  );
}
