"use client";

/**
 * One permission chip for the composer (Calm chat, v1.327.0).
 *
 * The chat's three approval postures (`lib/permissionLevels.ts`, the daemon's
 * approval vocabulary) as ONE quiet ghost chip, which the chat page renders in
 * the composer toolbar as `#chat-approval-mode` in place of the old select:
 * a shield, the level's plain name and a chevron, transparent at rest and
 * filled only on hover. A press opens a small menu of the levels by NAME,
 * the current one checked. The level that runs things without asking reads
 * amber on the chip and in the menu, so a chat running without asks looks
 * like one.
 *
 * v1.430.0 (the user: "the descriptions are too verbose … just the titles …
 * a popup of what it means if the user waves the mouse over it for long
 * enough"): a row's meaning is no longer printed under its name. It shows in
 * a small box BESIDE the menu once the pointer rests on the row for
 * `PERMISSION_HINT_DELAY_MS` (or the arrow keys rest on it; on a touch
 * screen, once a finger holds the row that long — the hold then picks
 * nothing). Once one meaning is showing, the next row's shows at once. Each
 * row keeps its meaning as its accessible description (`aria-describedby`),
 * so a screen reader still hears it; the box itself is for the eye only.
 *
 * The menu is drawn on the page's top layer (a portal into `document.body`,
 * `position: fixed` at the chip), like the old select's own list: the chat
 * page holds the chip inside a section that scrolls (`chat-card`), and a menu
 * placed inside it was cut off at that section's edge. On a phone's new chat
 * that hid the "Ask first" row, and a tap on it landed on the page.
 *
 * It holds only its own open/closed state. Saving the pick (the
 * conversation's setup, the user's default for new chats) stays the page's
 * job through `onChange`, which fires only when the pick CHANGES.
 *
 * v1.360.0 (the v1.356.0 G81 regression): each level's reading sentence
 * follows "Folders Jarvis may read". The chip reads it from lib/readFacts
 * (GET /settings/access, the Access summary's own facts), asked when the
 * user reaches for the chip (pointer over it, focus, open), never on mount;
 * until an answer lands it says what it always said (READS_LINE).
 *
 * Keyboard: Enter/Space or ArrowDown/ArrowUp on the chip opens the menu with
 * the current level active; arrows move (wrapping), Home/End jump, Enter or
 * Space picks, Esc closes and gives focus back to the chip without reaching
 * the page (it would stop a turn), Tab closes and moves on from the chip.
 * A press anywhere else closes.
 */

import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, FilePen, ListChecks, Shield, ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import {
  PERMISSION_MODES,
  PLAN_LEVEL,
  asPermissionMode,
  permissionLevel,
  permissionToneText,
  readsLevelLine,
  type PermissionLevel,
  type PermissionMode,
} from "@/lib/permissionLevels";

/** v1.406.0 part 2: a menu row — one of the approval levels, or a coding
 *  session's Plan mode (offered only when the caller passes `onPlanChange`). */
type MenuKey = PermissionMode | "plan";

/** The rows the menu lists, in order: the levels strictest first, with Plan
 *  (when offered) just before "Don't ask", which stays last with its warning. */
export function permissionMenuKeys(withPlan: boolean): MenuKey[] {
  if (!withPlan) return [...PERMISSION_MODES];
  const keys: MenuKey[] = PERMISSION_MODES.filter((m) => m !== "yolo");
  keys.push("plan");
  if (PERMISSION_MODES.includes("yolo")) keys.push("yolo");
  return keys;
}

/** Each level's own shield, so the shield alone still says which is on. */
function iconFor(key: MenuKey, warn: boolean) {
  if (warn) return ShieldAlert;
  if (key === "plan") return ListChecks;
  if (key === "always_ask") return ShieldQuestion;
  if (key === "accept_edits") return FilePen;
  if (key === "auto") return ShieldCheck;
  return Shield;
}
import { refreshReadFacts, useReadFacts } from "@/lib/readFacts";
import { RowHintBox, useRowHint } from "@/components/RowHint";
import { fitIconChip, fitWords, iconOnlyTitle } from "@/lib/composerChips";

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
  /** v1.400.0: the toolbar it sits in (lib/toolbarFit) has taken its `ask`
   *  step, so the chip is its shield alone: its tooltip leads with the
   *  level's name. The shape itself follows the toolbar's `data-fit-ask`
   *  (CSS). */
  iconOnly?: boolean;
  /** v1.406.0 part 2: a coding session's plan mode is ON. Only read when
   *  `onPlanChange` is given. */
  planMode?: boolean;
  /** v1.406.0 part 2: offer Plan in the menu (a coding session). Picking
   *  Plan calls it with true and keeps the level; picking a level while plan
   *  mode is on calls it with false (then `onChange` if the level changed). */
  onPlanChange?: (on: boolean) => void;
}

/**
 * The menu's box before it has rendered. v1.430.0: names only, so w-56
 * (224px) holds the longest ("Ask before every action") on one line, and
 * five or six one-line rows stand about 170-200px tall. This is only the
 * first-frame guess; once the menu is on screen its real box is measured (a
 * layout effect, before paint) and the placement is worked out again with it.
 */
export const PERMISSION_MENU_W = 224;
export const PERMISSION_MENU_H = 176;
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

/* v1.430.0 built the pause-to-explain box here; v1.432.0 lifted it into
 * components/RowHint so Build › Code's mode menu shares it. The names this
 * file exported then stay (the tests and any caller read them). */
export {
  ROW_HINT_DELAY_MS as PERMISSION_HINT_DELAY_MS,
  ROW_HINT_W as PERMISSION_HINT_W,
  rowHintPlacement as permissionHintPlacement,
  type HintPlacement,
} from "@/components/RowHint";

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
  iconOnly = false,
  planMode = false,
  onPlanChange,
}: PermissionChipProps) {
  const current = asPermissionMode(value);
  // v1.360.0: the reading sentence follows the readable-folders setting.
  const readsLine = readsLevelLine(useReadFacts());
  const withPlan = typeof onPlanChange === "function";
  const planning = withPlan && planMode;
  const keys = permissionMenuKeys(withPlan);
  const rowFor = (key: MenuKey): PermissionLevel => (key === "plan" ? PLAN_LEVEL : permissionLevel(key, readsLine));
  const shownKey: MenuKey = planning ? "plan" : current;
  const level = rowFor(shownKey);
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
  const aboutId = (i: number) => `${uid}-about-${i}`;
  // v1.430.0: which row's meaning shows beside the menu, and its timers
  // (components/RowHint since v1.432.0: the same box as Build › Code's modes).
  const rowHint = useRowHint<MenuKey>(open);

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
    refreshReadFacts();
    sizeRef.current = undefined;
    layout();
    setActive(Math.max(0, keys.indexOf(shownKey)));
    setOpen(true);
  }

  function close(refocus: boolean) {
    setOpen(false);
    if (refocus) chipRef.current?.focus();
  }

  function pick(key: MenuKey) {
    close(true);
    if (key === "plan") {
      if (!planning) onPlanChange?.(true);
      return;
    }
    if (planning) onPlanChange?.(false);
    if (key !== current) onChange(key);
  }

  // A turn starting (or the composer locking) under an open menu closes it.
  useEffect(() => {
    if (disabled && open) setOpen(false);
  }, [disabled, open]);

  // Keys are read on the menu, so it takes focus when it opens (without
  // scrolling anything to it), once per opening.
  useLayoutEffect(() => {
    if (open) menuRef.current?.focus({ preventScroll: true });
  }, [open]);

  // Then the placement is worked out again from the menu's REAL box, before
  // paint: the guess in `openMenu` cannot know how its descriptions wrapped.
  // v1.361.0: and again whenever the rows' words change while it is open. The
  // reading sentence (`readsLine`) is asked for when the user reaches for the
  // chip, so a tap can open the menu before the answer lands; the longer
  // "only in the folders you chose" rows then wrapped to more lines, and a menu
  // hung above the chip on its old height would grow past the window's top.
  // (Since v1.430.0 the rows show names only and the sentence rides the
  // meaning box, which is placed again on its own; this stays harmless.)
  useLayoutEffect(() => {
    if (!open) return;
    const menu = menuRef.current;
    if (!menu || typeof window === "undefined") return;
    const box = menu.getBoundingClientRect();
    if (!(box.width > 0 && box.height > 0)) return;
    sizeRef.current = { width: box.width, height: naturalHeight(menu, box.height) };
    layout(sizeRef.current);
    // `layout` reads only refs and setters; this runs once per opening and
    // once per change of the rows' words while open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, readsLine]);

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
    const n = keys.length;
    if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Home" || e.key === "End") {
      e.preventDefault();
      const next =
        e.key === "ArrowDown"
          ? (active + 1) % n
          : e.key === "ArrowUp"
            ? (active - 1 + n) % n
            : e.key === "Home"
              ? 0
              : n - 1;
      setActive(next);
      // v1.430.0: the keyboard's highlight resting on a row shows its meaning
      // too, after the same pause.
      rowHint.afterPause(keys[next]);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      pick(keys[active]);
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
  // v1.400.0 (review): each level has its OWN shield, so the shield alone (a
  // phone, or a desktop toolbar out of room) still says which level is on —
  // "Ask first" and "Ask when risky" used to share one plain shield, and with
  // their words gone nothing told them apart (not even a hue).
  // v1.406.0 part 2: Accept edits, Auto and Plan each have their own too.
  const Icon = iconFor(shownKey, warn);
  const chipTone = warn
    ? "text-tone-warn hover:bg-tone-warn/10"
    : "text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-200";
  // On a phone the chip is a 30px square holding the shield; the words and the
  // chevron come back from sm up.
  // v1.400.0: so is a chip in a toolbar that has given up its words
  // (lib/toolbarFit's `ask` step: fitIconChip / fitWords, read off the
  // toolbar's `data-fit-ask`), keeping its level's own shield and the amber
  // of the no-ask level; its tooltip then names the level (`iconOnly`).
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
      onMouseLeave={rowHint.hide}
      className="z-50 w-56 max-w-[calc(100vw-2rem)] rounded-xl border border-white/10 bg-ink-900/95 p-1 shadow-xl shadow-black/30 outline-none backdrop-blur-xl"
    >
      {keys.map((mode, i) => {
        const row = rowFor(mode);
        const checked = mode === shownKey;
        const on = i === active;
        const tone = permissionToneText(row.tone);
        return (
          <button
            key={mode}
            type="button"
            id={itemId(i)}
            role="menuitemradio"
            aria-checked={checked}
            aria-describedby={aboutId(i)}
            tabIndex={-1}
            data-testid="permission-item"
            data-mode={mode}
            data-active={on ? "true" : undefined}
            {...rowHint.rowProps(mode, () => setActive(i))}
            onClick={() => {
              // The lift after a hold that showed the meaning is not a pick.
              if (rowHint.heldClick()) return;
              pick(mode);
            }}
            className={`flex w-full select-none items-center gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors [-webkit-touch-callout:none] ${
              on ? "bg-white/[0.06]" : ""
            }`}
          >
            <span className="grid h-4 w-4 shrink-0 place-items-center">
              {checked && (
                <Check
                  size={14}
                  aria-hidden="true"
                  data-testid="permission-check"
                  className={tone || "text-accent"}
                />
              )}
            </span>
            <span className={`block min-w-0 text-body ${tone || "text-zinc-200"}`}>{row.label}</span>
          </button>
        );
      })}
      {/* Each row's meaning for a screen reader (the rows' aria-describedby);
          the eye reads it in the box beside the menu. */}
      {keys.map((mode, i) => (
        <span key={`about-${mode}`} id={aboutId(i)} hidden data-testid="permission-about" data-mode={mode}>
          {rowFor(mode).description}
        </span>
      ))}
    </div>
  ) : null;

  const hintKey = rowHint.shown;
  const hintAt = hintKey !== null ? keys.indexOf(hintKey) : -1;
  const hintBox =
    open && hintKey !== null && hintAt >= 0 ? (
      <RowHintBox
        text={rowFor(hintKey).description}
        mode={hintKey}
        menuRef={menuRef}
        rowId={itemId(hintAt)}
        watch={`${pos.top}|${pos.bottom}|${pos.left}|${pos.right}|${place.side}|${readsLine}`}
        testId="permission-hint"
      />
    ) : null;

  return (
    <div ref={wrapRef} className="relative inline-flex shrink-0" data-testid="permission-chip-wrap">
      <button
        ref={chipRef}
        id={id}
        type="button"
        data-testid="permission-chip"
        data-mode={current}
        data-plan={planning ? "true" : undefined}
        data-tone={level.tone}
        disabled={disabled}
        onClick={() => (open ? close(false) : openMenu())}
        onKeyDown={onChipKeyDown}
        onPointerEnter={() => refreshReadFacts()}
        onFocus={() => refreshReadFacts()}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`Permissions: ${level.label}`}
        title={iconOnly ? iconOnlyTitle(level.label, level.description) : level.description}
        className={`inline-flex h-[30px] items-center gap-1.5 whitespace-nowrap rounded-lg bg-transparent px-2.5 text-body transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-40 ${chipTone}${phoneChip} ${fitIconChip("ask")}`}
      >
        <Icon size={14} strokeWidth={1.8} aria-hidden="true" className="shrink-0" />
        <span className={`${phoneHide.trim()} ${fitWords("ask")}`.trim()}>{level.chip ?? level.label}</span>
        <ChevronDown size={12} aria-hidden="true" className={`shrink-0 opacity-70${phoneHide} ${fitWords("ask")}`} />
      </button>
      {menu && typeof document !== "undefined"
        ? createPortal(menu, document.body)
        : null}
      {hintBox}
    </div>
  );
}

export default PermissionChip;
