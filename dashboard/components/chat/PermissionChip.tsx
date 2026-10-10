"use client";

/**
 * One permission chip for the composer (Calm chat, v1.327.0).
 *
 * The chat's three approval postures (`lib/permissionLevels.ts`, the daemon's
 * approval vocabulary) as ONE quiet ghost chip, which the chat page renders in
 * the composer toolbar as `#chat-approval-mode` in place of the old select:
 * a shield, the level's plain name and a chevron, transparent at rest and
 * filled only on hover. A press opens a small menu of the levels, each with
 * a one-line description and the current one checked. The level that runs
 * things without asking reads amber on the chip and in the menu, so a chat
 * running without asks looks like one.
 *
 * The menu is drawn on the page's top layer (a portal into `document.body`,
 * `position: fixed` at the chip), like the old select's own list: the chat
 * page holds the chip inside a section that scrolls (`chat-card`), and a menu
 * placed inside it was cut off at that section's edge. On a phone's new chat
 * that hid the "Ask first" row, and a tap on it landed on the page.
 *
 * Pure: it holds only its own open/closed state. Saving the pick (the
 * conversation's setup, the user's default for new chats) stays the page's
 * job through `onChange`, which fires only when the pick CHANGES.
 *
 * Keyboard: Enter/Space or ArrowDown/ArrowUp on the chip opens the menu with
 * the current level active; arrows move (wrapping), Home/End jump, Enter or
 * Space picks, Esc closes and gives focus back to the chip without reaching
 * the page (it would stop a turn), Tab closes and moves on from the chip.
 * A press anywhere else closes.
 */

import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Shield, ShieldAlert } from "lucide-react";
import {
  PERMISSION_LEVELS,
  PERMISSION_MODES,
  asPermissionMode,
  permissionToneText,
  type PermissionMode,
} from "@/lib/permissionLevels";

export interface PermissionChipProps {
  /** The conversation's level (a wire value; an unknown one reads as the default). */
  value: string;
  /** Called with the new level when the user picks a DIFFERENT one. */
  onChange: (mode: PermissionMode) => void;
  /** A turn is running or the composer is locked: the chip cannot open. */
  disabled?: boolean;
  /** The chip button's id. The chat page passes `chat-approval-mode`, the id
   *  the old select carried, so anything that finds the control by id still does. */
  id?: string;
  /** Below sm show only the shield, like the other toolbar chips on a phone.
   *  The level's name stays in the chip's aria-label and title. */
  iconOnlyOnPhone?: boolean;
  /** v1.329.0: told each time the menu opens or closes (and closed when the
   *  chip unmounts with it open), so the page can keep "Jump to latest" off
   *  an open menu. Not called on mount. */
  onOpenChange?: (open: boolean) => void;
}

/**
 * The menu's box before it has rendered: w-72 is 288px, and at that width
 * every one of the three descriptions wraps to two lines (the box measured
 * 226px in Edge at 1440, 390, 360 and 320 wide). This is only the first-frame
 * guess; once the menu is on screen its real box is measured and the placement
 * is worked out again with it.
 */
export const PERMISSION_MENU_W = 288;
export const PERMISSION_MENU_H = 232;
/** Breathing room kept between the menu and the window's edge. */
const EDGE = 8;
/** The menu's `max-w-[calc(100vw-2rem)]`: it never gets wider than this. */
const MENU_GUTTER = 32;
/** The gap between the chip and the menu. */
export const PERMISSION_MENU_GAP = 6;

export interface MenuPlacement {
  /** Open above the chip (the composer sits low) or below it (a centred new chat). */
  side: "above" | "below";
  /**
   * Line the menu up with the chip's left edge, its right edge, or (when
   * neither keeps it inside the window, as on a narrow phone) shift it.
   */
  align: "start" | "end" | "shift";
  /** For "shift" only: the menu's left edge in px, measured from the chip's left edge. */
  left?: number;
  /**
   * Only when neither side has room for the whole menu (a very short window):
   * the most it may be tall on the side it opens, so it scrolls inside itself
   * instead of running off the window where nobody can reach the cut rows.
   */
  maxHeight?: number;
}

type Anchor = { top: number; bottom: number; left: number; right: number };

/**
 * Where the menu goes so it stays inside the window. Above by default (the
 * composer is usually at the bottom); below only when above does not fit and
 * below has more room. When neither side holds the whole menu, it opens on the
 * roomier side capped to that room (`maxHeight`). Left-aligned when that fits,
 * else right-aligned when that fits, else shifted so both edges stay at least
 * 8px inside the window.
 */
export function permissionMenuPlacement(
  anchor: Anchor,
  viewport: { width: number; height: number },
  menu: { width: number; height: number } = { width: PERMISSION_MENU_W, height: PERMISSION_MENU_H },
): MenuPlacement {
  const roomAbove = anchor.top - EDGE;
  const roomBelow = viewport.height - anchor.bottom - EDGE;
  const side = roomAbove >= menu.height || roomAbove >= roomBelow ? "above" : "below";
  const room = side === "above" ? roomAbove : roomBelow;
  const cap = room < menu.height ? { maxHeight: Math.max(0, Math.floor(room)) } : {};
  const width = Math.min(menu.width, Math.max(0, viewport.width - MENU_GUTTER));
  if (anchor.left >= EDGE && anchor.left + width <= viewport.width - EDGE) return { side, align: "start", ...cap };
  if (anchor.right - width >= EDGE && anchor.right <= viewport.width - EDGE) return { side, align: "end", ...cap };
  const maxLeft = viewport.width - width - EDGE;
  const left = Math.max(EDGE, Math.min(anchor.left, maxLeft));
  return { side, align: "shift", left: Math.round(left - anchor.left), ...cap };
}

/** The menu's fixed-position edges in window px, from the chip's box and a placement. */
export interface MenuPosition {
  top?: number;
  bottom?: number;
  left?: number;
  right?: number;
}

/**
 * Turns a placement into `position: fixed` edges at the chip: above hangs the
 * menu's BOTTOM edge just over the chip (so its height never matters), below
 * puts its top just under it; start/shift set its left edge, end its right.
 */
export function permissionMenuPosition(
  anchor: Anchor,
  place: MenuPlacement,
  viewport: { width: number; height: number },
): MenuPosition {
  const pos: MenuPosition =
    place.side === "above"
      ? { bottom: Math.round(viewport.height - anchor.top + PERMISSION_MENU_GAP) }
      : { top: Math.round(anchor.bottom + PERMISSION_MENU_GAP) };
  if (place.align === "end") pos.right = Math.round(viewport.width - anchor.right);
  else pos.left = Math.round(anchor.left + (place.align === "shift" ? (place.left ?? 0) : 0));
  return pos;
}

function samePlacement(a: MenuPlacement, b: MenuPlacement): boolean {
  return a.side === b.side && a.align === b.align && a.left === b.left && a.maxHeight === b.maxHeight;
}

function samePosition(a: MenuPosition, b: MenuPosition): boolean {
  return a.top === b.top && a.bottom === b.bottom && a.left === b.left && a.right === b.right;
}

/**
 * The menu's whole height, rows and all, even while a `maxHeight` holds it
 * shorter: its scrolled content plus its border. Falls back to the drawn box.
 */
function naturalHeight(menu: HTMLElement, drawn: number): number {
  const content = menu.scrollHeight + (menu.offsetHeight - menu.clientHeight);
  return Math.max(drawn, content);
}

/** The window the fixed menu is placed in: the layout viewport (no scrollbars). */
function layoutViewport(): { width: number; height: number } {
  const de = document.documentElement;
  return { width: de.clientWidth || window.innerWidth, height: de.clientHeight || window.innerHeight };
}

export function PermissionChip({
  value,
  onChange,
  disabled = false,
  id,
  iconOnlyOnPhone = false,
  onOpenChange,
}: PermissionChipProps) {
  const current = asPermissionMode(value);
  const level = PERMISSION_LEVELS[current];
  const [open, setOpen] = useState(false);
  const onOpenChangeRef = useRef(onOpenChange);
  onOpenChangeRef.current = onOpenChange;
  const toldOpenRef = useRef(false);
  useEffect(() => {
    if (toldOpenRef.current === open) return;
    toldOpenRef.current = open;
    onOpenChangeRef.current?.(open);
  }, [open]);
  useEffect(
    () => () => {
      if (toldOpenRef.current) onOpenChangeRef.current?.(false);
    },
    [],
  );
  const [active, setActive] = useState(0);
  const [place, setPlace] = useState<MenuPlacement>({ side: "above", align: "start" });
  const [pos, setPos] = useState<MenuPosition>({});
  const wrapRef = useRef<HTMLDivElement>(null);
  const chipRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  /** The menu's last measured size, for placing it again on a scroll or resize. */
  const sizeRef = useRef<{ width: number; height: number } | undefined>(undefined);
  const uid = useId();
  const menuId = `${uid}-menu`;
  const itemId = (i: number) => `${uid}-item-${i}`;

  /** Works the placement out from the chip's box now (and the menu's size, once known). */
  function layout(size?: { width: number; height: number }) {
    const el = chipRef.current;
    if (!el || typeof window === "undefined") return;
    const r = el.getBoundingClientRect();
    const anchor = { top: r.top, bottom: r.bottom, left: r.left, right: r.right };
    const next = permissionMenuPlacement(anchor, { width: window.innerWidth, height: window.innerHeight }, size);
    const nextPos = permissionMenuPosition(anchor, next, layoutViewport());
    setPlace((prev) => (samePlacement(prev, next) ? prev : next));
    setPos((prev) => (samePosition(prev, nextPos) ? prev : nextPos));
  }

  function openMenu() {
    if (disabled) return;
    sizeRef.current = undefined;
    layout();
    setActive(Math.max(0, PERMISSION_MODES.indexOf(current)));
    setOpen(true);
  }

  function close(refocus: boolean) {
    setOpen(false);
    if (refocus) chipRef.current?.focus();
  }

  function pick(mode: PermissionMode) {
    close(true);
    if (mode !== current) onChange(mode);
  }

  // A turn starting (or the composer locking) under an open menu closes it.
  useEffect(() => {
    if (disabled && open) setOpen(false);
  }, [disabled, open]);

  // Keys are read on the menu, so it takes focus when it opens (without
  // scrolling anything to it). Then the placement is worked out again from the
  // menu's REAL box, before paint: the guess in `openMenu` cannot know how its
  // descriptions wrapped.
  useLayoutEffect(() => {
    if (!open) return;
    const menu = menuRef.current;
    menu?.focus({ preventScroll: true });
    if (!menu || typeof window === "undefined") return;
    const box = menu.getBoundingClientRect();
    if (!(box.width > 0 && box.height > 0)) return;
    sizeRef.current = { width: box.width, height: naturalHeight(menu, box.height) };
    layout(sizeRef.current);
    // `layout` reads only refs and setters; this runs once per opening.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // The menu is fixed to the window, so it follows the chip when anything
  // scrolls or the window resizes, and closes when the chip itself goes away
  // (hidden for a question in the composer's place, or unmounted).
  useEffect(() => {
    if (!open) return;
    const follow = (e: Event) => {
      if (menuRef.current && e.target instanceof Node && menuRef.current.contains(e.target)) return;
      layout(sizeRef.current);
    };
    window.addEventListener("scroll", follow, true);
    window.addEventListener("resize", follow);
    let ro: ResizeObserver | undefined;
    const chip = chipRef.current;
    if (chip && typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(() => {
        const r = chip.getBoundingClientRect();
        if (!(r.width > 0 && r.height > 0)) setOpen(false);
        else layout(sizeRef.current);
      });
      ro.observe(chip);
    }
    return () => {
      window.removeEventListener("scroll", follow, true);
      window.removeEventListener("resize", follow);
      ro?.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // A press anywhere else closes the menu. The menu lives in a portal, so it
  // is not inside the chip's wrapper and is checked on its own.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (wrapRef.current?.contains(t) || menuRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  function onChipKeyDown(e: KeyboardEvent<HTMLButtonElement>) {
    if (open || disabled) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      openMenu();
    }
  }

  function onMenuKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const n = PERMISSION_MODES.length;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % n);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i - 1 + n) % n);
    } else if (e.key === "Home") {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setActive(n - 1);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      pick(PERMISSION_MODES[active]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      // Never let Esc reach the page: there it stops a turn or closes a panel.
      e.stopPropagation();
      close(true);
    } else if (e.key === "Tab") {
      // The menu sits at the end of the page; focus goes back to the chip
      // first, so Tab moves on from the chip as it did when the menu was
      // inside the toolbar.
      close(true);
    }
  }

  const warn = level.tone === "warn";
  const Icon = warn ? ShieldAlert : Shield;
  const chipTone = warn
    ? "text-tone-warn hover:bg-tone-warn/10"
    : "text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-200";
  // On a phone the chip is a 30px square holding the shield; the words and the
  // chevron come back from sm up.
  const phoneChip = iconOnlyOnPhone ? " max-sm:w-[30px] max-sm:justify-center max-sm:px-0" : "";
  const phoneHide = iconOnlyOnPhone ? " max-sm:hidden" : "";

  const menu = open ? (
    <div
      ref={menuRef}
      id={menuId}
      role="menu"
      aria-label="Permissions"
      tabIndex={-1}
      aria-activedescendant={itemId(active)}
      onKeyDown={onMenuKeyDown}
      data-testid="permission-menu"
      data-side={place.side}
      data-align={place.align}
      style={{
        position: "fixed",
        ...pos,
        ...(place.maxHeight !== undefined ? { maxHeight: place.maxHeight, overflowY: "auto" as const } : {}),
      }}
      className="z-50 w-72 max-w-[calc(100vw-2rem)] rounded-xl border border-white/10 bg-ink-900/95 p-1 shadow-xl shadow-black/30 outline-none backdrop-blur-xl"
    >
      {PERMISSION_MODES.map((mode, i) => {
        const row = PERMISSION_LEVELS[mode];
        const checked = mode === current;
        const on = i === active;
        const tone = permissionToneText(row.tone);
        return (
          <button
            key={mode}
            type="button"
            id={itemId(i)}
            role="menuitemradio"
            aria-checked={checked}
            tabIndex={-1}
            data-testid="permission-item"
            data-mode={mode}
            data-active={on ? "true" : undefined}
            onMouseEnter={() => setActive(i)}
            onClick={() => pick(mode)}
            className={`flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left transition-colors ${
              on ? "bg-white/[0.06]" : ""
            }`}
          >
            <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center">
              {checked && (
                <Check
                  size={14}
                  aria-hidden="true"
                  data-testid="permission-check"
                  className={tone || "text-accent"}
                />
              )}
            </span>
            <span className="min-w-0">
              <span className={`block text-body ${tone || "text-zinc-200"}`}>{row.label}</span>
              <span className="block text-meta text-zinc-500">{row.description}</span>
            </span>
          </button>
        );
      })}
    </div>
  ) : null;

  return (
    <div ref={wrapRef} className="relative inline-flex shrink-0" data-testid="permission-chip-wrap">
      <button
        ref={chipRef}
        id={id}
        type="button"
        data-testid="permission-chip"
        data-mode={current}
        data-tone={level.tone}
        disabled={disabled}
        onClick={() => (open ? close(false) : openMenu())}
        onKeyDown={onChipKeyDown}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`Permissions: ${level.label}`}
        title={level.description}
        className={`inline-flex h-[30px] items-center gap-1.5 whitespace-nowrap rounded-lg bg-transparent px-2.5 text-body transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-40 ${chipTone}${phoneChip}`}
      >
        <Icon size={14} strokeWidth={1.8} aria-hidden="true" className="shrink-0" />
        <span className={phoneHide.trim() || undefined}>{level.label}</span>
        <ChevronDown size={12} aria-hidden="true" className={`shrink-0 opacity-70${phoneHide}`} />
      </button>
      {menu && typeof document !== "undefined" ? createPortal(menu, document.body) : null}
    </div>
  );
}

export default PermissionChip;
