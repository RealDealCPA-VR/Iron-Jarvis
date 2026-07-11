"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  Sparkles,
  CheckCircle2,
  Circle,
  ArrowRight,
  X,
  Rocket,
  Wrench,
  TriangleAlert,
} from "lucide-react";
import { useApi } from "@/lib/useApi";
import type { Onboarding, OnboardingStep } from "@/lib/types";

const DISMISS_KEY = "ij_onboarding_dismissed";

/** Map a checklist step to the page that completes it. */
const STEP_LINK: Record<string, { href: string; cta: string }> = {
  connect_ai: { href: "/connections", cta: "Connect a model" },
  first_session: { href: "/sessions", cta: "New session" },
  work_with_document: { href: "/documents", cta: "Open Documents" },
  teach_style: { href: "/memory?scope=lessons", cta: "Review lessons" },
  set_up_voice: { href: "/connections", cta: "Enable voice" },
};

function stepLink(step: OnboardingStep) {
  return STEP_LINK[step.key] ?? { href: "/sessions", cta: "Get started" };
}

export function OnboardingWelcome() {
  const { data } = useApi<Onboarding>("/onboarding");
  const [dismissed, setDismissed] = useState(true); // assume dismissed until we read storage

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

  const failingChecks = (data.doctor?.checks ?? []).filter((c) => !c.ok);
  const relevant = data.first_run || data.next_step !== null || !data.doctor?.ok;

  // Everything is set up — nothing to nudge.
  if (!relevant) return null;

  // Collapsed: a small "Setup" re-open affordance.
  if (dismissed) {
    return (
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
  }

  return (
    <motion.div
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
                A few quick steps to get the most out of your local AI operating system.
              </p>
            </div>
          </div>
          <button
            onClick={dismiss}
            title="Dismiss"
            className="rounded-lg p-1 text-zinc-500 transition-colors hover:bg-white/[0.05] hover:text-zinc-300"
          >
            <X size={16} />
          </button>
        </div>

        {/* Checklist */}
        <ol className="mt-5 space-y-2">
          {data.checklist.map((step) => {
            const isNext = data.next_step?.key === step.key;
            const link = stepLink(step);
            return (
              <li
                key={step.key}
                className={`flex items-start gap-3 rounded-xl border px-3.5 py-3 transition-colors ${
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
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
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
                  <p className="mt-0.5 text-xs leading-relaxed text-zinc-500">{step.detail}</p>
                </div>
                {/* Always clickable — a completed step still links to its page
                    (e.g. "Connect your AI" done -> open Connections to manage
                    it). A done row previously rendered NO control at all, which
                    read as a broken button. */}
                <Link
                  href={link.href}
                  className={`mt-0.5 inline-flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                    step.done
                      ? "text-zinc-500 hover:bg-white/[0.05] hover:text-zinc-300"
                      : isNext
                        ? "bg-accent text-ink-950 shadow-glow-sm hover:bg-accent-soft"
                        : "border border-white/10 text-zinc-300 hover:bg-white/[0.05]"
                  }`}
                >
                  {step.done ? "Open" : link.cta} <ArrowRight size={13} />
                </Link>
              </li>
            );
          })}
        </ol>

        {/* Doctor: surface failing checks with their fix. */}
        {failingChecks.length > 0 && (
          <div className="mt-5 rounded-xl border border-amber-500/20 bg-amber-500/[0.05] p-3.5">
            <div className="mb-2 flex items-center gap-2 text-xs font-medium text-amber-200">
              <Wrench size={13} /> Environment checks
            </div>
            <ul className="space-y-2">
              {failingChecks.map((c) => (
                <li key={c.name} className="flex items-start gap-2.5 text-xs">
                  <TriangleAlert size={13} className="mt-0.5 shrink-0 text-amber-300" />
                  <div className="min-w-0">
                    <span className="font-mono text-amber-100/90">{c.name}</span>
                    <span className="text-zinc-500"> — {c.detail}</span>
                    {c.fix && <div className="mt-0.5 text-zinc-500">{c.fix}</div>}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </motion.div>
  );
}
