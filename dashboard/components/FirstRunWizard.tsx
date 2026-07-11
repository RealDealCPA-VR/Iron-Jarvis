"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  KeyRound,
  Loader2,
  Mic,
  Play,
  RefreshCw,
  Sparkles,
  SkipForward,
  Wand2,
} from "lucide-react";
import { post, put } from "@/lib/api";
import { useApi, usePolledApi } from "@/lib/useApi";
import { useDaemon } from "@/lib/daemon";
import { useDictation } from "@/lib/useDictation";
import type {
  ConnectionTestResult,
  Onboarding,
  OnboardingStep,
  SessionDetail,
  SessionView,
} from "@/lib/types";

/** One-shot choice: "done" (finished the flow) or "demo" (opted into mock). */
const CHOICE_KEY = "ij_first_run_choice";

/** Inherited CLI providers we celebrate as an instant "no setup needed" path. */
const INHERITED = new Set(["claude-cli", "codex-cli", "grok-cli", "ollama"]);

type KeyProvider = "anthropic" | "openai" | "custom";

const KEY_PROVIDERS: { id: KeyProvider; label: string; placeholder: string }[] = [
  { id: "anthropic", label: "Anthropic", placeholder: "sk-ant-…" },
  { id: "openai", label: "OpenAI", placeholder: "sk-…" },
  { id: "custom", label: "Custom endpoint", placeholder: "sk-… (optional)" },
];

/** One-tap magic tasks — small, fast, and unmistakably real when they run. */
const SUGGESTIONS = [
  "Summarize what you can do for me in 5 bullets",
  "Write a short haiku about an iron AI assistant",
  "Make a markdown checklist for launching a product",
  "Explain what makes you different from a plain chatbot",
];

interface VoiceStatus {
  available: boolean;
  backend: string | null;
  hint: string;
}

const TERMINAL = new Set(["completed", "failed", "cancelled"]);

/** The arc-reactor brand mark (mirrors the sidebar's, sized for the hero). */
function ArcMark() {
  return (
    <span className="relative grid h-12 w-12 place-items-center">
      <span className="absolute inset-0 rounded-xl bg-accent/15 blur-[8px]" />
      <svg
        viewBox="0 0 24 24"
        className="relative h-12 w-12 drop-shadow-[0_0_8px_rgba(34,211,238,0.55)]"
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
 * Show contract (ALL must hold):
 *  - `GET /onboarding` resolved AND reports `first_run: true`
 *  - localStorage `ij_first_run_choice` is unset (nothing renders until the key
 *    has actually been read, so there is no flash)
 *
 * Three steps, reconciled onto the /onboarding checklist so its titles/done
 * states match OnboardingWelcome exactly (no hardcoded drift):
 *  1. Connect a model INLINE — inherited CLIs show as an instant fast path; a
 *     rescan button re-detects logged-in CLIs / Ollama; a compact API-key form
 *     (anthropic / openai / custom endpoint) connects + tests without leaving.
 *  2. Optional VOICE — test the mic through useDictation (greens on a non-empty
 *     transcript) or skip; voice NEVER blocks.
 *  3. First MAGIC task — one-tap suggestions or free text; the streaming
 *     transcript + final result render in the modal, ending on an honest
 *     "it works" celebration. Real errors are shown, never fabricated.
 */
export function FirstRunWizard() {
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

  const [step, setStep] = useState<1 | 2 | 3>(1);

  // --- Step 1: connect a model -------------------------------------------
  const availableProviders = (health?.providers ?? []).filter((p) => p.available);
  const providerReady = availableProviders.length > 0;
  const inheritedReady = availableProviders.filter((p) => INHERITED.has(p.provider));

  const [keyProvider, setKeyProvider] = useState<KeyProvider>("anthropic");
  const [keyValue, setKeyValue] = useState("");
  const [customBaseUrl, setCustomBaseUrl] = useState("");
  const [customModel, setCustomModel] = useState("");
  const [keyBusy, setKeyBusy] = useState(false);
  const [keyResult, setKeyResult] = useState<ConnectionTestResult | null>(null);
  const [keyError, setKeyError] = useState<string | null>(null);
  const [rescanBusy, setRescanBusy] = useState(false);

  async function rescan() {
    setRescanBusy(true);
    try {
      await post("/providers/rescan");
    } catch {
      /* ignore — refreshAll surfaces the real state either way */
    } finally {
      refreshAll();
      setRescanBusy(false);
    }
  }

  async function connectKey() {
    const isCustom = keyProvider === "custom";
    if (isCustom ? !customBaseUrl.trim() : !keyValue.trim()) return;
    setKeyBusy(true);
    setKeyError(null);
    setKeyResult(null);
    try {
      if (isCustom) {
        // Save the endpoint FIRST so a key-less local server (LM Studio, etc.)
        // still sticks, then optionally attach a key.
        await put("/settings", {
          values: { custom_base_url: customBaseUrl.trim(), custom_model: customModel.trim() },
        });
        if (keyValue.trim()) {
          await post(`/connections/custom/key`, { key: keyValue.trim() });
        }
        const result = await post<ConnectionTestResult>(`/connections/custom/test`);
        setKeyResult(result);
      } else {
        await post(`/connections/${keyProvider}/key`, { key: keyValue.trim() });
        const result = await post<ConnectionTestResult>(`/connections/${keyProvider}/test`);
        setKeyResult(result);
      }
      setKeyValue("");
      refreshAll(); // immediate green — don't wait for the 5s health poll
    } catch (e) {
      setKeyError(e instanceof Error ? e.message : String(e));
    } finally {
      setKeyBusy(false);
    }
  }

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

  useEffect(() => {
    if (runStatus && TERMINAL.has(runStatus)) {
      setRunDone(true);
      refreshAll();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runStatus]);

  async function runFirstTask(preset?: string) {
    const trimmed = (preset ?? task).trim();
    if (!trimmed || submitting) return;
    if (preset) setTask(preset);
    setSubmitting(true);
    setSubmitError(null);
    setSessionId(null);
    setRunDone(false);
    try {
      const s = await post<SessionView>("/sessions", {
        task: trimmed,
        agent_type: "builder",
        wait: false,
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
    { n: 3 as const, label: "First task", title: sessionStep?.title ?? "Run your first task", done: runStatus === "completed" },
  ];

  const show = choice === "" && onboarding !== null && onboarding.first_run;

  return (
    <AnimatePresence>
      {show && (
        <motion.div
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
            <motion.div
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

                    {providerReady ? (
                      <div className="mt-3 rounded-xl border border-emerald-500/25 bg-emerald-500/[0.06] px-4 py-3">
                        <div className="flex items-center gap-2 text-sm font-medium text-emerald-200">
                          <CheckCircle2 size={16} className="text-emerald-400" />
                          {inheritedReady.length > 0
                            ? "Detected — ready to use"
                            : "Connected — ready to use"}
                        </div>
                        <p className="mt-1 text-xs text-emerald-300/80">
                          {availableProviders.map((p) => p.provider).join(", ")} — answers are real.
                        </p>
                      </div>
                    ) : (
                      <>
                        <p className="mt-2 text-xs leading-relaxed text-zinc-500">
                          Without a real model every reply is fabricated by an offline mock.
                          If you&apos;re logged into a CLI (Claude Code, Codex, Grok) or run
                          Ollama, Iron Jarvis can inherit it — just rescan. Otherwise paste a key.
                        </p>

                        {/* Rescan inherited CLIs / Ollama */}
                        <button
                          onClick={rescan}
                          disabled={rescanBusy}
                          className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:bg-white/[0.06] disabled:opacity-50"
                        >
                          {rescanBusy ? (
                            <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                          ) : (
                            <RefreshCw size={13} aria-hidden="true" />
                          )}
                          Rescan for logged-in CLIs / Ollama
                        </button>

                        {/* Compact API-key entry */}
                        <div className="mt-4 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3.5">
                          <div className="mb-2.5 flex items-center gap-1.5 text-xs font-medium text-zinc-300">
                            <KeyRound size={13} className="text-accent-soft" /> Or paste an API key
                          </div>
                          <div className="flex flex-wrap gap-1.5">
                            {KEY_PROVIDERS.map((p) => (
                              <button
                                key={p.id}
                                onClick={() => {
                                  setKeyProvider(p.id);
                                  setKeyResult(null);
                                  setKeyError(null);
                                }}
                                className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ${
                                  keyProvider === p.id
                                    ? "border-accent/40 bg-accent/[0.1] text-accent-soft"
                                    : "border-white/10 text-zinc-400 hover:bg-white/[0.04]"
                                }`}
                              >
                                {p.label}
                              </button>
                            ))}
                          </div>

                          {keyProvider === "custom" && (
                            <div className="mt-2.5 space-y-2">
                              <input
                                value={customBaseUrl}
                                onChange={(e) => setCustomBaseUrl(e.target.value)}
                                placeholder="Base URL (e.g. http://localhost:1234/v1)"
                                className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent/40 focus:outline-none"
                              />
                              <input
                                value={customModel}
                                onChange={(e) => setCustomModel(e.target.value)}
                                placeholder="Model id (optional)"
                                className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent/40 focus:outline-none"
                              />
                            </div>
                          )}

                          <form
                            onSubmit={(e) => {
                              e.preventDefault();
                              void connectKey();
                            }}
                            className="mt-2.5 flex items-center gap-2"
                          >
                            <input
                              type="password"
                              value={keyValue}
                              onChange={(e) => setKeyValue(e.target.value)}
                              placeholder={
                                KEY_PROVIDERS.find((p) => p.id === keyProvider)?.placeholder ?? "key"
                              }
                              className="min-w-0 flex-1 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent/40 focus:outline-none"
                            />
                            <button
                              type="submit"
                              disabled={
                                keyBusy ||
                                (keyProvider === "custom" ? !customBaseUrl.trim() : !keyValue.trim())
                              }
                              className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-ink-950 shadow-glow-sm transition-colors hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-40"
                            >
                              {keyBusy && <Loader2 size={13} className="animate-spin" aria-hidden="true" />}
                              Connect
                            </button>
                          </form>

                          {keyResult && (
                            <p
                              role="status"
                              className={`mt-2 text-xs ${
                                keyResult.ok ? "text-emerald-300" : "text-rose-300"
                              }`}
                            >
                              {keyResult.ok ? "Connected — " : "Not connected — "}
                              {keyResult.detail}
                            </p>
                          )}
                          {keyError && (
                            <p role="alert" className="mt-2 text-xs text-rose-300">
                              {keyError}
                            </p>
                          )}
                        </div>
                      </>
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
                    <p className="mt-2 text-xs leading-relaxed text-zinc-500">
                      Give the agent something small and watch it work end to end — right here.
                    </p>

                    {!sessionId && (
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
                        <div className="flex items-center gap-2 text-xs font-medium">
                          {runStatus === "completed" ? (
                            <CheckCircle2 size={14} className="text-emerald-400" />
                          ) : runStatus && TERMINAL.has(runStatus) ? (
                            <Wand2 size={14} className="text-rose-400" />
                          ) : (
                            <Loader2 size={14} className="animate-spin text-accent-soft" aria-hidden="true" />
                          )}
                          <span className="text-zinc-300">
                            {runStatus === "completed"
                              ? "It works — output is real"
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
                            <div className="mb-1 flex items-center gap-1.5 text-[11px] font-medium text-accent-soft">
                              <Sparkles size={12} /> Result
                            </div>
                            <p className="whitespace-pre-wrap text-xs leading-relaxed text-zinc-300">
                              {runResult}
                            </p>
                          </div>
                        )}

                        {runStatus === "completed" && (
                          <div className="mt-3 flex items-center gap-3">
                            <button
                              onClick={() => finish("done")}
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
            </motion.div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
