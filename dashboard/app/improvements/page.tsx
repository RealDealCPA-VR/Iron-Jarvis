"use client";

import { PageShell, Reveal } from "@/components/motion";
import { PageHeader } from "@/components/PageHeader";
import { ImprovementsInbox } from "@/components/improvements/ImprovementsInbox";
import { IMPROVEMENTS_SUBTITLE } from "@/lib/improvements";

/**
 * Improvements (v1.495.0): every suggestion Iron Jarvis is waiting on the
 * user to accept or decline, in one place.
 */
export default function ImprovementsPage() {
  return (
    <PageShell>
      <Reveal>
        <PageHeader title="Improvements" subtitle={IMPROVEMENTS_SUBTITLE} />
      </Reveal>
      <Reveal>
        <ImprovementsInbox />
      </Reveal>
    </PageShell>
  );
}
