"use client";

/**
 * v1.356.0 (G78 "turn a pack off without losing it", G74 "Ask before each
 * use"): the on/off switch of an extension row (Tools) and an app card
 * (Directory), and the row's ask-first switch.
 *
 * NOT A NEW LOOK. This is the switch Settings rows already draw
 * (components/settings/SettingRow.tsx `role="switch"`: a 44×24 track, the
 * accent when on) and, at `size="sm"`, the compact one the profile-share row
 * draws (components/memory/ProfileShareRow.tsx). Theme tokens only, no
 * border or shadow beyond the track the Settings switch already has, and the
 * knob's slide stops under prefers-reduced-motion.
 *
 * A real `role="switch"` button: Space/Enter toggle it, `aria-checked` says
 * where it points, and the accessible name stays the same whichever way it
 * points (the position is the state, never the label).
 */

export function PackSwitch({
  on,
  label,
  onChange,
  busy = false,
  title,
  testId,
  size = "md",
}: {
  on: boolean;
  /** The accessible name ("Use slack", "Ask before each use"). */
  label: string;
  onChange: (next: boolean) => void;
  busy?: boolean;
  title?: string;
  testId?: string;
  size?: "md" | "sm";
}) {
  const sm = size === "sm";
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      aria-busy={busy || undefined}
      title={title}
      disabled={busy}
      data-testid={testId}
      onClick={() => onChange(!on)}
      className={`relative shrink-0 rounded-full border transition-colors motion-reduce:transition-none disabled:opacity-60 ${
        sm ? "h-[18px] w-8" : "h-6 w-11"
      } ${on ? "border-accent/50 bg-accent/30" : "border-white/10 bg-white/[0.04]"}`}
    >
      <span
        aria-hidden
        className={`absolute top-1/2 -translate-y-1/2 rounded-full transition-all motion-reduce:transition-none ${
          sm ? "h-3 w-3" : "h-4 w-4"
        } ${
          on
            ? `${sm ? "left-[0.95rem]" : "left-6"} bg-accent`
            : `${sm ? "left-[3px]" : "left-1"} bg-zinc-500`
        }`}
      />
    </button>
  );
}
