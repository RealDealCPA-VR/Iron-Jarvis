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
  /** v1.433.0: the note after the press inside the desktop app. */
  restartingNote: "Restart requested — reconnecting. Watch the status dot in the sidebar.",
  /** v1.433.0: the desktop app was asked and could not restart it. */
  refused:
    "The desktop app couldn’t restart Iron Jarvis from here. Quit Iron Jarvis from the tray menu and open it again.",
  /** v1.433.0: in a browser tab the same press only STOPS Iron Jarvis — a
   *  page cannot start it again, so the words say what really happens. */
  browserBlurb:
    "Stops Iron Jarvis tidily. A browser page can’t start it again: if the Iron Jarvis desktop app is running, it starts it again; otherwise start Iron Jarvis yourself.",
  browserTitle: "Iron Jarvis stops tidily. A browser page can’t start it again.",
  /** v1.433.0: the note after the press in a browser tab. */
  stoppingNote:
    "Iron Jarvis is stopping. If the desktop app is running, it starts it again; otherwise start Iron Jarvis yourself.",
} as const;
