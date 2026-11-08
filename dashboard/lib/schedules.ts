// Shared schedule-trigger vocabulary (v1.169.0). ONE cron→label map for every
// surface that names a schedule's cadence — the Schedules page's preset picker
// and the project heartbeat card used to hold separate copies, and two maps
// naming one concept is exactly the drift the repo's vocabulary rule exists
// to prevent.

// v1.449.0 (Wave D item 35): "Every minute" is the LAST preset — it was the
// first, so a stray first pick (or a keyboard's Home) started a run 1,440
// times a day — and the forms show EVERY_MINUTE_WARNING beside it.
export const REPEAT_PRESETS: { label: string; cron: string }[] = [
  { label: "Every 15 minutes", cron: "*/15 * * * *" },
  { label: "Hourly", cron: "0 * * * *" },
  { label: "Daily at midnight", cron: "0 0 * * *" },
  { label: "Daily at 9am", cron: "0 9 * * *" },
  { label: "Weekdays at 8am", cron: "0 8 * * 1-5" },
  { label: "Weekdays at 9am", cron: "0 9 * * 1-5" },
  { label: "Weekly Mon 9am", cron: "0 9 * * 1" },
  { label: "Weekly Fri 4pm", cron: "0 16 * * 5" },
  { label: "Daily at 6pm", cron: "0 18 * * *" },
  { label: "Monthly 1st", cron: "0 0 1 * *" },
  { label: "Every minute", cron: "* * * * *" },
];

/** The one cron that means every minute. */
export const EVERY_MINUTE_CRON = "* * * * *";

/** Said beside a schedule set to every minute (add form and editor). */
export const EVERY_MINUTE_WARNING =
  "Every minute starts a new run 1,440 times a day, and every run spends model usage. " +
  "Pick this only for a quick check, and come back to change it.";

/* ---- v1.449.0 (Wave D item 35): a name from the task's first words ------ */

/** The most words a filled-in name takes from the task. */
export const NAME_FROM_TASK_WORDS = 5;

/** The longest a filled-in name gets. */
export const NAME_MAX_CHARS = 40;

/** A schedule's name when the user typed none: the task's first words as a
 *  slug ("Write my morning briefing: summarize…" → "write-my-morning-briefing-summarize").
 *  "" when the task has no word yet. */
export function nameFromTask(task: string): string {
  const words = (task ?? "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]+/gu, " ")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, NAME_FROM_TASK_WORDS);
  if (words.length === 0) return "";
  let slug = words.join("-").replace(/-+/g, "-").replace(/^-|-$/g, "");
  if (slug.length > NAME_MAX_CHARS) slug = slug.slice(0, NAME_MAX_CHARS).replace(/-+$/, "");
  return slug;
}

/** AN review (v1.449.0): a filled-in name no schedule already has. The daemon
 *  refuses a name in use ("already exists"), and two schedules made from the
 *  same task would otherwise both be called the same; the next free
 *  "<name>-2", "<name>-3"… is used instead, cut to fit NAME_MAX_CHARS. */
export function uniqueScheduleName(base: string, taken: Iterable<string>): string {
  if (!base) return "";
  const used = new Set(Array.from(taken, (n) => String(n)));
  if (!used.has(base)) return base;
  for (let i = 2; i < 1000; i++) {
    const tail = `-${i}`;
    const head = base.slice(0, Math.max(1, NAME_MAX_CHARS - tail.length)).replace(/-+$/, "");
    const next = `${head}${tail}`;
    if (!used.has(next)) return next;
  }
  return base;
}

/** The hint under an empty Name box. */
export function nameHintWords(suggested: string): string {
  return suggested
    ? `Give it a name — or it will be called “${suggested}”.`
    : "Give it a name — or type the task first and one is filled in from its first words.";
}

export const CRON_TO_LABEL = new Map(REPEAT_PRESETS.map((p) => [p.cron, p.label]));

/** Friendly name for a cron expression, or null when it matches no preset. */
export function cronLabel(cron: string): string | null {
  return CRON_TO_LABEL.get(cron) ?? null;
}

/* ---- v1.446.0 (ease-of-use review items 23-24): pause words -------------- */

/** A paused schedule's row (the switch and the Next run cell). */
export const PAUSED_WORDS = "Paused";

/** The Next run cell while Emergency stop is on (the time stays in the title). */
export const PAUSED_BY_STOP_WORDS = "Paused by Emergency stop";

/** Above the list while Emergency stop is on. */
export const EMERGENCY_STOP_SCHEDULES_WORDS =
  "Emergency stop is on — schedules are skipped until you turn it off.";

/** The note after a Pause or Resume press. */
export function pauseDoneWords(name: string, enabled: boolean): string {
  return enabled
    ? `Schedule "${name}" resumed — it runs on its own again.`
    : `Schedule "${name}" paused — it won't run until you resume it.`;
}
