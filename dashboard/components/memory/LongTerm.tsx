"use client";

// Long-term scope of the unified /memory surface: the durable knowledge base
// (built-in brain folder + your own sources) agents search on demand. Moved
// verbatim from the old app/ltm/page.tsx body, with storage-clarity tweaks:
// the markdown kind is presented as "Local folder / Obsidian vault", and the
// Notion kind takes its integration token inline (stored in the vault).

import { useEffect, useRef, useState } from "react";
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
  Upload,
  Cloud,
  Globe,
  ChevronDown,
  ChevronRight,
  Info,
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
import { Reveal } from "@/components/motion";
import { VoiceInput, appendDictation } from "@/components/VoiceInput";
import { DirectoryTree } from "@/components/terminal/DirectoryTree";

const DEFAULT_SOURCES = ["brain", "obsidian", "notion"];

const SOURCE_TONE: Record<string, Tone> = {
  brain: "cyan",
  obsidian: "violet",
  notion: "slate",
  ssh: "violet",
};

type Kind =
  | "markdown"
  | "notion"
  | "ssh"
  | "google_drive"
  | "onedrive"
  | "dropbox"
  | "http_rag"
  | "mcp";

/** What a pasted MCP config resolves to. Accepts a Claude-Desktop-style
 *  mcpServers block (or a single-server fragment), an mcp-remote command
 *  (flattened to its direct HTTP url + Authorization token), or a bare URL. */
interface ParsedMcp {
  name?: string;
  url?: string;
  token?: string;
  headers?: Record<string, string>;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  error?: string;
}

/** Extract the first {...} object after `"name": {` via brace matching. */
function firstServerFragment(raw: string): { name?: string; json?: string } {
  const m = /"([\w.-]+)"\s*:\s*\{/.exec(raw);
  if (!m) return {};
  const start = raw.indexOf("{", m.index + m[0].length - 1);
  let depth = 0;
  let inStr = false;
  for (let i = start; i < raw.length; i++) {
    const ch = raw[i];
    if (inStr) {
      if (ch === "\\") i++;
      else if (ch === '"') inStr = false;
    } else if (ch === '"') inStr = true;
    else if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return { name: m[1], json: raw.slice(start, i + 1) };
    }
  }
  return {};
}

export function parseMcpPaste(raw: string): ParsedMcp {
  const text = raw.trim();
  if (!text) return {};
  // Bare URL — the simplest paste.
  if (/^https?:\/\/\S+$/i.test(text)) return { url: text };
  let entry: Record<string, unknown> | null = null;
  let name: string | undefined;
  try {
    const parsed = JSON.parse(text) as Record<string, unknown>;
    const servers =
      (parsed.mcpServers as Record<string, unknown> | undefined) ?? parsed;
    if (typeof servers.command === "string" || typeof servers.url === "string") {
      entry = servers; // a single server object was pasted
    } else {
      const key = Object.keys(servers)[0];
      if (key && typeof servers[key] === "object") {
        name = key;
        entry = servers[key] as Record<string, unknown>;
      }
    }
  } catch {
    // Tolerate a fragment like `}, "hermes-brain": { ... }` — find the first
    // named object and brace-match it out.
    const frag = firstServerFragment(text);
    if (frag.json) {
      try {
        entry = JSON.parse(frag.json) as Record<string, unknown>;
        name = frag.name;
      } catch {
        return { error: "Couldn't parse that config — paste the full { … } block for one server." };
      }
    }
  }
  if (!entry) return { error: "No MCP server found in the paste." };
  const out: ParsedMcp = { name };
  const args = Array.isArray(entry.args) ? entry.args.map(String) : [];
  const headers: Record<string, string> = {
    ...((entry.headers as Record<string, string>) ?? {}),
  };
  // mcp-remote is a stdio→HTTP bridge: flatten to the DIRECT HTTP connection
  // (no npx/node needed — the daemon speaks streamable HTTP itself).
  if (args.some((a) => a.includes("mcp-remote")) || typeof entry.url === "string") {
    out.url =
      (typeof entry.url === "string" && entry.url) ||
      args.find((a) => /^https?:\/\//i.test(a));
    for (let i = 0; i < args.length; i++) {
      if (args[i] === "--header" && args[i + 1]) {
        const h = args[i + 1];
        const colon = h.indexOf(":");
        if (colon > 0) headers[h.slice(0, colon).trim()] = h.slice(colon + 1).trim();
      }
    }
  } else if (typeof entry.command === "string") {
    out.command = entry.command;
    out.args = args;
    if (entry.env && typeof entry.env === "object")
      out.env = entry.env as Record<string, string>;
    // A `cmd /c npx …` wrapper is noise the daemon doesn't need on Windows —
    // keep as-is; StdioTransport runs it verbatim.
  }
  // Pull a Bearer token OUT of the headers into the vault-bound field.
  const auth = headers["Authorization"] ?? headers["authorization"];
  if (auth) {
    const mtok = /^Bearer\s+(.+)$/i.exec(auth.trim());
    if (mtok) {
      out.token = mtok[1];
      delete headers["Authorization"];
      delete headers["authorization"];
    }
  }
  if (Object.keys(headers).length) out.headers = headers;
  if (!out.url && !out.command)
    return { error: "That config has neither a URL nor a command." };
  return out;
}

// The three OAuth-backed cloud drives resolve their token from the Connections
// registry, so the add-source form only needs a folder scope for them.
const DRIVE_KINDS: Kind[] = ["google_drive", "onedrive", "dropbox"];
const DRIVE_LABEL: Record<string, string> = {
  google_drive: "Google Drive (memory)",
  onedrive: "OneDrive (memory)",
  dropbox: "Dropbox (memory)",
};

function sourceTone(kind: string): Tone {
  if (kind === "notion") return "slate";
  if (kind === "ssh") return "violet";
  if (kind === "http_rag") return "amber";
  if (DRIVE_KINDS.includes(kind as Kind)) return "violet";
  return "cyan";
}

export function LongTerm() {
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
  // Notion: the integration token pasted inline (write-only -> vault) and, as an
  // advanced escape hatch, the name of an already-stored vault secret.
  const [srcNotionToken, setSrcNotionToken] = useState("");
  const [srcTokenSecret, setSrcTokenSecret] = useState("");
  const [notionAdvanced, setNotionAdvanced] = useState(false);
  // SSH (remote) source fields
  const [srcHost, setSrcHost] = useState("");
  const [srcPort, setSrcPort] = useState("22");
  const [srcUser, setSrcUser] = useState("");
  const [srcPassword, setSrcPassword] = useState("");
  // Offsite RAG (http_rag) fields
  const [srcEndpoint, setSrcEndpoint] = useState("");
  const [srcBearer, setSrcBearer] = useState(""); // write-only bearer token
  // MCP brain: the raw pasted config; parsed live for the preview + submit.
  const [mcpPaste, setMcpPaste] = useState("");
  const mcpParsed = parseMcpPaste(mcpPaste);

  // Browse memories: enumerable items from ONE source (MCP list tool /
  // markdown vault files); search-only sources return an honest note.
  const [browseSource, setBrowseSource] = useState("");
  const [browseItems, setBrowseItems] = useState<
    { title: string; snippet: string; ref: string }[]
  >([]);
  const [browseNote, setBrowseNote] = useState("");
  const [browseBusy, setBrowseBusy] = useState(false);

  async function loadBrowse(src?: string) {
    const name = (src ?? browseSource).trim();
    if (!name) return;
    setBrowseBusy(true);
    setBrowseNote("");
    try {
      const d = await get<{
        items: { title: string; snippet: string; ref: string }[];
        note?: string;
      }>(`/ltm/browse?source=${encodeURIComponent(name)}&limit=30`);
      setBrowseItems(d.items ?? []);
      setBrowseNote(d.note ?? "");
    } catch (e) {
      setBrowseItems([]);
      setBrowseNote(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBrowseBusy(false);
    }
  }

  // Seed the browse source once the active list arrives, and load it.
  useEffect(() => {
    const active = sourcesData?.active ?? [];
    if (!browseSource && active.length > 0) {
      setBrowseSource(active[0]);
      void loadBrowse(active[0]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourcesData?.active]);
  useEffect(() => {
    if (browseSource) void loadBrowse(browseSource);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [browseSource]);
  const [ragAdvanced, setRagAdvanced] = useState(false);
  const [ragQueryField, setRagQueryField] = useState("query");
  const [ragTopKField, setRagTopKField] = useState("k");
  const [ragResultsPath, setRagResultsPath] = useState("");
  const [ragTitleField, setRagTitleField] = useState("");
  const [ragTextField, setRagTextField] = useState("");
  const [ragRefField, setRagRefField] = useState("");
  const [ragAuthScheme, setRagAuthScheme] = useState(""); // "" = auto
  const [ragAuthHeader, setRagAuthHeader] = useState("");
  const [srcBusy, setSrcBusy] = useState(false);
  const [srcError, setSrcError] = useState<string | null>(null);
  const [srcOk, setSrcOk] = useState<string | null>(null);

  // Ingest-document upload
  const fileRef = useRef<HTMLInputElement>(null);
  const [ingestBusy, setIngestBusy] = useState(false);
  const [ingestError, setIngestError] = useState<string | null>(null);
  const [ingestOk, setIngestOk] = useState<string | null>(null);

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
      setSrcError("A local-folder source needs a folder path.");
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
    if (srcKind === "http_rag" && !srcEndpoint.trim()) {
      setSrcError("An offsite RAG source needs an endpoint URL.");
      return;
    }
    if (srcKind === "mcp" && !mcpParsed.url && !mcpParsed.command) {
      setSrcError(
        mcpParsed.error ?? "Paste an MCP config (or a server URL) first.",
      );
      return;
    }
    setSrcBusy(true);
    setSrcError(null);
    setSrcOk(null);
    try {
      // Build only the fields relevant to the chosen kind.
      const isDrive = DRIVE_KINDS.includes(srcKind);
      const body: Record<string, unknown> = {
        name: srcName.trim(),
        kind: srcKind,
      };
      if (srcKind === "markdown" || srcKind === "ssh") body.path = srcPath.trim();
      if (srcKind === "notion") {
        body.database_id = srcDb.trim();
        // The pasted token lands in the encrypted vault server-side; only its
        // generated secret NAME persists on the record (same as ssh/http_rag).
        if (srcNotionToken.trim()) body.token = srcNotionToken.trim();
        // Advanced: reuse an already-stored vault secret by name instead.
        if (srcTokenSecret.trim()) body.token_secret = srcTokenSecret.trim();
      }
      if (srcKind === "ssh") {
        body.host = srcHost.trim();
        body.port = Number(srcPort) || 22;
        body.username = srcUser.trim();
        body.password = srcPassword;
      }
      if (isDrive) {
        // A folder id/path to scope the index to; blank = whole drive. The token
        // is resolved from the Connections registry, not sent here.
        body.path = srcPath.trim();
      }
      if (srcKind === "http_rag") {
        body.endpoint_url = srcEndpoint.trim();
        if (srcBearer.trim()) body.token = srcBearer.trim();
        // Only send non-empty config fields.
        const cfg: Record<string, string> = {};
        if (ragQueryField.trim()) cfg.query_field = ragQueryField.trim();
        if (ragTopKField.trim()) cfg.top_k_field = ragTopKField.trim();
        if (ragResultsPath.trim()) cfg.results_path = ragResultsPath.trim();
        if (ragTitleField.trim()) cfg.title_field = ragTitleField.trim();
        if (ragTextField.trim()) cfg.text_field = ragTextField.trim();
        if (ragRefField.trim()) cfg.ref_field = ragRefField.trim();
        if (ragAuthScheme.trim()) cfg.auth_scheme = ragAuthScheme.trim();
        if (ragAuthHeader.trim()) cfg.auth_header = ragAuthHeader.trim();
        if (Object.keys(cfg).length) body.config = cfg;
      }
      if (srcKind === "mcp") {
        // The token goes to the encrypted vault server-side; everything else
        // (headers/command/args) rides config on the record.
        if (mcpParsed.url) body.endpoint_url = mcpParsed.url;
        if (mcpParsed.token) body.token = mcpParsed.token;
        const cfg: Record<string, unknown> = {};
        if (mcpParsed.headers && Object.keys(mcpParsed.headers).length)
          cfg.headers = mcpParsed.headers;
        if (mcpParsed.command) {
          cfg.command = mcpParsed.command;
          cfg.args = mcpParsed.args ?? [];
          if (mcpParsed.env) cfg.env = mcpParsed.env;
        }
        if (Object.keys(cfg).length) body.config = cfg;
      }
      await post("/ltm/sources", body);
      setSrcOk(`Source "${srcName.trim()}" added.`);
      setSrcName("");
      setSrcPath("");
      setSrcDb("");
      setSrcNotionToken("");
      setSrcTokenSecret("");
      setNotionAdvanced(false);
      setSrcHost("");
      setSrcPort("22");
      setSrcUser("");
      setSrcPassword("");
      setSrcEndpoint("");
      setSrcBearer("");
      setRagAdvanced(false);
      setRagQueryField("query");
      setRagTopKField("k");
      setRagResultsPath("");
      setRagTitleField("");
      setRagTextField("");
      setRagRefField("");
      setRagAuthScheme("");
      setRagAuthHeader("");
      setSrcKind("markdown");
      reloadSources();
    } catch (err) {
      setSrcError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSrcBusy(false);
    }
  }

  async function ingestFile(file: File) {
    setIngestBusy(true);
    setIngestError(null);
    setIngestOk(null);
    try {
      const content_b64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error("Could not read the file."));
        reader.onload = () => {
          const res = String(reader.result || "");
          // Strip the "data:<mime>;base64," prefix from the data URL.
          const comma = res.indexOf(",");
          resolve(comma >= 0 ? res.slice(comma + 1) : res);
        };
        reader.readAsDataURL(file);
      });
      const res = await post<{ ref: string; source: string; title: string; chars: number }>(
        "/ltm/ingest-document",
        { filename: file.name, content_b64 },
      );
      setIngestOk(
        `Added "${res.title}" to memory (${res.chars.toLocaleString()} chars)`,
      );
      reloadSources();
    } catch (err) {
      setIngestError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setIngestBusy(false);
    }
  }

  function onPickFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    // Reset the input so re-picking the same file fires onChange again.
    e.target.value = "";
    if (file) void ingestFile(file);
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
        <Card title="Browse memories" icon={<Database size={15} />}>
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label="Browse source"
              value={browseSource}
              onChange={(e) => setBrowseSource(e.target.value)}
              className="field w-auto py-1.5 text-[13px]"
            >
              {(sourcesData?.active ?? []).map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => void loadBrowse()}
              disabled={browseBusy || !browseSource}
              className="btn-ghost py-1.5 text-[13px]"
            >
              {browseBusy ? <LoaderInline label="Loading…" /> : "Refresh"}
            </button>
          </div>
          {browseNote && (
            <p className="mt-2 text-[12px] leading-relaxed text-zinc-500">
              {browseNote}
            </p>
          )}
          {browseItems.length > 0 && (
            <div className="mt-3 space-y-1.5">
              {browseItems.map((it, i) => (
                <div
                  key={`${it.ref}-${i}`}
                  className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2"
                >
                  <div className="text-[13px] font-medium text-zinc-200">
                    {it.title}
                  </div>
                  {it.snippet && (
                    <p className="mt-0.5 line-clamp-2 text-[11.5px] leading-relaxed text-zinc-500">
                      {it.snippet}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
          {!browseBusy && !browseNote && browseItems.length === 0 && (
            <p className="mt-2 text-[12px] text-zinc-600">
              Nothing here yet — memories appear as they&apos;re appended.
            </p>
          )}
        </Card>
      </Reveal>

      <Reveal>
        <Card title="Ingest document" icon={<Upload size={15} />}>
          <div className="flex flex-wrap items-center gap-4">
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.docx,.pptx,.xlsx,.html,.txt,.md"
              onChange={onPickFile}
              className="hidden"
            />
            <p className="min-w-[240px] flex-1 text-sm text-zinc-400">
              Converts PDFs / office docs (Word, PowerPoint, Excel, HTML) to
              Markdown and stores them as searchable memory.
            </p>
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              disabled={ingestBusy}
              className="btn-accent shrink-0"
            >
              {ingestBusy ? (
                <LoaderInline label="Ingesting…" />
              ) : (
                <>
                  <Upload size={14} /> Choose a document
                </>
              )}
            </button>
          </div>
          {ingestOk && (
            <div className="mt-3">
              <SuccessNote>{ingestOk}</SuccessNote>
            </div>
          )}
          {ingestError && (
            <div className="mt-3">
              <ErrorNote>{ingestError}</ErrorNote>
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
        <div className="space-y-3">
          <p className="px-1 text-sm text-zinc-400">
            Where long-term memory lives: the built-in local brain folder, plus
            any sources you add — a local folder or Obsidian vault, Notion, a
            cloud drive, SSH, or your own RAG endpoint.
          </p>
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
                      <option value="http_rag">Offsite RAG endpoint</option>
                      <option value="mcp">MCP brain (paste config)</option>
                      <option value="markdown">Local folder / Obsidian vault</option>
                      <option value="ssh">Remote folder (SSH)</option>
                      <option value="notion">Notion database</option>
                      <option value="google_drive">Google Drive (memory)</option>
                      <option value="onedrive">OneDrive (memory)</option>
                      <option value="dropbox">Dropbox (memory)</option>
                    </select>
                  </div>

                  {srcKind === "mcp" && (
                    <div>
                      <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                        MCP config
                      </label>
                      <textarea
                        value={mcpPaste}
                        onChange={(e) => {
                          setMcpPaste(e.target.value);
                          const p = parseMcpPaste(e.target.value);
                          if (p.name && !srcName.trim()) setSrcName(p.name);
                        }}
                        rows={6}
                        spellCheck={false}
                        placeholder={
                          'Paste the server\'s config block (Claude Desktop style), e.g.\n"my-brain": {\n  "command": "npx",\n  "args": ["-y", "mcp-remote", "http://host:8098/mcp", "--header", "Authorization: Bearer …"]\n}\n— or just the server URL.'
                        }
                        className="field resize-y font-mono text-[11px]"
                      />
                      {mcpPaste.trim() &&
                        (mcpParsed.error ? (
                          <p className="mt-1.5 text-[11px] leading-relaxed text-amber-300/90">
                            {mcpParsed.error}
                          </p>
                        ) : (
                          <p className="mt-1.5 text-[11px] leading-relaxed text-emerald-300/80">
                            {mcpParsed.url
                              ? `→ direct HTTP connection to ${mcpParsed.url}`
                              : `→ command: ${mcpParsed.command} ${(mcpParsed.args ?? []).slice(0, 3).join(" ")}…`}
                            {mcpParsed.token &&
                              " · Bearer token detected — it will be stored in the encrypted vault"}
                          </p>
                        ))}
                      <p className="mt-1 text-[10px] leading-relaxed text-zinc-600">
                        mcp-remote wrappers are flattened to their direct HTTP
                        connection — no npx needed. The server&apos;s
                        search/append tools are discovered automatically, and
                        this brain answers memory recalls alongside your other
                        sources.
                      </p>
                    </div>
                  )}
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
                        Any folder of .md files — point it at an Obsidian vault to
                        use it as Iron Jarvis&apos;s brain.
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
                  ) : srcKind === "notion" ? (
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
                          Integration token
                        </label>
                        <input
                          type="password"
                          value={srcNotionToken}
                          onChange={(e) => setSrcNotionToken(e.target.value)}
                          placeholder="ntn_… — write-only, stored encrypted"
                          autoComplete="off"
                          className="field"
                        />
                        <div className="mt-1 text-[11px] text-zinc-600">
                          Paste your Notion integration token — it&apos;s stored
                          encrypted in the vault, never shown again.
                        </div>
                      </div>
                      <div className="rounded-xl border border-white/[0.05] bg-white/[0.02]">
                        <button
                          type="button"
                          onClick={() => setNotionAdvanced((v) => !v)}
                          className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-[11px] uppercase tracking-[0.1em] text-zinc-400 transition-colors hover:text-accent-soft"
                        >
                          {notionAdvanced ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                          Advanced · reuse a stored secret
                        </button>
                        {notionAdvanced && (
                          <div className="border-t hairline px-3 py-3">
                            <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                              Vault secret name
                              <span className="ml-1.5 normal-case tracking-normal text-zinc-600">(optional)</span>
                            </label>
                            <input
                              value={srcTokenSecret}
                              onChange={(e) => setSrcTokenSecret(e.target.value)}
                              placeholder="name of an existing vault secret"
                              className="field"
                            />
                            <div className="mt-1 text-[11px] text-zinc-600">
                              Instead of pasting a token, reference a Notion token
                              already stored on the Secrets page.
                            </div>
                          </div>
                        )}
                      </div>
                    </>
                  ) : DRIVE_KINDS.includes(srcKind) ? (
                    <>
                      <div>
                        <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                          Folder to index
                        </label>
                        <input
                          value={srcPath}
                          onChange={(e) => setSrcPath(e.target.value)}
                          placeholder="folder id or path (blank = whole drive)"
                          className="field font-mono"
                        />
                        <div className="mt-1 text-[11px] text-zinc-600">
                          Folder to index — blank = whole drive.
                        </div>
                      </div>
                      <div className="flex items-start gap-2 rounded-xl border border-accent/20 bg-accent/[0.06] px-3 py-2.5">
                        <Cloud size={14} className="mt-0.5 shrink-0 text-accent-soft/80" />
                        <div className="text-[11px] leading-relaxed text-zinc-400">
                          Connect {DRIVE_LABEL[srcKind]?.replace(" (memory)", "")} on the{" "}
                          <span className="font-semibold text-accent-soft">Connections</span> page first —
                          this source resolves its access token from that OAuth connection.
                        </div>
                      </div>
                    </>
                  ) : (
                    <>
                      <div>
                        <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                          Endpoint URL
                        </label>
                        <div className="relative">
                          <Globe
                            size={14}
                            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-600"
                          />
                          <input
                            value={srcEndpoint}
                            onChange={(e) => setSrcEndpoint(e.target.value)}
                            placeholder="https://my-rag.example.com/search"
                            className="field pl-9 font-mono"
                          />
                        </div>
                        <div className="mt-1 text-[11px] text-zinc-600">
                          Point Iron Jarvis at your own RAG service — it POSTs the query and reads back matches.
                        </div>
                      </div>
                      <div>
                        <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                          Bearer token
                          <span className="ml-1.5 normal-case tracking-normal text-zinc-600">(optional)</span>
                        </label>
                        <input
                          type="password"
                          value={srcBearer}
                          onChange={(e) => setSrcBearer(e.target.value)}
                          placeholder="write-only — stored encrypted"
                          autoComplete="off"
                          className="field"
                        />
                      </div>

                      <div className="rounded-xl border border-white/[0.05] bg-white/[0.02]">
                        <button
                          type="button"
                          onClick={() => setRagAdvanced((v) => !v)}
                          className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-[11px] uppercase tracking-[0.1em] text-zinc-400 transition-colors hover:text-accent-soft"
                        >
                          {ragAdvanced ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                          Advanced · request/response mapping
                        </button>
                        {ragAdvanced && (
                          <div className="space-y-3 border-t hairline px-3 py-3">
                            <div className="grid grid-cols-2 gap-2">
                              <div>
                                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                                  Query field
                                </label>
                                <input
                                  value={ragQueryField}
                                  onChange={(e) => setRagQueryField(e.target.value)}
                                  placeholder="query"
                                  className="field font-mono"
                                />
                              </div>
                              <div>
                                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                                  Top-k field
                                </label>
                                <input
                                  value={ragTopKField}
                                  onChange={(e) => setRagTopKField(e.target.value)}
                                  placeholder="k"
                                  className="field font-mono"
                                />
                              </div>
                            </div>
                            <div>
                              <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                                Results path
                              </label>
                              <input
                                value={ragResultsPath}
                                onChange={(e) => setRagResultsPath(e.target.value)}
                                placeholder="results / documents / data / matches"
                                className="field font-mono"
                              />
                              <div className="mt-1 flex items-start gap-1 text-[11px] text-zinc-600">
                                <Info size={12} className="mt-0.5 shrink-0" />
                                Leave blank to auto-detect where the matches live in the response.
                              </div>
                            </div>
                            <div className="grid grid-cols-3 gap-2">
                              <div>
                                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                                  Title field
                                </label>
                                <input
                                  value={ragTitleField}
                                  onChange={(e) => setRagTitleField(e.target.value)}
                                  placeholder="title"
                                  className="field font-mono"
                                />
                              </div>
                              <div>
                                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                                  Text field
                                </label>
                                <input
                                  value={ragTextField}
                                  onChange={(e) => setRagTextField(e.target.value)}
                                  placeholder="text"
                                  className="field font-mono"
                                />
                              </div>
                              <div>
                                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                                  Ref field
                                </label>
                                <input
                                  value={ragRefField}
                                  onChange={(e) => setRagRefField(e.target.value)}
                                  placeholder="id"
                                  className="field font-mono"
                                />
                              </div>
                            </div>
                            <div className="grid grid-cols-2 gap-2">
                              <div>
                                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                                  Auth scheme
                                </label>
                                <select
                                  aria-label="Auth scheme"
                                  value={ragAuthScheme}
                                  onChange={(e) => setRagAuthScheme(e.target.value)}
                                  className="field"
                                >
                                  <option value="">Auto</option>
                                  <option value="bearer">bearer</option>
                                  <option value="header">header</option>
                                  <option value="none">none</option>
                                </select>
                              </div>
                              <div>
                                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                                  Auth header
                                </label>
                                <input
                                  value={ragAuthHeader}
                                  onChange={(e) => setRagAuthHeader(e.target.value)}
                                  placeholder="X-Api-Key"
                                  className="field font-mono"
                                />
                              </div>
                            </div>
                          </div>
                        )}
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
                    No custom sources yet — add a local folder, Obsidian vault, or
                    Notion database on the left.
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
                            <Badge value={s.kind} tone={sourceTone(s.kind)} />
                          </div>
                          <div className="mt-0.5 truncate font-mono text-[11px] text-zinc-500">
                            {s.kind === "notion"
                              ? s.database_id || "—"
                              : s.kind === "ssh"
                                ? `${s.username ? s.username + "@" : ""}${s.host || "—"}:${s.path || ""}`
                                : s.kind === "http_rag"
                                  ? String(s.endpoint_url || "—")
                                  : DRIVE_KINDS.includes(s.kind as Kind)
                                    ? s.path
                                      ? String(s.path)
                                      : "whole drive"
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
        </div>
      </Reveal>

      {/* Folder browser modal — pick a local notes folder off the machine ---- */}
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
    </>
  );
}
