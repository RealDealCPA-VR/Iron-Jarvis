"use client";

/**
 * The Code view's MODE CHIP (v1.409.0; its own file since v1.412.0, when the
 * start composer began to use it too). Choosing an ordinary mode is one press;
 * a DANGEROUS one (Full access) arms first ("Press again…") and only the
 * second press chooses it, reported with `confirm: true`.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Shield, ShieldAlert } from "lucide-react";
import { isFullAccess, modeLabel, type CodingTool } from "@/lib/buildApp";

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
  const dangerous = isFullAccess(value, tool);
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
          className="absolute bottom-full left-0 z-20 mb-1 w-64 max-w-[calc(100vw-2rem)] rounded-xl bg-ink-800 p-1 shadow-card"
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              setOpen(false);
            }
          }}
        >
          {(tool?.hiddenModes ?? []).length ? (
            <div data-testid={`${testId}-hidden`} className="border-b border-white/[0.06] px-2.5 pb-1.5 pt-1">
              {(tool?.hiddenModes ?? []).map((h) => (
                <p key={h.id} data-testid={`${testId}-hidden-${h.id}`} className="text-[12px] text-zinc-500">
                  <span className="text-zinc-400">{h.label}</span> is not offered here. {h.reason}
                </p>
              ))}
            </div>
          ) : null}
          {modes.map((m) => {
            const isArmed = armed === m.id;
            return (
              <button
                key={m.id}
                type="button"
                role="menuitemradio"
                aria-checked={m.id === value}
                data-testid={`${testId}-option-${m.id}`}
                data-armed={isArmed ? "true" : undefined}
                onKeyDown={(e) => {
                  // A held key repeats its press: never let that be the second.
                  if (e.repeat && (e.key === "Enter" || e.key === " ")) e.preventDefault();
                }}
                onClick={() => {
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
                className={`flex w-full flex-col items-start rounded-lg px-2.5 py-1.5 text-left text-[13px] transition-colors hover:bg-white/[0.06] ${
                  m.dangerous ? "text-tone-warn" : m.id === value ? "text-zinc-100" : "text-zinc-300"
                }`}
              >
                <span>{m.label}</span>
                {isArmed ? (
                  <span className="text-[12px] text-tone-warn">
                    Press again to let it do anything without asking.
                  </span>
                ) : m.dangerous ? (
                  <span className="text-[12px] text-zinc-500">Runs everything without asking. Two presses.</span>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

export default ModeChip;
