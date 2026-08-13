// Shared schedule-trigger vocabulary (v1.169.0). ONE cron→label map for every
// surface that names a schedule's cadence — the Schedules page's preset picker
// and the project heartbeat card used to hold separate copies, and two maps
// naming one concept is exactly the drift the repo's vocabulary rule exists
// to prevent.

export const REPEAT_PRESETS: { label: string; cron: string }[] = [
  { label: "Every minute", cron: "* * * * *" },
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
];

export const CRON_TO_LABEL = new Map(REPEAT_PRESETS.map((p) => [p.cron, p.label]));

/** Friendly name for a cron expression, or null when it matches no preset. */
export function cronLabel(cron: string): string | null {
  return CRON_TO_LABEL.get(cron) ?? null;
}
