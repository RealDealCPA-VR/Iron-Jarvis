import { RELOCATED } from "@/lib/relocated";
import { surfaceFor } from "@/lib/surfaces";

/**
 * v1.354.0 (G67): a Settings row that holds a page which once had its own
 * address is NAMED like that page.
 *
 * Everything › Setup lists Connections, Directory, Secrets and Tools; each
 * one's old address redirects into a Settings row (lib/relocated.ts), and the
 * row used to carry a THIRD name ("Apps & tools" for the Directory, "Keys &
 * secrets", "Tools & permissions", "Accounts & keys") above a page whose
 * heading said the first. Now the row's name is the nav catalogue's label for
 * the route that lands on it (lib/surfaces.ts reads lib/nav.ts), the same
 * word the Everything entry, Ctrl K and the page's own heading use — so a
 * page renamed in the catalogue is renamed here too, with no second list.
 */

/** The old address that now opens Settings at `section`, if one does. */
export function relocatedRouteOf(section: string): string | undefined {
  for (const [route, to] of Object.entries(RELOCATED)) {
    if (to.base === "/settings" && to.extra.section === section) return route;
  }
  return undefined;
}

/** The row's name: the page's own name when the row holds a relocated page,
 *  else `fallback` (a panel that never had an address keeps its own words). */
export function settingsRowLabel(section: string, fallback = ""): string {
  const route = relocatedRouteOf(section);
  return (route && surfaceFor(route)?.label) || fallback;
}
