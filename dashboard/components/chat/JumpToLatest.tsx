"use client";

/**
 * The "Jump to latest" pill (v1.329.0, calm chat W5 G1: lifted out of the
 * chat page so it can read the composer store).
 *
 * It floats just above the dock, over the transcript's faded foot, and the
 * composer's menus open upward into that same space. On a phone the pill sat
 * on top of the open "@" menu and covered its Chats rows. So it steps aside
 * while ANY composer menu is open: the "@" and "/" menus (worked out from what
 * is typed, read here from the composer store so the PAGE never subscribes to
 * it, the v1.250.0 rule) and the page's own menus ("+", tools, model, project,
 * permission, an app prompt's form), passed in as `menuOpen`.
 */

import { memo } from "react";
import { ChevronDown } from "lucide-react";
import { useComposer, type ComposerStore } from "@/lib/composerStore";
import { tokenMenuOpen } from "@/lib/composerMenus";

export const JumpToLatest = memo(function JumpToLatest({
  store,
  busy,
  show,
  menuOpen,
  onJump,
}: {
  store: ComposerStore;
  /** A turn is running (the typed menus cannot open then). */
  busy: boolean;
  /** The reader scrolled up and there is something to jump to. */
  show: boolean;
  /** One of the page's own composer menus is open. */
  menuOpen: boolean;
  onJump: () => void;
}) {
  const state = useComposer(store);
  if (!show || menuOpen || tokenMenuOpen(state, busy)) return null;
  return (
    <button
      type="button"
      data-testid="jump-to-latest"
      onClick={onJump}
      className="absolute bottom-full left-1/2 z-10 mb-2 flex w-auto -translate-x-1/2 items-center gap-1.5 whitespace-nowrap rounded-full border border-accent/40 bg-ink-850/90 px-3 py-1 text-[12px] font-medium text-accent-soft shadow-glow-sm backdrop-blur transition-colors hover:bg-ink-800"
      title="Scroll to the latest message"
    >
      <ChevronDown size={13} /> Jump to latest
    </button>
  );
});

export default JumpToLatest;
