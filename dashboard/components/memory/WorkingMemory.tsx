"use client";

// Working-memory scope of the unified /memory surface: the layered key-value
// store (session/project/user) agents read mid-run. Moved verbatim from the
// old app/memory/page.tsx body — search UI + scored results table.

import { useState } from "react";
import { Search, BrainCircuit } from "lucide-react";
import { get, ApiError } from "@/lib/api";
import type { MemoryResult } from "@/lib/types";
import { Card, Badge, OfflineHint, Empty, ErrorNote, LoaderInline } from "@/components/ui";
import { Reveal } from "@/components/motion";
import { VoiceInput, appendDictation } from "@/components/VoiceInput";
import { num } from "@/lib/format";

export function WorkingMemory() {
  const [q, setQ] = useState("");
  const [k, setK] = useState(5);
  const [results, setResults] = useState<MemoryResult[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);

  async function search(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    setBusy(true);
    setError(null);
    setOffline(false);
    try {
      const data = await get<{ results: MemoryResult[] }>(
        `/memory/search?q=${encodeURIComponent(q.trim())}&k=${k}`,
      );
      setResults(data.results);
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) setOffline(true);
      else setError(err instanceof ApiError ? err.message : String(err));
      setResults(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <Reveal>
        <Card>
          <form onSubmit={search} className="flex flex-wrap items-end gap-3">
            <div className="min-w-[240px] flex-1">
              <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                Query
              </label>
              <div className="relative">
                <Search
                  size={15}
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-600"
                />
                <input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="Search memory… or dictate"
                  className="field pl-9 pr-12"
                />
                <div className="absolute right-1.5 top-1/2 -translate-y-1/2">
                  <VoiceInput
                    size="sm"
                    onTranscript={(chunk) => setQ((p) => appendDictation(p, chunk))}
                  />
                </div>
              </div>
            </div>
            <div className="w-20">
              <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                k
              </label>
              <input
                type="number"
                min={1}
                max={50}
                value={k}
                onChange={(e) => setK(Number(e.target.value) || 5)}
                aria-label="Results to retrieve (k)"
                className="field"
              />
            </div>
            <button type="submit" disabled={busy || !q.trim()} className="btn-accent">
              {busy ? <LoaderInline label="Searching…" /> : "Search"}
            </button>
          </form>
          {error && (
            <div className="mt-3">
              <ErrorNote>{error}</ErrorNote>
            </div>
          )}
        </Card>
      </Reveal>

      <Reveal>
        <Card title="Results" icon={<BrainCircuit size={15} />}>
          {results === null ? (
            <Empty icon={<Search size={22} />}>Run a search to see results.</Empty>
          ) : results.length === 0 ? (
            <Empty>No matches.</Empty>
          ) : (
            <div className="-mx-1 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b hairline text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    <th className="px-2 py-2.5 font-medium">Score</th>
                    <th className="px-2 py-2.5 font-medium">Layer</th>
                    <th className="px-2 py-2.5 font-medium">Key</th>
                    <th className="px-2 py-2.5 font-medium">Text</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((r, i) => (
                    <tr
                      key={`${r.layer}/${r.key}/${i}`}
                      className="border-b border-white/[0.04] align-top last:border-0 hover:bg-white/[0.02]"
                    >
                      <td className="px-2 py-2.5 font-mono text-accent-soft">{num(r.score, 3)}</td>
                      <td className="px-2 py-2.5">
                        <Badge value={r.layer} tone="violet" />
                      </td>
                      <td className="px-2 py-2.5 font-mono text-zinc-300">{r.key}</td>
                      <td className="px-2 py-2.5 text-zinc-400">{r.text}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </Reveal>
    </>
  );
}
