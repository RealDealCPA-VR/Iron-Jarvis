"use client";

/**
 * One quiet line in the chat's project drawer (v1.406.0): which brain profile
 * this project's chats use ("Brain profile: Notion (this project)"), with a
 * link to the project page where it is chosen. Said only when a profile
 * applies; an older Iron Jarvis (404) or none at all says nothing.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { get } from "@/lib/api";
import { brainApplies, decodeBrainEffective, effectiveWords, type BrainEffective } from "@/lib/brainProfiles";

export function ChatBrainLine({ projectId }: { projectId: string }) {
  const [effective, setEffective] = useState<BrainEffective | null>(null);

  useEffect(() => {
    let live = true;
    setEffective(null);
    get<unknown>(`/brain/effective?project_id=${encodeURIComponent(projectId)}`)
      .then((raw) => {
        if (live) setEffective(decodeBrainEffective(raw));
      })
      .catch(() => {
        if (live) setEffective(null);
      });
    return () => {
      live = false;
    };
  }, [projectId]);

  if (!brainApplies(effective)) return null;
  return (
    <p data-testid="chat-brain-line" className="mt-1 text-[12px] leading-relaxed text-zinc-500">
      Brain profile: {effectiveWords(effective)}.{" "}
      <Link
        href={`/projects/${encodeURIComponent(projectId)}`}
        className="underline decoration-zinc-700 underline-offset-2 hover:text-zinc-300"
      >
        Change it
      </Link>
    </p>
  );
}
