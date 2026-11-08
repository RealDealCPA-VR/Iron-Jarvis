"use client";

/**
 * v1.450.0 (Wave D, item 38): the visible half of `lib/whyDisabled` — one
 * quiet line beside a disabled press saying what it waits for. Nothing when
 * `why` is null (the press is enabled). The same words are the press's
 * `title`, so a pointer and a screen reader get them too.
 */
export function DisabledNote({
  why,
  testId = "disabled-note",
  className = "",
  id,
}: {
  why: string | null;
  testId?: string;
  className?: string;
  /** For `aria-describedby` on the press. */
  id?: string;
}) {
  if (!why) return null;
  return (
    <p id={id} data-testid={testId} className={`text-[12px] text-zinc-500 ${className}`.trim()}>
      {why}
    </p>
  );
}
