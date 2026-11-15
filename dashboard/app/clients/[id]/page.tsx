"use client";

import { use } from "react";
import { PageShell, Reveal } from "@/components/motion";
import { ClientPage } from "@/components/clients/ClientPage";

/**
 * One client's page (v1.499.0, vision plan leap 3, Phase 3): its folders,
 * what it wants, what a chat in its folders is told, the decisions from
 * checked recaps, its work, files and the names seen in them. Every line
 * names the record it came from. An id that is not a client id reads
 * nothing and says "That client is not on record."
 */
export default function ClientRoute({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <PageShell>
      <Reveal>
        <ClientPage id={id} />
      </Reveal>
    </PageShell>
  );
}
