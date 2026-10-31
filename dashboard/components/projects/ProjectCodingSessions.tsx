"use client";

/**
 * A project's coding sessions (v1.414.0) — Chat › Code and Build › Code
 * sessions whose folder is in the project (the daemon finds them by folder).
 * One quiet list: the title, which tool (Build › Code), its state in words,
 * when, and Open — into that session in its own view. Renders NOTHING when
 * the project has none, so a project without coding sessions looks exactly
 * as it did.
 */

import Link from "next/link";
import { ArrowRight, Code2 } from "lucide-react";
import { timeAgo } from "@/lib/format";
import { CODING_SESSIONS_LIST } from "@/lib/buildWords";
import {
  codingSessionHref,
  codingStateWords,
  decodeCodingSessions,
} from "@/lib/codingSessions";

export function ProjectCodingSessions({ items }: { items: unknown }) {
  const rows = decodeCodingSessions(items);
  if (rows.length === 0) return null;
  return (
    <section data-testid="project-coding-sessions" aria-label={CODING_SESSIONS_LIST} className="space-y-2">
      <h3 className="flex items-center gap-1.5 text-[12px] font-medium text-zinc-400">
        <Code2 size={13} aria-hidden /> {CODING_SESSIONS_LIST}
      </h3>
      <ul className="space-y-1.5">
        {rows.map((c) => (
          <li key={`${c.view}:${c.id}`}>
            <Link
              href={codingSessionHref(c)}
              data-testid={`project-coding-session-${c.id}`}
              data-state={c.state}
              className="group flex items-center justify-between gap-3 rounded-lg px-3 py-2 transition-colors hover:bg-white/[0.04]"
            >
              <span className="min-w-0">
                <span className="block truncate text-[13px] text-zinc-200">{c.title || "Untitled"}</span>
                <span className="block text-[11px] text-zinc-500">
                  {c.tool ? `${c.tool} · ` : ""}
                  <span
                    className={
                      c.state === "waiting"
                        ? "text-tone-warn"
                        : c.state === "finished" && c.outcome === "failed"
                          ? "text-tone-danger"
                          : ""
                    }
                  >
                    {codingStateWords(c)}
                  </span>
                  {c.updatedAt ? ` · ${timeAgo(c.updatedAt)}` : ""}
                </span>
              </span>
              <span className="flex shrink-0 items-center gap-1 text-[12px] text-accent-soft">
                Open <ArrowRight size={12} aria-hidden />
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default ProjectCodingSessions;
