"use client";

/**
 * One permission chip for the composer (Calm chat, v1.327.0).
 *
 * The chat's three approval postures (`lib/permissionLevels.ts`, the same
 * values the `#chat-approval-mode` select offers) as ONE quiet ghost chip:
 * a shield, the level's plain name and a chevron, transparent at rest and
 * filled only on hover. A press opens a small menu of the levels, each with
 * a one-line description and the current one checked. The level that runs
 * things without asking reads amber on the chip and in the menu, so a chat
 * running without asks looks like one.
 *
 * Pure: it holds only its own open/closed state. Saving the pick (the
 * conversation's setup, the user's default for new chats) stays the page's
 * job through `onChange`, which fires only when the pick CHANGES.
 *
 * Keyboard: Enter/Space or ArrowDown/ArrowUp on the chip opens the menu with
 * the current level active; arrows move (wrapping), Home/End jump, Enter or
 * Space picks, Esc closes and gives focus back to the chip without reaching
 * the page (it would stop a turn), Tab closes. A press anywhere else closes.
 */

import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
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
}

/**
 * Where the menu goes so it stays inside the window. Above by default (the
 * composer is usually at the bottom); below only when above does not fit and
 * below has more room. Left-aligned when that fits, else right-aligned when
 * that fits, else shifted so both edges stay at least 8px inside the window.
 */
export function permissionMenuPlacement(
  anchor: { top: number; bottom: number; left: number; right: number },
  viewport: { width: number; height: number },
  menu: { width: number; height: number } = { width: PERMISSION_MENU_W, height: PERMISSION_MENU_H },
): MenuPlacement {
  const roomAbove = anchor.top - EDGE;
  const roomBelow = viewport.height - anchor.bottom - EDGE;
  const side = roomAbove >= menu.height || roomAbove >= roomBelow ? "above" : "below";
  const width = Math.min(menu.width, Math.max(0, viewport.width - MENU_GUTTER));
  if (anchor.left >= EDGE && anchor.left + width <= viewport.width - EDGE) return { side, align: "start" };
  if (anchor.right - width >= EDGE && anchor.right <= viewport.width - EDGE) return { side, align: "end" };
  const maxLeft = viewport.width - width - EDGE;
  const left = Math.max(EDGE, Math.min(anchor.left, maxLeft));
  return { side, align: "shift", left: Math.round(left - anchor.left) };
}

function samePlacement(a: MenuPlacement, b: MenuPlacement): boolean {
  return a.side === b.side && a.align === b.align && a.left === b.left;
}

export function PermissionChip({ value, onChange, disabled = false }: PermissionChipProps) {
  const current = asPermissionMode(value);
  const level = PERMISSION_LEVELS[current];
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [place, setPlace] = useState<MenuPlacement>({ side: "above", align: "start" });
  const wrapRef = useRef<HTMLDivElement>(null);
  const chipRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const uid = useId();
  const menuId = `${uid}-menu`;
  const itemId = (i: number) => `${uid}-item-${i}`;

  function openMenu() {
    if (disabled) return;
    const el = chipRef.current;
    if (el && typeof window !== "undefined") {
      const r = el.getBoundingClientRect();
      setPlace(
        permissionMenuPlacement(
          { top: r.top, bottom: r.bottom, left: r.left, right: r.right },
          { width: window.innerWidth, height: window.innerHeight },
        ),
      );
    }
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

  // Keys are read on the menu, so it takes focus when it opens. Then the
  // placement is worked out again from the menu's REAL box, before paint: the
  // guess in `openMenu` cannot know how its descriptions wrapped.
  useLayoutEffect(() => {
    if (!open) return;
    const menu = menuRef.current;
    menu?.focus();
    const chip = chipRef.current;
    if (!menu || !chip || typeof window === "undefined") return;
    const box = menu.getBoundingClientRect();
    if (!(box.width > 0 && box.height > 0)) return;
    const r = chip.getBoundingClientRect();
    const next = permissionMenuPlacement(
      { top: r.top, bottom: r.bottom, left: r.left, right: r.right },
      { width: window.innerWidth, height: window.innerHeight },
      { width: box.width, height: box.height },
    );
    setPlace((prev) => (samePlacement(prev, next) ? prev : next));
  }, [open]);

  // A press anywhere else closes the menu.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
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
      setOpen(false);
    }
  }

  const warn = level.tone === "warn";
  const Icon = warn ? ShieldAlert : Shield;
  const chipTone = warn
    ? "text-tone-warn hover:bg-tone-warn/10"
    : "text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-200";

  return (
    <div ref={wrapRef} className="relative inline-flex" data-testid="permission-chip-wrap">
      <button
        ref={chipRef}
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
        className={`inline-flex h-[30px] items-center gap-1.5 whitespace-nowrap rounded-lg bg-transparent px-2.5 text-body transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-40 ${chipTone}`}
      >
        <Icon size={14} strokeWidth={1.8} aria-hidden="true" className="shrink-0" />
        <span>{level.label}</span>
        <ChevronDown size={12} aria-hidden="true" className="shrink-0 opacity-70" />
      </button>
      {open && (
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
          style={place.align === "shift" ? { left: place.left ?? 0 } : undefined}
          className={`absolute z-40 w-72 max-w-[calc(100vw-2rem)] rounded-xl border border-white/10 bg-ink-900/95 p-1 shadow-xl shadow-black/30 outline-none backdrop-blur-xl ${
            place.side === "above" ? "bottom-full mb-1.5" : "top-full mt-1.5"
          } ${place.align === "start" ? "left-0" : place.align === "end" ? "right-0" : ""}`}
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
      )}
    </div>
  );
}

export default PermissionChip;
