"use client";

import { useEffect, useState } from "react";
import { Keyboard, X } from "lucide-react";

/**
 * PowerTips (v1.198.0) — one small dismissible card on the Overview that
 * teaches the shortcuts previously documented only in the GitHub README,
 * which a packaged-installer user never sees.
 *
 * Every tip is verified against the code that implements it:
 *  - Ctrl+K  -> CommandPalette.tsx keydown listener (metaKey||ctrlKey + "k").
 *  - "/"     -> the chat composer's slash skill picker (app/chat/page.tsx).
 *  - Ctrl+Shift+J / Ctrl+Shift+Space -> GLOBAL hotkeys registered by the
 *    DESKTOP app only (desktop/main.js HOTKEY / SPOTLIGHT_HOTKEY). A browser
 *    session (pnpm dev from source) has no Electron main process, so showing
 *    those two rows there would advertise keys that do nothing — we gate them
 *    on `window.ironjarvis`, which only the Electron preload exposes.
 *  - v1.229.0 (audit D2): the desktop rows show the key that is REALLY
 *    registered, read from `window.ironjarvis.shell.getState()` — main.js
 *    tries Ctrl+Shift+J, then Ctrl+Alt+J, and another app can hold both. A
 *    row for a taken key says so instead of naming a key that does nothing.
 */

const DISMISS_KEY = "ij_power_tips_dismissed";

type Tip = { keys: string[]; label: string };

/** True in every environment (dashboard-level bindings). */
const UNIVERSAL_TIPS: Tip[] = [
  {
    keys: ["Ctrl", "K"],
    label: "Search everything — pages, skills, chats, buried controls",
  },
  {
    keys: ["/"],
    label: "Type it in a chat message to invoke a skill",
  },
];

/** What desktop/main.js reports through the preload bridge (`shell:getState`). */
type ShellState = {
  hotkeys?: { window?: string | null; spotlight?: string | null };
  preferred?: { window?: string | null; spotlight?: string | null };
};

const WINDOW_TIP = "Reopen the Iron Jarvis window from anywhere";
const SPOTLIGHT_TIP = "Spotlight — quick-ask an agent from anywhere";

/** The desktop rows, built from the LIVE registration. `shell` undefined =
 *  an older bridge with no getState: the defaults are the best knowledge we
 *  have. `null` for a key = every rung of that ladder is taken by another app,
 *  rendered as a sentence, not a keycap. */
function desktopTips(shell: ShellState | undefined): Tip[] {
  const keysOf = (label: string) => label.split("+");
  const win = shell === undefined ? "Ctrl+Shift+J" : (shell.hotkeys?.window ?? null);
  const spot = shell === undefined ? "Ctrl+Shift+Space" : (shell.hotkeys?.spotlight ?? null);
  const preferredWin = shell?.preferred?.window ?? "Ctrl+Shift+J";
  return [
    win
      ? { keys: keysOf(win), label: WINDOW_TIP }
      : {
          keys: [],
          label: `Reopen the window: hotkey unavailable (${preferredWin} is taken by another app) — use the tray icon`,
        },
    spot
      ? { keys: keysOf(spot), label: SPOTLIGHT_TIP }
      : { keys: [], label: "Spotlight: hotkey unavailable (taken by another app) — use the tray’s Quick task" },
  ];
}

export function PowerTips() {
  // null = storage not read yet. Rendering NOTHING until then avoids the
  // card flashing in and disappearing for a user who already dismissed it
  // (same null-until-read shape as FirstRunWizard / OnboardingWelcome).
  const [state, setState] = useState<{
    dismissed: boolean;
    desktop: boolean;
    shell?: ShellState;
  } | null>(null);

  useEffect(() => {
    // Both reads happen client-side in one effect: localStorage is not
    // available during SSR, and `window.ironjarvis` (the Electron preload
    // bridge) decides whether the global-hotkey rows are true here.
    const bridge = typeof window !== "undefined" ? (window as any).ironjarvis : undefined;
    const dismissed = localStorage.getItem(DISMISS_KEY) === "1";
    const desktop = Boolean(bridge);
    const getState = bridge?.shell?.getState;
    if (desktop && typeof getState === "function") {
      // Ask the shell which keys it really holds before rendering a row.
      let cancelled = false;
      Promise.resolve()
        .then(() => getState())
        .then((shell: ShellState | null) => {
          if (!cancelled) setState({ dismissed, desktop, shell: shell ?? undefined });
        })
        .catch(() => {
          if (!cancelled) setState({ dismissed, desktop });
        });
      return () => {
        cancelled = true;
      };
    }
    setState({ dismissed, desktop });
  }, []);

  // Dismissal is one-shot on purpose: no re-open affordance. The Help page
  // will carry this information permanently; a resurrectable nag is worse
  // than a card you can make go away for good.
  function dismiss() {
    localStorage.setItem(DISMISS_KEY, "1");
    setState((s) => (s ? { ...s, dismissed: true } : s));
  }

  if (!state || state.dismissed) return null;

  const tips = state.desktop
    ? [...UNIVERSAL_TIPS, ...desktopTips(state.shell)]
    : UNIVERSAL_TIPS;
  // The count is computed, not hardcoded — the browser sees two tips, the
  // desktop app four, and a fixed number would be wrong in one of them.
  const countWord = tips.length === 2 ? "Two" : "Four";

  return (
    <div className="relative overflow-hidden rounded-2xl border border-accent/20 bg-accent/[0.03] p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-accent/30 bg-accent/[0.1]">
            <Keyboard size={15} className="text-accent-soft" />
          </span>
          <h2 className="text-sm font-semibold tracking-tight text-zinc-100">
            {countWord} shortcuts worth learning
          </h2>
        </div>
        <button
          onClick={dismiss}
          title="Dismiss"
          aria-label="Dismiss"
          className="rounded-lg p-1 text-zinc-500 transition-colors hover:bg-white/[0.05] hover:text-zinc-300"
        >
          <X size={14} />
        </button>
      </div>

      <ul className="mt-3 space-y-1.5">
        {tips.map((tip) => (
          <li
            key={tip.keys.length ? tip.keys.join("+") : tip.label}
            className="flex items-center gap-3 rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2"
          >
            <span className="flex shrink-0 items-center gap-1">
              {tip.keys.map((k, i) => (
                <span key={k} className="flex items-center gap-1">
                  {i > 0 && <span className="text-[10px] text-zinc-600">+</span>}
                  <kbd className="rounded border border-white/10 bg-white/[0.04] px-1.5 py-0.5 font-mono text-[11px] text-zinc-300">
                    {k}
                  </kbd>
                </span>
              ))}
            </span>
            <span className="min-w-0 text-xs text-zinc-400">{tip.label}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
