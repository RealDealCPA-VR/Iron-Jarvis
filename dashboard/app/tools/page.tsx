"use client";

import { useRef, useState, type ReactNode } from "react";
import {
  Wrench,
  Plus,
  Terminal,
  Braces,
  Clock,
  User,
  X,
  Info,
  Boxes,
  Check,
  Globe,
  Radio,
  Search,
  FolderOpen,
  FileText,
  HardDrive,
  GitBranch,
  FileArchive,
  ExternalLink,
  Sparkles,
  Plug,
  Server,
  Lightbulb,
  ChevronRight,
} from "lucide-react";
import { post, del, ApiError } from "@/lib/api";
import { useApi, usePolledApi } from "@/lib/useApi";
import {
  Card,
  Badge,
  OfflineHint,
  Empty,
  SkeletonRows,
  ErrorNote,
  SuccessNote,
  LoaderInline,
  ConfirmButton,
  SectionLabel,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { timeAgo } from "@/lib/format";

/* -------------------------------------------------------------------------- */
/*  Types (local — lib/types.ts is intentionally untouched)                    */
/* -------------------------------------------------------------------------- */

type ParamType = "string" | "integer" | "number" | "boolean";

const PARAM_TYPES: ParamType[] = ["string", "integer", "number", "boolean"];

/** A typed parameter that fills a {placeholder} in the command template. */
interface ToolParam {
  name: string;
  type: ParamType;
  required: boolean;
  description: string;
}

/** A custom (agent/user-authored) reusable tool, as returned by the daemon. */
interface CustomTool {
  name: string;
  description: string;
  parameters: ToolParam[];
  command: string[];
  timeout_seconds: number;
  created_by: string;
  created_at: string;
}

/** The spec the LLM designer registered, as echoed by /tools/custom/generate. */
interface GeneratedSpec {
  name: string;
  description: string;
  parameters: ToolParam[];
  command: string[];
  timeout_seconds: number;
}

/** Response of POST /tools/custom/generate. */
interface GeneratedTool {
  name: string;
  spec: GeneratedSpec;
  reply: string;
}

/** A parameter row in the create form (carries a stable key id). */
interface ParamRow extends ToolParam {
  id: number;
}

/** Split a space-separated argv string into a clean string[] (drops empties). */
function tokenize(command: string): string[] {
  return command
    .split(/\s+/)
    .map((t) => t.trim())
    .filter(Boolean);
}

/* -------------------------------------------------------------------------- */
/*  MCP types + helpers                                                        */
/* -------------------------------------------------------------------------- */

/** One entry of the curated GET /mcp/catalog. Args may hold "<placeholders>". */
interface McpCatalogEntry {
  id: string;
  name: string;
  description: string;
  command: string;
  args: string[];
  env_keys?: string[];
  /** "reference" (official) | "integration" — groups the cards. */
  category?: string;
  /** Prerequisite runtime, plain language: "Node" | "Python (uv)". */
  needs?: string;
}

/**
 * Turn a pasted npm package or GitHub repo into a ready MCP launch command
 * — DETERMINISTIC, no code runs here. The exact command is shown to the user,
 * who reviews (and can edit) it before Connect actually starts the server.
 *   - "@scope/pkg" or "pkg"            -> npx -y <pkg>
 *   - "github.com/owner/repo" / URL    -> npx -y github:owner/repo
 *   - "owner/repo"                     -> npx -y github:owner/repo
 * Returns null when the input does not look like either.
 */
function buildFromSource(
  raw: string,
): { name: string; command: string; args: string[]; github: boolean } | null {
  const s = raw.trim().replace(/\s+/g, "");
  if (!s) return null;
  // A GitHub URL or bare owner/repo (but NOT an npm scope like @scope/pkg).
  const gh = s.match(
    /^(?:https?:\/\/)?(?:www\.)?github\.com\/([^/]+)\/([^/#?]+)|^([^@/][^/]*)\/([^/@]+)$/,
  );
  if (gh) {
    const owner = gh[1] ?? gh[3];
    const repo = (gh[2] ?? gh[4]).replace(/\.git$/, "");
    if (owner && repo) {
      return {
        name: repo,
        command: "npx",
        args: ["-y", `github:${owner}/${repo}`],
        github: true,
      };
    }
  }
  // An npm package spec: "@scope/name", "@scope/name@1.2.3", or "name".
  if (/^@?[\w.-]+(?:\/[\w.-]+)?(?:@[\w.-]+)?$/.test(s)) {
    const bare = s.replace(/^@[^/]+\//, "").replace(/@[\w.-]+$/, "");
    const name = (bare || s).replace(/[^\w.-]/g, "-");
    return { name, command: "npx", args: ["-y", s], github: false };
  }
  return null;
}

/** A configured MCP server, as returned by GET /mcp/servers. Daemons ≤1.88.0
 *  omit `env`/`args` on rows saved by the marketplace connect flow, so both
 *  stay optional here and every use is guarded — a missing env must never
 *  crash the page (it did, on real installs). */
interface McpServer {
  name: string;
  command: string;
  args?: string[];
  env?: Record<string, string>;
  /** How many of this server's tools are live in the registry (0 = not loaded). */
  tools_loaded: number;
  /** Short names of the loaded tools, e.g. ["send_email","list_messages"]. */
  tool_names: string[];
  /** When true, agents may run this server's tools without a prompt. */
  auto_approve?: boolean;
}

/** Response of POST /mcp/servers. `note` is set when live-load failed. */
interface McpAddResult {
  name: string;
  added: boolean;
  tools_loaded: number;
  auto_approve: boolean;
  note: string | null;
}

/** Response of POST /mcp/servers/{name}/test — a live, read-only tool listing. */
interface McpTestResult {
  ok: boolean;
  count: number;
  tools: string[];
  error: string | null;
}

/** Response payload of POST /mcp/suggest. `args`/`env` are model-derived and
 *  may be absent — consumers guard. */
interface McpSuggestion {
  name: string;
  command: string;
  args?: string[];
  env?: Record<string, string>;
  reply: string;
}

const MCP_PLACEHOLDER_RE = /<[^>]+>/g;

/** Distinct "<placeholder>" tokens appearing in a catalog entry's args. */
function placeholdersOf(args: string[]): string[] {
  return Array.from(new Set(args.flatMap((a) => a.match(MCP_PLACEHOLDER_RE) ?? [])));
}

/** Fill "<placeholder>" tokens from user-supplied values (untouched if empty). */
function substituteArgs(args: string[], values: Record<string, string>): string[] {
  return args.map((a) =>
    a.replace(MCP_PLACEHOLDER_RE, (m) => (values[m] ?? "").trim() || m),
  );
}

/* -------------------------------------------------------------------------- */
/*  Small shared renderers                                                     */
/* -------------------------------------------------------------------------- */

/** Argv (or command+args) rendered as chips; {param} / <placeholder> pop. */
function ArgvChips({ argv }: { argv: string[] }) {
  return (
    <div className="flex flex-wrap gap-1">
      {argv.map((tok, i) => {
        const isPh = /^\{.+\}$/.test(tok) || /^<.+>$/.test(tok);
        return (
          <span
            key={i}
            className={`rounded-md border px-1.5 py-0.5 font-mono text-[11px] ${
              isPh
                ? "border-accent/30 bg-accent/[0.08] text-accent-soft"
                : "border-white/[0.06] bg-white/[0.03] text-zinc-300"
            }`}
          >
            {tok}
          </span>
        );
      })}
    </div>
  );
}

/** Typed-parameter chips (name, required marker, type). */
function ParamChips({ params }: { params: ToolParam[] }) {
  if (params.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {params.map((p) => (
        <span
          key={p.name}
          title={p.description || undefined}
          className="inline-flex items-center gap-1 rounded-md border border-white/[0.07] bg-white/[0.03] px-1.5 py-0.5 font-mono text-[11px] text-zinc-300"
        >
          {p.name}
          {p.required && (
            <span className="text-rose-300" title="required">
              *
            </span>
          )}
          <span className="text-zinc-600">{p.type}</span>
        </span>
      ))}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Tool suite — curated, one-click, Windows-friendly prebuilt tools           */
/* -------------------------------------------------------------------------- */

/** A ready-made tool template posted verbatim to POST /tools/custom. */
interface SuiteTool {
  name: string;
  description: string;
  /** Plain-language use-case shown as the card TITLE (e.g. "Fetch a web page").
   *  The technical `name` is what the daemon registers + agents call it by. */
  title: string;
  /** One-line "what it does for you", shown under the title (UI only —
   *  `description` is what actually gets registered with the daemon). */
  blurb: string;
  parameters: ToolParam[];
  command: string[];
  timeout_seconds: number;
  icon: ReactNode;
}

/** Shorthand for a required string parameter (the only kind the suite needs). */
function strParam(name: string, description: string): ToolParam {
  return { name, type: "string", required: true, description };
}

/**
 * The curated gallery. Commands are argv (no shell), so {placeholder} tokens —
 * which the daemon fills from the declared parameters — are injection-safe.
 * Everything here targets Windows (PowerShell / cmd built-ins).
 */
const TOOL_SUITE: SuiteTool[] = [
  {
    name: "http_get",
    title: "Fetch a web page",
    description: "Fetch a URL and print the response.",
    blurb: "Grab the contents of any web page or API link.",
    parameters: [strParam("url", "The URL to fetch.")],
    command: ["curl", "-s", "{url}"],
    timeout_seconds: 30,
    icon: <Globe size={16} className="text-accent-soft" />,
  },
  {
    name: "ping_host",
    title: "Check if a site is reachable",
    description: "Ping a host 4 times.",
    blurb: "See whether a website or machine is up and responding.",
    parameters: [strParam("host", "Hostname or IP address to ping.")],
    command: ["ping", "-n", "4", "{host}"],
    timeout_seconds: 30,
    icon: <Radio size={16} className="text-accent-soft" />,
  },
  {
    name: "dns_lookup",
    title: "Look up a site's address",
    description: "DNS lookup for a hostname.",
    blurb: "Find the internet (IP) address behind a website name.",
    parameters: [strParam("host", "Hostname to resolve.")],
    command: ["nslookup", "{host}"],
    timeout_seconds: 20,
    icon: <Search size={16} className="text-accent-soft" />,
  },
  {
    name: "list_dir",
    title: "List what's in a folder",
    description: "List a directory.",
    blurb: "See the files and folders inside any folder on your computer.",
    parameters: [strParam("path", "Directory path to list.")],
    command: ["powershell", "-NoProfile", "-Command", "Get-ChildItem -Force '{path}'"],
    timeout_seconds: 20,
    icon: <FolderOpen size={16} className="text-accent-soft" />,
  },
  {
    name: "word_count",
    title: "Count words in a document",
    description: "Count words in a text file.",
    blurb: "Count how many words are in a text document.",
    parameters: [strParam("file", "Path to the text file.")],
    command: [
      "powershell",
      "-NoProfile",
      "-Command",
      "(Get-Content '{file}' -Raw | Measure-Object -Word).Words",
    ],
    timeout_seconds: 30,
    icon: <FileText size={16} className="text-accent-soft" />,
  },
  {
    name: "disk_free",
    title: "Check free disk space",
    description: "Show free disk space.",
    blurb: "See how much storage space you have left on each drive.",
    parameters: [],
    command: [
      "powershell",
      "-NoProfile",
      "-Command",
      "Get-PSDrive -PSProvider FileSystem | Select-Object Name,Used,Free",
    ],
    timeout_seconds: 20,
    icon: <HardDrive size={16} className="text-accent-soft" />,
  },
  {
    name: "git_status",
    title: "See what changed in a project",
    description: "git status of a repo.",
    blurb: "See what's changed in a code project since the last save point.",
    parameters: [strParam("repo", "Path to the git repository.")],
    command: ["git", "-C", "{repo}", "status", "--short"],
    timeout_seconds: 30,
    icon: <GitBranch size={16} className="text-accent-soft" />,
  },
  {
    name: "zip_folder",
    title: "Zip up a folder",
    description: "Zip a folder.",
    blurb: "Bundle a folder into a single .zip file, ready to share.",
    parameters: [
      strParam("source", "Folder (or path) to compress."),
      strParam("dest", "Destination .zip path."),
    ],
    command: [
      "powershell",
      "-NoProfile",
      "-Command",
      "Compress-Archive -Path '{source}' -DestinationPath '{dest}' -Force",
    ],
    timeout_seconds: 120,
    icon: <FileArchive size={16} className="text-accent-soft" />,
  },
  {
    name: "open_url",
    title: "Open a link in the browser",
    description: "Open a URL in the default browser.",
    blurb: "Pop a link open in your default browser.",
    parameters: [strParam("url", "The URL to open.")],
    command: ["powershell", "-NoProfile", "-Command", "Start-Process '{url}'"],
    timeout_seconds: 15,
    icon: <ExternalLink size={16} className="text-accent-soft" />,
  },
];

export default function ToolsPage() {
  const { data, error, loading, reload } = usePolledApi<{ tools: CustomTool[] }>(
    "/tools/custom",
    8000,
  );
  const offline = error && error.status === 0;
  const tools = data?.tools ?? [];

  // Describe-a-tool (LLM designer) -----------------------------------------
  const [genDesc, setGenDesc] = useState("");
  const [genBusy, setGenBusy] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [genResult, setGenResult] = useState<GeneratedTool | null>(null);

  async function generate(e: React.FormEvent) {
    e.preventDefault();
    const description = genDesc.trim();
    if (!description || genBusy) return;
    setGenBusy(true);
    setGenError(null);
    setGenResult(null);
    try {
      const res = await post<GeneratedTool>("/tools/custom/generate", { description });
      setGenResult(res);
      setGenDesc("");
      reload();
    } catch (err) {
      // 409 (name collision) / 422 (bad spec) carry a `detail` the api client
      // already surfaces as the message.
      setGenError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setGenBusy(false);
    }
  }

  // Manual create form ------------------------------------------------------
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [timeout, setTimeoutSecs] = useState("60");
  const [command, setCommand] = useState("");
  const [rows, setRows] = useState<ParamRow[]>([]);
  const nextId = useRef(1);

  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const argv = tokenize(command);

  function addRow() {
    setRows((r) => [
      ...r,
      { id: nextId.current++, name: "", type: "string", required: false, description: "" },
    ]);
  }
  function updateRow(id: number, patch: Partial<ParamRow>) {
    setRows((r) => r.map((x) => (x.id === id ? { ...x, ...patch } : x)));
  }
  function removeRow(id: number) {
    setRows((r) => r.filter((x) => x.id !== id));
  }

  function resetForm() {
    setName("");
    setDescription("");
    setTimeoutSecs("60");
    setCommand("");
    setRows([]);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || argv.length === 0) return;
    setBusy(true);
    setFormError(null);
    setOk(null);

    const parameters: ToolParam[] = rows
      .filter((r) => r.name.trim())
      .map((r) => ({
        name: r.name.trim(),
        type: r.type,
        required: r.required,
        description: r.description.trim(),
      }));

    const body = {
      name: name.trim(),
      description: description.trim(),
      parameters,
      command: argv,
      timeout_seconds: Number(timeout) || 60,
    };

    try {
      await post<{ name: string }>("/tools/custom", body);
      setOk(`Tool "${name.trim()}" created.`);
      resetForm();
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : String(err));
    }
    setBusy(false);
  }

  async function remove(toolName: string) {
    setOk(null);
    setFormError(null);
    try {
      await del(`/tools/custom/${encodeURIComponent(toolName)}`);
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : String(err));
    }
  }

  // Tool suite (one-click add) --------------------------------------------
  const installed = new Set(tools.map((t) => t.name));
  const [adding, setAdding] = useState<string | null>(null);
  const [suiteError, setSuiteError] = useState<string | null>(null);
  const [suiteOk, setSuiteOk] = useState<string | null>(null);

  async function addFromSuite(t: SuiteTool) {
    setAdding(t.name);
    setSuiteError(null);
    setSuiteOk(null);
    try {
      await post<{ name: string }>("/tools/custom", {
        name: t.name,
        description: t.description,
        parameters: t.parameters,
        command: t.command,
        timeout_seconds: t.timeout_seconds,
      });
      setSuiteOk(`Tool "${t.name}" added.`);
      reload();
    } catch (err) {
      setSuiteError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setAdding(null);
    }
  }

  // MCP servers -------------------------------------------------------------
  const {
    data: mcpData,
    loading: mcpLoading,
    reload: reloadServers,
  } = usePolledApi<{ servers: McpServer[] }>("/mcp/servers", 10000);
  const servers = mcpData?.servers ?? [];
  const serverNames = new Set(servers.map((s) => s.name));

  const { data: catData, error: catError } = useApi<{ catalog: McpCatalogEntry[] }>(
    "/mcp/catalog",
  );
  const catalog = catData?.catalog ?? [];

  const [mcpBusy, setMcpBusy] = useState<string | null>(null);
  const [mcpError, setMcpError] = useState<string | null>(null);
  const [mcpOk, setMcpOk] = useState<string | null>(null);

  // Auto-approve: applies to whatever pack you connect next (default OFF).
  const [autoApprove, setAutoApprove] = useState(false);

  // Per-server "Test" (live tool listing) — busy name + results keyed by name.
  const [mcpTestBusy, setMcpTestBusy] = useState<string | null>(null);
  const [mcpTests, setMcpTests] = useState<Record<string, McpTestResult>>({});

  /** POST /mcp/servers/{name}/test — connect now, list tools, show inline. */
  async function testServer(serverName: string) {
    setMcpTestBusy(serverName);
    setMcpTests((t) => {
      const next = { ...t };
      delete next[serverName];
      return next;
    });
    try {
      const res = await post<McpTestResult>(
        `/mcp/servers/${encodeURIComponent(serverName)}/test`,
      );
      setMcpTests((t) => ({ ...t, [serverName]: res }));
    } catch (err) {
      setMcpTests((t) => ({
        ...t,
        [serverName]: {
          ok: false,
          count: 0,
          tools: [],
          error: err instanceof ApiError ? err.message : String(err),
        },
      }));
    } finally {
      setMcpTestBusy(null);
    }
  }

  // Which catalog entry has its config (placeholders / env keys) form open.
  const [cfgId, setCfgId] = useState<string | null>(null);
  const [cfgValues, setCfgValues] = useState<Record<string, string>>({});
  const [cfgEnv, setCfgEnv] = useState<Record<string, string>>({});

  function toggleConfig(entry: McpCatalogEntry) {
    if (cfgId === entry.id) {
      setCfgId(null);
      return;
    }
    setCfgId(entry.id);
    setCfgValues({});
    setCfgEnv({});
  }

  /** POST /mcp/servers and report tools_loaded / the restart note honestly. */
  async function addMcpServer(
    serverName: string,
    cmd: string,
    args: string[],
    env: Record<string, string>,
    busyKey: string,
  ): Promise<boolean> {
    setMcpBusy(busyKey);
    setMcpError(null);
    setMcpOk(null);
    try {
      const res = await post<McpAddResult>("/mcp/servers", {
        name: serverName,
        command: cmd,
        args,
        env,
        auto_approve: autoApprove,
      });
      const approveNote = res.auto_approve
        ? " Agents may use it without asking after the next restart."
        : "";
      setMcpOk(
        res.note
          ? `Tool pack "${serverName}" connected — ${res.note}${approveNote}`
          : `Tool pack "${serverName}" connected — ${res.tools_loaded} tool${
              res.tools_loaded === 1 ? "" : "s"
            } ready to use.${approveNote}`,
      );
      setCfgId(null);
      reloadServers();
      return true;
    } catch (err) {
      setMcpError(err instanceof ApiError ? err.message : String(err));
      return false;
    } finally {
      setMcpBusy(null);
    }
  }

  async function removeServer(serverName: string) {
    setMcpError(null);
    setMcpOk(null);
    try {
      await del(`/mcp/servers/${encodeURIComponent(serverName)}`);
      setMcpOk(
        `Tool pack "${serverName}" disconnected. Restart Iron Jarvis to fully unload its tools.`,
      );
      reloadServers();
    } catch (err) {
      setMcpError(err instanceof ApiError ? err.message : String(err));
    }
  }

  // MCP suggest ("describe what you want to connect") -----------------------
  const [sugDesc, setSugDesc] = useState("");
  const [sugBusy, setSugBusy] = useState(false);
  const [sugError, setSugError] = useState<string | null>(null);
  const [suggestion, setSuggestion] = useState<McpSuggestion | null>(null);

  // Add-from-source (paste an npm package or GitHub repo) --------------------
  const [srcInput, setSrcInput] = useState("");
  const [srcName, setSrcName] = useState("");
  const [srcEnvText, setSrcEnvText] = useState("");
  const srcBuilt = buildFromSource(srcInput);

  /** Parse "KEY=value" lines into an env dict (blank/comment lines ignored). */
  function parseEnv(text: string): Record<string, string> {
    const env: Record<string, string> = {};
    for (const line of text.split("\n")) {
      const t = line.trim();
      if (!t || t.startsWith("#")) continue;
      const eq = t.indexOf("=");
      if (eq <= 0) continue;
      env[t.slice(0, eq).trim()] = t.slice(eq + 1).trim();
    }
    return env;
  }

  async function addFromSource() {
    if (!srcBuilt) return;
    const name = (srcName.trim() || srcBuilt.name).replace(/[^\w.-]/g, "-");
    const okAdd = await addMcpServer(
      name,
      srcBuilt.command,
      srcBuilt.args,
      parseEnv(srcEnvText),
      `src:${name}`,
    );
    if (okAdd) {
      setSrcInput("");
      setSrcName("");
      setSrcEnvText("");
    }
  }

  /** One curated MCP catalog card (extracted so the two category groups share it). */
  function catalogCard(entry: McpCatalogEntry) {
    const connected = serverNames.has(entry.id);
    const phs = placeholdersOf(entry.args);
    const envKeys = entry.env_keys ?? [];
    const needsConfig = phs.length > 0 || envKeys.length > 0;
    const open = cfgId === entry.id;
    const isBusy = mcpBusy === entry.id;
    const canConnect =
      phs.every((ph) => (cfgValues[ph] ?? "").trim().length > 0) &&
      envKeys.every((k) => (cfgEnv[k] ?? "").trim().length > 0);
    return (
      <div
        key={entry.id}
        className="flex flex-col rounded-xl border border-white/[0.06] bg-white/[0.015] p-4 transition-colors hover:border-white/10 hover:bg-white/[0.03]"
      >
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <Plug size={14} className="text-accent-soft" />
            <span className="truncate text-sm font-semibold text-zinc-100">
              {entry.name}
            </span>
          </div>
          {connected ? (
            <span className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-emerald-500/30 bg-emerald-500/[0.1] px-2 py-1 text-[11px] font-medium text-emerald-300">
              <Check size={12} /> Added
            </span>
          ) : (
            <button
              type="button"
              onClick={() =>
                needsConfig
                  ? toggleConfig(entry)
                  : void addMcpServer(entry.id, entry.command, entry.args, {}, entry.id)
              }
              disabled={isBusy}
              title={`Add "${entry.name}"`}
              className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-accent/30 bg-accent/[0.08] px-2 py-1 text-[11px] font-medium text-accent-soft transition-colors hover:bg-accent/[0.14] disabled:opacity-50"
            >
              {isBusy ? (
                <LoaderInline label="Adding…" />
              ) : open ? (
                <>
                  <X size={12} /> Close
                </>
              ) : (
                <>
                  <Plus size={12} /> Add
                </>
              )}
            </button>
          )}
        </div>

        <p className="mt-2 text-[13px] text-zinc-400">{entry.description}</p>

        {entry.needs && (
          <div className="mt-2">
            <span className="inline-flex items-center gap-1 rounded-md border border-white/[0.07] bg-white/[0.03] px-1.5 py-0.5 text-[10px] font-medium text-zinc-400">
              needs {entry.needs}
            </span>
          </div>
        )}

        {/* Raw launch command tucked away — plain description leads. */}
        <details className="group mt-3">
          <summary className="flex cursor-pointer select-none items-center gap-1 text-[11px] font-medium text-zinc-500 transition-colors hover:text-zinc-300 [&::-webkit-details-marker]:hidden">
            <ChevronRight
              size={12}
              className="transition-transform duration-200 group-open:rotate-90"
            />
            Show command
          </summary>
          <div className="mt-2">
            <ArgvChips argv={[entry.command, ...entry.args]} />
          </div>
        </details>

        {/* Placeholder / env config before connecting */}
        {open && !connected && (
          <div className="mt-3 space-y-2 rounded-lg border border-white/[0.05] bg-ink-900/40 p-2.5">
            {phs.map((ph) => (
              <label key={ph} className="block">
                <span className="mb-1 block font-mono text-[11px] text-accent-soft/80">
                  {ph}
                </span>
                <input
                  value={cfgValues[ph] ?? ""}
                  onChange={(e) => setCfgValues((v) => ({ ...v, [ph]: e.target.value }))}
                  placeholder="value"
                  className="field px-2 py-1.5 font-mono text-xs"
                />
              </label>
            ))}
            {envKeys.map((k) => (
              <label key={k} className="block">
                <span className="mb-1 block font-mono text-[11px] text-zinc-400">
                  {k} <span className="text-zinc-600">(env)</span>
                </span>
                <input
                  value={cfgEnv[k] ?? ""}
                  onChange={(e) => setCfgEnv((v) => ({ ...v, [k]: e.target.value }))}
                  placeholder="value"
                  className="field px-2 py-1.5 font-mono text-xs"
                />
              </label>
            ))}
            <button
              type="button"
              disabled={!canConnect || isBusy}
              onClick={() =>
                void addMcpServer(
                  entry.id,
                  entry.command,
                  substituteArgs(entry.args, cfgValues),
                  Object.fromEntries(envKeys.map((k) => [k, (cfgEnv[k] ?? "").trim()])),
                  entry.id,
                )
              }
              className="btn-accent w-full"
            >
              {isBusy ? (
                <LoaderInline label="Connecting…" />
              ) : (
                <>
                  <Plug size={14} /> Connect
                </>
              )}
            </button>
          </div>
        )}
      </div>
    );
  }

  async function suggest(e: React.FormEvent) {
    e.preventDefault();
    const d = sugDesc.trim();
    if (!d || sugBusy) return;
    setSugBusy(true);
    setSugError(null);
    setSuggestion(null);
    try {
      const res = await post<{ suggestion: McpSuggestion }>("/mcp/suggest", {
        description: d,
      });
      setSuggestion(res.suggestion);
    } catch (err) {
      setSugError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSugBusy(false);
    }
  }

  async function addSuggested() {
    if (!suggestion) return;
    const okAdd = await addMcpServer(
      suggestion.name,
      suggestion.command,
      suggestion.args ?? [],
      suggestion.env ?? {},
      `suggest:${suggestion.name}`,
    );
    if (okAdd) setSuggestion(null);
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Tools"
          subtitle="Tools are what agents can DO — read files, search the web, generate media. These are yours to extend."
        />
      </Reveal>
      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      {/* ------------------------------------------------------------------ */}
      {/*  Hero — describe a tool in plain language, an LLM builds it        */}
      {/* ------------------------------------------------------------------ */}
      <Reveal>
        <Card title="Teach Iron Jarvis something new" icon={<Sparkles size={15} />}>
          <p className="mb-3.5 text-sm text-zinc-400">
            Say what you want it to be able to do, in plain language. Iron Jarvis
            designs the command, names it, and saves it — every agent can use it
            right away.
          </p>
          <form onSubmit={generate} className="space-y-3">
            <textarea
              value={genDesc}
              onChange={(e) => setGenDesc(e.target.value)}
              rows={3}
              placeholder="e.g. A tool that converts a CSV to a formatted summary table"
              className="field resize-y"
            />
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="submit"
                disabled={genBusy || !genDesc.trim()}
                className="btn-accent"
              >
                {genBusy ? (
                  <LoaderInline label="Designing your tool…" />
                ) : (
                  <>
                    <Sparkles size={14} /> Build
                  </>
                )}
              </button>
              <span className="text-[11px] text-zinc-600">
                Usually takes 5–20 seconds.
              </span>
            </div>
          </form>

          {genError && (
            <div className="mt-3.5">
              <ErrorNote>{genError}</ErrorNote>
            </div>
          )}
          {genResult && (
            <div className="mt-3.5 space-y-3">
              <SuccessNote>{genResult.reply}</SuccessNote>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.015] px-4 py-3.5">
                <div className="flex flex-wrap items-center gap-2">
                  <Wrench size={14} className="text-accent-soft" />
                  <span className="font-mono text-sm font-semibold text-zinc-100">
                    {genResult.spec.name}
                  </span>
                  <span className="inline-flex items-center gap-1 text-[11px] text-zinc-500">
                    <Clock size={11} /> {genResult.spec.timeout_seconds}s
                  </span>
                </div>
                {genResult.spec.description && (
                  <p className="mt-1.5 text-sm text-zinc-400">
                    {genResult.spec.description}
                  </p>
                )}
                <div className="mt-3">
                  <ArgvChips argv={genResult.spec.command} />
                </div>
                {genResult.spec.parameters.length > 0 && (
                  <div className="mt-2.5">
                    <ParamChips params={genResult.spec.parameters} />
                  </div>
                )}
              </div>
            </div>
          )}
        </Card>
      </Reveal>

      {/* ------------------------------------------------------------------ */}
      {/*  Existing custom tools                                             */}
      {/* ------------------------------------------------------------------ */}
      <Reveal>
        <Card
          title={`Your tools${tools.length ? ` · ${tools.length}` : ""}`}
          icon={<Wrench size={15} />}
        >
          <p className="mb-3.5 text-sm text-zinc-400">
            Commands you&apos;ve taught Iron Jarvis — every agent can use them by name.
          </p>
          {loading && !data ? (
            <SkeletonRows rows={4} />
          ) : tools.length === 0 ? (
            <Empty icon={<Wrench size={24} />}>
              You haven&apos;t taught Iron Jarvis any commands yet. Describe one above
              and hit Build — every agent will be able to use it.
            </Empty>
          ) : (
            <div className="space-y-3">
              {formError && <ErrorNote>{formError}</ErrorNote>}
              {tools.map((tool) => (
                <div
                  key={tool.name}
                  className="rounded-xl border border-white/[0.06] bg-white/[0.015] px-4 py-3.5 transition-colors hover:border-white/10 hover:bg-white/[0.03]"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <Wrench size={14} className="text-accent-soft" />
                        <span className="font-mono text-sm font-semibold text-zinc-100">
                          {tool.name}
                        </span>
                        <span className="inline-flex items-center gap-1 text-[11px] text-zinc-500">
                          <Clock size={11} /> {tool.timeout_seconds}s
                        </span>
                      </div>
                      {tool.description && (
                        <p className="mt-1.5 text-sm text-zinc-400">
                          {tool.description}
                        </p>
                      )}
                    </div>
                    <ConfirmButton
                      onConfirm={() => remove(tool.name)}
                      label="Delete"
                      title={`Delete tool "${tool.name}"`}
                    />
                  </div>

                  {/* argv command preview */}
                  <div className="mt-3 overflow-x-auto rounded-lg border border-white/[0.06] bg-ink-900/60 px-3 py-2">
                    <code className="whitespace-pre font-mono text-[12px]">
                      {tool.command.map((tok, i) => {
                        const isPh = /^\{.+\}$/.test(tok);
                        return (
                          <span
                            key={i}
                            className={isPh ? "text-accent-soft" : "text-zinc-300"}
                          >
                            {tok}
                            {i < tool.command.length - 1 ? " " : ""}
                          </span>
                        );
                      })}
                    </code>
                  </div>

                  {/* parameter chips */}
                  {tool.parameters.length > 0 && (
                    <div className="mt-2.5">
                      <ParamChips params={tool.parameters} />
                    </div>
                  )}

                  <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-zinc-600">
                    <span className="inline-flex items-center gap-1">
                      <User size={11} />
                      {tool.created_by || "unknown"}
                    </span>
                    <span>·</span>
                    <span>{timeAgo(tool.created_at)}</span>
                    {tool.parameters.length > 0 && (
                      <>
                        <span>·</span>
                        <Badge
                          value={`${tool.parameters.length} param${
                            tool.parameters.length === 1 ? "" : "s"
                          }`}
                          tone="violet"
                        />
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </Reveal>

      {/* ------------------------------------------------------------------ */}
      {/*  Tool suite — curated, one-click ready-made tools                  */}
      {/* ------------------------------------------------------------------ */}
      <Reveal>
        <Card
          title={`Ready-made tools · ${TOOL_SUITE.length}`}
          icon={<Boxes size={15} />}
        >
          <p className="mb-4 text-sm text-zinc-400">
            One-click helpers for everyday jobs — checking a website, zipping a
            folder, seeing what&apos;s eating your disk. Click{" "}
            <span className="font-medium text-accent-soft">Add</span> and every agent
            can use it — no setup, built for Windows.
          </p>
          {suiteOk && (
            <div className="mb-3">
              <SuccessNote>{suiteOk}</SuccessNote>
            </div>
          )}
          {suiteError && (
            <div className="mb-3">
              <ErrorNote>{suiteError}</ErrorNote>
            </div>
          )}

          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {TOOL_SUITE.map((t) => {
              const added = installed.has(t.name);
              const isAdding = adding === t.name;
              return (
                <div
                  key={t.name}
                  className="flex flex-col rounded-xl border border-white/[0.06] bg-white/[0.015] p-4 transition-colors hover:border-white/10 hover:bg-white/[0.03]"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex min-w-0 items-start gap-2">
                      <span className="mt-0.5 shrink-0">{t.icon}</span>
                      <div className="min-w-0">
                        <div className="text-sm font-semibold text-zinc-100">
                          {t.title}
                        </div>
                        <div className="truncate font-mono text-[11px] text-zinc-500">
                          {t.name}
                        </div>
                      </div>
                    </div>
                    {added ? (
                      <span className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-emerald-500/30 bg-emerald-500/[0.1] px-2 py-1 text-[11px] font-medium text-emerald-300">
                        <Check size={12} /> Added
                      </span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => addFromSuite(t)}
                        disabled={isAdding}
                        title={`Add "${t.name}"`}
                        className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-accent/30 bg-accent/[0.08] px-2 py-1 text-[11px] font-medium text-accent-soft transition-colors hover:bg-accent/[0.14] disabled:opacity-50"
                      >
                        {isAdding ? (
                          <LoaderInline label="Adding…" />
                        ) : (
                          <>
                            <Plus size={12} /> Add
                          </>
                        )}
                      </button>
                    )}
                  </div>

                  <p className="mt-2 text-[13px] text-zinc-400">{t.blurb}</p>

                  {/* Command tucked away — the use-case leads, the jargon hides. */}
                  <details className="group mt-3">
                    <summary className="flex cursor-pointer select-none items-center gap-1 text-[11px] font-medium text-zinc-500 transition-colors hover:text-zinc-300 [&::-webkit-details-marker]:hidden">
                      <ChevronRight
                        size={12}
                        className="transition-transform duration-200 group-open:rotate-90"
                      />
                      Show command
                    </summary>
                    <div className="mt-2 space-y-2">
                      <ArgvChips argv={t.command} />
                      {t.parameters.length > 0 && <ParamChips params={t.parameters} />}
                    </div>
                  </details>
                </div>
              );
            })}
          </div>
        </Card>
      </Reveal>

      {/* ------------------------------------------------------------------ */}
      {/*  MCP — plug-in tool servers                                        */}
      {/* ------------------------------------------------------------------ */}
      <Reveal>
        <Card title="Plug-in tool packs (MCP)" icon={<Plug size={15} />}>
          <p className="mb-4 text-sm text-zinc-400">
            Tool packs bundle whole new abilities — talking to databases, browsing
            the web, using cloud apps. Connect one and everything inside it becomes
            available to your agents.
          </p>

          {mcpOk && (
            <div className="mb-3">
              <SuccessNote>{mcpOk}</SuccessNote>
            </div>
          )}
          {mcpError && (
            <div className="mb-3">
              <ErrorNote>{mcpError}</ErrorNote>
            </div>
          )}

          {/* Auto-approve — applies to whatever pack you connect next ------ */}
          <label className="mb-4 flex cursor-pointer items-start gap-3 rounded-xl border border-white/[0.06] bg-white/[0.015] px-4 py-3 transition-colors hover:border-white/10">
            <input
              type="checkbox"
              checked={autoApprove}
              onChange={(e) => setAutoApprove(e.target.checked)}
              className="mt-0.5 h-4 w-4 shrink-0 accent-accent"
            />
            <span className="min-w-0 text-sm">
              <span className="font-medium text-zinc-200">
                Let agents use this without asking
              </span>
              <span className="mt-1 block text-[12px] leading-relaxed text-zinc-500">
                When on, autonomous agents may run this pack&apos;s tools without a
                prompt — chat already approves tools when you arm them. It applies after
                the next Iron Jarvis restart and trusts every tool this pack exposes.
                Leave off (the default) to keep approving each use.
              </span>
            </span>
          </label>

          {/* Catalog grid ------------------------------------------------- */}
          <div className="mb-2.5">
            <SectionLabel>Popular packs</SectionLabel>
          </div>
          {catError ? (
            <p className="text-[13px] text-zinc-600">
              Pack list unavailable{catError.status !== 0 ? ` — ${catError.message}` : ""}.
            </p>
          ) : catalog.length === 0 ? (
            <p className="text-[13px] text-zinc-600">No packs to show.</p>
          ) : (
            <div className="space-y-5">
              {catalog.some((e) => (e.category ?? "reference") === "reference") && (
                <div>
                  <p className="mb-2 text-[12px] text-zinc-500">
                    Official reference servers — safe, first-party building blocks.
                  </p>
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {catalog
                      .filter((e) => (e.category ?? "reference") === "reference")
                      .map(catalogCard)}
                  </div>
                </div>
              )}
              {catalog.some((e) => e.category === "integration") && (
                <div>
                  <p className="mb-2 text-[12px] text-zinc-500">
                    Popular integrations — connect an app or service.
                  </p>
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {catalog.filter((e) => e.category === "integration").map(catalogCard)}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Add from npm / GitHub — the deterministic "paste a package" flow */}
          <div className="mb-2.5 mt-6">
            <SectionLabel>Add from npm or GitHub</SectionLabel>
          </div>
          <p className="mb-2 text-[13px] text-zinc-500">
            Paste an npm package (e.g.{" "}
            <code className="rounded bg-black/40 px-1 py-0.5 font-mono text-[11px] text-accent-soft/90">
              @modelcontextprotocol/server-slack
            </code>
            ) or a GitHub repo (e.g.{" "}
            <code className="rounded bg-black/40 px-1 py-0.5 font-mono text-[11px] text-accent-soft/90">
              owner/repo
            </code>
            ). Iron Jarvis builds the launch command for you — nothing runs until you
            review it and hit Connect.
          </p>
          <input
            value={srcInput}
            onChange={(e) => setSrcInput(e.target.value)}
            placeholder="@scope/package · owner/repo · https://github.com/owner/repo"
            className="field font-mono"
          />
          {srcInput.trim() && !srcBuilt && (
            <p className="mt-2 text-[12px] text-amber-300/80">
              That doesn&apos;t look like an npm package or a GitHub repo. Try{" "}
              <span className="font-mono">@scope/name</span> or{" "}
              <span className="font-mono">owner/repo</span>.
            </p>
          )}
          {srcBuilt && (
            <div className="mt-3 rounded-xl border border-accent/20 bg-accent/[0.04] p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Server size={14} className="text-accent-soft" />
                <span className="font-mono text-sm font-semibold text-zinc-100">
                  {srcName.trim() || srcBuilt.name}
                </span>
                {srcBuilt.github && <Badge value="from GitHub" tone="violet" />}
              </div>
              <p className="mt-2 text-[12px] text-zinc-500">
                This is the command it will run when you Connect:
              </p>
              <div className="mt-1">
                <ArgvChips argv={[srcBuilt.command, ...srcBuilt.args]} />
              </div>
              {srcBuilt.github && (
                <p className="mt-2 text-[11px] text-zinc-600">
                  GitHub repos launch via npx. If it&apos;s a Python server instead, add
                  it above by its package name (which uses uvx).
                </p>
              )}
              <label className="mt-3 block">
                <span className="mb-1 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  Name (optional)
                </span>
                <input
                  value={srcName}
                  onChange={(e) => setSrcName(e.target.value)}
                  placeholder={srcBuilt.name}
                  className="field px-2 py-1.5 font-mono text-xs"
                />
              </label>
              <label className="mt-2 block">
                <span className="mb-1 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  Environment variables (optional)
                </span>
                <textarea
                  value={srcEnvText}
                  onChange={(e) => setSrcEnvText(e.target.value)}
                  rows={2}
                  placeholder="KEY=value — one per line, e.g. API_TOKEN=..."
                  className="field resize-y font-mono text-xs"
                />
              </label>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={() => void addFromSource()}
                  disabled={!!mcpBusy && mcpBusy.startsWith("src:")}
                  className="btn-accent"
                >
                  {mcpBusy && mcpBusy.startsWith("src:") ? (
                    <LoaderInline label="Connecting…" />
                  ) : (
                    <>
                      <Plug size={14} /> Connect this pack
                    </>
                  )}
                </button>
                <span className="text-[11px] text-zinc-600">
                  Nothing is added until you confirm.
                </span>
              </div>
            </div>
          )}

          {/* Suggest a server ---------------------------------------------- */}
          <div className="mb-2.5 mt-6">
            <SectionLabel>Or describe what you want to connect</SectionLabel>
          </div>
          <form onSubmit={suggest} className="flex flex-col gap-2 sm:flex-row">
            <input
              value={sugDesc}
              onChange={(e) => setSugDesc(e.target.value)}
              placeholder="e.g. Let agents query my Postgres database"
              className="field flex-1"
            />
            <button
              type="submit"
              disabled={sugBusy || !sugDesc.trim()}
              className="btn-accent sm:w-auto"
            >
              {sugBusy ? (
                <LoaderInline label="Thinking…" />
              ) : (
                <>
                  <Lightbulb size={14} /> Suggest
                </>
              )}
            </button>
          </form>
          {sugError && (
            <div className="mt-3">
              <ErrorNote>{sugError}</ErrorNote>
            </div>
          )}
          {suggestion && (
            <div className="mt-3 rounded-xl border border-accent/20 bg-accent/[0.04] p-4">
              <p className="text-sm text-zinc-300">{suggestion.reply}</p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Server size={14} className="text-accent-soft" />
                <span className="font-mono text-sm font-semibold text-zinc-100">
                  {suggestion.name}
                </span>
              </div>
              <div className="mt-2.5">
                <ArgvChips argv={[suggestion.command, ...(suggestion.args ?? [])]} />
              </div>
              {Object.keys(suggestion.env ?? {}).length > 0 && (
                <div className="mt-3 space-y-2">
                  {Object.entries(suggestion.env ?? {}).map(([k, v]) => (
                    <label key={k} className="flex items-center gap-2">
                      <span className="w-44 shrink-0 truncate font-mono text-[11px] text-zinc-400">
                        {k}
                      </span>
                      <input
                        value={v}
                        onChange={(e) =>
                          setSuggestion((s) =>
                            s ? { ...s, env: { ...s.env, [k]: e.target.value } } : s,
                          )
                        }
                        placeholder="value"
                        className="field flex-1 px-2 py-1.5 font-mono text-xs"
                      />
                    </label>
                  ))}
                </div>
              )}
              <div className="mt-3.5 flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={() => void addSuggested()}
                  disabled={mcpBusy === `suggest:${suggestion.name}`}
                  className="btn-accent"
                >
                  {mcpBusy === `suggest:${suggestion.name}` ? (
                    <LoaderInline label="Adding…" />
                  ) : (
                    <>
                      <Plus size={14} /> Connect this pack
                    </>
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => setSuggestion(null)}
                  className="text-xs font-medium text-zinc-500 transition-colors hover:text-zinc-300"
                >
                  Dismiss
                </button>
                <span className="text-[11px] text-zinc-600">
                  Nothing is added until you confirm.
                </span>
              </div>
            </div>
          )}

          {/* Connected servers --------------------------------------------- */}
          <div className="mb-2.5 mt-6">
            <SectionLabel>
              Connected packs{servers.length ? ` · ${servers.length}` : ""}
            </SectionLabel>
          </div>
          {mcpLoading && !mcpData ? (
            <SkeletonRows rows={2} />
          ) : servers.length === 0 ? (
            <p className="text-[13px] text-zinc-600">
              No tool packs connected yet — add one from the list above.
            </p>
          ) : (
            <div className="space-y-2">
              {servers.map((s) => {
                const loaded = s.tools_loaded ?? 0;
                const names = s.tool_names ?? [];
                const testing = mcpTestBusy === s.name;
                const test = mcpTests[s.name];
                return (
                  <div
                    key={s.name}
                    className="rounded-xl border border-white/[0.06] bg-white/[0.015] px-3.5 py-2.5"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <Server size={13} className="text-accent-soft" />
                          <span className="font-mono text-sm font-semibold text-zinc-100">
                            {s.name}
                          </span>
                          <Badge
                            value={`${loaded} tool${loaded === 1 ? "" : "s"} loaded`}
                            tone={loaded > 0 ? "green" : "slate"}
                          />
                          {s.auto_approve && (
                            <Badge value="auto-approve" tone="amber" />
                          )}
                          {Object.keys(s.env ?? {}).length > 0 && (
                            <Badge
                              value={`${Object.keys(s.env ?? {}).length} env`}
                              tone="violet"
                            />
                          )}
                        </div>
                        <div className="mt-1 overflow-x-auto">
                          <code className="whitespace-pre font-mono text-[11px] text-zinc-500">
                            {[s.command, ...(s.args ?? [])].join(" ")}
                          </code>
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <button
                          type="button"
                          onClick={() => void testServer(s.name)}
                          disabled={testing}
                          title={`Test "${s.name}" — connect now and list its tools`}
                          className="inline-flex items-center gap-1 rounded-lg border border-accent/30 bg-accent/[0.08] px-2 py-1 text-[11px] font-medium text-accent-soft transition-colors hover:bg-accent/[0.14] disabled:opacity-50"
                        >
                          {testing ? (
                            <LoaderInline label="Testing…" />
                          ) : (
                            <>
                              <Radio size={12} /> Test
                            </>
                          )}
                        </button>
                        <ConfirmButton
                          onConfirm={() => removeServer(s.name)}
                          label="Delete"
                          title={`Disconnect tool pack "${s.name}"`}
                        />
                      </div>
                    </div>

                    {/* loaded tool names, as chips */}
                    {names.length > 0 && (
                      <div className="mt-2.5 flex flex-wrap gap-1">
                        {names.map((n) => (
                          <span
                            key={n}
                            className="rounded-md border border-white/[0.06] bg-white/[0.03] px-1.5 py-0.5 font-mono text-[11px] text-zinc-300"
                          >
                            {n}
                          </span>
                        ))}
                      </div>
                    )}

                    {/* inline Test result */}
                    {test && (
                      <div className="mt-2.5">
                        {test.ok ? (
                          <div className="rounded-lg border border-emerald-500/25 bg-emerald-500/[0.06] px-3 py-2">
                            <div className="flex items-center gap-1.5 text-[12px] font-medium text-emerald-300">
                              <Check size={13} /> Connected — {test.count} tool
                              {test.count === 1 ? "" : "s"} available now
                            </div>
                            {test.tools.length > 0 && (
                              <div className="mt-2 flex flex-wrap gap-1">
                                {test.tools.map((n) => (
                                  <span
                                    key={n}
                                    className="rounded-md border border-emerald-500/20 bg-emerald-500/[0.08] px-1.5 py-0.5 font-mono text-[11px] text-emerald-200"
                                  >
                                    {n}
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>
                        ) : (
                          <ErrorNote>{test.error ?? "Test failed."}</ErrorNote>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      </Reveal>

      {/* ------------------------------------------------------------------ */}
      {/*  Manual tool builder (advanced) — the original hand-rolled form    */}
      {/* ------------------------------------------------------------------ */}
      <Reveal>
        <details className="card-surface group">
          <summary className="flex cursor-pointer select-none items-center gap-2 px-5 py-3.5 text-[13px] font-semibold tracking-wide text-zinc-200 [&::-webkit-details-marker]:hidden">
            <ChevronRight
              size={15}
              className="text-accent-soft/80 transition-transform duration-200 group-open:rotate-90"
            />
            Build a tool by hand (advanced)
            <span className="font-normal text-zinc-500">
              — write the exact command yourself
            </span>
          </summary>
          <div className="space-y-4 border-t hairline p-5">
            <div className="flex items-start gap-3 rounded-2xl border border-accent/20 bg-accent/[0.05] px-4 py-3.5">
              <Info size={18} className="mt-0.5 shrink-0 text-accent-soft" />
              <div className="text-sm text-zinc-400">
                <span className="font-semibold text-zinc-200">How it works.</span> A tool
                is <span className="text-zinc-200">one command with fill-in-the-blank slots</span>.
                Iron Jarvis fills each{" "}
                <code className="rounded bg-black/40 px-1 py-0.5 font-mono text-[12px] text-accent-soft">
                  {"{param}"}
                </code>{" "}
                slot from the parameters you declare below — and only those slots, so
                nothing unexpected can sneak into the command. For example,{" "}
                <code className="rounded bg-black/40 px-1.5 py-0.5 font-mono text-[12px] text-zinc-200">
                  wc -l {"{file}"}
                </code>{" "}
                with a required{" "}
                <code className="rounded bg-black/40 px-1 py-0.5 font-mono text-[12px] text-accent-soft">
                  file
                </code>{" "}
                parameter counts the lines in whatever file an agent passes.
              </div>
            </div>

            <form onSubmit={submit} className="max-w-2xl space-y-3.5">
              <div>
                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  Name
                </label>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="count_lines"
                  className="field font-mono"
                />
                <div className="mt-1 text-[11px] text-zinc-600">
                  A short, unique name agents will call it by.
                </div>
              </div>

              <div>
                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  Description
                </label>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={2}
                  placeholder="Count the number of lines in a file."
                  className="field resize-y"
                />
              </div>

              <div>
                <label className="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  <Terminal size={12} /> Command
                </label>
                <input
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                  placeholder="wc -l {file}"
                  className="field font-mono"
                />
                <div className="mt-1 text-[11px] text-zinc-600">
                  The command, word by word. Write{" "}
                  <code className="font-mono text-accent-soft/80">{"{param}"}</code>{" "}
                  wherever a parameter below should fill in the blank.
                </div>
                {argv.length > 0 && (
                  <div className="mt-2">
                    <ArgvChips argv={argv} />
                  </div>
                )}
              </div>

              <div>
                <div className="mb-1.5 flex items-center justify-between">
                  <label className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    <Braces size={12} /> Parameters
                    {rows.length ? ` · ${rows.length}` : ""}
                  </label>
                  <button
                    type="button"
                    onClick={addRow}
                    className="inline-flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft"
                  >
                    <Plus size={12} /> Add
                  </button>
                </div>
                <div className="space-y-2 rounded-xl border border-white/[0.06] bg-ink-900/40 p-2.5">
                  {rows.length === 0 ? (
                    <p className="px-0.5 py-1 text-[11px] text-zinc-600">
                      No fill-in-the-blanks yet. Add one for each{" "}
                      <code className="font-mono text-accent-soft/70">
                        {"{placeholder}"}
                      </code>{" "}
                      in the command.
                    </p>
                  ) : (
                    rows.map((row) => (
                      <div
                        key={row.id}
                        className="flex flex-wrap items-center gap-2 rounded-lg border border-white/[0.05] bg-white/[0.015] p-2"
                      >
                        <input
                          value={row.name}
                          onChange={(e) =>
                            updateRow(row.id, { name: e.target.value })
                          }
                          placeholder="file"
                          className="field min-w-[6rem] flex-1 px-2 py-1.5 font-mono text-xs"
                        />
                        <select
                          aria-label="Parameter type"
                          value={row.type}
                          onChange={(e) =>
                            updateRow(row.id, { type: e.target.value as ParamType })
                          }
                          className="field w-auto px-2 py-1.5 text-xs"
                        >
                          {PARAM_TYPES.map((t) => (
                            <option key={t} value={t}>
                              {t}
                            </option>
                          ))}
                        </select>
                        <label
                          className={`flex cursor-pointer select-none items-center gap-1 rounded-lg border px-2 py-1.5 text-[11px] font-medium transition-colors ${
                            row.required
                              ? "border-rose-500/40 bg-rose-500/[0.1] text-rose-200"
                              : "border-white/10 text-zinc-400 hover:bg-white/[0.04]"
                          }`}
                          title="Is this parameter required?"
                        >
                          <input
                            type="checkbox"
                            checked={row.required}
                            onChange={(e) =>
                              updateRow(row.id, { required: e.target.checked })
                            }
                            className="h-3 w-3 accent-rose-400"
                          />
                          req
                        </label>
                        <button
                          type="button"
                          onClick={() => removeRow(row.id)}
                          title="Remove parameter"
                          className="grid h-7 w-7 shrink-0 place-items-center rounded-lg border border-white/10 text-zinc-500 transition-colors hover:border-rose-500/40 hover:text-rose-300"
                        >
                          <X size={13} />
                        </button>
                        <input
                          value={row.description}
                          onChange={(e) =>
                            updateRow(row.id, { description: e.target.value })
                          }
                          placeholder="description (optional)"
                          className="field min-w-[8rem] flex-1 basis-full px-2 py-1.5 text-xs"
                        />
                      </div>
                    ))
                  )}
                </div>
              </div>

              <div>
                <label className="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  <Clock size={12} /> Timeout (seconds)
                </label>
                <input
                  type="number"
                  min={1}
                  value={timeout}
                  onChange={(e) => setTimeoutSecs(e.target.value)}
                  placeholder="60"
                  className="field"
                />
              </div>

              <button
                type="submit"
                disabled={busy || !name.trim() || argv.length === 0}
                className="btn-accent w-full"
              >
                {busy ? (
                  <LoaderInline label="Creating…" />
                ) : (
                  <>
                    <Plus size={14} /> Create tool
                  </>
                )}
              </button>
              {ok && <SuccessNote>{ok}</SuccessNote>}
              {formError && <ErrorNote>{formError}</ErrorNote>}
            </form>
          </div>
        </details>
      </Reveal>
    </PageShell>
  );
}
