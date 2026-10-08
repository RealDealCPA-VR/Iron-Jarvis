"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AnimatePresence, m } from "framer-motion"; // v1.250.0 (S-08)
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  CircleAlert,
  Loader2,
  Mic,
  Play,
  RefreshCw,
  Sparkles,
  SkipForward,
  Wand2,
} from "lucide-react";
import { post } from "@/lib/api";
import { useApi, usePolledApi } from "@/lib/useApi";
import { useDaemon } from "@/lib/daemon";
import { useDictation } from "@/lib/useDictation";
import { ConnectDoors } from "@/components/onboarding/ConnectDoors";
import { AnswerPressNote, useAnswerPress } from "@/components/onboarding/AnswerPress";
import {
  availableRows,
  candidateFor,
  firstTaskProvider,
  friendlyProvider,
  isDemoDefault,
} from "@/lib/onboarding";
import type { Onboarding, OnboardingStep, SessionDetail, SessionView } from "@/lib/types";

/** One-shot choice: "done" (finished the flow) or "demo" (opted into mock). */
const CHOICE_KEY = "ij_first_run_choice";

/* The three connect doors (v1.197.0) live in components/onboarding/
   ConnectDoors.tsx since v1.310.0 — shared with the chat empty state, so the
   wizard's step 1, its gated step 3 and chat all offer the SAME way forward. */

/** One-tap magic tasks — small, fast, and unmistakably real when they run. */
const SUGGESTIONS = [
  "Summarize what you can do for me in 5 bullets",
  "Write a short haiku about an iron AI assistant",
  // v1.319.0: everyday tasks, not developer ones ("markdown", "chatbot").
  "Make a packing checklist for a weekend trip",
  "Suggest three ways you could save me time this week",
];

interface VoiceStatus {
  available: boolean;
  backend: string | null;
  hint: string;
}

const TERMINAL = new Set(["completed", "failed", "cancelled"]);

/** The slice of `GET /sessions/{id}/result` the finale reads (the ledger's
 *  own file lists — agents/outcome.session_result). Read here instead of
 *  importing SessionFiles: that module brings the whole ArtifactsRail, and the
 *  wizard is mounted on every page. */
interface FirstTaskResult {
  found?: boolean;
  files_created?: unknown;
  files_changed?: unknown;
}

/** The names of the files a run wrote, created then changed, de-duplicated
 *  (a basename — the full path is one click away on the session page). */
function writtenFiles(result: FirstTaskResult | null): string[] {
  if (!result?.found) return [];
  const all = [result.files_created, result.files_changed].flatMap((l) =>
    Array.isArray(l) ? l.filter((x): x is string => typeof x === "string" && x !== "") : [],
  );
  return Array.from(new Set(all.map((p) => p.split(/[\\/]/).pop() || p)));
}

/** The arc-reactor brand mark (mirrors the sidebar's, sized for the hero). */
function ArcMark() {
  return (
    <span className="relative grid h-12 w-12 place-items-center">
      <span className="absolute inset-0 rounded-xl bg-accent/15 blur-[8px]" />
      <svg
        viewBox="0 0 24 24"
        className="relative h-12 w-12 drop-shadow-[0_0_8px_rgb(var(--accent-rgb)/0.55)]"
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

/**
 * Self-contained first-run wizard for a brand-new install. Everything happens
 * INSIDE the modal — connecting a model, testing voice, and running the first
 * real task — so the user goes from download to working in minutes without ever
 * ejecting to another page.
 *
 * Show contract:
 *  - the wizard OPENS when `GET /onboarding` reports `first_run: true` while
 *    localStorage `ij_first_run_choice` is unset (nothing renders until the
 *    key has actually been read, so there is no flash);
 *  - once open it is LATCHED (v1.197.0): `first_run` flips false the moment
 *    the wizard SUCCEEDS at its own steps (a provider connects, a first
 *    session row exists) and every success path reloads /onboarding — so the
 *    show condition must not re-evaluate `first_run` mid-flow or the modal
 *    unmounts out from under the user right as things start working. Only
 *    finish() — the finale button, "View full session", or the skip link —
 *    closes it.
 *
 * Three steps, reconciled onto the /onboarding checklist so its titles/done
 * states match OnboardingWelcome exactly (no hardcoded drift):
 *  1. Connect a model INLINE. Since v1.197.0 the not-yet-connected branch is
 *     three plain-language DOORS — "I already pay for Claude or ChatGPT"
 *     (Claude Code / Codex signed in on this PC), "Free & private on this PC"
 *     (Ollama), and "I have an API key" (the compact anthropic / openai /
 *     custom form) — because the old copy led with jargon a first-runner may
 *     not know. Gemini is OAuth-only, so it is a link to /connections, never
 *     a key form. Since v1.310.0 the doors are the shared ConnectDoors, and
 *     they stay up (with the explicit "Use it for answers" press on top)
 *     while the default is still the offline demo.
 *  2. Optional VOICE — test the mic through useDictation (greens on a non-empty
 *     transcript) or skip; voice NEVER blocks.
 *  3. First MAGIC task — one-tap suggestions or free text; the streaming
 *     transcript + final result render in the modal. v1.310.0: with no real
 *     model ready the step shows the doors instead (it never runs on the
 *     demo); the task names its provider while the default is the demo; and
 *     the verdict reads the provider that ACTUALLY answered (the session
 *     row) — the mock is an amber "offline demo — no model ran", a real run
 *     names its model and lists the files it wrote (or says it wrote none).
 *     A real answer on a demo DEFAULT is not yet "chat works": the finale
 *     then offers the explicit "Use <it> for answers" press (W2-1) beside
 *     "Start using Iron Jarvis" — never automatic, never navigating.
 *     "Start using Iron Jarvis" finishes AND navigates to /chat (v1.197.0) —
 *     the hero surface — rather than closing onto an arbitrary page.
 */
export function FirstRunWizard() {
  const router = useRouter();
  const { health, refresh: refreshHealth } = useDaemon();
  const { data: onboarding, reload: reloadOnboarding } = useApi<Onboarding>("/onboarding");
  const { data: voice, reload: reloadVoice } = useApi<VoiceStatus>("/voice/status");

  // Push every green check to update NOW instead of waiting for the 5s poll.
  const refreshAll = useCallback(() => {
    refreshHealth();
    reloadOnboarding();
    reloadVoice();
  }, [refreshHealth, reloadOnboarding, reloadVoice]);

  // null = storage not read yet (render NOTHING — avoids a flash);
  // "" = unset (eligible to show); anything else = a prior choice.
  const [choice, setChoice] = useState<string | null>(null);
  useEffect(() => {
    setChoice(localStorage.getItem(CHOICE_KEY) ?? "");
  }, []);

  /* LATCH (v1.197.0). Eligibility is only ever checked to OPEN the wizard,
     never to keep it open: `first_run` flips false the moment the wizard
     succeeds at its own steps (a key connects → a provider exists; the first
     task runs → a session row exists) and refreshAll() reloads /onboarding on
     every one of those successes. Deriving `show` from first_run directly
     unmounted the modal mid-flow — the finale was unreachable in production. */
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (choice === "" && onboarding?.first_run) setOpen(true);
  }, [choice, onboarding]);

  const [step, setStep] = useState<1 | 2 | 3>(1);

  // --- Step 1: connect a model -------------------------------------------
  /* v1.310.0: "ready" (a real provider answers) and "the demo still answers by
     default" are DIFFERENT facts. A signed-in Claude Code or a reachable
     Ollama is ready, but every chat turn still goes to the default — the
     offline demo on a fresh install — until the user presses "Use it for
     answers" (W2-1). Step 1 therefore stays on the doors (with that press on
     top) until the default is the user's own; it never says "answers are
     real" over the demo (finding mock-default-trap-cli-ollama). */
  const availableProviders = availableRows(health);
  const providerReady = availableProviders.length > 0;
  const demoDefault = isDemoDefault(health);

  /* The provider the user just enabled through a door: the first task names
     it explicitly so a demo default cannot answer it. */
  const [justEnabled, setJustEnabled] = useState<string | null>(null);

  /* "Sign in" opens a Build pane the user must SEE to type /login, so the
     wizard steps aside (minimised to a pill) instead of covering it — the
     latch still holds; nothing is finished. */
  const [minimized, setMinimized] = useState(false);

  // --- Step 2: optional voice --------------------------------------------
  const dictation = useDictation();
  const [voiceTesting, setVoiceTesting] = useState(false);
  const [voiceHeard, setVoiceHeard] = useState("");
  const [voiceSkipped, setVoiceSkipped] = useState(false);
  const voiceDone = voiceHeard.trim().length > 0 || voiceSkipped;

  // One utterance: the moment a non-empty transcript arrives, we've heard the
  // user — capture it, stop listening, and green the step.
  useEffect(() => {
    if (voiceTesting && dictation.transcript.trim()) {
      setVoiceHeard(dictation.transcript.trim());
      setVoiceTesting(false);
      dictation.stop();
      refreshAll();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [voiceTesting, dictation.transcript]);

  function startVoiceTest() {
    setVoiceHeard("");
    setVoiceSkipped(false);
    dictation.reset();
    setVoiceTesting(true);
    dictation.start();
  }
  function stopVoiceTest() {
    setVoiceTesting(false);
    dictation.stop();
  }
  function skipVoice() {
    stopVoiceTest();
    setVoiceSkipped(true);
    refreshAll();
  }

  // --- Step 3: first magic task ------------------------------------------
  const [task, setTask] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [runDone, setRunDone] = useState(false);

  const { data: detail } = usePolledApi<SessionDetail>(
    sessionId && !runDone ? `/sessions/${sessionId}` : null,
    1200,
  );
  // NESTED endpoint: status lives at .session.status, NOT the top level.
  const runStatus = detail?.session.status ?? null;
  const runResult = detail?.session.summary ?? "";
  const runTools = detail?.transcript.tools ?? [];
  /* Who ACTUALLY answered (the session row, v1.310.0). "mock" = the demo. */
  const runProvider = detail?.session.provider ?? "";
  const runModel = detail?.session.model ?? "";
  const ranOnDemo = runProvider === "mock";
  /* The run's files, from the ledger — read once the run has finished. */
  const { data: runResultFiles } = useApi<FirstTaskResult>(
    sessionId && runDone && runStatus === "completed" && !ranOnDemo
      ? `/sessions/${sessionId}/result`
      : null,
  );
  const runFiles = runResultFiles ? writtenFiles(runResultFiles) : null;

  /* The finale's press (review, v1.310.0). The first task named its model, so
     it answered for real — but every chat turn after the wizard still goes to
     the DEFAULT, which is still the offline demo. "It worked" followed by a
     chat full of scripted replies is the mock-default trap by another door,
     so while the demo is the default the finale offers the explicit W2-1
     press for the model that just answered. A click, never automatic (cloud
     vs local is the user's call), and it never navigates: the user reads
     what happened and then chooses "Start using Iron Jarvis". */
  const finalePress = useAnswerPress(refreshAll);
  /* Kept on screen after a press so its answer stays readable once /health
     follows the new default (the press itself then goes away). */
  const finaleCandidate =
    runStatus === "completed" && !ranOnDemo && (demoDefault || finalePress.note !== null)
      ? candidateFor(health, runProvider)
      : null;
  /* Which model the pinned first task will use, said BEFORE it runs. */
  const pinnedFor = demoDefault ? candidateFor(health, firstTaskProvider(health, justEnabled) ?? "") : null;

  useEffect(() => {
    if (runStatus && TERMINAL.has(runStatus)) {
      setRunDone(true);
      refreshAll();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runStatus]);

  async function runFirstTask(preset?: string) {
    const trimmed = (preset ?? task).trim();
    if (!trimmed || submitting || !providerReady) return;
    if (preset) setTask(preset);
    setSubmitting(true);
    setSubmitError(null);
    setSessionId(null);
    setRunDone(false);
    try {
      /* v1.310.0: name the provider explicitly while the default is still
         the demo (finding wizard-first-task-claims-real-on-mock) — the
         inherited API name for a signed-in CLI so the quality dial applies.
         A real default the user chose is never overridden: no pin then. */
      const provider = firstTaskProvider(health, justEnabled);
      const s = await post<SessionView>("/sessions", {
        task: trimmed,
        agent_type: "builder",
        wait: false,
        ...(provider ? { provider } : {}),
      });
      setSessionId(s.id);
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  // --- close ------------------------------------------------------------
  function finish(value: "done" | "demo") {
    localStorage.setItem(CHOICE_KEY, value);
    setChoice(value);
  }

  // --- reconcile step titles/done onto the /onboarding checklist ---------
  const byKey = (key: string): OnboardingStep | undefined =>
    onboarding?.checklist.find((s) => s.key === key);
  const connectStep = byKey("connect_ai");
  const voiceStep = byKey("set_up_voice");
  const sessionStep = byKey("first_session");

  const steps = [
    { n: 1 as const, label: "Connect", title: connectStep?.title ?? "Connect a model", done: providerReady },
    { n: 2 as const, label: "Voice", title: voiceStep?.title ?? "Set up voice (optional)", done: voiceDone },
    {
      n: 3 as const,
      label: "First task",
      title: sessionStep?.title ?? "Run your first task",
      // The demo's "completed" is not a first task done (v1.310.0).
      done: runStatus === "completed" && !ranOnDemo,
    },
  ];

  /* Latched open; only finish() (or the skip link) closes it — see above.
     Minimised (v1.310.0) is NOT closed: the user stepped out to a Build pane
     to sign in, and the pill brings the wizard back where they left it. */
  const latched = open && choice === "";
  const show = latched && !minimized;

  return (
    <AnimatePresence>
      {latched && minimized && (
        <button
          key="first-run-pill"
          type="button"
          onClick={() => {
            setMinimized(false);
            refreshAll();
          }}
          className="fixed bottom-4 right-4 z-[80] inline-flex items-center gap-1.5 rounded-full border border-accent/30 bg-ink-950 px-4 py-2 text-xs font-semibold text-accent-soft shadow-glow-sm transition-colors hover:bg-accent/[0.08]"
        >
          <Sparkles size={13} aria-hidden="true" /> Back to setting up Iron Jarvis
        </button>
      )}
      {show && (
        <m.div
          key="first-run"
          role="dialog"
          aria-modal="true"
          aria-labelledby="ij-first-run-title"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
          className="fixed inset-0 z-[80] overflow-y-auto bg-black/70 backdrop-blur-md"
        >
          <div className="flex min-h-full items-center justify-center p-4">
            <m.div
              initial={{ opacity: 0, y: 16, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 8, scale: 0.98 }}
              transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
              className="relative w-full max-w-xl overflow-hidden rounded-2xl border border-accent/20 bg-ink-950 shadow-glow-sm"
            >
              <div className="pointer-events-none absolute -right-14 -top-20 h-56 w-56 rounded-full bg-accent/10 blur-3xl" />

              <div className="relative p-7">
                {/* Wordmark / welcome */}
                <div className="flex items-center gap-4">
                  <ArcMark />
                  <div>
                    <h1
                      id="ij-first-run-title"
                      className="text-xl font-semibold tracking-tight text-zinc-50"
                    >
                      Welcome to Iron Jarvis
                    </h1>
                    <p className="text-sm text-zinc-400">
                      Your local-first AI operating system. Three quick steps — all right here.
                    </p>
                  </div>
                </div>

                {/* Stepper */}
                <div className="mt-6 flex items-center gap-2">
                  {steps.map((s, i) => {
                    const active = step === s.n;
                    return (
                      <div key={s.n} className="flex flex-1 items-center gap-2">
                        <button
                          onClick={() => setStep(s.n)}
                          className={`flex min-w-0 flex-1 items-center gap-2 rounded-lg border px-2.5 py-1.5 text-left transition-colors ${
                            active
                              ? "border-accent/40 bg-accent/[0.08]"
                              : "border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.04]"
                          }`}
                        >
                          {s.done ? (
                            <CheckCircle2 size={15} className="shrink-0 text-emerald-400" />
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
                            className={`truncate text-xs font-medium ${
                              active ? "text-zinc-100" : s.done ? "text-zinc-400" : "text-zinc-500"
                            }`}
                          >
                            {s.label}
                          </span>
                        </button>
                        {i < steps.length - 1 && (
                          <ArrowRight size={12} className="shrink-0 text-zinc-700" aria-hidden="true" />
                        )}
                      </div>
                    );
                  })}
                </div>

                {/* ---------------- STEP 1: connect ---------------- */}
                {step === 1 && (
                  <div className="mt-5">
                    <div className="flex items-center justify-between">
                      <h2 className="text-sm font-semibold text-zinc-100">
                        {connectStep?.title ?? "Connect a model"}
                      </h2>
                      <button
                        onClick={refreshAll}
                        className="inline-flex items-center gap-1 text-[11px] text-zinc-400 transition-colors hover:text-zinc-200"
                      >
                        <RefreshCw size={11} /> Re-check
                      </button>
                    </div>

                    {providerReady && !demoDefault ? (
                      /* The default is the user's own real choice: say WHICH
                         model answers — the one claim worth making here. */
                      <div className="mt-3 rounded-xl border border-emerald-500/25 bg-emerald-500/[0.06] px-4 py-3">
                        <div className="flex items-center gap-2 text-sm font-medium text-emerald-200">
                          <CheckCircle2 size={16} className="text-emerald-400" />
                          Connected — ready to use
                        </div>
                        <p className="mt-1 text-xs text-emerald-300/80">
                          Answers come from {friendlyProvider(health?.default_provider ?? "")}
                          {health?.default_model ? ` (${health.default_model})` : ""}.
                        </p>
                      </div>
                    ) : (
                      <ConnectDoors
                        className="mt-2"
                        onChanged={refreshAll}
                        onEnabled={setJustEnabled}
                        onOpenPane={() => setMinimized(true)}
                      />
                    )}
                  </div>
                )}

                {/* ---------------- STEP 2: voice ---------------- */}
                {step === 2 && (
                  <div className="mt-5">
                    <div className="flex items-center justify-between">
                      <h2 className="text-sm font-semibold text-zinc-100">
                        {voiceStep?.title ?? "Set up voice (optional)"}
                      </h2>
                      <button
                        onClick={refreshAll}
                        className="inline-flex items-center gap-1 text-[11px] text-zinc-400 transition-colors hover:text-zinc-200"
                      >
                        <RefreshCw size={11} /> Re-check
                      </button>
                    </div>
                    <p className="mt-2 text-xs leading-relaxed text-zinc-500">
                      Talk to Iron Jarvis hands-free. This is optional — you can always skip it
                      and set it up later.
                    </p>

                    {voiceDone ? (
                      <div className="mt-3 rounded-xl border border-emerald-500/25 bg-emerald-500/[0.06] px-4 py-3">
                        <div className="flex items-center gap-2 text-sm font-medium text-emerald-200">
                          <CheckCircle2 size={16} className="text-emerald-400" />
                          {voiceHeard ? "Microphone works" : "Skipped — set up voice later"}
                        </div>
                        {voiceHeard && (
                          <p className="mt-1 text-xs text-emerald-300/80">Heard: “{voiceHeard}”</p>
                        )}
                      </div>
                    ) : (
                      <div className="mt-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3.5">
                        {dictation.supported ? (
                          <>
                            <button
                              onClick={voiceTesting ? stopVoiceTest : startVoiceTest}
                              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors ${
                                voiceTesting
                                  ? "border border-accent/40 bg-accent/[0.1] text-accent-soft"
                                  : "bg-accent text-ink-950 shadow-glow-sm hover:bg-accent-soft"
                              }`}
                            >
                              {voiceTesting ? (
                                <>
                                  <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                                  Listening — say something…
                                </>
                              ) : (
                                <>
                                  <Mic size={13} aria-hidden="true" /> Test your microphone
                                </>
                              )}
                            </button>
                            {dictation.processing && (
                              <p className="mt-2 inline-flex items-center gap-1.5 text-xs text-zinc-500">
                                <Loader2 size={11} className="animate-spin" aria-hidden="true" />
                                transcribing…
                              </p>
                            )}
                            {(dictation.interim || dictation.transcript) && voiceTesting && (
                              <p className="mt-2 text-xs italic text-zinc-400">
                                {dictation.transcript} {dictation.interim}
                              </p>
                            )}
                          </>
                        ) : (
                          <p className="text-xs text-zinc-500">
                            {dictation.reason ??
                              voice?.hint ??
                              "Voice isn't available here yet — you can set it up later."}
                          </p>
                        )}

                        {dictation.error && (
                          <p role="alert" className="mt-2 text-xs text-rose-300">
                            {dictation.error}
                          </p>
                        )}

                        <button
                          onClick={skipVoice}
                          className="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-zinc-400 transition-colors hover:text-zinc-200"
                        >
                          <SkipForward size={12} aria-hidden="true" /> Skip — set up voice later
                        </button>
                      </div>
                    )}
                  </div>
                )}

                {/* ---------------- STEP 3: first task ---------------- */}
                {step === 3 && (
                  <div className="mt-5">
                    <h2 className="text-sm font-semibold text-zinc-100">
                      {sessionStep?.title ?? "Run your first task"}
                    </h2>
                    {/* v1.310.0: no real model ready → the doors, never a task
                       box that would run on the offline demo (however the
                       user got here: Continue or a jump on the stepper). */}
                    {!sessionId && !providerReady ? (
                      <>
                        <p className="mt-2 text-xs leading-relaxed text-zinc-500">
                          First, connect a model — your first task deserves a real answer.
                        </p>
                        <ConnectDoors
                          className="mt-2"
                          onChanged={refreshAll}
                          onEnabled={setJustEnabled}
                          onOpenPane={() => setMinimized(true)}
                        />
                      </>
                    ) : (
                      <p className="mt-2 text-xs leading-relaxed text-zinc-500">
                        Give the agent something small and watch it work end to end — right here.
                        {!sessionId && pinnedFor
                          ? ` This test asks ${pinnedFor.label} by name; chat keeps the offline demo until you choose what answers you.`
                          : ""}
                      </p>
                    )}

                    {!sessionId && providerReady && (
                      <>
                        <div className="mt-3 flex flex-wrap gap-1.5">
                          {SUGGESTIONS.map((s) => (
                            <button
                              key={s}
                              onClick={() => void runFirstTask(s)}
                              disabled={submitting}
                              className="rounded-lg border border-white/10 bg-white/[0.02] px-2.5 py-1 text-left text-xs text-zinc-300 transition-colors hover:border-accent/30 hover:bg-accent/[0.06] disabled:opacity-50"
                            >
                              {s}
                            </button>
                          ))}
                        </div>
                        <form
                          onSubmit={(e) => {
                            e.preventDefault();
                            void runFirstTask();
                          }}
                          className="mt-3 flex items-center gap-2"
                        >
                          <input
                            value={task}
                            onChange={(e) => setTask(e.target.value)}
                            placeholder="…or type your own first task"
                            className="min-w-0 flex-1 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent/40 focus:outline-none"
                          />
                          <button
                            type="submit"
                            disabled={!task.trim() || submitting}
                            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-ink-950 shadow-glow-sm transition-colors hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            {submitting ? (
                              <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                            ) : (
                              <Play size={13} aria-hidden="true" />
                            )}
                            Run it
                          </button>
                        </form>
                        {submitError && (
                          <p role="alert" className="mt-2 text-xs text-rose-300">
                            Could not start the session: {submitError}
                          </p>
                        )}
                      </>
                    )}

                    {/* Live transcript + result */}
                    {sessionId && (
                      <div className="mt-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3.5">
                        {/* THE VERDICT reads the provider that ACTUALLY answered
                            (the session row), never the health list (v1.310.0):
                            a pin the daemon could not honour, or a demo
                            default, shows up HERE as provider "mock". */}
                        <div
                          data-testid="first-task-verdict"
                          className={`flex items-start gap-2 text-xs font-medium ${
                            ranOnDemo ? "text-amber-200" : "text-zinc-300"
                          }`}
                        >
                          {ranOnDemo ? (
                            <CircleAlert size={14} className="mt-px shrink-0 text-amber-400" />
                          ) : runStatus === "completed" ? (
                            <CheckCircle2 size={14} className="mt-px shrink-0 text-emerald-400" />
                          ) : runStatus && TERMINAL.has(runStatus) ? (
                            <Wand2 size={14} className="mt-px shrink-0 text-rose-400" />
                          ) : (
                            <Loader2 size={14} className="mt-px shrink-0 animate-spin text-accent-soft" aria-hidden="true" />
                          )}
                          <span>
                            {ranOnDemo
                              ? "That was the offline demo — no model ran. Its reply below is a script, not an answer."
                              : runStatus === "completed"
                                ? runProvider
                                  ? `It worked — answered by ${friendlyProvider(runProvider)} (${runProvider}${
                                      runModel ? ` · ${runModel}` : ""
                                    }).`
                                  : "Finished."
                                : runStatus === "failed"
                                  ? "The task failed"
                                  : runStatus === "cancelled"
                                    ? "The task was cancelled"
                                    : "Working…"}
                          </span>
                        </div>

                        {runTools.length > 0 && (
                          <ul className="mt-2.5 space-y-1">
                            {runTools.slice(-6).map((t) => (
                              <li key={t.id} className="flex items-center gap-2 text-[11px] text-zinc-500">
                                <span
                                  className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                                    t.ok ? "bg-emerald-400" : "bg-rose-400"
                                  }`}
                                />
                                <span className="font-mono text-zinc-400">{t.tool}</span>
                              </li>
                            ))}
                          </ul>
                        )}

                        {runDone && runResult && (
                          <div className="mt-3 rounded-lg border border-white/[0.06] bg-black/30 p-3">
                            <div
                              className={`mb-1 flex items-center gap-1.5 text-[11px] font-medium ${
                                ranOnDemo ? "text-amber-300/80" : "text-accent-soft"
                              }`}
                            >
                              <Sparkles size={12} /> {ranOnDemo ? "What the demo said" : "Result"}
                            </div>
                            <p className="whitespace-pre-wrap text-xs leading-relaxed text-zinc-300">
                              {runResult}
                            </p>
                          </div>
                        )}

                        {/* The handover (the SessionFiles rule): what a REAL run
                            wrote, by name — the answer often lives in a file the
                            summary never mentions — or a plain "none". The demo's
                            files are part of its script, so they are not listed. */}
                        {runStatus === "completed" && !ranOnDemo && runFiles && (
                          <div className="mt-2 text-[11px] leading-relaxed text-zinc-400">
                            {runFiles.length === 0 ? (
                              <p>It didn&apos;t write any files — the answer is all above.</p>
                            ) : (
                              <>
                                <p>
                                  It wrote {runFiles.length === 1 ? "1 file" : `${runFiles.length} files`} — open
                                  the full session to see or download {runFiles.length === 1 ? "it" : "them"}:
                                </p>
                                <ul className="mt-1 space-y-0.5">
                                  {runFiles.slice(0, 8).map((f) => (
                                    <li key={f} className="font-mono text-zinc-300">
                                      {f}
                                    </li>
                                  ))}
                                  {runFiles.length > 8 && (
                                    <li className="text-zinc-500">…and {runFiles.length - 8} more</li>
                                  )}
                                </ul>
                              </>
                            )}
                          </div>
                        )}

                        {ranOnDemo && (
                          <button
                            onClick={() => {
                              setSessionId(null);
                              setRunDone(false);
                              setStep(1);
                            }}
                            className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/[0.06] px-3 py-1.5 text-xs font-medium text-amber-200 transition-colors hover:bg-amber-500/[0.1]"
                          >
                            Connect a model for real answers
                          </button>
                        )}

                        {finaleCandidate && (
                          <div
                            data-testid="first-task-use-for-answers"
                            className="mt-3 rounded-lg border border-accent/25 bg-accent/[0.05] px-3 py-2.5"
                          >
                            {demoDefault && (
                              <>
                                <p className="text-[11px] leading-relaxed text-zinc-300">
                                  This test asked {finaleCandidate.label} by name. Chat doesn&apos;t use it yet:
                                  until you choose, chat replies are a script, not real answers —
                                  nothing switches on its own.
                                  {finaleCandidate.where
                                    ? ` With ${finaleCandidate.label}, what you type ${finaleCandidate.where}.`
                                    : ""}
                                </p>
                                <button
                                  type="button"
                                  onClick={() => void finalePress.press(finaleCandidate)}
                                  disabled={finalePress.busy !== null}
                                  className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-ink-950 shadow-glow-sm transition-colors hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-40"
                                >
                                  {finalePress.busy !== null && (
                                    <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                                  )}
                                  Use {finaleCandidate.label} for answers
                                </button>
                              </>
                            )}
                            <AnswerPressNote note={finalePress.note} />
                          </div>
                        )}

                        {runStatus === "completed" && (
                          <div className="mt-3 flex items-center gap-3">
                            <button
                              onClick={() => {
                                /* v1.197.0: finishing lands on /chat — the product's
                                   hero surface — instead of dead-ending on whatever
                                   page happened to sit under the modal. */
                                finish("done");
                                router.push("/chat");
                              }}
                              className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-ink-950 shadow-glow-sm transition-colors hover:bg-accent-soft"
                            >
                              <Sparkles size={13} /> Start using Iron Jarvis
                            </button>
                            <Link
                              href={`/sessions/${sessionId}`}
                              onClick={() => finish("done")}
                              className="inline-flex items-center gap-1 text-xs font-medium text-zinc-400 transition-colors hover:text-zinc-200"
                            >
                              View full session <ArrowRight size={12} />
                            </Link>
                          </div>
                        )}

                        {runStatus && TERMINAL.has(runStatus) && runStatus !== "completed" && (
                          <button
                            onClick={() => {
                              setSessionId(null);
                              setRunDone(false);
                            }}
                            className="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-zinc-400 transition-colors hover:text-zinc-200"
                          >
                            <RefreshCw size={12} /> Try another task
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {/* Footer navigation */}
                <div className="mt-6 flex items-center justify-between border-t border-white/[0.06] pt-4">
                  {step > 1 ? (
                    <button
                      onClick={() => setStep((s) => (s - 1) as 1 | 2 | 3)}
                      className="inline-flex items-center gap-1.5 text-xs font-medium text-zinc-400 transition-colors hover:text-zinc-200"
                    >
                      <ArrowLeft size={13} /> Back
                    </button>
                  ) : (
                    <button
                      onClick={() => finish("demo")}
                      className="text-xs text-zinc-500 underline decoration-zinc-700 underline-offset-4 transition-colors hover:text-zinc-300"
                    >
                      Skip — try demo mode (output simulated)
                    </button>
                  )}

                  {step < 3 && (
                    <button
                      onClick={() => setStep((s) => (s + 1) as 1 | 2 | 3)}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3.5 py-1.5 text-xs font-semibold text-ink-950 shadow-glow-sm transition-colors hover:bg-accent-soft"
                    >
                      Continue <ArrowRight size={13} />
                    </button>
                  )}
                </div>
              </div>
            </m.div>
          </div>
        </m.div>
      )}
    </AnimatePresence>
  );
}
