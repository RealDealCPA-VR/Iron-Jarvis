"use client";

import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import WorkflowCanvas from "@/components/workflow/WorkflowCanvas";

export default function WorkflowsPage() {
  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Workflows"
          subtitle="Wire agents into a visual, multi-step workflow, then run it (§24)."
        />
      </Reveal>
      <Reveal>
        <WorkflowCanvas />
      </Reveal>
    </PageShell>
  );
}
