"use client";

/**
 * What removing a model's server, or disconnecting an account, does to the
 * DEFAULT model — said BEFORE the press (v1.449.0, readiness Wave C item 24;
 * ease-of-use review item 47).
 *
 * The daemon decides (`GET /fleet/default-after-removal?leaving=…`, the same
 * rule it then applies when the removal lands: another connected model
 * through the Connections page's own "Make default" pairing, else the demo
 * model with a warning). This component only asks and draws the words. An
 * older daemon answers 404 → nothing is drawn, and the removal still works
 * as it always did.
 */

import { ApiError, get } from "@/lib/api";

export interface DefaultImpact {
  affected: boolean;
  current: { provider: string; label: string } | null;
  replacement: { provider: string; model: string; label: string; demo: boolean } | null;
  demo: boolean;
  words: string;
}

/** The daemon's reading for the providers that would leave, or null when it
 *  cannot say (an older daemon, a failed read). Never throws. */
export async function fetchDefaultImpact(leaving: readonly string[]): Promise<DefaultImpact | null> {
  const names = leaving.map((s) => s.trim()).filter(Boolean);
  if (names.length === 0) return null;
  try {
    const res = await get<Partial<DefaultImpact>>(
      `/fleet/default-after-removal?leaving=${encodeURIComponent(names.join(","))}`,
    );
    if (!res || typeof res !== "object" || typeof res.affected !== "boolean") return null;
    return {
      affected: res.affected,
      current: res.current ?? null,
      replacement: res.replacement ?? null,
      demo: Boolean(res.demo),
      words: typeof res.words === "string" ? res.words : "",
    };
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    return null;
  }
}

/** The words a finished removal's answer carries (`default_replaced`), or "". */
export function defaultReplacedWords(answer: unknown): string {
  if (!answer || typeof answer !== "object") return "";
  const r = (answer as { default_replaced?: unknown }).default_replaced;
  if (!r || typeof r !== "object") return "";
  const words = (r as { words?: unknown }).words;
  return typeof words === "string" ? words : "";
}

/** One line, in the warn tone, drawn only when the default is affected. */
export function DefaultImpactLine({
  impact,
  testId = "default-impact",
}: {
  impact: DefaultImpact | null;
  testId?: string;
}) {
  if (!impact?.affected || !impact.words) return null;
  return (
    <p
      role="note"
      data-testid={testId}
      data-demo={impact.demo ? "1" : undefined}
      className="text-[11px] leading-relaxed text-tone-warn"
    >
      {impact.words}
    </p>
  );
}
