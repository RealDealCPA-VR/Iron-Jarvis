/**
 * What a tool DOES to your machine, said in four words or fewer (v1.216.0).
 *
 * From the review: "Add a tiny risk chip: read, write, network, browser… Equal
 * looking + Add buttons flatten risk. Opening a browser or zipping a folder is
 * not the same as DNS lookup."
 *
 * WHY THIS IS A TABLE AND NOT AN INFERENCE. Risk is the one thing on a card a
 * user should not have to take on faith, and a keyword guess over a command
 * string would be wrong in exactly the cases that matter (a "fetch" that also
 * writes, a "list" that also deletes). The built-in suite is eight fixed
 * entries and the curated catalog is nine, so both are enumerated BY ID and
 * reviewed by a person. `capsFor` falls back to a conservative guess only for
 * an id this build has never seen — a catalog the daemon grew after this
 * dashboard shipped — and says so by returning nothing rather than a
 * reassuring "read".
 */

/** The four the review asked for, plus `system` for machine-level reads. */
export type Capability = "read" | "write" | "network" | "browser" | "system";

export const CAPABILITY_LABEL: Record<Capability, string> = {
  read: "reads files",
  write: "writes files",
  network: "network",
  browser: "opens browser",
  system: "system info",
};

/** The short word on the chip itself. */
export const CAPABILITY_CHIP: Record<Capability, string> = {
  read: "read",
  write: "write",
  network: "network",
  browser: "browser",
  system: "system",
};

/** Ordered loosest → most reaching, so a card's chips read in a stable order
 *  and the most alarming one is never buried first. */
export const CAPABILITY_ORDER: Capability[] = [
  "system",
  "read",
  "network",
  "write",
  "browser",
];

/** Built-in suite tools, by their registered name. */
const SUITE_CAPS: Record<string, Capability[]> = {
  http_get: ["network"],
  ping_host: ["network"],
  dns_lookup: ["network"],
  list_dir: ["read"],
  disk_free: ["system"],
  git_status: ["read"],
  // Reads a whole folder and writes an archive somewhere else — the one
  // built-in that puts new bytes on disk.
  zip_folder: ["read", "write"],
  // Hands a URL to the Windows shell's protocol handler: whatever opens it,
  // opens. Not the same act as a DNS lookup, and the chip says so.
  open_url: ["browser"],
};

/** Curated MCP catalog entries, by their catalog id. */
const PACK_CAPS: Record<string, Capability[]> = {
  filesystem: ["read", "write"],
  fetch: ["network"],
  memory: ["read", "write"],
  sequentialthinking: [],
  git: ["read", "write"],
  everything: [],
  github: ["network", "write"],
  playwright: ["browser", "network"],
  box: ["network", "read", "write"],
};

function sorted(caps: Capability[]): Capability[] {
  return CAPABILITY_ORDER.filter((c) => caps.includes(c));
}

/** Capabilities for a built-in suite tool. Unknown name → nothing claimed. */
export function suiteCaps(name: string): Capability[] {
  return sorted(SUITE_CAPS[name] ?? []);
}

/**
 * Capabilities for a catalog pack.
 *
 * An id this build does not know returns `[]` — no chips — rather than a
 * guess. A card that quietly claims "read" about a pack nobody reviewed is
 * worse than a card that claims nothing.
 */
export function packCaps(id: string): Capability[] {
  return sorted(PACK_CAPS[id] ?? []);
}

/** Does this build know what the pack does? Drives "unreviewed" wording. */
export function packIsKnown(id: string): boolean {
  return id in PACK_CAPS;
}

/**
 * The richer extension that covers the same ground as a built-in (review §2:
 * "Treat ready-made items as thin, zero-setup tools and MCP as capability
 * packs… On each ready-made card, if a richer plugin exists: 'Need write
 * access / pagination / auth? Use Files & folders instead.'").
 *
 * People were adding both and could not say why one existed.
 */
export const RICHER_PACK: Record<string, { id: string; name: string; why: string }> = {
  http_get: {
    id: "fetch",
    name: "Fetch web pages",
    why: "follows redirects, handles large pages and returns clean text",
  },
  list_dir: {
    id: "filesystem",
    name: "Files & folders",
    why: "can also read, write and move files, not just list them",
  },
  git_status: {
    id: "git",
    name: "Git repositories",
    why: "reads history and diffs, not just what changed",
  },
};

/**
 * The catalog id whose only job is to prove the plumbing works.
 *
 * Review §8: "'Demo / connection test' is useful; pin it as a Verify setup
 * action at the top of Extensions, not as a peer of Long-term memory." It is a
 * diagnostic, and listing it beside real capability packs invited people to
 * install a demo server and wonder what it gave them.
 */
export const VERIFY_PACK_ID = "everything";

/** Official reference servers vs community integrations (review §8: the badge
 *  should be said ONCE and clearly, not stamped on every card as wallpaper). */
export function isOfficial(category?: string): boolean {
  return (category ?? "") === "reference";
}

/** Runtimes a pack can need, as the catalog words them. */
export type Runtime = "Node" | "Python (uv)";

/**
 * How to get a missing runtime, in the user's language.
 *
 * Review §4: "Promote runtime to a real state: 'Python not installed — Install
 * uv' instead of a gray needs Python (uv) pill." The dashboard cannot detect
 * what is installed (the daemon runs the command, and a probe per card would
 * be nine spawns on page load), so this is the ACTION half only: the state is
 * still "needs Node", never a claimed "Node is missing". Saying which one it
 * needs and how to get it is the part that was missing.
 */
export const RUNTIME_HELP: Record<string, { label: string; href: string; how: string }> = {
  Node: {
    label: "Node",
    href: "https://nodejs.org/en/download",
    how: "Packs marked Node run through npx, which ships with Node.js.",
  },
  "Python (uv)": {
    label: "Python (uv)",
    href: "https://docs.astral.sh/uv/getting-started/installation/",
    how: "Packs marked Python run through uvx, which comes with uv.",
  },
};
