"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowRight,
  Cable,
  CheckCircle2,
  Loader2,
  Play,
  SkipForward,
} from "lucide-react";
import { post } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { useDaemon } from "@/lib/daemon";
import type { Onboarding } from "@/lib/types";

/** One-shot choice: "done" (connected / ran a task / skipped) or "demo". */
const CHOICE_KEY = "ij_first_run_choice";

/** Never overlay the pages the wizard sends people to — the whole point of
 *  step 1 is to go connect a model, so /connections (and /settings, for the
 *  token) must stay fully usable. */
const EXEMPT_PREFIXES = ["/connections", "/settings"];

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
 * Blocking first-hour overlay for a brand-new install.
 *
 * Show contract (ALL must hold):
 *  - `GET /onboarding` resolved successfully AND reports `first_run: true`
 *    (pending or errored fetch — e.g. daemon offline — renders nothing)
 *  - localStorage `ij_first_run_choice` is unset (nothing renders until the
 *    key has actually been read, so there is no flash)
 *  - the current route is not /connections or /settings (no trapping the
 *    user away from the very pages that complete setup)
 *
 * Two steps + an always-visible escape hatch:
 *  1. Connect a real model (links to /connections; the shared /health poll
 *     flips this to "done" live and auto-advances)
 *  2. Run one real task (POST /sessions {task, agent_type, wait:false}) or
 *     skip — either way `ij_first_run_choice = "done"` and the wizard closes.
 *  Escape hatch: "Continue in demo mode" sets `"demo"` and closes; the global
 *  SimulatedBanner keeps warning about fabricated output afterwards.
 */
export function FirstRunWizard() {
  const pathname = usePathname();
  const router = useRouter();
  const { health } = useDaemon();
  const { data } = useApi<Onboarding>("/onboarding");

  // null = storage not read yet (render NOTHING — avoids a flash);
  // "" = unset (eligible to show); anything else = a prior choice.
  const [choice, setChoice] = useState<string | null>(null);
  useEffect(() => {
    setChoice(localStorage.getItem(CHOICE_KEY) ?? "");
  }, []);

  const [task, setTask] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // The shared /health poll (every 5s). Mock is already filtered out of
  // `providers`, so any available entry means a REAL model is wired up.
  const availableProviders = (health?.providers ?? []).filter((p) => p.available);
  const providerReady = availableProviders.length > 0;
  const step: 1 | 2 = providerReady ? 2 : 1;

  function finish(value: "done" | "demo") {
    localStorage.setItem(CHOICE_KEY, value);
    setChoice(value);
  }

  async function runFirstTask() {
    const trimmed = task.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await post<unknown>("/sessions", {
        task: trimmed,
        agent_type: "builder",
        wait: false,
      });
      finish("done");
      router.push("/sessions");
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  const exempt = EXEMPT_PREFIXES.some(
    (p) => pathname === p || pathname.startsWith(`${p}/`),
  );
  const show = !exempt && choice === "" && data !== null && data.first_run;

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
              className="relative w-full max-w-lg overflow-hidden rounded-2xl border border-accent/20 bg-ink-950 shadow-glow-sm"
            >
              {/* glow flourish */}
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
                      Your local-first AI operating system. Two steps to make it real.
                    </p>
                  </div>
                </div>

                {/* Step 1 — Connect your AI */}
                <div
                  className={`mt-6 rounded-xl border px-4 py-3.5 transition-colors ${
                    step === 1
                      ? "border-accent/30 bg-accent/[0.06]"
                      : "border-white/[0.06] bg-white/[0.02]"
                  }`}
                >
                  <div className="flex items-center gap-2.5">
                    {providerReady ? (
                      <CheckCircle2 size={18} className="shrink-0 text-emerald-400" />
                    ) : (
                      <span className="grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full border border-accent/40 text-[10px] font-semibold text-accent-soft">
                        1
                      </span>
                    )}
                    <span
                      className={`text-sm font-medium ${
                        providerReady ? "text-zinc-400" : "text-zinc-100"
                      }`}
                    >
                      Connect your AI
                    </span>
                    {providerReady && (
                      <span className="truncate text-xs text-emerald-300/80">
                        {availableProviders.map((p) => p.provider).join(", ")} connected
                      </span>
                    )}
                  </div>
                  {!providerReady && (
                    <>
                      <p className="mt-2 text-xs leading-relaxed text-zinc-500">
                        Without a real model — Claude, OpenAI, or local Ollama — every
                        reply is fabricated by an offline mock. Connect one so answers
                        are real.
                      </p>
                      <div className="mt-3 flex items-center gap-3">
                        <Link
                          href="/connections"
                          className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-ink-950 shadow-glow-sm transition-colors hover:bg-accent-soft"
                        >
                          <Cable size={13} /> Connect a model <ArrowRight size={13} />
                        </Link>
                        <span className="inline-flex items-center gap-1.5 text-[11px] text-zinc-500">
                          <Loader2 size={11} className="animate-spin" aria-hidden="true" />
                          watching for a connection…
                        </span>
                      </div>
                    </>
                  )}
                </div>

                {/* Step 2 — Run one real task */}
                <div
                  className={`mt-3 rounded-xl border px-4 py-3.5 transition-colors ${
                    step === 2
                      ? "border-accent/30 bg-accent/[0.06]"
                      : "border-white/[0.06] bg-white/[0.02] opacity-60"
                  }`}
                >
                  <div className="flex items-center gap-2.5">
                    <span
                      className={`grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full border text-[10px] font-semibold ${
                        step === 2
                          ? "border-accent/40 text-accent-soft"
                          : "border-white/15 text-zinc-500"
                      }`}
                    >
                      2
                    </span>
                    <span
                      className={`text-sm font-medium ${
                        step === 2 ? "text-zinc-100" : "text-zinc-400"
                      }`}
                    >
                      Run one real task
                    </span>
                  </div>
                  {step === 2 && (
                    <>
                      <p className="mt-2 text-xs leading-relaxed text-zinc-500">
                        Give the agent something small and watch it work end to end.
                      </p>
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          void runFirstTask();
                        }}
                        className="mt-3 flex items-center gap-2"
                      >
                        <label htmlFor="ij-first-task" className="sr-only">
                          Your first task
                        </label>
                        <input
                          id="ij-first-task"
                          value={task}
                          onChange={(e) => setTask(e.target.value)}
                          placeholder="e.g. Summarize what you can do for me"
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
                      <button
                        onClick={() => finish("done")}
                        className="mt-2.5 inline-flex items-center gap-1.5 text-xs font-medium text-zinc-400 transition-colors hover:text-zinc-200"
                      >
                        <SkipForward size={12} aria-hidden="true" /> Skip — I&apos;ll
                        explore first
                      </button>
                    </>
                  )}
                </div>

                {/* Escape hatch — always visible, honest about the consequence. */}
                <div className="mt-5 border-t border-white/[0.06] pt-4 text-center">
                  <button
                    onClick={() => finish("demo")}
                    className="text-xs text-zinc-500 underline decoration-zinc-700 underline-offset-4 transition-colors hover:text-zinc-300"
                  >
                    Continue in demo mode — output will be simulated
                  </button>
                </div>
              </div>
            </motion.div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
