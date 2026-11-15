"use client";

import { PageShell, Reveal } from "@/components/motion";
import { PageHeader } from "@/components/PageHeader";
import { ClientList } from "@/components/clients/ClientList";
import { CLIENTS_ABOUT } from "@/lib/clients";

/**
 * Clients (v1.499.0, vision plan leap 3, Phase 3): the people and businesses
 * the owner works for, each known by its folders and confirmed by the owner,
 * and the names Iron Jarvis noticed that wait for an answer. The about line
 * is on the page itself (not a hover hint): it says the one rule that
 * matters, that a chat is told about a client only in that client's folders.
 */
export default function ClientsPage() {
  return (
    <PageShell>
      <Reveal>
        <PageHeader title="Clients" />
      </Reveal>
      <p className="mb-4 max-w-2xl text-[13px] text-zinc-500" data-testid="clients-about">
        {CLIENTS_ABOUT}
      </p>
      <Reveal>
        <ClientList />
      </Reveal>
    </PageShell>
  );
}
