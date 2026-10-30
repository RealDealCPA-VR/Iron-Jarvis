/**
 * v1.412.0 review: which coding sessions (Chat › Code) the user has PUT
 * something into in this window — words in the box, a file, an "@" file, a
 * skill, a message. New session makes a server row at once, so an abandoned
 * one used to stay as an empty "New session" row; IjHarnessView deletes one it
 * made when the user leaves it, but never one marked here (and the daemon's
 * `only_if_empty` guard refuses one with a message, a waiting message or a
 * running reply). Module state: one window, its lifetime.
 */

const used = new Set<string>();

/** The user put something into this session (once is enough: kept). */
export function noteSessionUsed(id: string): void {
  if (id) used.add(id);
}

export function sessionUsed(id: string): boolean {
  return used.has(id);
}

/** Tests: forget everything (no argument), or one session. */
export function forgetSessionUse(id?: string): void {
  if (id === undefined) used.clear();
  else used.delete(id);
}
