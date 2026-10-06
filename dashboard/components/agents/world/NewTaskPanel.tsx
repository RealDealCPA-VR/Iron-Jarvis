"use client";

/**
 * "New task" (v1.304.0): give this project's TEAM a job — the project's own
 * `ProjectTasks` (plan → one bundled grant → run, or queue for an agent),
 * with the "Assign to" choices limited to the team. The no-assignee choice
 * reads "Whole team — Jarvis decides": the plain project task, no assignee
 * on the wire, exactly the flow the project page runs.
 *
 * Mounted only while its tab is open, so the project detail it reads (the
 * run history and whether the project has a folder) is fetched on demand.
 */

import { ProjectTasks } from "@/components/project/ProjectTasks";
import { SkeletonRows } from "@/components/ui";
import { useApi } from "@/lib/useApi";
import type { SessionView } from "@/lib/types";
import { teamAssigneeChoices, type WorldMember } from "@/lib/agentWorlds";

export const WHOLE_TEAM_LABEL = "Whole team — Jarvis decides";

export function NewTaskPanel({
  projectId,
  team,
}: {
  projectId: string;
  team: WorldMember[];
}) {
  const detail = useApi<{ project?: { root?: string }; sessions?: SessionView[] }>(
    `/projects/${encodeURIComponent(projectId)}`,
  );
  if (!detail.data && detail.loading) return <SkeletonRows rows={3} />;
  return (
    <div data-testid="world-new-task">
      <ProjectTasks
        projectId={projectId}
        hasRoot={Boolean(detail.data?.project?.root)}
        sessions={detail.data?.sessions ?? []}
        reloadSessions={detail.reload}
        assigneeChoices={teamAssigneeChoices(team)}
        selfLabel={WHOLE_TEAM_LABEL}
      />
    </div>
  );
}

export default NewTaskPanel;
