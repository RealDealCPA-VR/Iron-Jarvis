/**
 * The /events types the Overview page reads (v1.311.0, wave 3,
 * events-unfiltered-page-subscribers).
 *
 * In a module of its own because `app/page.tsx` is a Next page, and a page
 * file may not export anything but its component and Next's own fields.
 * The page passes this list to `useEvents(40, { types })`, so a frame of any
 * other type — the add-on's tab switches, the router's provider.routed per
 * LLM call — costs the Overview no render. Add a type HERE in the same
 * change that makes the page read it, or the page never sees that event.
 */

/** Event types that describe an agent starting or finishing a run. */
export const OVERVIEW_LIVE_TYPES: readonly string[] = ["agent.started", "agent.completed"];

/** Everything the Overview reads off the live stream: the live-run rows and
 *  the reflex fires. (GoalsStrip, inside the page, filters its own `goal.*`.) */
export const OVERVIEW_EVENT_TYPES: readonly string[] = [...OVERVIEW_LIVE_TYPES, "reflex.fired"];
