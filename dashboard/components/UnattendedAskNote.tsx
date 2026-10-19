/**
 * The five-minute sentence under a form that sets up unattended work
 * (v1.355.0, G65): Schedules' Add schedule, Autonomy's New goal and the
 * Workflows editor. A quiet grey line in the hint style those forms already
 * use (no box, no icon, theme tokens only); the words live in
 * lib/unattendedWords so all three say exactly the same thing.
 */

import { UNATTENDED_ASK_NOTE } from "@/lib/unattendedWords";

export function UnattendedAskNote({ className = "" }: { className?: string }) {
  return (
    <p
      data-testid="unattended-ask-note"
      className={`text-[11px] leading-relaxed text-zinc-500 ${className}`.trim()}
    >
      {UNATTENDED_ASK_NOTE}
    </p>
  );
}
