/**
 * v1.311.0 (UX wave 3, SPEED, track C) — a filtered `useEvents` caller must
 * list EVERY type it reads. A missing type is a silent regression: the bell
 * never shows that row, the orb never turns, the Overview never lists the
 * run — and no render-count test notices, because nothing renders.
 *
 * Each surface's type list is an exported constant; this pin reads the
 * surface's own SOURCE, collects every event-type literal it compares
 * against (`.type === "x"`, `.type !== "x"`, `.type.startsWith("x.")`,
 * `X_TYPES.has(...)` sets), and asserts each one is admitted by the list
 * under `useEvents`' own rule (an entry ending in `.*` is a prefix).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));

import { BELL_EVENT_TYPES } from "@/components/NotificationBell";
import { MOOD_EVENT_TYPES } from "@/components/MoodOrb";
import { DESKTOP_NOTIFY_EVENT_TYPES } from "@/components/DesktopNotifyBridge";
import { GOALS_EVENT_TYPES } from "@/components/GoalsStrip";
import { PROVIDER_DOWNGRADE_EVENT_TYPES } from "@/components/ProviderDowngradeBanner";
import { OVERVIEW_EVENT_TYPES, OVERVIEW_LIVE_TYPES } from "@/components/overview/eventTypes";

/** Source of a dashboard file, CRLF-normalised at the reader (CI checks out
 *  with autocrlf). */
function src(rel: string): string {
  return readFileSync(join(process.cwd(), rel), "utf8").replace(
    /\r\n/g,
    "\n",
  );
}

/** Every event-type literal a source compares an event's `.type` against.
 *  Prefix tests come back as `"x.*"`. `typeof … === "string"` is not one. */
function readTypes(code: string): string[] {
  const out = new Set<string>();
  for (const m of code.matchAll(/\.type\s*[!=]==\s*"([a-z_][a-z0-9_.]*)"/g)) {
    if (m[1] !== "string") out.add(m[1]);
  }
  for (const m of code.matchAll(/\.type\.startsWith\("([a-z_][a-z0-9_.]*)"\)/g)) {
    out.add(m[1].endsWith(".") ? `${m[1]}*` : m[1]);
  }
  return [...out].sort();
}

/** `useEvents`' rule: exact, or a `.*` entry as a prefix (keeping the dot). */
function admits(list: readonly string[], type: string): boolean {
  if (type.endsWith(".*")) return list.includes(type);
  return list.some((t) => (t.endsWith(".*") ? type.startsWith(t.slice(0, -1)) : t === type));
}

const CASES: [string, string, readonly string[]][] = [
  ["the bell", "components/NotificationBell.tsx", BELL_EVENT_TYPES],
  ["the MoodOrb", "components/MoodOrb.tsx", MOOD_EVENT_TYPES],
  ["the desktop bridge", "components/DesktopNotifyBridge.tsx", DESKTOP_NOTIFY_EVENT_TYPES],
  ["GoalsStrip", "components/GoalsStrip.tsx", GOALS_EVENT_TYPES],
  ["the downgrade banner", "components/ProviderDowngradeBanner.tsx", PROVIDER_DOWNGRADE_EVENT_TYPES],
  ["the Overview", "app/page.tsx", OVERVIEW_EVENT_TYPES],
];

describe("each filtered subscriber lists every event type it reads", () => {
  it.each(CASES)("%s", (_name, file, list) => {
    const read = readTypes(src(file));
    expect(read.length).toBeGreaterThan(0); // anti-vacuity: the scan found its comparisons
    expect(read.filter((t) => !admits(list, t))).toEqual([]);
  });

  it("the bell's list covers every type toActivity maps (named, not just scanned)", () => {
    // The scan above would also pass if toActivity's comparisons were
    // rewritten into a shape the regex misses; these are the rows by name.
    for (const t of [
      "session.completed",
      "preference.suggested",
      "detection.finding",
      "trust.lowered",
      "context.blocked",
      "comm.received",
      "schedule.fired",
      "agent.paused",
      "agent.allowance_warning",
      "assignment.blocked",
      "assignment.finished",
      "assignment.requeued",
      "grant.revoked",
      "coach.proposal",
      "computeruse.run_finished",
      "review.requested",
      "review.approved",
      "approval.requested",
      "approval.resolved",
      "workflow.waiting",
    ]) {
      expect(admits(BELL_EVENT_TYPES, t), t).toBe(true);
    }
  });

  it("the Overview passes its list to useEvents, and its live rows use the shared constant", () => {
    const page = src("app/page.tsx");
    expect(page).toMatch(/useEvents\(40,\s*\{\s*types:\s*OVERVIEW_EVENT_TYPES\s*\}\)/);
    expect(page).toContain("new Set(OVERVIEW_LIVE_TYPES)");
    for (const t of OVERVIEW_LIVE_TYPES) expect(OVERVIEW_EVENT_TYPES).toContain(t);
  });

  it("the scan itself catches a missing type (mutation control)", () => {
    const withoutSchedule = BELL_EVENT_TYPES.filter((t) => t !== "schedule.fired");
    const read = readTypes(src("components/NotificationBell.tsx"));
    expect(read.filter((t) => !admits(withoutSchedule, t))).toEqual(["schedule.fired"]);
  });
});
