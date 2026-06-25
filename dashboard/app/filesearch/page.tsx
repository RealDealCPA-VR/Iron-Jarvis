"use client";

import { useState } from "react";
import { FileSearch, Search, FileText, HardDrive, FolderOpen } from "lucide-react";
import { get, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import type { FileSearchResult, Drive } from "@/lib/types";
import {
  Card,
  Badge,
  OfflineHint,
  Empty,
  ErrorNote,
  LoaderInline,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { VoiceInput, appendDictation } from "@/components/VoiceInput";

type Mode = "content" | "name" | "semantic";
const MODES: Mode[] = ["content", "name", "semantic"];

const PROJECT_ROOT = ""; // empty root === search the project (daemon default)

export default function FileSearchPage() {
  const { data: drivesData } = useApi<{ drives: Drive[] }>("/filesearch/drives");
  const drives = drivesData?.drives ?? [];

  const [q, setQ] = useState("");
  const [mode, setMode] = useState<Mode>("content");
  // The drive/root chosen from the <select>; "" means the project default.
  const [rootSel, setRootSel] = useState(PROJECT_ROOT);
  // Optional free-text path to drill into a sub-folder; overrides the select.
  const [customPath, setCustomPath] = useState("");
  const [results, setResults] = useState<FileSearchResult[] | null>(null);
  const [searchedRoot, setSearchedRoot] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);

  // The path actually sent as `root` (custom path wins over the dropdown).
  const effectiveRoot = customPath.trim() || rootSel;
  const rootLabel =
    effectiveRoot ||
    drives.find((d) => d.path === rootSel)?.label ||
    "Project (default)";

  async function search(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    setBusy(true);
    setError(null);
    setOffline(false);
    try {
      const params = new URLSearchParams({
        q: q.trim(),
        mode,
        limit: "50",
      });
      if (effectiveRoot) params.set("root", effectiveRoot);
      const data = await get<{ results: FileSearchResult[] }>(
        `/filesearch?${params.toString()}`,
      );
      setResults(data.results);
      setSearchedRoot(effectiveRoot || "Project (default)");
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) setOffline(true);
      else setError(err instanceof ApiError ? err.message : String(err));
      setResults(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="File Search"
          subtitle="Search the project or any local drive by file name, file content, or semantic meaning. Use the mic to dictate your query."
        />
      </Reveal>
      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <Reveal>
        <Card>
          <form onSubmit={search} className="space-y-3">
            <div className="flex flex-wrap items-end gap-3">
              <div className="min-w-[240px] flex-1">
                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-500">
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
                    placeholder="Search files… or dictate"
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
              <button type="submit" disabled={busy || !q.trim()} className="btn-accent">
                {busy ? <LoaderInline label="Searching…" /> : "Search"}
              </button>
            </div>

            {/* Drive / root selector --------------------------------------- */}
            <div className="flex flex-wrap items-end gap-3">
              <div className="w-48">
                <label className="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                  <HardDrive size={12} /> Search in
                </label>
                <select
                  value={rootSel}
                  onChange={(e) => {
                    setRootSel(e.target.value);
                    setCustomPath("");
                  }}
                  className="field"
                >
                  <option value={PROJECT_ROOT}>Project (default)</option>
                  {drives.map((d) => (
                    <option key={d.path} value={d.path}>
                      {d.label} — {d.path}
                    </option>
                  ))}
                </select>
              </div>
              <div className="min-w-[200px] flex-1">
                <label className="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                  <FolderOpen size={12} /> Or a specific folder path
                </label>
                <input
                  value={customPath}
                  onChange={(e) => setCustomPath(e.target.value)}
                  placeholder="e.g. C:\\Users\\me\\Documents (optional)"
                  className="field font-mono"
                />
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-1.5">
                {MODES.map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => setMode(m)}
                    className={`rounded-lg border px-3 py-1.5 text-xs font-medium capitalize transition-colors ${
                      mode === m
                        ? "border-accent/40 bg-accent/[0.1] text-accent-soft"
                        : "border-white/10 text-zinc-400 hover:border-white/20 hover:text-zinc-200"
                    }`}
                  >
                    {m}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-1.5 text-[11px] text-zinc-500">
                <HardDrive size={12} className="text-accent-soft/70" />
                Searching:{" "}
                <span className="font-mono text-accent-soft" title={rootLabel}>
                  {rootLabel}
                </span>
              </div>
            </div>
          </form>
          {error && (
            <div className="mt-3">
              <ErrorNote>{error}</ErrorNote>
            </div>
          )}
        </Card>
      </Reveal>

      <Reveal>
        <Card
          title={`Results${results ? ` · ${results.length}` : ""}`}
          icon={<FileSearch size={15} />}
          right={
            searchedRoot ? (
              <span className="flex items-center gap-1.5 text-[11px] text-zinc-500">
                <HardDrive size={12} />
                <span className="font-mono text-zinc-400">{searchedRoot}</span>
              </span>
            ) : undefined
          }
        >
          {results === null ? (
            <Empty icon={<Search size={22} />}>
              Run a search to see matching files. Pick a drive or type a folder path to search beyond the project.
            </Empty>
          ) : results.length === 0 ? (
            <Empty>No matches.</Empty>
          ) : (
            <ul className="space-y-1.5">
              {results.map((r, i) => (
                <li
                  key={`${r.path}:${r.line ?? i}`}
                  className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5 transition-colors hover:bg-white/[0.04]"
                >
                  <div className="flex items-center gap-2">
                    <FileText size={13} className="shrink-0 text-zinc-600" />
                    <span className="truncate font-mono text-[13px] text-accent-soft" title={r.path}>
                      {r.path}
                      {r.line != null && <span className="text-zinc-500">:{r.line}</span>}
                    </span>
                    {r.root && <Badge value={r.root} tone="slate" />}
                  </div>
                  {r.text && (
                    <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap break-words pl-5 font-mono text-xs text-zinc-400">
                      {r.text}
                    </pre>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </Reveal>
    </PageShell>
  );
}
