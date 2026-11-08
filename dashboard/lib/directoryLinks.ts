/**
 * The links a Directory / Tools card gives beside a need (v1.449.0,
 * readiness Wave C item 23 — ease-of-use review items 17 and 56).
 *
 * - "Needs Node.js" used to carry its download address only in the badge's
 *   tooltip; a person with no Node saw an amber word and no way to get it.
 *   `runtimeDownload` gives every helper program the app can name a real
 *   "Get X (free)" link (the daemon's own `runtime_url` first, this table as
 *   the fallback for the Tools page, which only knows the runtime's word).
 * - The Microsoft 365 card said "the Handbook has the steps" with no link;
 *   `ADMIN_GUIDES` opens the Handbook section (`lib/helpLinks.helpGuideHref`
 *   — the same door the chat's page steps use). `M365_ADMIN_SECTION` is the
 *   daemon's `page_doors.M365_ADMIN_SECTION`, pinned equal by
 *   `tests/test_directory_links_v14490.py`.
 * - A card's "Docs" link was labelled "Docs" even when it opened the
 *   program's GitHub page; `docsLinkLabel` says what the link is.
 *
 * Import-free on purpose (two pages and the tests read it).
 */

/** The Handbook section that walks a Microsoft 365 admin's steps. */
export const M365_ADMIN_SECTION = "microsoft-365-locked-down-for-client-data";

/** Where a helper program is downloaded from, by the word a card uses for it. */
export const RUNTIME_DOWNLOADS: Record<string, { name: string; url: string }> = {
  node: { name: "Node.js", url: "https://nodejs.org" },
  "node.js": { name: "Node.js", url: "https://nodejs.org" },
  uv: { name: "uv", url: "https://docs.astral.sh/uv/" },
  "python (uv)": { name: "uv", url: "https://docs.astral.sh/uv/" },
  python: { name: "Python", url: "https://www.python.org/downloads/" },
  bun: { name: "Bun", url: "https://bun.sh" },
  docker: { name: "Docker", url: "https://www.docker.com/products/docker-desktop/" },
};

/** True for an address a download link may open: https only — a daemon
 *  (or an extension's own manifest) that sends anything else is not followed
 *  (a plain http download of a program is a swap waiting to happen; a
 *  javascript: or file: address is not a download at all). */
export function isDownloadAddress(url: string | null | undefined): boolean {
  try {
    const u = new URL((url ?? "").trim());
    return u.protocol === "https:" && Boolean(u.hostname);
  } catch {
    return false;
  }
}

/** The download link for a helper program's word ("Node", "Node.js",
 *  "Python (uv)"), with the daemon's own address winning when it sent an
 *  https one. Null when the word names nothing the table knows. */
export function runtimeDownload(
  needs: string | null | undefined,
  url?: string | null,
): { label: string; href: string } | null {
  const key = (needs ?? "").trim().toLowerCase();
  const known = RUNTIME_DOWNLOADS[key];
  const sent = (url ?? "").trim();
  const href = (isDownloadAddress(sent) ? sent : "") || known?.url || "";
  if (!href) return null;
  const name = known?.name ?? (needs ?? "").trim();
  if (!name) return null;
  return { label: `Get ${name} (free)`, href };
}

/** The Node.js link for a daemon sentence that sends the user to nodejs.org
 *  (the Microsoft 365 install note, an extension that did not start), else
 *  null. Read off the words, never guessed from the card. */
export function runtimeLinkInError(text: string | null | undefined): { label: string; href: string } | null {
  const t = (text ?? "").toLowerCase();
  if (t.includes("nodejs.org") || /\bnode\.js\b/.test(t)) return runtimeDownload("Node.js");
  return null;
}

/** A card's "how your admin sets this up" door, by connection id. */
export const ADMIN_GUIDES: Record<string, { label: string; href: string }> = {
  microsoft_365: {
    label: "How your admin sets this up",
    href: `/help?guide=handbook&section=${M365_ADMIN_SECTION}`,
  },
};

/** What a card's documentation link IS, from its address: a GitHub page is
 *  the program's own page, anything else is its documentation. */
export function docsLinkLabel(url: string | null | undefined): string {
  let host = "";
  try {
    host = new URL(url ?? "").hostname.toLowerCase();
  } catch {
    return "Docs";
  }
  if (host === "github.com" || host.endsWith(".github.com")) return "The program’s own page on GitHub";
  return "Docs";
}
