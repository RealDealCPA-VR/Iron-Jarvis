/**
 * The composer card's quiet controls (Calm chat W1-2, v1.326.0).
 *
 * The composer is the ONE card on the chat screen and every control lives
 * inside it as a ghost chip: transparent at rest, filled only on hover. These
 * helpers hold the shared class strings (so every chip in the toolbar looks
 * the same) and the tools chip's words, which are pure and tested on their
 * own (`composer-card-v1326.test.tsx`).
 *
 * Colours are theme tokens only (`zinc-*`, `accent`, `white` overlays), so
 * the dark Marks and the light Daylight / Liquid Glass Marks both read right.
 */

/** One toolbar chip: 30px tall, transparent at rest, a soft fill on hover,
 *  and a visible ring for a keyboard user (focus-visible only). */
const CHIP_BASE =
  "inline-flex h-[30px] shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-2 text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-40";
const CHIP_IDLE = "text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-200";
const CHIP_ON = "text-accent-soft hover:bg-accent/10";

/** The class for a toolbar chip; `on` tints it with the accent (a project is
 *  chosen, a switch is on, tools are armed). */
export function composerChipClass(on = false): string {
  return `${CHIP_BASE} ${on ? CHIP_ON : CHIP_IDLE}`;
}

/** A round icon button in the toolbar (the mic). Same quiet rules. */
export const COMPOSER_ICON_BUTTON =
  "relative grid h-[34px] w-[34px] shrink-0 place-items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-40";

/**
 * The card's edge: a 0.5px hairline drawn with box-shadow (no border, and no
 * focus ring on the card, where the caret is the focus cue) plus a faint soft
 * shadow. `--white` is the theme's overlay ink, which the light Marks set to a
 * dark ink, so the hairline flips with the theme; on a light theme the soft
 * shadow is that ink too instead of black.
 */
export const COMPOSER_CARD_EDGE =
  "shadow-[0_0_0_0.5px_rgb(var(--white)/0.12),0_6px_24px_rgb(0_0_0/0.35),0_0_32px_rgb(var(--accent-rgb)/0.05)] [[data-scheme=light]_&]:shadow-[0_0_0_0.5px_rgb(var(--white)/0.16),0_4px_18px_rgb(var(--white)/0.08)]";

export interface ToolsChipWords {
  /** The chip's words from sm up. */
  text: string;
  /** The tooltip: what the chip means and that a press opens the menu. */
  title: string;
  /** Tools armed by hand (web research not counted; it has its own chip). */
  armed: number;
  /** Tint the chip: something is on. */
  on: boolean;
}

/**
 * What the tools chip says. Auto tools is the everyday setting (each request
 * picks the safe tools it needs); tools armed by hand are counted so a pick
 * always leaves a trace on screen.
 */
export function toolsChipWords(autoTools: boolean, armed: number): ToolsChipWords {
  const n = Math.max(0, Math.floor(armed));
  const count = `${n} tool${n === 1 ? "" : "s"}`;
  if (n > 0) {
    return {
      text: autoTools ? `Auto tools + ${n}` : count,
      title: autoTools
        ? `Auto tools on, plus ${count} you turned on for this chat. Press to change.`
        : `${count} turned on for this chat. Auto tools is off. Press to change.`,
      armed: n,
      on: true,
    };
  }
  return autoTools
    ? {
        text: "Auto tools",
        title: "Each request picks the safe tools it needs (files, documents, web, images). Press to change.",
        armed: 0,
        on: false,
      }
    : {
        text: "Tools off",
        title: "Auto tools is off and no tool is turned on for this chat. Press to change.",
        armed: 0,
        on: false,
      };
}

/** The quiet keyboard line under the card (shown from sm up; a touch screen
 *  has no keys to hint at). v1.329.0: in a project with a folder, "@" also
 *  offers its files (`files`), and the line says so. */
export function composerKeyHint(busy: boolean, steerable: boolean, files: boolean = false): string {
  if (busy && steerable) {
    return "Enter sends a note Jarvis reads at its next step · Ctrl+Enter sends after this reply · Esc stops";
  }
  if (busy) return "Esc stops";
  const at = files ? "@ for agents, files and chats" : "@ for agents and chats";
  return `Enter to send · Shift+Enter new line · / for skills · ${at}`;
}
