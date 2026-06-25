"use client";

import { useState } from "react";
import { Sparkles, BookOpen } from "lucide-react";
import { useApi } from "@/lib/useApi";
import type { Skill, SkillDetail } from "@/lib/types";
import { Card, Spinner, OfflineHint, Empty, SkeletonRows } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

export default function SkillsPage() {
  const { data, error, loading } = useApi<{ skills: Skill[] }>("/skills");
  const [selected, setSelected] = useState<string | null>(null);
  const detail = useApi<SkillDetail>(selected ? `/skills/${selected}` : null, [selected]);

  const offline = error && error.status === 0;
  const skills = data?.skills ?? [];

  return (
    <PageShell>
      <Reveal>
        <PageHeader title="Skills" subtitle="Reusable agent skills (§23)." />
      </Reveal>
      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <Reveal>
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-1">
            <Card title={`Available · ${skills.length}`} icon={<Sparkles size={15} />}>
              {loading && !data ? (
                <SkeletonRows rows={5} />
              ) : skills.length === 0 ? (
                <Empty icon={<Sparkles size={22} />}>No skills.</Empty>
              ) : (
                <ul className="space-y-1">
                  {skills.map((s) => (
                    <li key={s.name}>
                      <button
                        onClick={() => setSelected(s.name)}
                        className={`w-full rounded-xl border px-3 py-2.5 text-left text-sm transition-colors ${
                          selected === s.name
                            ? "border-accent/30 bg-accent/[0.08] text-accent-soft"
                            : "border-transparent text-zinc-300 hover:border-white/10 hover:bg-white/[0.04]"
                        }`}
                      >
                        <div className="font-medium">{s.name}</div>
                        <div className="truncate text-xs text-zinc-500">{s.description}</div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          <div className="lg:col-span-2">
            <Card title={selected ?? "Instructions"} icon={<BookOpen size={15} />}>
              {!selected ? (
                <Empty icon={<BookOpen size={22} />}>Select a skill to view its instructions.</Empty>
              ) : detail.loading && !detail.data ? (
                <Spinner />
              ) : detail.data ? (
                <div className="space-y-3">
                  <p className="text-sm text-zinc-400">{detail.data.description}</p>
                  <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-xl border border-white/[0.06] bg-ink-950 p-4 text-xs leading-relaxed text-zinc-300">
                    {detail.data.instructions}
                  </pre>
                </div>
              ) : (
                <Empty>Could not load skill.</Empty>
              )}
            </Card>
          </div>
        </div>
      </Reveal>
    </PageShell>
  );
}
