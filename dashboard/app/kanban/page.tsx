import { redirect } from "next/navigation";
import { RELOCATED, relocatedHref, type SearchParams } from "@/lib/relocated";

/**
 * The Session board — now the Board view of Sessions (calm UI redesign S10, AUDIT Q8). The old address redirects there
 * with its query, so every link and deep link that pointed here still works.
 */
export default async function Relocated({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const to = RELOCATED["/kanban"];
  redirect(relocatedHref(to.base, await searchParams, to.extra));
}
