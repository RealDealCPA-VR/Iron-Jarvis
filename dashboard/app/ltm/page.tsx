"use client";

import { useState } from "react";
import {
  Database,
  Search,
  NotebookPen,
  FileText,
  Plus,
  FolderPlus,
  FolderOpen,
  Layers,
  X,
} from "lucide-react";
import { get, post, del, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import type { LtmResult, LtmSource } from "@/lib/types";
import {
  Card,
  Badge,
  OfflineHint,
  Empty,
  ErrorNote,
  SuccessNote,
  LoaderInline,
  ConfirmButton,
  type Tone,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { VoiceInput, appendDictation } from "@/components/VoiceInput";
import { DirectoryTree } from "@/components/terminal/DirectoryTree";

const DEFAULT_SOURCES = ["brain", "obsidian", "notion"];

const SOURCE_TONE: Record<string, Tone> = {
  brain: "cyan",
  obsidian: "violet",
  notion: "slate",
  ssh: "violet",
};

type Kind = "markdown" | "notion" | "ssh";

export default function LtmPage() {
  const {
    data: sourcesData,
    reload: reloadSources,
  } = useApi<{ sources: LtmSource[]; active: string[] }>("/ltm/sources");
  const customSources = sourcesData?.sources ?? [];
  // The active source names power the filter/append dropdowns; fall back to the
  // built-in defaults when the daemon is unreachable.
  const sourceOptions = sourcesData?.active?.length
    ? sourcesData.active
    : DEFAULT_SOURCES;

  const [q, setQ] = useState("");
  const [source, setSource] = useState("");
  const [k, setK] = useState(5);
  const [results, setResults] = useState<LtmResult[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);

  // Append form
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [appendSource, setAppendSource] = useState("brain");
  const [appendBusy, setAppendBusy] = useState(false);
  const [appendError, setAppendError] = useState<string | null>(null);
  const [appendOk, setAppendOk] = useState<string | null>(null);

  // Add-source panel
  const [srcName, setSrcName] = useState("");
  const [srcKind, setSrcKind] = useState<Kind>("markdown");
  const [srcPath, setSrcPath] = useState("");
  const [srcDb, setSrcDb] = useState("");
  const [srcToken, setSrcToken] = useState("");
  // SSH (remote) source fields
  const [srcHost, setSrcHost] = useState("");
  const [srcPort, setSrcPort] = useState("22");
  const [srcUser, setSrcUser] = useState("");
  const [srcPassword, setSrcPassword] = useState("");
  const [srcBusy, setSrcBusy] = useState(false);
  const [srcError, setSrcError] = useState<string | null>(null);
  const [srcOk, setSrcOk] = useState<string | null>(null);

  // Folder browser (for the markdown-source path). `browsePick` tracks the
  // directory highlighted in the tree before the user confirms it.
  const [browseOpen, setBrowseOpen] = useState(false);
  const [browsePick, setBrowsePick] = useState<string | null>(null);

  function openBrowse() {
    setBrowsePick(srcPath.trim() || null);
    setBrowseOpen(true);
  }

  function useFolder(path: string) {
    if (path) setSrcPath(path);
    setBrowseOpen(false);
  }

  async function search(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    setBusy(true);
    setError(null);
    setOffline(false);
    try {
      const params = new URLSearchParams({ q: q.trim(), k: String(k) });
      if (source) params.set("source", source);
      const data = await get<{ results: LtmResult[] }>(`/ltm/search?${params.toString()}`);
      setResults(data.results);
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) setOffline(true);
      else setError(err instanceof ApiError ? err.message : String(err));
      setResults(null);
    } finally {
      setBusy(false);
    }
  }

  async function append(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim() || !content.trim()) return;
    setAppendBusy(true);
    setAppendError(null);
    setAppendOk(null);
    try {
      const res = await post<{ ref: string; source: string }>("/ltm/append", {
        title: title.trim(),
        content: content.trim(),
        source: appendSource,
      });
      setAppendOk(`Saved to ${res.source} → ${res.ref}`);
      setTitle("");
      setContent("");
    } catch (err) {
      setAppendError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setAppendBusy(false);
    }
  }

  async function addSource(e: React.FormEvent) {
    e.preventDefault();
    if (!srcName.trim()) return;
    if (srcKind === "markdown" && !srcPath.trim()) {
      setSrcError("A markdown source needs a folder path.");
      return;
    }
    if (srcKind === "notion" && !srcDb.trim()) {
      setSrcError("A Notion source needs a database id.");
      return;
    }
    if (srcKind === "ssh" && (!srcHost.trim() || !srcPath.trim())) {
      setSrcError("An SSH source needs a host and a remote folder path.");
      return;
    }
    setSrcBusy(true);
    setSrcError(null);
    setSrcOk(null);
    try {
      await post("/ltm/sources", {
        name: srcName.trim(),
        kind: srcKind,
        // `path` is a LOCAL folder for markdown, the REMOTE folder for ssh.
        path: srcKind === "markdown" || srcKind === "ssh" ? srcPath.trim() : "",
        database_id: srcKind === "notion" ? srcDb.trim() : "",
        token_secret: srcKind === "notion" ? srcToken.trim() : "",
        host: srcKind === "ssh" ? srcHost.trim() : "",
        port: srcKind === "ssh" ? Number(srcPort) || 22 : 22,
        username: srcKind === "ssh" ? srcUser.trim() : "",
        password: srcKind === "ssh" ? srcPassword : "",
      });
      setSrcOk(`Source "${srcName.trim()}" added.`);
      setSrcName("");
      setSrcPath("");
      setSrcDb("");
      setSrcToken("");
      setSrcHost("");
      setSrcPort("22");
      setSrcUser("");
      setSrcPassword("");
      setSrcKind("markdown");
      reloadSources();
    } catch (err) {
      setSrcError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSrcBusy(false);
    }
  }

  async function removeSource(nm: string) {
    setSrcError(null);
    try {
      await del(`/ltm/sources/${encodeURIComponent(nm)}`);
      reloadSources();
    } catch (err) {
      setSrcError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Long-term Memory"
          subtitle="Search and append durable notes across built-in and your own custom sources. Use the mic to dictate."
        />
      </Reveal>
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
                  placeholder="Search long-term memory… or dictate"
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
            <div className="w-40">
              <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                Source
              </label>
              <select aria-label="Source" value={source} onChange={(e) => setSource(e.target.value)} className="field">
                <option value="">All</option>
                {sourceOptions.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
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
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <Card title={`Results${results ? ` · ${results.length}` : ""}`} icon={<Database size={15} />}>
              {results === null ? (
                <Empty icon={<Search size={22} />}>Run a search to see notes.</Empty>
              ) : results.length === 0 ? (
                <Empty>No matches.</Empty>
              ) : (
                <ul className="space-y-2.5">
                  {results.map((r, i) => (
                    <li
                      key={`${r.ref ?? r.title}/${i}`}
                      className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-3.5 py-3"
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="truncate text-sm font-semibold text-zinc-100">
                          {r.title}
                        </span>
                        <Badge value={r.source} tone={SOURCE_TONE[r.source] ?? "slate"} />
                      </div>
                      <p className="mt-1 text-sm text-zinc-400">{r.snippet}</p>
                      {r.ref && (
                        <div className="mt-1.5 font-mono text-[11px] text-zinc-600">{r.ref}</div>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          <div className="lg:col-span-1">
            <Card title="Append note" icon={<NotebookPen size={15} />}>
              <form onSubmit={append} className="space-y-3.5">
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Title
                  </label>
                  <input
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder="Note title"
                    className="field"
                  />
                </div>
                <div>
                  <div className="mb-1.5 flex items-center justify-between">
                    <label className="block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                      Content
                    </label>
                    <VoiceInput
                      size="sm"
                      onTranscript={(chunk) => setContent((p) => appendDictation(p, chunk))}
                    />
                  </div>
                  <textarea
                    value={content}
                    onChange={(e) => setContent(e.target.value)}
                    rows={4}
                    placeholder="Write or dictate the note…"
                    className="field resize-y"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Source
                  </label>
                  <select
                    aria-label="Source"
                    value={appendSource}
                    onChange={(e) => setAppendSource(e.target.value)}
                    className="field"
                  >
                    {sourceOptions.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </div>
                <button
                  type="submit"
                  disabled={appendBusy || !title.trim() || !content.trim()}
                  className="btn-accent w-full"
                >
                  {appendBusy ? <LoaderInline label="Saving…" /> : <><FileText size={14} /> Append note</>}
                </button>
                {appendOk && <SuccessNote>{appendOk}</SuccessNote>}
                {appendError && <ErrorNote>{appendError}</ErrorNote>}
              </form>
            </Card>
          </div>
        </div>
      </Reveal>

      {/* Custom memory sources ------------------------------------------------ */}
      <Reveal>
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-1">
            <Card title="Add memory source" icon={<FolderPlus size={15} />}>
              <form onSubmit={addSource} className="space-y-3.5">
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Name
                  </label>
                  <input
                    value={srcName}
                    onChange={(e) => setSrcName(e.target.value)}
                    placeholder="my-notes"
                    className="field"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Kind
                  </label>
                  <select
                    aria-label="Kind"
                    value={srcKind}
                    onChange={(e) => setSrcKind(e.target.value as Kind)}
                    className="field"
                  >
                    <option value="markdown">Markdown folder</option>
                    <option value="ssh">Remote folder (SSH)</option>
                    <option value="notion">Notion database</option>
                  </select>
                </div>

                {srcKind === "markdown" ? (
                  <div>
                    <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                      Folder path
                    </label>
                    <div className="flex items-stretch gap-2">
                      <input
                        value={srcPath}
                        onChange={(e) => setSrcPath(e.target.value)}
                        placeholder="C:\\Users\\me\\notes"
                        className="field flex-1 font-mono"
                      />
                      <button
                        type="button"
                        onClick={openBrowse}
                        title="Browse for a folder on this machine"
                        className="inline-flex shrink-0 items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.02] px-3 text-[13px] font-medium text-zinc-300 transition-colors hover:border-accent/30 hover:bg-accent/[0.08] hover:text-accent-soft"
                      >
                        <FolderOpen size={14} /> Browse
                      </button>
                    </div>
                    <div className="mt-1 text-[11px] text-zinc-600">
                      A local folder of .md files to index — type a path or Browse to pick one.
                    </div>
                  </div>
                ) : srcKind === "ssh" ? (
                  <>
                    <div className="grid grid-cols-3 gap-2">
                      <div className="col-span-2">
                        <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                          Host
                        </label>
                        <input
                          value={srcHost}
                          onChange={(e) => setSrcHost(e.target.value)}
                          placeholder="nas.local"
                          className="field font-mono"
                        />
                      </div>
                      <div>
                        <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                          Port
                        </label>
                        <input
                          value={srcPort}
                          onChange={(e) => setSrcPort(e.target.value)}
                          placeholder="22"
                          className="field font-mono"
                        />
                      </div>
                    </div>
                    <div>
                      <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                        Username
                      </label>
                      <input
                        value={srcUser}
                        onChange={(e) => setSrcUser(e.target.value)}
                        placeholder="me"
                        className="field font-mono"
                      />
                    </div>
                    <div>
                      <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                        Remote folder path
                      </label>
                      <input
                        value={srcPath}
                        onChange={(e) => setSrcPath(e.target.value)}
                        placeholder="/home/me/notes"
                        className="field font-mono"
                      />
                    </div>
                    <div>
                      <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                        Password
                      </label>
                      <input
                        type="password"
                        value={srcPassword}
                        onChange={(e) => setSrcPassword(e.target.value)}
                        placeholder="SSH password"
                        autoComplete="off"
                        className="field"
                      />
                      <div className="mt-1 text-[11px] text-zinc-600">
                        Stored encrypted in the vault. A remote folder of .md notes,
                        searched + appended to over SSH.
                      </div>
                    </div>
                  </>
                ) : (
                  <>
                    <div>
                      <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                        Database id
                      </label>
                      <input
                        value={srcDb}
                        onChange={(e) => setSrcDb(e.target.value)}
                        placeholder="notion database id"
                        className="field font-mono"
                      />
                    </div>
                    <div>
                      <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                        Token secret
                      </label>
                      <input
                        value={srcToken}
                        onChange={(e) => setSrcToken(e.target.value)}
                        placeholder="name of stored Notion-token secret"
                        className="field"
                      />
                      <div className="mt-1 text-[11px] text-zinc-600">
                        The name of a stored secret holding the Notion token.
                      </div>
                    </div>
                  </>
                )}

                <button
                  type="submit"
                  disabled={srcBusy || !srcName.trim()}
                  className="btn-accent w-full"
                >
                  {srcBusy ? <LoaderInline label="Adding…" /> : <><Plus size={14} /> Add source</>}
                </button>
                {srcOk && <SuccessNote>{srcOk}</SuccessNote>}
                {srcError && <ErrorNote>{srcError}</ErrorNote>}
              </form>
            </Card>
          </div>

          <div className="lg:col-span-2">
            <Card
              title={`Custom sources${customSources.length ? ` · ${customSources.length}` : ""}`}
              icon={<Layers size={15} />}
            >
              {customSources.length === 0 ? (
                <Empty icon={<Layers size={22} />}>
                  No custom sources yet — add a markdown folder or Notion database on the left.
                </Empty>
              ) : (
                <ul className="space-y-2">
                  {customSources.map((s) => (
                    <li
                      key={s.name}
                      className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.05] bg-white/[0.02] px-3.5 py-2.5"
                    >
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold text-zinc-100">{s.name}</span>
                          <Badge
                            value={s.kind}
                            tone={s.kind === "notion" ? "slate" : s.kind === "ssh" ? "violet" : "cyan"}
                          />
                        </div>
                        <div className="mt-0.5 truncate font-mono text-[11px] text-zinc-500">
                          {s.kind === "notion"
                            ? s.database_id || "—"
                            : s.kind === "ssh"
                              ? `${s.username ? s.username + "@" : ""}${s.host || "—"}:${s.path || ""}`
                              : s.path || "—"}
                        </div>
                      </div>
                      <ConfirmButton
                        onConfirm={() => removeSource(s.name)}
                        label="Remove"
                        title={`Remove memory source "${s.name}"`}
                      />
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </div>
      </Reveal>

      {/* Folder browser modal — pick a markdown folder off the machine ------- */}
      {browseOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
          onClick={() => setBrowseOpen(false)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Pick a markdown folder"
            onClick={(e) => e.stopPropagation()}
            className="flex max-h-[82vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-white/10 bg-ink-850/95 shadow-card-hover backdrop-blur-xl"
          >
            <header className="flex items-center gap-2 border-b hairline px-4 py-3">
              <FolderOpen size={16} className="text-accent-soft/80" />
              <h2 className="text-[13px] font-semibold tracking-wide text-zinc-200">
                Pick a markdown folder
              </h2>
              <button
                type="button"
                onClick={() => setBrowseOpen(false)}
                title="Close"
                className="ml-auto grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
              >
                <X size={14} />
              </button>
            </header>

            <div className="min-h-0 flex-1 p-3">
              <div className="h-[56vh]">
                <DirectoryTree
                  selectedPath={browsePick}
                  onSelect={setBrowsePick}
                  hideAction
                />
              </div>
            </div>

            <footer className="flex items-center gap-3 border-t hairline px-4 py-3">
              <div
                className="min-w-0 flex-1 truncate font-mono text-[12px] text-accent-soft"
                title={browsePick ?? undefined}
              >
                {browsePick ?? "— select a folder —"}
              </div>
              <button
                type="button"
                onClick={() => setBrowseOpen(false)}
                className="btn-ghost py-1.5 text-[13px]"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => browsePick && useFolder(browsePick)}
                disabled={!browsePick}
                className="btn-accent py-1.5 text-[13px]"
              >
                Use this folder
              </button>
            </footer>
          </div>
        </div>
      )}
    </PageShell>
  );
}
