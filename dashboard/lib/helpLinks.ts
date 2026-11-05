/**
 * Links into the Help page's guides (v1.436.0, chat-first plan Wave 5, B3).
 *
 * A chat reply about a step the user does in their vendor's own account (a
 * Microsoft 365 admin's approval) or a choice the app's guide explains (which
 * local model to download) carries a door to the guide's own words:
 * `/help?guide=<slug>&section=<section>`. The Help page reads it ONCE when it
 * opens (window.location — never `useSearchParams`, which would force a
 * Suspense boundary on a static page), opens that guide in its viewer and
 * brings the section's heading into view.
 *
 * `headingSlug` is the ONE section rule, and the daemon's twin
 * (`daemon/page_doors.heading_slug`) is held equal by
 * `__tests__/page-doors-v1436.test.tsx`: a section names the FIRST heading
 * whose slug is the section or starts with it and a "-", so a heading's
 * version suffix ("… (v1.428.0)") may change without breaking the door.
 *
 * Import-free on purpose (the Help page and the tests both read it).
 */

/** A heading's slug: lower case, every run of anything but a-z and 0-9 one
 *  "-", none at either end. */
export function headingSlug(text: string): string {
  return String(text ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** True when the heading slug `slug` is the one `section` names. */
export function sectionMatches(slug: string, section: string): boolean {
  const s = headingSlug(section);
  if (!s || !slug) return false;
  return slug === s || slug.startsWith(`${s}-`);
}

/** The guide (and section) a Help address asks for, or null. */
export function readGuideLink(search: string | null | undefined): { guide: string; section: string } | null {
  let q: URLSearchParams;
  try {
    q = new URLSearchParams(search ?? "");
  } catch {
    return null;
  }
  const guide = (q.get("guide") ?? "").trim();
  if (!guide || !/^[a-z0-9-]{1,40}$/.test(guide)) return null;
  const section = headingSlug(q.get("section") ?? "").slice(0, 120);
  return { guide, section };
}

/** The Help page opened on `guide` (and on its `section`). */
export function helpGuideHref(guide: string, section = ""): string {
  const s = headingSlug(section);
  return s ? `/help?guide=${guide}&section=${s}` : `/help?guide=${guide}`;
}

/** The plain text of a React heading's children (strings, numbers and the
 *  text inside nested elements such as `<code>` or `<strong>`). */
export function textOf(children: unknown): string {
  if (children == null || typeof children === "boolean") return "";
  if (typeof children === "string" || typeof children === "number") return String(children);
  if (Array.isArray(children)) return children.map(textOf).join("");
  if (typeof children === "object" && "props" in (children as object)) {
    return textOf((children as { props?: { children?: unknown } }).props?.children);
  }
  return "";
}
