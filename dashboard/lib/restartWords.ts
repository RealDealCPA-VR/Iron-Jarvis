/**
 * v1.354.0 (G59): the words for Settings › Maintenance's restart, in ONE place.
 *
 * Both Maintenance cards (components/settings/MaintenanceCard.tsx in the
 * System group, and the older full form's card in LegacySettingsForm.tsx)
 * show the same control; they used to say "Restart daemon" and "Gracefully
 * stops the daemon", a word a new user has never met. The press still posts
 * POST /shutdown and the desktop app still starts the service again: only
 * the words changed, and they name the app the user knows.
 */
export const RESTART_WORDS = {
  /** The section heading and the button. */
  label: "Restart Iron Jarvis",
  /** The sentence under the heading. */
  blurb:
    "Applies “restart” settings and clears a stuck state. Iron Jarvis stops tidily and the desktop app starts it again, usually within a few seconds.",
  /** The button's hover title. */
  title: "Iron Jarvis stops tidily and the desktop app starts it again.",
  /** Under a changed setting that only applies after a restart. */
  needed: "One or more changed settings need a restart to take full effect. After saving, use “Restart Iron Jarvis” under Maintenance.",
  /** The small “restart” tag's hover title on such a setting. */
  tagTitle: "Takes full effect after Iron Jarvis restarts",
} as const;
