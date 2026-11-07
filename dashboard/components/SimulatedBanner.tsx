"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Cable, Cpu, Sparkles, TriangleAlert } from "lucide-react";
import { useDaemon } from "@/lib/daemon";
import { noModelChosen } from "@/lib/onboarding";
// v1.357.0 (G76): the ONE demo name and sentence.
import { DEMO_REPLIES, DEMO_STATE } from "@/lib/demoWords";
// v1.444.0 (ease-of-use review item 2): the ONE way out of the demo.
import { chosenNote, useAnswerPress } from "@/components/onboarding/AnswerPress";
import {
  CONNECT_A_MODEL,
  CONNECTIONS_HREF,
  openConnect,
  pressableCandidates,
  realModelRows,
} from "@/lib/demoExit";

/**
 * A slim, PERSISTENT (deliberately non-dismissable) strip shown whenever the
 * daemon is online but every reply is the offline demo's script. This is the
 * product's biggest trust hazard, so unlike the onboarding checklist there is
 * no way to hide it; the only way to make it go away is to choose a model.
 *
 * Two cases, two honest sentences (v1.313.0):
 *  1. NOTHING is connected — "Demo replies" (v1.357.0; was "Simulated
 *     mode", a second name for the same demo), and the way out is
 *     Connections ("Connect a model").
 *  2. A model IS connected (a Claude/Codex sign-in, Ollama…) but none has
 *     been chosen to answer, so the default is still the demo. Before
 *     v1.313.0 the strip hid the moment any provider was available, and no
 *     page outside the Overview said the replies were fake. Saying "no model
 *     is connected" here would be false, so this case says what is true and
 *     its button opens the title bar's model menu — the pick is one click
 *     away on every page (the Overview's setup card can be dismissed, so it
 *     never sends the user to "/").
 *
 * v1.444.0 (ease-of-use review item 2): "connected" counts only a real model
 * (lib/demoExit.realModelRows — never the demo model or a saved web sign-in).
 * When ONE press can choose it, case 2's button IS that press ("Use Claude
 * Code for answers", POST /onboarding/use-model), which sets the default for
 * every new chat, agent and schedule — the old "Choose a model" changed only
 * the chat on screen. Case 1's "Connect a model" shows the chat page's connect
 * choices where the user is (lib/demoExit.openConnect), else goes to
 * Connections. "Choose a model" stays only for a model this press cannot
 * choose (Grok, OpenCode, a fleet machine), whose menu row is not greyed.
 *
 * Show/hide contract:
 *  - hidden until the first /health poll resolves (no flash on load)
 *  - hidden while the daemon is offline (DaemonBanner owns that state)
 *  - hidden once a model is chosen (`noModelChosen` — the ONE rule, U1-1)
 *    and something real is available. /health already filters the mock out
 *    of `providers`, so "no available entry" is exactly "simulated mode".
 */
/** The CSS variable the strip publishes its rendered height on (v1.314.0).
 *  Full-height modules (chat, the agents team screen, the workflow canvas,
 *  Build's rail) size themselves as `100vh - <chrome>` — without subtracting
 *  this, the strip pushed their bottom row (the chat composer's footer) below
 *  the window whenever replies were a demo. 0px when the strip is hidden. */
export const STRIP_HEIGHT_VAR = "--ij-strip-h";

/** v1.444.0: how long the strip keeps the press's own "Done" words after the
 *  default changed (the strip's reason is gone, so it then hides). */
export const DONE_SHOWN_MS = 8000;

function setStripHeight(px: number) {
  try {
    document.documentElement.style.setProperty(STRIP_HEIGHT_VAR, `${Math.max(0, Math.round(px))}px`);
  } catch {
    /* no document (SSR) — nothing to size */
  }
}

export function SimulatedBanner() {
  const { online, checking, health, refresh } = useDaemon();

  // v1.444.0 (ease-of-use review item 2): what is REALLY connected. The demo
  // model itself (provider "mock", class "mock") and a saved web sign-in
  // (class "browser") never count, so a fresh install never reads "a model is
  // connected" (lib/demoExit.realModelRows).
  const hasRealProvider = realModelRows(health).length > 0;
  // What ONE press can choose for answers everywhere (POST
  // /onboarding/use-model): a signed-in Claude Code or Codex, a key, a
  // detected Ollama, LM Studio or llama.cpp.
  const candidates = pressableCandidates(health);
  const top = candidates[0];
  const demoDefault = !!health && noModelChosen(health.default_provider);
  const { busy: pressBusy, note: pressNote, press } = useAnswerPress(refresh);
  // The press's own words, kept a moment after the strip's reason is gone
  // (the default changed, so /health hides the strip on its next answer).
  const [doneText, setDoneText] = useState("");
  useEffect(() => {
    if (!doneText) return;
    const t = window.setTimeout(() => setDoneText(""), DONE_SHOWN_MS);
    return () => window.clearTimeout(t);
  }, [doneText]);
  const demoShown = !checking && online && !!health && (!hasRealProvider || demoDefault);
  const visible = demoShown || !!doneText;

  // Publish the strip's height while it shows; reset to 0 when it hides or
  // unmounts. A ResizeObserver follows wrapping on narrow windows.
  const observer = useRef<ResizeObserver | null>(null);
  const measure = useCallback((el: HTMLDivElement | null) => {
    observer.current?.disconnect();
    observer.current = null;
    if (!el) return;
    setStripHeight(el.getBoundingClientRect().height);
    if (typeof ResizeObserver !== "undefined") {
      observer.current = new ResizeObserver(() => setStripHeight(el.getBoundingClientRect().height));
      observer.current.observe(el);
    }
  }, []);
  useEffect(() => {
    if (!visible) setStripHeight(0);
  }, [visible]);
  useEffect(
    () => () => {
      observer.current?.disconnect();
      setStripHeight(0);
    },
    [],
  );

  if (!visible) return null;

  // The chat page's model menu listens for this (ModelMenuBridge brings the
  // chat forward first on other pages; the Ctrl K palette uses it too).
  const openModelMenu = () => window.dispatchEvent(new CustomEvent("ij:open-switcher"));

  if (!demoShown) {
    return (
      <div
        ref={measure}
        role="status"
        aria-live="polite"
        data-testid="simulated-banner"
        data-state="done"
        className="border-b border-white/[0.06] backdrop-blur-sm"
      >
        <p className="px-4 py-2 text-[13px] leading-snug text-tone-success sm:px-6 lg:px-10">{doneText}</p>
      </div>
    );
  }

  /* "Connect a model": the connect choices where the user is (the chat
     page's doors, lib/demoExit.openConnect), else Settings › Connections. */
  const connectLink = (
    <Link
      href={CONNECTIONS_HREF}
      data-testid="simulated-banner-connect"
      onClick={(e) => {
        if (openConnect()) e.preventDefault();
      }}
      className="flex shrink-0 items-center gap-1.5 rounded-lg border border-amber-500/30 px-2.5 py-1 text-xs font-medium text-amber-200 transition-colors hover:bg-amber-500/15"
    >
      <Cable size={12} aria-hidden="true" /> {CONNECT_A_MODEL}
    </Link>
  );

  return (
    <div
      ref={measure}
      role="status"
      aria-live="polite"
      data-testid="simulated-banner"
      className="border-b border-amber-500/25 bg-amber-500/[0.08] backdrop-blur-sm"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2 sm:px-6 lg:px-10">
        <TriangleAlert size={15} className="shrink-0 text-amber-300" aria-hidden="true" />
        {top ? (
          <>
            {/* v1.444.0: the ONE press that sets the default everywhere (new
                chats, agents, schedules), never only this one chat. */}
            <p className="min-w-0 flex-1 text-[13px] leading-snug text-amber-100/90">
              <span className="font-semibold text-amber-200">{DEMO_STATE}</span>
              <span className="text-amber-100/70">
                {" "}
                — {top.label} is ready to answer but not chosen yet, so replies are {DEMO_REPLIES}.
              </span>
            </p>
            <button
              type="button"
              data-testid="simulated-banner-use"
              disabled={pressBusy !== null}
              title={top.where ? `Your words ${top.where}. Used for every chat, agent and schedule.` : "Used for every chat, agent and schedule."}
              onClick={() => void press(top, (_p, label) => setDoneText(chosenNote(label)))}
              className="flex shrink-0 items-center gap-1.5 rounded-lg border border-amber-500/30 px-2.5 py-1 text-xs font-medium text-amber-200 transition-colors hover:bg-amber-500/15 disabled:opacity-50"
            >
              <Sparkles size={12} aria-hidden="true" /> Use {top.label} for answers
            </button>
            {candidates.length > 1 ? (
              <button
                type="button"
                onClick={() => {
                  if (!openConnect()) openModelMenu();
                }}
                className="shrink-0 text-xs text-amber-100/70 underline-offset-2 hover:text-amber-100 hover:underline"
              >
                Other choices
              </button>
            ) : null}
            {pressNote && pressNote.kind !== "ok" ? (
              <span
                role={pressNote.kind === "error" ? "alert" : undefined}
                data-testid="simulated-banner-note"
                className="basis-full text-xs text-amber-100/80"
              >
                {pressNote.text}
              </span>
            ) : null}
          </>
        ) : hasRealProvider ? (
          <>
            <p className="min-w-0 flex-1 text-[13px] leading-snug text-amber-100/90">
              <span className="font-semibold text-amber-200">{DEMO_STATE}</span>
              <span className="text-amber-100/70">
                {" "}
                — a model is connected but not chosen yet, so replies are {DEMO_REPLIES}.
              </span>
            </p>
            <button
              type="button"
              onClick={openModelMenu}
              className="flex shrink-0 items-center gap-1.5 rounded-lg border border-amber-500/30 px-2.5 py-1 text-xs font-medium text-amber-200 transition-colors hover:bg-amber-500/15"
            >
              <Cpu size={12} aria-hidden="true" /> Choose a model
            </button>
          </>
        ) : (
          <>
            <p className="min-w-0 flex-1 text-[13px] leading-snug text-amber-100/90">
              <span className="font-semibold text-amber-200">{DEMO_STATE}</span>
              <span className="text-amber-100/70">
                {" "}
                — no AI model is connected, so replies are {DEMO_REPLIES}.
              </span>
            </p>
            {connectLink}
          </>
        )}
      </div>
    </div>
  );
}
