"use client";

/**
 * The Overview's first-run strip (v1.310.0, wave 2 — "the first five minutes").
 *
 * Finding overview-buries-first-action: a new user landed on daemon metrics,
 * a reactor hero and thirty module tiles; the one panel that turned a click
 * into a first result ("Try it now") sat inside the collapsed "Systems &
 * admin" card, and the getting-started checklist came after the grid. The
 * value was not obvious in the first minute, and the obvious next action was
 * below the fold.
 *
 * So while setup is unfinished (the page gates on /onboarding's next_step
 * ALONE, so the strip never flickers on a sessions/threads poll) this strip
 * sits directly under the page header, above everything else, and holds the
 * three things a first-time user needs:
 *
 *   1. the welcome checklist (with the honest "which model answers" card);
 *   2. ONE "Ask Jarvis anything" box — it routes to /chat?ask=<text>, which
 *      PREFILLS the composer and runs nothing (suggest, never act: the user's
 *      Enter in Chat is the only thing that starts work, with its receipt);
 *   3. the "Try it now" starters, now pointing at Chat the same way, so the
 *      first result happens in the hero lane instead of a builder session page.
 *
 * Saved tasks that PIN a provider/model are not here: /chat?ask only carries
 * text, so they keep the session path (and their pin) in "Systems & admin".
 */

import { useState, type FormEvent, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, FolderSearch, Mail, MessageSquare, ScrollText, Sparkles } from "lucide-react";
import type { ApiState } from "@/lib/useApi";
import type { Onboarding } from "@/lib/types";
import { OnboardingWelcome } from "@/components/OnboardingWelcome";

/** One-click, broadly-safe starter tasks that take a first-time user straight
 *  to a real result. Shared by the strip (as /chat?ask= links) and by the
 *  Overview's "Systems & admin" once setup is done (as one-click runs). */
export const FIRST_WIN_TASKS: {
  key: string;
  title: string;
  task: string;
  icon: ReactNode;
}[] = [
  {
    key: "downloads",
    title: "Tidy my Downloads",
    task: "List the largest files in my Downloads folder and suggest what's safe to delete",
    icon: <FolderSearch size={18} />,
  },
  {
    key: "examples",
    title: "What can you do?",
    task: "Give me 5 example tasks you can do for me right now",
    icon: <Sparkles size={18} />,
  },
  {
    key: "recap",
    title: "Recap today",
    task: "Summarize what you did for me today",
    icon: <ScrollText size={18} />,
  },
  {
    key: "email",
    title: "Draft a follow-up",
    task: "Draft a polite follow-up email to someone who hasn't replied",
    icon: <Mail size={18} />,
  },
];

/** Where a question goes: Chat, with the text waiting in the composer. */
export function chatAskHref(text: string): string {
  return `/chat?ask=${encodeURIComponent(text)}`;
}

export function FirstRunStrip({ onboarding }: { onboarding: ApiState<Onboarding> }) {
  return (
    <section data-testid="first-run-strip" aria-label="Get started" className="space-y-4">
      <OnboardingWelcome state={onboarding} />
      <AskAndStart />
    </section>
  );
}

/**
 * The one "Ask Jarvis anything" box and the Try-it-now starters (v1.318.0:
 * lifted out so the Simple home, components/overview/HomeStart.tsx, offers
 * the SAME box once setup is done — never a second copy that could drift).
 */
export function AskAndStart({ starters = true }: { starters?: boolean } = {}) {
  const router = useRouter();
  const [text, setText] = useState("");

  function ask(e: FormEvent) {
    e.preventDefault();
    const q = text.trim();
    // A blank press goes nowhere — an empty Chat is not an answer.
    if (!q) return;
    router.push(chatAskHref(q));
  }

  return (
    <div className="card-surface p-5" data-testid="ask-and-start">
      <form onSubmit={ask} className="flex flex-col gap-2 sm:flex-row">
        <div className="relative min-w-0 flex-1">
          <MessageSquare
            size={16}
            className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-accent-soft/80"
          />
          <input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            aria-label="Ask Jarvis anything"
            placeholder="Ask Jarvis anything — “summarize this PDF”, “draft a reply to…”"
            className="w-full rounded-xl border border-white/10 bg-white/[0.03] py-2.5 pl-10 pr-3 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-accent/40 focus:outline-none focus:ring-2 focus:ring-accent/20"
          />
        </div>
        {/* v1.313.0: outlined, not solid. The strip is up only while a
            setup step is next, and that step's button in the card above is
            the page's ONE solid primary — two bright buttons for two
            different "first" actions left a new user guessing. */}
        <button
          type="submit"
          className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-xl border border-accent/35 bg-accent/[0.08] px-4 py-2.5 text-sm font-medium text-accent-soft transition-colors hover:border-accent/50 hover:bg-accent/[0.14]"
        >
          Ask <ArrowRight size={14} />
        </button>
      </form>
      <p className="mt-2 text-xs text-zinc-500">
        Opens Chat with your question ready to go. Nothing runs until you press send.
      </p>
      {starters && (
        <>
        <div className="mt-5 mb-2 text-[12.5px] font-semibold tracking-wide text-zinc-300">
          Try it now
          <span className="ml-2 text-xs font-normal text-zinc-500">or start with one of these</span>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {FIRST_WIN_TASKS.map((t) => (
            <Link
              key={t.key}
              href={chatAskHref(t.task)}
              className="group relative flex h-full flex-col gap-3 overflow-hidden rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4 text-left transition-all duration-300 hover:-translate-y-0.5 hover:border-accent/30 hover:bg-accent/[0.04] hover:shadow-card-hover"
            >
              <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-accent/20 bg-accent/[0.08] text-accent-soft">
                {t.icon}
              </span>
              <div className="flex-1">
                <div className="text-sm font-semibold text-zinc-100">{t.title}</div>
                <p className="mt-1 line-clamp-3 text-xs leading-relaxed text-zinc-500">{t.task}</p>
              </div>
              <span className="flex items-center gap-1.5 text-xs font-medium text-accent-soft">
                Open in Chat
                <ArrowRight size={13} className="transition-transform group-hover:translate-x-0.5" />
              </span>
            </Link>
          ))}
        </div>
        </>
      )}
    </div>
  );
}
