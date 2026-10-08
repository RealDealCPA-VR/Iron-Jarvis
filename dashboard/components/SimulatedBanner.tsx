"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef } from "react";
import { Cable, Cpu, TriangleAlert } from "lucide-react";
import { useDaemon } from "@/lib/daemon";
import { noModelChosen } from "@/lib/onboarding";

/**
 * A slim, PERSISTENT (deliberately non-dismissable) strip shown whenever the
 * daemon is online but every reply is the offline demo's script. This is the
 * product's biggest trust hazard, so unlike the onboarding checklist there is
 * no way to hide it; the only way to make it go away is to choose a model.
 *
 * Two cases, two honest sentences (v1.313.0):
 *  1. NOTHING is connected — "Simulated mode", and the way out is
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

function setStripHeight(px: number) {
  try {
    document.documentElement.style.setProperty(STRIP_HEIGHT_VAR, `${Math.max(0, Math.round(px))}px`);
  } catch {
    /* no document (SSR) — nothing to size */
  }
}

export function SimulatedBanner() {
  const { online, checking, health } = useDaemon();

  const hasRealProvider =
    !!health && (health.providers?.some((p) => p.available) ?? false);
  const demoDefault = !!health && noModelChosen(health.default_provider);
  const visible = !checking && online && !!health && (!hasRealProvider || demoDefault);

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

  // The ModelSwitcher listens for this (the Ctrl K palette uses it too).
  const openModelMenu = () => window.dispatchEvent(new CustomEvent("ij:open-switcher"));

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
        {hasRealProvider ? (
          <>
            <p className="min-w-0 flex-1 text-[13px] leading-snug text-amber-100/90">
              <span className="font-semibold text-amber-200">Demo replies</span>
              <span className="text-amber-100/70">
                {" "}
                — a model is connected but not chosen yet, so replies are scripted.
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
              <span className="font-semibold text-amber-200">Simulated mode</span>
              <span className="text-amber-100/70">
                {" "}
                — no AI model is connected, so replies are fabricated by an offline mock.
              </span>
            </p>
            <Link
              href="/connections"
              className="flex shrink-0 items-center gap-1.5 rounded-lg border border-amber-500/30 px-2.5 py-1 text-xs font-medium text-amber-200 transition-colors hover:bg-amber-500/15"
            >
              <Cable size={12} aria-hidden="true" /> Connect a model
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
