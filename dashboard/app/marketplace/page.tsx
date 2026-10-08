import { redirect } from "next/navigation";
import { RELOCATED, relocatedHref, type SearchParams } from "@/lib/relocated";

/**
 * The Directory — now Settings › Connections › Apps & tools (calm UI redesign S10, AUDIT Q8). The old address redirects there
 * with its query, so every link and deep link that pointed here still works.
 */
export default async function Relocated({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const to = RELOCATED["/marketplace"];
  redirect(relocatedHref(to.base, await searchParams, to.extra));
}
