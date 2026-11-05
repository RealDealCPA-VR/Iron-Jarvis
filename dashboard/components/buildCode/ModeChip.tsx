"use client";

/**
 * The Code view's MODE CHIP (v1.409.0; its own file since v1.412.0, when the
 * start composer began to use it too). Choosing an ordinary mode is one press;
 * a DANGEROUS one (Full access) arms first ("Press again…") and only the
 * second press chooses it, reported with `confirm: true`.
 *
 * v1.412.0: each mode says what it does (the daemon's `about`), and the chip
 * is never disabled for a reply that runs — the mode changes at any time (the
 * daemon applies it live where the tool can; Codex from the next message),
 * and while the tool waits on a card the chip sits beside the card.
 *
 * v1.432.0 (the user, after the chat's approvals menu went names-only in
 * v1.430.0: "give Build › Code's mode menu the same look"): the menu lists
 * the modes by NAME. What a mode does (the daemon's `about`, or Full access's
 * own sentence) shows in a box beside its row after the pointer rests on it
 * (components/RowHint, the chat's box); each row keeps it as its accessible
 * description. A mode the tool does not offer here is a greyed name AFTER the
 * offered ones, picking nothing, its reason in the same box (it was a block
 * of sentences at the top). Full access's "Press again…" stays printed under
 * its name while armed: that is an instruction, not a description.
 */

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { Shield, ShieldAlert } from "lucide-react";
import { RowHintBox, useRowHint } from "@/components/RowHint";
import { isFullAccess, modeLabel, type CodingTool, type HiddenMode, type ToolMode } from "@/lib/buildApp";

/** What Full access does, said in its pause box (its row says only its name). */
export const FULL_ACCESS_ABOUT = "Runs everything without asking. Two presses.";

/** What a mode does, for its pause box; "" = nothing to say. */
function modeMeaning(m: ToolMode): string {
  return m.dangerous ? FULL_ACCESS_ABOUT : (m.about ?? "");
}

/** Why a mode is not offered, for its pause box. */
export function hiddenModeWords(h: HiddenMode): string {
  return `${h.label} is not offered here. ${h.reason}`;
}

/** A not-offered mode's row key (never the same as an offered mode's id). */
const hiddenKey = (id: string) => `hidden:${id}`;

/** Full access: a second press this soon after the first does not count (a
 *  held or doubled Enter must never give full access by itself). */
export const FULL_ARM_GUARD_MS = 400;
/** Full access: an armed press that is not followed up is forgotten. */
export const FULL_DISARM_MS = 3_000;

const QUIET_BTN =
  "inline-flex h-7 items-center gap-1.5 rounded-lg px-2 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50";

// ── the mode chip: Full access takes two presses ────────────────────────────

/** The mode menu. Choosing an ordinary mode is one press; a DANGEROUS one
 *  (Full access) arms first ("Press again…") and only the second press
 *  chooses it, reported with `confirm: true`. */
export function ModeChip({
  tool,
  value,
  onChange,
  disabled,
  testId = "code-mode-chip",
}: {
  tool: CodingTool | null;
  value: string;
  onChange: (mode: string, confirm: boolean) => void;
  disabled?: boolean;
  testId?: string;
}) {
  const [open, setOpen] = useState(false);
  const [armed, setArmed] = useState<string | null>(null);
  const armedAt = useRef(0);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [shift, setShift] = useState(0);
  const modes = tool?.modes ?? [];
  const hidden = tool?.hiddenModes ?? [];
  const dangerous = isFullAccess(value, tool);
  // v1.432.0: which row's meaning shows beside the menu (components/RowHint).
  const uid = useId();
  const rowId = (key: string) => `${uid}-row-${key}`;
  const aboutId = (key: string) => `${uid}-about-${key}`;
  const rowHint = useRowHint<string>(open);
  const shownKey = rowHint.shown;
  const shownHidden = shownKey ? hidden.find((h) => hiddenKey(h.id) === shownKey) : undefined;
  const shownMode = shownKey ? modes.find((m) => m.id === shownKey) : undefined;
  const hintText = shownHidden ? hiddenModeWords(shownHidden) : shownMode ? modeMeaning(shownMode) : "";
  useEffect(() => {
    if (!open) setArmed(null);
  }, [open]);
  // An armed Full access that is not pressed again is forgotten.
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(null), FULL_DISARM_MS);
    return () => clearTimeout(t);
  }, [armed]);
  // The menu stays inside the stage (a 360 px phone): moved left or right
  // by however much it would stick out.
  useLayoutEffect(() => {
    if (!open) {
      setShift(0);
      return;
    }
    const el = menuRef.current;
    if (!el) return;
    const box = el.closest("[data-code-fit]")?.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    const left = Math.max(box ? box.left : 0, 0) + 8;
    const right = Math.min(box ? box.right : window.innerWidth, window.innerWidth) - 8;
    const base = r.left - shift;
    let next = 0;
    if (base + r.width > right) next = right - (base + r.width);
    if (base + next < left) next = left - base;
    if (next !== shift) setShift(next);
    // Measured once per opening.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  return (
    <div className="relative" data-testid={testId}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled || modes.length === 0}
        data-testid={`${testId}-button`}
        onClick={() => setOpen((v) => !v)}
        className={`${QUIET_BTN} ${dangerous ? "text-tone-warn" : ""}`}
        title="What the tool may do without asking you"
      >
        {dangerous ? <ShieldAlert size={13} aria-hidden="true" /> : <Shield size={13} aria-hidden="true" />}
        {modeLabel(value, tool)}
      </button>
      {open ? (
        <div
          ref={menuRef}
          role="menu"
          data-testid={`${testId}-menu`}
          style={shift ? { transform: `translateX(${shift}px)` } : undefined}
          className="absolute bottom-full left-0 z-20 mb-1 w-56 max-w-[calc(100vw-2rem)] rounded-xl bg-ink-800 p-1 shadow-card"
          onMouseLeave={rowHint.hide}
          onBlur={(e) => {
            if (!(e.relatedTarget instanceof Node && e.currentTarget.contains(e.relatedTarget))) rowHint.hide();
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              setOpen(false);
            }
          }}
        >
          {modes.map((m) => {
            const isArmed = armed === m.id;
            const meaning = modeMeaning(m);
            return (
              <button
                key={m.id}
                type="button"
                id={rowId(m.id)}
                role="menuitemradio"
                aria-checked={m.id === value}
                aria-describedby={meaning ? aboutId(m.id) : undefined}
                data-testid={`${testId}-option-${m.id}`}
                data-armed={isArmed ? "true" : undefined}
                {...(meaning ? rowHint.rowProps(m.id) : {})}
                onFocus={() => {
                  if (meaning) rowHint.afterPause(m.id);
                }}
                onKeyDown={(e) => {
                  // A held key repeats its press: never let that be the second.
                  if (e.repeat && (e.key === "Enter" || e.key === " ")) e.preventDefault();
                }}
                onClick={() => {
                  // The lift after a finger's hold that showed the meaning is not a press.
                  if (rowHint.heldClick()) return;
                  if (m.dangerous && !isArmed) {
                    armedAt.current = Date.now();
                    setArmed(m.id);
                    return;
                  }
                  // The second press must be a second, deliberate press.
                  if (m.dangerous && Date.now() - armedAt.current < FULL_ARM_GUARD_MS) return;
                  setOpen(false);
                  onChange(m.id, m.dangerous);
                }}
                className={`flex w-full select-none flex-col items-start rounded-lg px-2.5 py-1.5 text-left text-[13px] transition-colors [-webkit-touch-callout:none] hover:bg-white/[0.06] ${
                  m.dangerous ? "text-tone-warn" : m.id === value ? "text-zinc-100" : "text-zinc-300"
                }`}
              >
                <span>{m.label}</span>
                {isArmed ? (
                  // An instruction after the first press, not a description: it stays printed.
                  <span className="text-[12px] text-tone-warn">
                    Press again to let it do anything without asking.
                  </span>
                ) : null}
              </button>
            );
          })}
          {hidden.map((h) => (
            // A mode the tool does not offer here: its name, greyed, picking
            // nothing; why, on a pause (and as its description).
            <div
              key={h.id}
              id={rowId(hiddenKey(h.id))}
              role="menuitem"
              aria-disabled="true"
              tabIndex={0}
              aria-describedby={aboutId(hiddenKey(h.id))}
              data-testid={`${testId}-hidden-${h.id}`}
              {...rowHint.rowProps(hiddenKey(h.id))}
              onFocus={() => rowHint.afterPause(hiddenKey(h.id))}
              onClick={() => {
                rowHint.heldClick();
              }}
              className="flex w-full cursor-default select-none items-center rounded-lg px-2.5 py-1.5 text-left text-[13px] text-zinc-600 [-webkit-touch-callout:none] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
            >
              {h.label}
            </div>
          ))}
          {/* Each row's meaning for a screen reader (the rows' aria-describedby);
              the eye reads it in the box beside the menu. */}
          {modes.map((m) =>
            modeMeaning(m) ? (
              <span key={`about-${m.id}`} id={aboutId(m.id)} hidden data-testid={`${testId}-about-${m.id}`}>
                {modeMeaning(m)}
              </span>
            ) : null,
          )}
          {hidden.map((h) => (
            <span
              key={`about-hidden-${h.id}`}
              id={aboutId(hiddenKey(h.id))}
              hidden
              data-testid={`${testId}-hidden-about-${h.id}`}
            >
              {hiddenModeWords(h)}
            </span>
          ))}
        </div>
      ) : null}
      {open && shownKey && hintText ? (
        <RowHintBox
          text={hintText}
          mode={shownKey}
          menuRef={menuRef}
          rowId={rowId(shownKey)}
          watch={String(shift)}
          testId={`${testId}-hint`}
        />
      ) : null}
    </div>
  );
}

export default ModeChip;
