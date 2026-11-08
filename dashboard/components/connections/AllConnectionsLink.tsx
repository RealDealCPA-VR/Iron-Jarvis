"use client";

/**
 * The quiet link back to the ONE connections front door (v1.455.0, readiness
 * Wave H item 35), on every page that holds one kind of connection.
 */

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { CONNECTIONS_HOME_HREF, CONNECTIONS_HOME_LABEL } from "@/lib/connectionsHome";

export function AllConnectionsLink({ testId = "all-connections-link" }: { testId?: string }) {
  return (
    <Link
      href={CONNECTIONS_HOME_HREF}
      data-testid={testId}
      className="inline-flex w-fit items-center gap-1 text-[12px] text-zinc-400 underline-offset-2 transition-colors hover:text-accent-soft hover:underline"
    >
      {CONNECTIONS_HOME_LABEL} <ArrowRight size={12} aria-hidden="true" />
    </Link>
  );
}
