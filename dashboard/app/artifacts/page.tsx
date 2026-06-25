"use client";

import { useState } from "react";
import { Package, FileCode } from "lucide-react";
import { useApi } from "@/lib/useApi";
import { Card, Spinner, OfflineHint, Empty, SkeletonRows } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

interface ArtifactDetail {
  name: string;
  version: number;
  size: number;
  versions: unknown[];
  content: string | null;
}

export default function ArtifactsPage() {
  const { data, error, loading } = useApi<{ artifacts: string[] }>("/artifacts");
  const [selected, setSelected] = useState<string | null>(null);
  const detail = useApi<ArtifactDetail>(
    selected ? `/artifacts/${encodeURIComponent(selected)}` : null,
    [selected],
  );

  const offline = error && error.status === 0;
  const names = data?.artifacts ?? [];

  return (
    <PageShell>
      <Reveal>
        <PageHeader title="Artifacts" subtitle="Versioned outputs produced by sessions (§26)." />
      </Reveal>
      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <Reveal>
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-1">
            <Card title={`Artifacts · ${names.length}`} icon={<Package size={15} />}>
              {loading && !data ? (
                <SkeletonRows rows={5} />
              ) : names.length === 0 ? (
                <Empty icon={<Package size={22} />}>No artifacts.</Empty>
              ) : (
                <ul className="space-y-1">
                  {names.map((n) => (
                    <li key={n}>
                      <button
                        onClick={() => setSelected(n)}
                        className={`w-full truncate rounded-xl border px-3 py-2.5 text-left text-sm transition-colors ${
                          selected === n
                            ? "border-accent/30 bg-accent/[0.08] text-accent-soft"
                            : "border-transparent text-zinc-300 hover:border-white/10 hover:bg-white/[0.04]"
                        }`}
                      >
                        {n}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          <div className="lg:col-span-2">
            <Card
              title={selected || "Artifact"}
              icon={<FileCode size={15} />}
              right={
                detail.data ? (
                  <span className="font-mono text-xs text-zinc-500">
                    v{detail.data.version} · {detail.data.size} bytes
                  </span>
                ) : null
              }
            >
              {!selected ? (
                <Empty icon={<FileCode size={22} />}>Select an artifact to view its content.</Empty>
              ) : detail.loading && !detail.data ? (
                <Spinner />
              ) : detail.data ? (
                <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-xl border border-white/[0.06] bg-ink-950 p-4 text-xs leading-relaxed text-zinc-300">
                  {detail.data.content ?? "(binary or empty content)"}
                </pre>
              ) : (
                <Empty>Could not load artifact.</Empty>
              )}
            </Card>
          </div>
        </div>
      </Reveal>
    </PageShell>
  );
}
