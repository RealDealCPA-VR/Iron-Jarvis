"use client";

/**
 * Leap 05-a: the four lines of a rule — When, Do, Where it works, Limits —
 * on its card in chat and on its row on Rules. The words are the daemon's
 * (composed by code, `triggers/starts.describe`), never the model's.
 * At 390 px each label sits above its line; wider, beside it.
 */

import type { RuleCardLines } from "@/lib/configCards";

export const RULE_LINE_LABELS: readonly [keyof RuleCardLines, string][] = [
  ["when", "When"],
  ["does", "Do"],
  ["where", "Where it works"],
  ["limits", "Limits"],
];

export function RuleLines({ lines, testId = "rule-lines" }: { lines: RuleCardLines; testId?: string }) {
  const rows = RULE_LINE_LABELS.filter(([key]) => lines[key]);
  if (!rows.length) return null;
  return (
    <dl
      data-testid={testId}
      className="grid min-w-0 basis-full grid-cols-1 gap-x-3 gap-y-0.5 text-[12px] sm:grid-cols-[max-content_minmax(0,1fr)]"
    >
      {rows.map(([key, label]) => (
        <div key={key} className="contents">
          <dt className="text-zinc-500 sm:pt-px">{label}</dt>
          <dd className="mb-1 min-w-0 break-words text-zinc-300 sm:mb-0">{lines[key]}</dd>
        </div>
      ))}
    </dl>
  );
}
