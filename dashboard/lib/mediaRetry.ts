/**
 * v1.455.0 (readiness Wave F, item 16): "Try again" under a media line that
 * says a picture, clip or sound could not be made.
 *
 * The daemon appends that line itself when a background media job fails
 * (`media/jobs.failed_line`: "Your image could not be made: <reason>",
 * `panelKind: "media"`, `mediaJob: <id>`). The user had to type the request
 * again. The press reads the job's own prompt (GET /media/jobs/{id}) and
 * sends it as the next message — the words a user would type — so every
 * card and spend check applies to the turn it starts, as before.
 *
 * No imports: read by the chat page and its tests.
 */

/** The media line's kind marker (the daemon's `media/jobs.PANEL_KIND`). */
export const MEDIA_PANEL_KIND = "media";

/** The press's words. */
export const MEDIA_TRY_AGAIN = "Try again";

/** What a press says when the job's request can no longer be read. */
export const MEDIA_RETRY_GONE = "Couldn't find what was asked for. Ask for it again in your own words.";

/** True for the daemon's FAILED media line (never the "is ready" one). */
export function isFailedMediaLine(m: {
  role?: unknown;
  panelKind?: unknown;
  mediaJob?: unknown;
  content?: unknown;
}): boolean {
  return (
    m.role === "assistant" &&
    m.panelKind === MEDIA_PANEL_KIND &&
    typeof m.mediaJob === "string" &&
    m.mediaJob.trim() !== "" &&
    typeof m.content === "string" &&
    /^Your \S+ could not be made/.test(m.content)
  );
}

/** The message the press sends: the job's own request, made again. */
export function mediaRetryText(kind: unknown, prompt: unknown): string {
  const k = typeof kind === "string" && kind.trim() ? kind.trim() : "picture";
  const p = typeof prompt === "string" ? prompt.trim() : "";
  return p ? `Make the ${k} again: ${p}` : "";
}
