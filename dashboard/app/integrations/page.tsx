import { redirect } from "next/navigation";

/**
 * Integrations merged into Connections (v1.100.0).
 *
 * These were two sidebar entries for one job. Integrations was mostly a
 * directory pointing at pages the sidebar already listed, and its one real
 * feature — direct REST hookups — was buried underneath it. Both now live on
 * /connections as a single hub.
 *
 * Kept as a redirect rather than deleted: the route is referenced by older
 * notes, bookmarks and anything a user pinned, and a 404 is a worse answer than
 * landing where the feature actually went.
 */
export default function IntegrationsPage() {
  redirect("/connections");
}
