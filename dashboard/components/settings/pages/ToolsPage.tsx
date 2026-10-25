"use client";

import { useRef, useState, type ReactNode } from "react";
import Link from "next/link";
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
  HardDrive,
  GitBranch,
  FileArchive,
  ExternalLink,
  Sparkles,
  Plug,
  Server,
  Lightbulb,
  ChevronRight,
  ShieldAlert,
  RefreshCw,
  Pencil,
} from "lucide-react";
import { post, patch, del, ApiError } from "@/lib/api";
import { useApi, usePolledApi } from "@/lib/useApi";
import { useFocusRef } from "@/lib/useFocusRef";
import {
  Card,
  Badge,
  OfflineHint,
  Empty,
  SkeletonRows,
  ErrorNote,
  SuccessNote,
  WarnNote,
  LoaderInline,
  ConfirmButton,
  SectionLabel,
} from "@/components/ui";
import { addedButNotStarted, editedButNotStarted, packDidNotStart } from "@/lib/packStartWords";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { timeAgo } from "@/lib/format";
import { ProposalsCard } from "@/components/capability/ProposalsCard";
import {
  PrimaryAction,
  RiskChips,
  SourceChip,
  StatusChip,
} from "@/components/tools/chips";
import {
  EMPTY_FILTERS,
  FilterBar,
  filtersActive,
  type ToolFilters,
} from "@/components/tools/FilterBar";
import { EnableDialog, type EnablePlan } from "@/components/tools/EnableDialog";
import { PermissionsPanel } from "@/components/tools/PermissionsPanel";
import { AddPack } from "@/components/tools/AddPack";
import { mentionsNode } from "@/lib/addPackWords";
import { EditPack, type EditSaved } from "@/components/tools/EditPack";
import { PackSwitch } from "@/components/tools/PackSwitch";
import {
  KEYS_KEPT_HINT,
  PACK_FAILING_CONSEQUENCE,
  PACK_OFF_LINE,
  PACK_OFF_WORD,
  addressLine,
  humanFieldLabel,
  isPackOff,
  packSwitchLabel,
  packSwitchTitle,
  savedWhileOff,
  switchedNote,
  type SwitchAnswer,
} from "@/lib/extensionWords";
import {
  LAUNCHER_CHOICES,
  bearerAuth,
  buildLaunch,
  guessLauncher,
  safeName,
  type Launcher,
} from "@/lib/mcpImport";
import type { McpToolRow } from "@/lib/types";
import {
  RICHER_PACK,
  RUNTIME_HELP,
  VERIFY_PACK_ID,
  isOfficial,
  packCaps,
  suiteCaps,
  type Capability,
} from "@/components/tools/meta";

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
  /** v1.316.0: the daemon's launcher found this pack's command on this PC
   *  right now (the SAME resolution that starts the pack). Absent on an older
   *  daemon — then the card keeps the amber "Needs X" exactly as before. */
  runtime_ready?: boolean;
  /** v1.355.0 (G44): a REMOTE pack (GitHub's own server) — "http" | "sse",
   *  its address, and which of `env_keys` is the token sent as
   *  "Authorization: Bearer". Such a row has no command; Add posts the http
   *  body through the same POST /mcp/servers door. */
  transport?: string;
  url?: string;
  auth_field?: string;
  /** v1.357.0 (G82): the Directory card that adds this SAME pack under the
   *  SAME `mcp_servers` name ("" when none) — read off the one catalog file
   *  (`connectors/catalog.tools_twin`), so the "Also in the Directory" link
   *  can never drift from the Directory. Absent on an older daemon: no link. */
  directory_twin?: string;
}

/** v1.355.0 (G44): a catalog row that is a server address, not a command. */
function isRemoteEntry(entry: McpCatalogEntry): boolean {
  return entry.transport === "http" || entry.transport === "sse";
}

/**
 * v1.316.0 (UX wave 4, three-overlapping-catalogs): packs the Directory ALSO
 * offers under the SAME id. Both surfaces write one `mcp_servers` entry named
 * by that id (Directory: `connectors/service.py` `{"name": connector.id}`), so
 * adding in either place is the same pack — never a second copy. Only exact
 * twins: Directory's browser card is not Tools' Playwright, and its
 * `sequential_thinking` is not Tools' `sequentialthinking` (v1.353.0: the
 * Directory card still LIGHTS from that pack — connectors.service.PACK_ALIASES).
 * v1.357.0 (G82): no longer a hand-kept list here — each GET /mcp/catalog row
 * carries `directory_twin` from the one catalog file.
 */
function hasDirectoryTwin(entry: McpCatalogEntry): boolean {
  return typeof entry.directory_twin === "string" && entry.directory_twin.trim() !== "";
}

/**
 * Turn a pasted npm package or GitHub repo into a ready MCP launch command
 * — DETERMINISTIC, no code runs here. The exact command is shown to the user,
 * who reviews (and can edit) it before Connect actually starts the server.
 *   - "@scope/pkg" or "pkg"            -> npx -y <pkg>   (or uvx <pkg>)
 *   - "github.com/owner/repo" / URL    -> npx -y github:owner/repo
 *   - "owner/repo"                     -> npx -y github:owner/repo
 *   - launcher "command"               -> the text as a command line
 * Returns null when the input does not look like either.
 * v1.343.0: the launcher is a choice (npx / uvx / a typed command); the name
 * follows the daemon's own rule (`safeName`: dots become "-").
 */
function buildFromSource(
  raw: string,
  launcher: Launcher = "npx",
): { name: string; command: string; args: string[]; github: boolean } | null {
  if (launcher === "command") {
    const line = raw.trim();
    if (!line) return null;
    const built = buildLaunch(line, "command");
    if (!built.command) return null;
    const last = built.args[built.args.length - 1] ?? built.command;
    return { name: safeName(last.replace(/^@[^/]+\//, "")) || safeName(built.command), ...built, github: false };
  }
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
        name: safeName(repo),
        command: "npx",
        args: ["-y", `github:${owner}/${repo}`],
        github: true,
      };
    }
  }
  // An npm or PyPI package spec: "@scope/name", "@scope/name@1.2.3", "name",
  // "pip:name".
  const spec = s.replace(/^(pip|pypi|npm):/i, "");
  if (/^@?[\w.-]+(?:\/[\w.-]+)?(?:@[\w.-]+)?$/.test(spec)) {
    const bare = spec.replace(/^@[^/]+\//, "").replace(/@[\w.-]+$/, "");
    const name = safeName(bare || spec);
    return { name, ...buildLaunch(spec, launcher), github: false };
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
  /** v1.229.0: why the last load attempt skipped this server (the exception
   *  text, e.g. "FileNotFoundError: npx not found"); null = it loaded, or no
   *  attempt has been made in this daemon process. */
  last_error?: string | null;
  /** v1.311.0: "starting" while the daemon's background load has not
   *  answered yet (packs load after boot); null/absent on older daemons. */
  state?: string | null;
  /** v1.256.0 (R-02): the same failure in plain words — "npx isn't installed,
   *  or isn't on the PATH this app can see" — classified once on the daemon's
   *  load record so this row and the Overview hero cannot disagree. */
  reason?: string | null;
  /** v1.256.0 (R-02): the next action, or "" when there isn't an honest one to
   *  name. Never a guess. */
  fix?: string | null;
  last_attempt_at?: string | null;
  /** v1.299.0: tools that APPEARED (or changed shape) after the pack was
   *  first trusted and are write-like — they ask until trusted, whatever the
   *  pack's or the global auto-approve says. Absent on an older daemon. */
  quarantined?: string[];
  /** v1.299.0: the per-tool view (name, write_like, quarantined). */
  tools?: McpToolRow[];
  /** v1.342.0: the child's last stderr lines, secrets masked by the daemon,
   *  at most 2000 chars; "" (or absent on an older daemon) when none. */
  stderr_tail?: string | null;
  /** v1.342.0: call-time failures since the last success or reload. */
  failures?: number | null;
  /** v1.342.0: the env KEY names the pack launches with. Values never ride
   *  GET /mcp/servers any more; `env` stays only for an older daemon. */
  env_keys?: string[];
  /** v1.342.0: the env KEY names whose values live in the vault. */
  secret_keys?: string[];
  /** v1.343.0: an HTTP pack. "stdio" (or absent) is a program this PC runs. */
  transport?: string | null;
  url?: string | null;
  /** v1.343.0: header NAMES only (an older daemon may send a map; only its
   *  keys are read, never a value). */
  header_names?: string[];
  headers?: Record<string, unknown> | null;
  /** v1.343.0: the auth shape with `value_set`, never the value. */
  auth?: Record<string, unknown> | null;
  cwd?: string | null;
  /** v1.356.0 (G78): false = the user switched it off (its row reads
   *  `state: "off"`): kept with its settings and keys, never loaded. Absent
   *  on an older daemon = on. */
  enabled?: boolean | null;
}

/** v1.342.0: the four states of a pack's load record. An older daemon sends
 *  no `state`: a row with an error reads as failed, every other as ok, so the
 *  v1.229.0 rows render as they always did. v1.356.0 (G78): "off" = the user
 *  switched it off (no load record by design). */
type PackState = "starting" | "ok" | "failed" | "failing" | "off";

function packState(s: McpServer): PackState {
  if (isPackOff(s)) return "off";
  const st = s.state;
  if (st === "starting" || st === "ok" || st === "failed" || st === "failing") return st;
  return s.last_error ? "failed" : "ok";
}

/** The env key names of a pack: the daemon's `env_keys` when it sends them,
 *  else the keys of the (older) `env` map. Never a value. */
function envKeysOf(s: McpServer): string[] {
  if (Array.isArray(s.env_keys)) return s.env_keys.filter((k) => typeof k === "string");
  return Object.keys(s.env ?? {});
}

function secretKeysOf(s: McpServer): string[] {
  return Array.isArray(s.secret_keys) ? s.secret_keys.filter((k) => typeof k === "string") : [];
}

/** v1.342.0: a pack's last output, folded. Monospace, wrapped, twelve lines
 *  visible (192px at this size) with its own scroll, so a long tail never
 *  pushes the row off the screen. Nothing when the tail is empty. */
function LastOutput({ tail, testId }: { tail: string | null | undefined; testId: string }) {
  const text = (tail ?? "").trim();
  if (!text) return null;
  return (
    <details data-testid={testId} className="group w-full">
      <summary className="flex cursor-pointer select-none items-center gap-1 text-[11px] font-medium text-zinc-500 transition-colors hover:text-zinc-300 [&::-webkit-details-marker]:hidden">
        <ChevronRight size={12} className="transition-transform duration-200 group-open:rotate-90" />
        Last output
      </summary>
      <pre className="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap break-all rounded-lg border border-white/[0.06] bg-ink-900/60 px-2 py-1 font-mono text-[11px] leading-4 text-zinc-400">
        {text}
      </pre>
    </details>
  );
}

/** The quarantined tool names of a pack — from `quarantined` when the daemon
 *  sends it, else derived from the per-tool rows; [] on an older daemon. */
function quarantinedOf(s: McpServer): string[] {
  if (Array.isArray(s.quarantined)) return s.quarantined.filter((n) => typeof n === "string");
  return (s.tools ?? []).filter((t) => t && t.quarantined === true).map((t) => t.name);
}

/** Response of POST /mcp/servers/{name}/reload — the Retry for a pack that did not start. */
interface McpReloadResult {
  ok: boolean;
  tools_loaded: number;
  last_error: string | null;
  /** v1.342.0: the plain cause, the next step and the child's last output. */
  reason?: string | null;
  fix?: string | null;
  stderr_tail?: string | null;
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
  /** v1.342.0: when ok is false, the REAL load error in plain words, the
   *  next step and the child's last output (masked by the daemon). */
  reason?: string | null;
  fix?: string | null;
  stderr_tail?: string | null;
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

/** v1.342.0: what happens to a value the user types. The daemon splits
 *  secrets by KEY name (token, key, secret, password, auth, credential) into
 *  the encrypted vault; the config file keeps only the vault name. One
 *  sentence, said beside every box that takes a NAME=value. v1.356.0 (G74):
 *  in plain words (it named an "environment variable"). */
const SECRET_ENV_HINT = KEYS_KEPT_HINT;

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

/** Programs that PARSE an argument as a script. The daemon fills a {param} by
 *  PLAIN TEXTUAL substitution into one argv element (`CommandTool._render` in
 *  src/iron_jarvis/tools/dynamic.py) and runs `shell=False` — which keeps a
 *  value from becoming a new argv WORD, but does nothing once the program on
 *  the other end re-parses that word as code. Matched on the bare program name
 *  (path + ".exe" stripped). */
const SHELL_PROGRAMS = new Set([
  "powershell",
  "pwsh",
  "cmd",
  "command",
  "bash",
  "sh",
  "zsh",
  "wsl",
  "mshta",
  "cscript",
  "wscript",
  "node",
  "python",
  "python3",
  "perl",
  "ruby",
]);

const PLACEHOLDER_TOKEN_RE = /\{[A-Za-z_][A-Za-z0-9_]*\}/;

/** True when an argv hands a {placeholder} to a program that re-parses it as a
 *  script — the shape that made four curated tools injectable before v1.192.0.
 *  A shell command with NO placeholder (a fixed script like `disk_free`) is not
 *  flagged: nothing untrusted reaches it. */
function interpolatesIntoShell(command: string[]): boolean {
  const program = (command[0] ?? "")
    .trim()
    .toLowerCase()
    .split(/[\\/]/)
    .pop()!
    .replace(/\.(exe|com|cmd|bat)$/, "");
  if (!SHELL_PROGRAMS.has(program)) return false;
  return command.slice(1).some((element) => PLACEHOLDER_TOKEN_RE.test(element));
}

/**
 * The curated gallery. Every command is argv and the daemon runs it with
 * `shell=False`, so a value can never become an extra argv word — but that is
 * only the WHOLE safety story when the program does not itself interpret its
 * argument. NOTHING HERE MAY HAND A {placeholder} TO A SHELL.
 *
 * Four entries did until v1.192.0. They wrapped the placeholder in a PowerShell
 * script string (`powershell -NoProfile -Command "Get-ChildItem -Force
 * '{path}'"`), and `powershell.exe -Command` IS an interpreter: a value with a
 * single quote closed the literal and everything after it ran. MEASURED on this
 * machine (Windows PowerShell 5.1), not assumed:
 *   - inline form, path = `C:\x'; Write-Output INJECTED; '` → INJECTED ran;
 *     and the innocent path `C:\Users\O'Brien` failed to parse at all.
 *   - THE OBVIOUS FIX DOES NOT WORK. Giving the value its own argv element
 *     (`-Command Get-ChildItem -Force -LiteralPath <value>`) is still injectable:
 *     powershell.exe strips the process-level quoting and rejoins the tail into
 *     ONE script string, so `C:\x; Write-Output INJECTED` ran and
 *     `$(Write-Output INJECTED)` expanded. There is no safe way to pass an
 *     untrusted value to `-Command` under a textual substitution.
 * So these tools now run NATIVE executables (attrib / tar / rundll32), which
 * take their arguments from argv and never re-parse them. `word_count` was
 * REMOVED rather than escaped: Windows ships no native word counter, and a
 * fragile escape that merely looks safe is worse than a missing convenience
 * (the built-in `read_document` tool still reads text files).
 * `disk_free` keeps PowerShell on purpose — it has no parameters, so its script
 * is a constant.
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
    description: "List the files and folders (with attributes) inside a folder.",
    blurb: "See the files and folders inside any folder on your computer.",
    parameters: [strParam("path", "Directory path to list.")],
    // attrib.exe is a native program: it takes this one argv element as a
    // literal filespec and never re-parses it, so a path holding a quote, a
    // ';' or an '&' lists correctly instead of executing (verified). The
    // trailing \* is what makes it list the CONTENTS; /D includes folders.
    command: ["attrib", "/D", "{path}\\*"],
    timeout_seconds: 20,
    icon: <FolderOpen size={16} className="text-accent-soft" />,
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
    description: "Zip a folder's contents into a .zip file.",
    blurb: "Bundle a folder into a single .zip file, ready to share.",
    parameters: [
      strParam("source", "Folder to compress."),
      strParam("dest", "Destination .zip path."),
    ],
    // Windows 10 1803+ ships bsdtar as tar.exe; `-a` picks zip from the .zip
    // suffix. BOTH values are OPTION ARGUMENTS (-f, -C), never operands, so a
    // value can be neither re-parsed as script (tar is native, not a shell) nor
    // read as an option — a bare `--use-compress-program=…` operand would
    // otherwise run a program of its own. The one operand is the constant ".",
    // which also keeps the archive relative to the folder instead of storing
    // the whole absolute path. A box without tar.exe fails honestly with
    // "command not found".
    command: ["tar", "-a", "-c", "-f", "{dest}", "-C", "{source}", "."],
    timeout_seconds: 120,
    icon: <FileArchive size={16} className="text-accent-soft" />,
  },
  {
    name: "open_url",
    title: "Open a link in the browser",
    description: "Open a URL in the default browser.",
    blurb: "Pop a link open in your default browser.",
    parameters: [strParam("url", "The URL to open.")],
    // rundll32 is native and hands the value to the Windows shell's
    // FileProtocolHandler (a ShellExecute, NOT a command interpreter), so the
    // value is opened, never executed as script. Same reach as the old
    // Start-Process — it opens whatever the URL's handler is — minus the
    // parser that turned a quote into arbitrary PowerShell.
    command: ["rundll32", "url.dll,FileProtocolHandler", "{url}"],
    timeout_seconds: 15,
    icon: <ExternalLink size={16} className="text-accent-soft" />,
  },
];

export default function ToolsPage() {
  // Deep-link target: /tools?focus=packs lands on the MCP tool-packs card
  // (where auto-approve lives — the setting people search for by name).
  const packsFocusRef = useFocusRef<HTMLDivElement>("packs");
  // v1.354.0 integrate: /tools?focus=add lands on the add area — the
  // "Add a tool" tile, the Connections "+" menu and the hookups note all go
  // here (it sits below the built-in tools and the catalog).
  const addFocusRef = useFocusRef<HTMLDivElement>("add");

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
  // The SAVED argv per name. A suite tool the daemon already holds with a
  // DIFFERENT command (every install that added the pre-v1.192.0 PowerShell
  // versions) is offered as an Update — POST /tools/custom upserts by name, so
  // one click replaces the injectable definition. Without this the card just
  // says "Added" forever and the rewrite never reaches the daemon that has it.
  const savedCommands = new Map(tools.map((t) => [t.name, t.command] as const));
  const [adding, setAdding] = useState<string | null>(null);
  const [suiteError, setSuiteError] = useState<string | null>(null);
  const [suiteOk, setSuiteOk] = useState<string | null>(null);

  async function addFromSuite(t: SuiteTool, replacing = false) {
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
      setSuiteOk(`Tool "${t.name}" ${replacing ? "updated" : "added"}.`);
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
  } = usePolledApi<{
    servers: McpServer[];
    auto_approve_global?: boolean;
    auto_approve_effective?: boolean;
  }>("/mcp/servers", 10000);
  const servers = mcpData?.servers ?? [];
  const serversOn = servers.filter((s) => !isPackOff(s)).length;
  const serverNames = new Set(servers.map((s) => s.name));
  const serverByName = new Map(servers.map((s) => [s.name, s] as const));

  const { data: catData, error: catError } = useApi<{ catalog: McpCatalogEntry[] }>(
    "/mcp/catalog",
  );
  const catalog = catData?.catalog ?? [];

  const [mcpBusy, setMcpBusy] = useState<string | null>(null);
  const [mcpError, setMcpError] = useState<string | null>(null);
  // v1.351.0 (G08): the Extensions card's one note carries its tone. An add
  // that SAVED a pack which loaded no tools is a warning ("Added, but it
  // didn't start"), never the success box; every other note keeps its
  // green, and `mcpOk` / `setMcpOk` read and write it exactly as before.
  const [mcpNote, setMcpNote] = useState<{ text: string; warn: boolean } | null>(null);
  const mcpOk = mcpNote && !mcpNote.warn ? mcpNote.text : null;
  const mcpWarn = mcpNote?.warn ? mcpNote.text : null;
  const setMcpOk = (text: string | null) => setMcpNote(text === null ? null : { text, warn: false });

  // Auto-approve is a PERSISTED setting (v1.127.0), not a form field for the
  // next connect — that version was reported twice as "the box doesn't stick".
  // The checkbox binds to the daemon's EFFECTIVE state (global switch OR any
  // per-plug-in flag) so it can never show "off" while agents are trusted.
  const autoApprove = mcpData?.auto_approve_effective ?? false;
  const [autoApproveBusy, setAutoApproveBusy] = useState(false);

  /** PATCH /mcp/settings — save the global auto-approve switch immediately. */
  async function saveAutoApprove(next: boolean) {
    setAutoApproveBusy(true);
    setMcpError(null);
    setMcpOk(null);
    try {
      const res = await patch<{ note?: string | null }>("/mcp/settings", {
        auto_approve: next,
      });
      setMcpOk(
        res.note
          ? `Auto-approve ${next ? "on" : "off"} — ${res.note}.`
          : `Auto-approve ${next ? "on" : "off"} — saved.`,
      );
      reloadServers();
    } catch (err) {
      setMcpError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setAutoApproveBusy(false);
    }
  }

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

  /** POST /mcp/servers/{name}/reload — Retry a pack that did not start
   *  (v1.229.0). Unlike Test, a success LOADS the tools for every agent; the
   *  result rides the same inline slot as a Test result, and the row is
   *  re-read so the amber line clears (or shows the new reason). */
  async function retryServer(serverName: string) {
    setMcpTestBusy(serverName);
    setMcpTests((t) => {
      const next = { ...t };
      delete next[serverName];
      return next;
    });
    try {
      const res = await post<McpReloadResult>(
        `/mcp/servers/${encodeURIComponent(serverName)}/reload`,
      );
      setMcpTests((t) => ({
        ...t,
        [serverName]: {
          ok: res.ok,
          count: res.tools_loaded,
          tools: [],
          error: res.last_error,
          reason: res.reason ?? null,
          fix: res.fix ?? null,
          stderr_tail: res.stderr_tail ?? null,
        },
      }));
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
      void reloadServers();
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

  /** POST /mcp/servers and report tools_loaded / the restart note honestly.
   *  v1.355.0 (G44): `remote` posts a server ADDRESS instead of a command
   *  (GitHub's own server): `{name, transport, url, auth}` with the token as
   *  "Authorization: Bearer" (the daemon vaults it; it never comes back). */
  async function addMcpServer(
    serverName: string,
    cmd: string,
    args: string[],
    env: Record<string, string>,
    busyKey: string,
    launcher?: Launcher,
    remote?: { transport: "http" | "sse"; url: string; token: string },
  ): Promise<boolean> {
    setMcpBusy(busyKey);
    setMcpError(null);
    setMcpOk(null);
    try {
      // No auto_approve here: trust is governed by the persisted global switch
      // (the checkbox above the catalog), not stamped per-connect — a per-row
      // stamp would silently survive the user later unchecking the box.
      // v1.343.0: the npm door names its launcher; a catalog add sends none
      // and its body stays byte for byte what it was.
      const res = await post<McpAddResult>(
        "/mcp/servers",
        remote
          ? {
              name: serverName,
              transport: remote.transport,
              url: remote.url,
              ...(remote.token ? { auth: bearerAuth(remote.token) } : {}),
            }
          : {
              name: serverName,
              command: cmd,
              args,
              env,
              ...(launcher ? { launcher } : {}),
            },
      );
      const approveNote = res.auto_approve
        ? " Autonomous agents may run its tools without asking after the next restart."
        : "";
      if (packDidNotStart(res)) {
        // v1.351.0 (G08): saved, but it loaded nothing: say so in the warn
        // tone with the daemon's reason; its row below carries Retry.
        setMcpNote({ text: `${addedButNotStarted(res.note, `Extension "${serverName}"`)}${approveNote}`, warn: true });
      } else {
        setMcpOk(
          res.note
            ? `Extension "${serverName}" connected — ${res.note}${approveNote}`
            : `Extension "${serverName}" connected — ${res.tools_loaded} tool${
                res.tools_loaded === 1 ? "" : "s"
              } ready to use.${approveNote}`,
        );
      }
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

  /** POST /mcp/servers/{name}/tools/{tool}/trust (v1.299.0) — a write-like
   *  tool that appeared after the pack was first trusted asks until the user
   *  trusts it by name. One press, one tool; the list re-reads. */
  async function trustTool(serverName: string, tool: string) {
    setMcpBusy(`trust:${serverName}:${tool}`);
    setMcpError(null);
    setMcpOk(null);
    try {
      await post(
        `/mcp/servers/${encodeURIComponent(serverName)}/tools/${encodeURIComponent(tool)}/trust`,
      );
      setMcpOk(`"${tool}" from ${serverName} is trusted — it follows the extension's permission now.`);
      reloadServers();
    } catch (err) {
      setMcpError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setMcpBusy(null);
    }
  }

  /** PATCH /mcp/servers/{name} — flip auto-approve on a CONNECTED pack. */
  async function toggleAutoApprove(server: McpServer) {
    const next = !server.auto_approve;
    setMcpBusy(`auto:${server.name}`);
    setMcpError(null);
    setMcpOk(null);
    try {
      await patch(`/mcp/servers/${encodeURIComponent(server.name)}`, {
        auto_approve: next,
      });
      setMcpOk(
        next
          ? `"${server.name}" runs without asking — restart Iron Jarvis for autonomous agents to pick it up.`
          : `"${server.name}" will ask before each use again — restart Iron Jarvis to apply.`,
      );
      reloadServers();
    } catch (err) {
      setMcpError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setMcpBusy(null);
    }
  }

  /** v1.356.0 (G78): PATCH /mcp/servers/{name} {enabled} — switch a pack
   *  OFF without losing it (its tools leave chats and agents at once; its
   *  settings and keys stay) or back ON (the daemon reloads it the way Retry
   *  does; nothing is typed again). The note says what happened in words. */
  async function setPackOn(server: McpServer, next: boolean) {
    setMcpBusy(`switch:${server.name}`);
    setMcpError(null);
    setMcpOk(null);
    try {
      const res = await patch<SwitchAnswer>(`/mcp/servers/${encodeURIComponent(server.name)}`, {
        enabled: next,
      });
      setMcpNote(switchedNote(server.name, next, res));
      reloadServers();
    } catch (err) {
      setMcpError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setMcpBusy(null);
    }
  }

  async function removeServer(serverName: string) {
    setMcpError(null);
    setMcpOk(null);
    try {
      await del(`/mcp/servers/${encodeURIComponent(serverName)}`);
      // v1.342.0: the route closes the pack's tools itself, so there is no
      // restart to ask for.
      setMcpOk(`Removed "${serverName}". Its tools are closed.`);
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
  // v1.343.0: the launcher is a choice. Until the user picks one it follows
  // the daemon's guess (a "pip:" prefix or a PyPI-looking name reads as uvx).
  const [srcLauncherPick, setSrcLauncherPick] = useState<Launcher | null>(null);
  const srcPick: Launcher = srcLauncherPick ?? guessLauncher(srcInput);
  const srcBuilt = buildFromSource(srcInput, srcPick);
  // A GitHub ref always launches through npx (buildFromSource builds
  // `npx -y github:owner/repo` whatever the select held), so the launcher
  // the select shows and the POST body carries is the one the built command
  // really uses. A pick of uvx followed by a GitHub paste used to send
  // `command: "npx"` beside `launcher: "uvx"`.
  const srcLauncher: Launcher = srcBuilt?.github ? "npx" : srcPick;
  // v1.343.0: Edit on a pack row opens the inline editor for that name.
  const [editing, setEditing] = useState<string | null>(null);

  function onPackEdited(serverName: string, res: EditSaved) {
    setEditing(null);
    setMcpError(null);
    // v1.356.0 (G78): an edit to a switched-off pack is saved and stays off
    // (the daemon loads nothing behind the switch) — not a failure.
    if (isPackOff(res as { enabled?: boolean | null; state?: string | null })) {
      setMcpOk(savedWhileOff(serverName));
      reloadServers();
      return;
    }
    // v1.351.0 integrate (G08): an edit whose pack did not reload is SAVED,
    // not working — the warn note with the daemon's reason, never the green.
    if (res.reloaded === false) {
      setMcpNote({ text: editedButNotStarted(res.note, `"${serverName}"`), warn: true });
      reloadServers();
      return;
    }
    setMcpOk(res.note ? `"${serverName}" saved. ${res.note}` : `"${serverName}" saved and reloaded.`);
    reloadServers();
  }

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
    // v1.343.0: the daemon's own name rule (dots become "-"), applied here so
    // the name the user reads is the name the row will carry.
    const name = safeName(srcName.trim() || srcBuilt.name);
    if (!name) return;
    const okAdd = await addMcpServer(
      name,
      srcBuilt.command,
      srcBuilt.args,
      parseEnv(srcEnvText),
      `src:${name}`,
      srcLauncher,
    );
    if (okAdd) {
      setSrcInput("");
      setSrcName("");
      setSrcEnvText("");
      setSrcLauncherPick(null);
    }
  }

  /**
   * One catalog card, answering the review's four questions in order:
   * what it does · status · risk · action (§4).
   *
   * The old card led with the pack name and a small "+ Add" chip in the
   * corner, put the runtime in a grey pill that read as decoration, and said
   * nothing at all about what the pack could reach. Adding was one click from
   * a grid — which the review rightly called a flattened risk surface.
   */
  function catalogCard(entry: McpCatalogEntry) {
    const connected = serverNames.has(entry.id);
    const caps = packCaps(entry.id);
    const official = isOfficial(entry.category);
    const isBusy = mcpBusy === entry.id;
    // v1.342.0: a pack that is saved but did not start (or whose calls fail)
    // must not wear the green "Enabled" chip here while the row below says
    // it is broken. The card reads the same load record as the row.
    const row = connected ? serverByName.get(entry.id) : undefined;
    const rowState = row ? packState(row) : null;
    const broken = rowState === "failed" || rowState === "failing";
    // v1.356.0 (G78): added but switched off — kept, not in use; never green.
    const off = rowState === "off";
    return (
      <div
        key={entry.id}
        data-pack={entry.id}
        className={`flex flex-col rounded-xl border p-4 transition-colors ${
          connected && !broken && !off
            ? "border-emerald-500/20 bg-emerald-500/[0.03] hover:border-emerald-500/30"
            : "border-white/[0.06] bg-white/[0.015] hover:border-white/10 hover:bg-white/[0.03]"
        }`}
      >
        {/* 1. WHAT IT DOES. */}
        <div className="flex items-start gap-2">
          <Plug size={14} className="mt-1 shrink-0 text-accent-soft" aria-hidden />
          <span className="min-w-0 flex-1 text-sm font-semibold text-zinc-100">
            {entry.name}
          </span>
          {/* 2. STATUS — including the runtime, promoted from a grey pill to
              a real state the user can act on. */}
          {off ? (
            <Badge
              value={PACK_OFF_WORD}
              tone="slate"
              variant="calm"
              keepCase
              data-testid="status-off"
              title={PACK_OFF_LINE}
            />
          ) : broken ? (
            <Badge
              value={rowState === "failing" ? "Failing" : "Didn’t start"}
              tone="amber"
              variant="calm"
              keepCase
              data-testid="status-failed"
              title={row?.reason || row?.last_error || undefined}
            />
          ) : (
            <StatusChip
              status={
                connected
                  ? "added"
                  : entry.runtime_ready === true && entry.needs
                    ? "ready"
                    : entry.needs
                      ? "blocked"
                      : "available"
              }
              needs={entry.needs}
            />
          )}
        </div>

        <p className="mt-2 text-[13px] leading-relaxed text-zinc-400">
          {entry.description}
        </p>

        {/* 3. RISK, plus provenance said once. */}
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
          <RiskChips caps={caps} />
          <SourceChip official={official} />
        </div>

        {/* 4. ACTION. */}
        <div className="mt-auto pt-3">
          {connected ? (
            /* v1.356.0 (G74): one word for taking an extension away, the
               same on this card, its row below and the Directory: Remove. */
            <PrimaryAction
              tone="quiet"
              label="Remove"
              icon={<X size={13} />}
              title={`Remove "${entry.name}" and its saved settings`}
              testId={`pack-remove-${entry.id}`}
              disabled={mcpBusy === `del:${entry.id}`}
              onClick={() => void removeServer(entry.id)}
            />
          ) : (
            <PrimaryAction
              label={isBusy ? <LoaderInline label="Enabling…" /> : "Add"}
              icon={isBusy ? undefined : <Plus size={13} />}
              disabled={isBusy}
              title={`Review and enable "${entry.name}"`}
              testId={`pack-add-${entry.id}`}
              onClick={() => {
                setPendingError(null);
                setPending(planForPack(entry));
              }}
            />
          )}
        </div>

        {/* v1.316.0: the same pack has a guided setup in the Directory —
            one name for that page everywhere ("Directory", never
            Marketplace). Only exact twins (same mcp_servers name). */}
        {hasDirectoryTwin(entry) && (
          <Link
            href="/marketplace"
            className="mt-2 inline-flex w-fit items-center gap-1 text-[11px] text-zinc-500 transition-colors hover:text-accent-soft"
            title="The Directory adds this same extension with a guided form — adding it in either place is the same thing"
          >
            Also in the Directory — guided setup <ChevronRight size={11} aria-hidden />
          </Link>
        )}

        {/* The launch command, default closed. */}
        <details className="group mt-2.5">
          <summary className="flex cursor-pointer select-none items-center gap-1 text-[11px] font-medium text-zinc-500 transition-colors hover:text-zinc-300 [&::-webkit-details-marker]:hidden">
            <ChevronRight
              size={12}
              className="transition-transform duration-200 group-open:rotate-90"
            />
            Show command
            <span className="ml-1 font-mono text-zinc-600">{entry.id}</span>
          </summary>
          <div className="mt-2">
            {/* v1.355.0 (G44): a remote pack starts from its address. */}
            <ArgvChips
              argv={isRemoteEntry(entry) ? [entry.url ?? ""] : [entry.command, ...entry.args]}
            />
          </div>
        </details>
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


  /* ------------------------------------------------------------------ */
  /*  ONE CATALOG, TWO DEPTHS (v1.216.0)                                  */
  /* ------------------------------------------------------------------ */
  /**
   * The review's §2: "You have two nearly duplicate products… People will add
   * both and not know why." Fetch a web page / Fetch web pages. List what's in
   * a folder / Files & folders. See what changed in a project / Git
   * repositories.
   *
   * They are not merged — they really are different things (an argv command
   * the daemon runs vs a server that exposes a whole toolset) — but they are
   * now DESCRIBED as one model with two depths, filtered by one control, and
   * a built-in whose ground a pack also covers says so on its own card.
   */

  const [filters, setFilters] = useState<ToolFilters>(EMPTY_FILTERS);

  /** How many built-ins are actually saved with the daemon — the "Enabled"
   *  half of the summary. Counted from the daemon's own tool list, never from
   *  a local optimistic flag, so the strip cannot claim an add that failed. */
  const enabledBuiltins = TOOL_SUITE.filter((t) => installed.has(t.name)).length;

  /** Everything a filter needs to decide, for either kind of item. */
  type FilterItem = {
    kind: "builtin" | "extension";
    id: string;
    title: string;
    text: string;
    caps: Capability[];
    added: boolean;
    needs?: string;
  };

  function matchesFilters(it: FilterItem): boolean {
    const q = filters.q.trim().toLowerCase();
    if (q && !`${it.title} ${it.id} ${it.text}`.toLowerCase().includes(q)) return false;
    if (filters.status === "added" && !it.added) return false;
    if (filters.status === "available" && it.added) return false;
    if (filters.kind !== "all" && filters.kind !== it.kind) return false;
    if (filters.readyOnly && it.needs) return false;
    if (filters.caps.length > 0 && !filters.caps.some((c) => it.caps.includes(c)))
      return false;
    return true;
  }

  /** What the current filter leaves visible. The filter bar reports these so
   *  a filter that hides everything reads as a filter, not as an empty app. */
  const shownBuiltinCount = TOOL_SUITE.filter((t) =>
    matchesFilters({
      kind: "builtin",
      id: t.name,
      title: t.title,
      text: t.blurb,
      caps: suiteCaps(t.name),
      added: installed.has(t.name),
    }),
  ).length;
  const shownPackCount = catalog.filter(
    (e) =>
      e.id !== VERIFY_PACK_ID &&
      matchesFilters({
        kind: "extension",
        id: e.id,
        title: e.name,
        text: e.description,
        caps: packCaps(e.id),
        added: serverNames.has(e.id),
        needs: e.needs,
      }),
  ).length;

  /* ------------------------------------------------------------------ */
  /*  The consequence preview (review §6)                                 */
  /* ------------------------------------------------------------------ */
  /**
   * "Clicking Add on 'Files & folders' is a trust moment." Nothing is enabled
   * from a grid button any more; the button opens a dialog that says what the
   * agent will be able to do, collects the values the pack needs BEFORE it
   * starts, states the runtime, names who gets it, and asks ask-vs-allow.
   *
   * ONE HONEST LIMIT, stated in the dialog rather than faked: a built-in like
   * "List what's in a folder" takes its path PER CALL — the agent supplies it
   * when it runs. There is no folder to pick at enable time, and a picker that
   * implied the tool was confined to one folder would be a lie. So those cards
   * say the agent chooses the folder each time, which is the fact the risk
   * chip is warning about.
   */
  type Pending =
    | { kind: "suite"; tool: SuiteTool; replacing: boolean; plan: EnablePlan }
    | { kind: "pack"; entry: McpCatalogEntry; plan: EnablePlan };
  const [pending, setPending] = useState<Pending | null>(null);
  const [pendingError, setPendingError] = useState<string | null>(null);
  const pendingBusy =
    pending?.kind === "suite"
      ? adding === pending.tool.name
      : pending?.kind === "pack"
        ? mcpBusy === pending.entry.id
        : false;

  function planForSuite(t: SuiteTool, replacing: boolean): Pending {
    const caps = suiteCaps(t.name);
    const perCall = t.parameters.filter((prm) =>
      /path|dir|repo|source|dest/i.test(prm.name),
    );
    const summary = replacing
      ? `${t.blurb} This replaces the copy you already have, which runs an older command.`
      : t.blurb;
    return {
      kind: "suite",
      tool: t,
      replacing,
      plan: {
        kind: "builtin",
        title: t.title,
        id: t.name,
        summary:
          perCall.length > 0
            ? `${summary} The agent chooses the ${perCall
                .map((prm) => prm.name)
                .join(" and ")} when it runs this — it is not limited to one folder.`
            : summary,
        caps,
        offerAutoApprove: false,
      },
    };
  }

  function planForPack(entry: McpCatalogEntry): Pending {
    const phs = placeholdersOf(entry.args);
    const envKeys = entry.env_keys ?? [];
    return {
      kind: "pack",
      entry,
      plan: {
        kind: "extension",
        title: entry.name,
        id: entry.id,
        summary: entry.description,
        caps: packCaps(entry.id),
        needs: entry.needs,
        official: isOfficial(entry.category),
        offerAutoApprove: true,
        // v1.356.0 (G74): each field labelled in words ("Folder", "GitHub
        // personal access token"); the raw placeholder or key stays in its
        // title, where someone matching it to the docs can still find it.
        fields: [
          ...phs.map((ph) => ({
            key: `ph:${ph}`,
            label: humanFieldLabel(ph),
            title: ph,
            kind: /path|dir|folder|root/i.test(ph) ? ("path" as const) : ("text" as const),
            hint: /path|dir|folder|root/i.test(ph)
              ? "The extension may only work inside this folder."
              : undefined,
          })),
          ...envKeys.map((k) => ({
            key: `env:${k}`,
            label: humanFieldLabel(k),
            title: k,
            kind: "text" as const,
            hint: SECRET_ENV_HINT,
          })),
        ],
      },
    };
  }

  /** The dialog's one primary button. */
  async function confirmEnable(values: Record<string, string>, auto: boolean) {
    if (!pending) return;
    setPendingError(null);
    if (pending.kind === "suite") {
      await addFromSuite(pending.tool, pending.replacing);
      setPending(null);
      return;
    }
    const entry = pending.entry;
    const phValues: Record<string, string> = {};
    const env: Record<string, string> = {};
    for (const [k, v] of Object.entries(values)) {
      if (k.startsWith("ph:")) phValues[k.slice(3)] = v;
      else if (k.startsWith("env:")) env[k.slice(4)] = v;
    }
    // v1.355.0 (G44): a remote row (GitHub's own server) posts its address
    // and sends its one key as the bearer token; every other row is a command.
    const ok = isRemoteEntry(entry)
      ? await addMcpServer(entry.id, "", [], {}, entry.id, undefined, {
          transport: entry.transport === "sse" ? "sse" : "http",
          url: entry.url ?? "",
          token: entry.auth_field ? (env[entry.auth_field] ?? "") : "",
        })
      : await addMcpServer(
          entry.id,
          entry.command,
          substituteArgs(entry.args, phValues),
          env,
          entry.id,
        );
    if (!ok) {
      setPendingError("The extension could not be connected — see the note above.");
      return;
    }
    // THE PER-EXTENSION GRANT, applied after the connect rather than stamped
    // into it. `addMcpServer` deliberately sends no auto_approve (a stamp used
    // to survive the user later clearing the global box); this is a separate,
    // scoped write that the Permissions panel shows by name and can revoke.
    if (auto) {
      try {
        await patch(`/mcp/servers/${encodeURIComponent(entry.id)}`, {
          auto_approve: true,
        });
        reloadServers();
      } catch (e) {
        // v1.226.0: the server IS connected, but the grant the user just
        // ticked did not land — closing as success would leave them believing
        // it did. Keep the dialog open and say exactly what happened.
        reloadServers();
        setPendingError(
          `Connected, but auto-approve could not be saved: ${
            e instanceof ApiError ? e.message : String(e)
          }`,
        );
        return;
      }
    }
    setPending(null);
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

      {/* WHAT IS LIVE RIGHT NOW (review §1). "Users cannot answer 'what is live
          right now?' without hunting." Two counts and the fleet scope, as the
          first line of content, before any catalog — and the filter that acts
          on both catalogs sits with them so the answer and the way to narrow it
          are the same control surface. */}
      <Reveal>
        <div
          data-testid="tools-summary"
          className="rounded-2xl border border-white/[0.06] bg-white/[0.015] p-4"
        >
          <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            {/* v1.356.0 (G78): a switched-off extension is kept, not enabled. */}
            <span className="text-[13px] font-semibold tracking-wide text-zinc-200">
              Enabled: {enabledBuiltins} built-in ·{" "}
              {serversOn} extension{serversOn === 1 ? "" : "s"} ·{" "}
              {tools.length} of your own
            </span>
            {/* v1.314.0: "all your agents", not "every agent in this fleet" (jargon). */}
            <span className="text-[12px] text-zinc-500">
              available to all your agents
            </span>
          </div>
          {/* ONE JOB STORY, replacing the two similar blurbs (review §7). */}
          <p className="mb-3.5 text-[12.5px] leading-relaxed text-zinc-500">
            {/* Readiness 12A (0d.6): files are BUILT IN (the Directory lists
                Files & folders as built in), so extensions are not "files". */}
            Built-in tools work immediately, files and folders included. Extensions
            add abilities from other apps and services (repositories, memory, cloud
            apps) and may need Node or Python. You choose the folder or account when
            you enable one.
          </p>
          <FilterBar
            value={filters}
            onChange={setFilters}
            counts={{ builtin: shownBuiltinCount, extension: shownPackCount }}
          />
        </div>
      </Reveal>

      {/* ------------------------------------------------------------------ */}
      {/*  Hero — describe a tool in plain language, an LLM builds it        */}
      {/* ------------------------------------------------------------------ */}
      {/* CATALOGS FIRST, AUTHORING AFTER (v1.216.0). The filter bar acts on
          these two sections, and it used to sit three cards above them — you
          narrowed the list at the top of the page and then scrolled past the
          authoring cards to see what the filter had done. Reading order now
          matches the review's §1 question: what is live, what can I add, and
          only then how do I make one of my own. */}
      <Reveal>
        <Card
          title={`Built-in tools · ${TOOL_SUITE.length}`}
          icon={<Boxes size={15} />}
        >
          {/* Renamed from "Ready-made tools" (review §2: name the two sections
              in user language — "Built-in tools (no runtime)" vs "Extensions
              (MCP — needs Node or Python)"). */}
          <p className="mb-4 text-sm text-zinc-400">
            <span className="text-zinc-300">Built-in tools work immediately</span>{" "}
            — nothing to install, no setup. One-click helpers for everyday tasks: checking
            a website, zipping a folder, seeing what&apos;s eating your disk.
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

          {(() => {
            const rows = TOOL_SUITE.map((t) => {
              const saved = savedCommands.get(t.name);
              const added = installed.has(t.name);
              // Saved under this name, but not THIS command (an install that
              // still holds a pre-v1.192.0 definition).
              const stale =
                saved !== undefined && JSON.stringify(saved) !== JSON.stringify(t.command);
              return { t, added, stale, caps: suiteCaps(t.name) };
            });
            const shown = rows.filter((r) =>
              matchesFilters({
                kind: "builtin",
                id: r.t.name,
                title: r.t.title,
                text: r.t.blurb,
                caps: r.caps,
                added: r.added,
              }),
            );
            if (shown.length === 0) {
              return (
                <p className="text-[13px] text-zinc-500">
                  No built-in tools match this filter.
                </p>
              );
            }
            // ENABLED FIRST (review §1 + §8: "Group 'Enabled' first"; "Don't
            // make the only installed plugin the same visual weight as five
            // uninstalled ones"). Within each group the curated order stands.
            const ordered = [
              ...shown.filter((r) => r.added),
              ...shown.filter((r) => !r.added),
            ];
            return (
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {ordered.map(({ t, added, stale, caps }) => {
                  const isAdding = adding === t.name;
                  const richer = RICHER_PACK[t.name];
                  return (
                    <div
                      key={t.name}
                      data-suite-tool={t.name}
                      data-argv={JSON.stringify(t.command)}
                      className={`flex flex-col rounded-xl border p-4 transition-colors ${
                        added
                          ? "border-emerald-500/20 bg-emerald-500/[0.03] hover:border-emerald-500/30"
                          : "border-white/[0.06] bg-white/[0.015] hover:border-white/10 hover:bg-white/[0.03]"
                      }`}
                    >
                      {/* 1. WHAT IT DOES. The technical name is demoted to the
                          command disclosure (review §4: "Demote http_get /
                          list_dir… casual users do not choose tools by function
                          name"). */}
                      <div className="flex items-start gap-2">
                        <span className="mt-0.5 shrink-0">{t.icon}</span>
                        <div className="min-w-0 flex-1 text-sm font-semibold text-zinc-100">
                          {t.title}
                        </div>
                        {/* 2. STATUS. */}
                        <StatusChip status={added && !stale ? "added" : "available"} />
                      </div>

                      <p className="mt-2 text-[13px] leading-relaxed text-zinc-400">
                        {t.blurb}
                      </p>

                      {/* 3. RISK. */}
                      <div className="mt-2.5">
                        <RiskChips caps={caps} />
                      </div>

                      {stale && (
                        <p className="mt-2 text-[11px] text-amber-300/90">
                          The saved copy runs an older command. Update replaces it.
                        </p>
                      )}

                      {/* ONE MODEL, TWO DEPTHS (review §2). Where a capability
                          pack covers the same ground with more reach, the card
                          says so instead of leaving the user to add both and
                          wonder why. */}
                      {richer && (
                        <p className="mt-2 text-[11px] leading-relaxed text-zinc-500">
                          Need more?{" "}
                          <span className="text-zinc-300">{richer.name}</span> (an
                          extension) {richer.why}.
                        </p>
                      )}

                      <div className="mt-auto pt-3">
                        {/* 4. ACTION — the primary visual, not a corner chip. */}
                        {added && !stale ? (
                          <PrimaryAction
                            tone="quiet"
                            label="Remove"
                            icon={<X size={13} />}
                            title={`Remove "${t.name}" from your tools`}
                            testId={`suite-remove-${t.name}`}
                            disabled={isAdding}
                            onClick={() => void remove(t.name)}
                          />
                        ) : (
                          <PrimaryAction
                            label={
                              isAdding ? (
                                <LoaderInline label={stale ? "Updating…" : "Adding…"} />
                              ) : stale ? (
                                "Update"
                              ) : (
                                "Add"
                              )
                            }
                            icon={
                              isAdding ? undefined : stale ? (
                                <RefreshCw size={13} />
                              ) : (
                                <Plus size={13} />
                              )
                            }
                            disabled={isAdding}
                            title={
                              stale
                                ? `Replace the saved "${t.name}" with this command`
                                : `Add "${t.name}"`
                            }
                            testId={`suite-add-${t.name}`}
                            onClick={() => {
                              setPendingError(null);
                              setPending(planForSuite(t, stale));
                            }}
                          />
                        )}
                      </div>

                      {/* Command tucked away — default closed, jargon last. */}
                      <details className="group mt-2.5">
                        <summary className="flex cursor-pointer select-none items-center gap-1 text-[11px] font-medium text-zinc-500 transition-colors hover:text-zinc-300 [&::-webkit-details-marker]:hidden">
                          <ChevronRight
                            size={12}
                            className="transition-transform duration-200 group-open:rotate-90"
                          />
                          Show command
                          <span className="ml-1 font-mono text-zinc-600">{t.name}</span>
                        </summary>
                        <div className="mt-2 space-y-2">
                          <ArgvChips argv={t.command} />
                          {t.parameters.length > 0 && <ParamChips params={t.parameters} />}
                        </div>
                      </details>
                    </div>
                  );
                })}

                {/* THE HOLE IN THE LAST ROW (review §4: "Fill the hole in the
                    last row (8 items in 3 columns)… or a 'Create custom tool'
                    tile so the grid does not look unfinished"). It is a real
                    door, not filler: it jumps to the authoring card below. */}
                {!filtersActive(filters) && (
                  <a
                    href="#teach"
                    data-testid="create-custom-tile"
                    className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-white/[0.10] p-4 text-center transition-colors hover:border-accent/40 hover:bg-white/[0.03]"
                  >
                    <span className="grid h-9 w-9 place-items-center rounded-full border border-white/[0.10] text-accent-soft">
                      <Plus size={16} aria-hidden />
                    </span>
                    <span className="text-sm font-semibold text-zinc-200">
                      Create your own
                    </span>
                    <span className="text-[11.5px] leading-relaxed text-zinc-500">
                      Describe a command in plain language and Iron Jarvis builds it.
                    </span>
                  </a>
                )}
              </div>
            );
          })()}
        </Card>
      </Reveal>

      {/* ------------------------------------------------------------------ */}
      {/*  MCP — plug-in tool servers                                        */}
      {/* ------------------------------------------------------------------ */}
      <Reveal>
        {/* ?focus=packs lands here — Card doesn't forward refs, so wrap it. */}
        <div ref={packsFocusRef}>
        <Card
          title={`Extensions${servers.length ? ` · ${servers.length} connected` : ""}`}
          icon={<Plug size={15} />}
        >
          {/* "Plug-ins (MCP)" was insider jargon on a first-run screen (review
              §2). v1.353.0 (G42): the parenthetical "(MCP)" went too; the
              blurb states the one thing that decides whether an extension
              will even start, in plain words, and that the page says so. */}
          <p className="mb-3 text-sm text-zinc-400">
            <span className="text-zinc-300">Extensions</span> add abilities from other
            apps and services (repositories, memory, cloud apps; files and folders are
            built in), and you choose the folder or account when you enable one. Some
            need a free helper program (Node.js).
            We&apos;ll tell you if one is missing.
          </p>

          {mcpOk && (
            <div className="mb-3">
              <SuccessNote>{mcpOk}</SuccessNote>
            </div>
          )}
          {mcpWarn && (
            <div className="mb-3" data-testid="mcp-add-warn">
              <WarnNote>{mcpWarn}</WarnNote>
            </div>
          )}
          {mcpError && (
            <div className="mb-3">
              <ErrorNote>{mcpError}</ErrorNote>
            </div>
          )}

          {/* PERMISSIONS (review §3). The essay that used to sit inline is
              behind the global switch's confirm step; the per-extension
              grants the daemon has always supported are now visible and
              revocable by name instead of being folded into one checkbox that
              read as global. See PermissionsPanel for the conflation bug this
              fixes. */}
          <div className="mb-4">
            <PermissionsPanel
              rows={servers.map((sv) => ({
                name: sv.name,
                autoApprove: Boolean(sv.auto_approve),
                tools: sv.tools_loaded ?? 0,
                quarantined: quarantinedOf(sv),
              }))}
              globalOn={mcpData?.auto_approve_global ?? false}
              busyKey={
                autoApproveBusy
                  ? "global"
                  : mcpBusy && (mcpBusy.startsWith("auto:") || mcpBusy.startsWith("trust:"))
                    ? mcpBusy
                    : null
              }
              onTrustTool={(server, tool) => void trustTool(server, tool)}
              onToggleServer={(name, next) => {
                const sv = servers.find((x) => x.name === name);
                if (sv) void toggleAutoApprove({ ...sv, auto_approve: !next });
              }}
              onSetGlobal={(next) => void saveAutoApprove(next)}
            />
          </div>

          {/* VERIFY SETUP (review §8) — the demo server is a diagnostic, so it
              is an action at the top of Extensions rather than a card sitting
              between Long-term memory and Git repositories as though it were a
              capability of its own. */}
          {(() => {
            const probe = catalog.find((e) => e.id === VERIFY_PACK_ID);
            if (!probe) return null;
            const on = serverNames.has(probe.id);
            return (
              <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-white/[0.06] bg-white/[0.015] px-3.5 py-2.5">
                <Lightbulb size={14} className="shrink-0 text-accent-soft/80" aria-hidden />
                <span className="min-w-0 flex-1 text-[12.5px] leading-relaxed text-zinc-400">
                  Not sure extensions work on this machine?{" "}
                  <span className="text-zinc-300">{probe.name}</span> connects a
                  harmless test server and reports what loaded.
                </span>
                <button
                  type="button"
                  data-testid="verify-setup"
                  disabled={mcpBusy === probe.id || mcpBusy === `del:${probe.id}`}
                  onClick={() => {
                    if (on) {
                      void removeServer(probe.id);
                      return;
                    }
                    setPendingError(null);
                    setPending(planForPack(probe));
                  }}
                  className="shrink-0 rounded-lg border border-white/10 px-2.5 py-1 text-[11.5px] font-medium text-zinc-300 transition-colors hover:border-accent/40 hover:text-accent-soft disabled:opacity-50"
                >
                  {mcpBusy === probe.id ? "…" : on ? "Remove test server" : "Verify setup"}
                </button>
              </div>
            );
          })()}

          {/* Catalog grid ------------------------------------------------- */}
          {catError ? (
            <p className="text-[13px] text-zinc-600">
              Extension list unavailable{catError.status !== 0 ? ` — ${catError.message}` : ""}.
            </p>
          ) : catalog.length === 0 ? (
            <p className="text-[13px] text-zinc-600">No extensions to show.</p>
          ) : (
            (() => {
              // The verify-setup probe has its own action above; it is a
              // diagnostic, not a capability, and listing it beside Long-term
              // memory invited people to install a demo and wonder what it gave
              // them (review §8).
              const listed = catalog.filter((e) => e.id !== VERIFY_PACK_ID);
              const shown = listed.filter((e) =>
                matchesFilters({
                  kind: "extension",
                  id: e.id,
                  title: e.name,
                  text: e.description,
                  caps: packCaps(e.id),
                  added: serverNames.has(e.id),
                  needs: e.needs,
                }),
              );
              if (shown.length === 0) {
                return (
                  <p className="text-[13px] text-zinc-500">
                    No extensions match this filter.
                  </p>
                );
              }
              // ENABLED FIRST (review §1, §8). The old grid split by
              // reference/integration, which is a provenance question the
              // source chip now answers on the card — and it buried the one
              // connected extension among five uninstalled ones.
              const on = shown.filter((e) => serverNames.has(e.id));
              const off = shown.filter((e) => !serverNames.has(e.id));
              return (
                <div className="space-y-5">
                  {on.length > 0 && (
                    <div data-testid="packs-enabled">
                      <div className="mb-2">
                        <SectionLabel>Enabled · {on.length}</SectionLabel>
                      </div>
                      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                        {on.map(catalogCard)}
                      </div>
                    </div>
                  )}
                  {off.length > 0 && (
                    <div data-testid="packs-available">
                      <div className="mb-2">
                        <SectionLabel>Available to add · {off.length}</SectionLabel>
                      </div>
                      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                        {off.map(catalogCard)}
                      </div>
                    </div>
                  )}
                </div>
              );
            })()
          )}

          {/* v1.343.0: ADD A PACK, three doors into one preview list (paste a
              config, import from your other apps, an HTTP server). v1.354.0
              integrate: named "Add a tool" like the tile and menu that open it
              (/tools?focus=add), and the deep-link target. */}
          <div ref={addFocusRef} data-testid="tools-add-area">
            <div className="mb-2.5 mt-6">
              <SectionLabel>Add a tool</SectionLabel>
            </div>
            <AddPack onAdded={() => void reloadServers()} />
          </div>

          <div className="mb-2.5 mt-6">
            <SectionLabel>Add from npm, PyPI or GitHub</SectionLabel>
          </div>
          <p className="mb-2 text-[13px] text-zinc-500">
            Paste an npm package (e.g.{" "}
            {/* v1.353.0 (G43): an example that installs. It named the Slack
                reference server, which npm marks "no longer supported". */}
            <code className="rounded bg-black/40 px-1 py-0.5 font-mono text-[11px] text-accent-soft/90">
              @modelcontextprotocol/server-filesystem
            </code>
            ) or a GitHub repo (e.g.{" "}
            <code className="rounded bg-black/40 px-1 py-0.5 font-mono text-[11px] text-accent-soft/90">
              owner/repo
            </code>
            ). Iron Jarvis builds the launch command for you — nothing runs until you
            review it and hit Connect.
          </p>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              value={srcInput}
              onChange={(e) => setSrcInput(e.target.value)}
              placeholder="@scope/package · owner/repo · https://github.com/owner/repo"
              className="field font-mono"
              data-testid="npm-door-input"
            />
            <select
              value={srcLauncher}
              onChange={(e) => setSrcLauncherPick(e.target.value as Launcher)}
              aria-label="Launcher"
              data-testid="npm-door-launcher"
              className="field sm:w-56"
              disabled={Boolean(srcBuilt?.github)}
              title={srcBuilt?.github ? "A GitHub repo is fetched and run by npx." : undefined}
            >
              {LAUNCHER_CHOICES.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>
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
                <span className="font-mono text-sm font-semibold text-zinc-100" data-testid="npm-door-name">
                  {safeName(srcName.trim() || srcBuilt.name)}
                </span>
                {srcBuilt.github && <Badge value="from GitHub" tone="violet" />}
              </div>
              <p className="mt-2 text-[12px] text-zinc-500">
                This is the command it will run when you Connect:
              </p>
              <div className="mt-1" data-testid="npm-door-argv">
                <ArgvChips argv={[srcBuilt.command, ...srcBuilt.args]} />
              </div>
              {srcBuilt.github && (
                <p className="mt-2 text-[11px] text-zinc-600">
                  GitHub repos launch via npx. If it&apos;s a Python server instead, add
                  it above by its package name and pick uvx.
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
                  data-testid="npm-door-custom-name"
                  className="field px-2 py-1.5 font-mono text-xs"
                />
              </label>
              <label className="mt-2 block">
                <span className="mb-1 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  Settings it needs (optional)
                </span>
                <textarea
                  value={srcEnvText}
                  onChange={(e) => setSrcEnvText(e.target.value)}
                  rows={2}
                  placeholder="NAME=value, one per line (e.g. API_TOKEN=...)"
                  title="Passed to the extension as environment variables"
                  className="field resize-y font-mono text-xs"
                />
                <span
                  data-testid="mcp-env-vault-hint"
                  className="mt-1 block text-[11px] leading-relaxed text-zinc-500"
                >
                  {SECRET_ENV_HINT}
                </span>
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
                      <Plug size={14} /> Connect this extension
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
                  <span className="block text-[11px] leading-relaxed text-zinc-500">
                    {SECRET_ENV_HINT}
                  </span>
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
                      <Plus size={14} /> Connect this extension
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
              Connected extensions{servers.length ? ` · ${servers.length}` : ""}
            </SectionLabel>
          </div>
          {mcpLoading && !mcpData ? (
            <SkeletonRows rows={2} />
          ) : servers.length === 0 ? (
            <p className="text-[13px] text-zinc-600">
              No extensions connected yet — add one from the list above.
            </p>
          ) : (
            <div className="space-y-2">
              {servers.map((s) => {
                const loaded = s.tools_loaded ?? 0;
                const names = s.tool_names ?? [];
                const testing = mcpTestBusy === s.name;
                const test = mcpTests[s.name];
                // v1.342.0: the row reads the pack's load record. The state
                // is said in words; the amber line carries the plain cause,
                // the fix and the folded last output; Retry shows for every
                // failed or failing pack AND for a pack holding zero tools
                // (today's rule needed an error text, so a pack that loaded
                // nothing without one had no way back).
                const state = packState(s);
                // v1.356.0 (G78): switched off — kept with its settings and
                // keys, never loaded, so never "broken" and never a Retry.
                const off = state === "off";
                const failures = typeof s.failures === "number" && s.failures > 0 ? s.failures : 0;
                const broken = state === "failed" || state === "failing";
                const problem = !off && (broken || !!s.last_error);
                const showRetry = !off && (broken || loaded === 0);
                const stateWords =
                  off
                    ? PACK_OFF_WORD
                    : state === "starting"
                    ? "Starting"
                    : state === "failed"
                      ? "Didn’t start"
                      : state === "failing"
                        ? `Failing: ${failures} call${failures === 1 ? "" : "s"}`
                        : `${loaded} tool${loaded === 1 ? "" : "s"}`;
                const stateTone =
                  state === "ok" ? (loaded > 0 ? "green" : "slate") : broken ? "amber" : "slate";
                const switching = mcpBusy === `switch:${s.name}`;
                const cause = s.reason || s.last_error || "";
                const envKeys = envKeysOf(s);
                const secretKeys = secretKeysOf(s);
                const retryButton = (
                  <button
                    type="button"
                    data-testid={`mcp-retry-${s.name}`}
                    onClick={() => void retryServer(s.name)}
                    disabled={testing}
                    className="inline-flex items-center gap-1 rounded-md border border-amber-400/30 bg-amber-400/[0.08] px-2 py-0.5 text-[11px] font-medium text-amber-100 transition-colors hover:bg-amber-400/[0.14] disabled:opacity-50"
                  >
                    <RefreshCw size={11} /> Retry
                  </button>
                );
                return (
                  <div
                    key={s.name}
                    // v1.354.0: the add area's "Open its row" finds the row
                    // by this (lib/addPackWords.packRowParts).
                    data-pack-row={s.name}
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
                            value={stateWords}
                            tone={stateTone}
                            variant="calm"
                            keepCase
                            data-testid={`mcp-state-${s.name}`}
                            title={
                              off
                                ? PACK_OFF_LINE
                                : state === "failing"
                                ? "Loaded, but its calls fail. It is restarted on the next call."
                                : state === "starting"
                                  ? "Starting this extension…"
                                  : undefined
                            }
                          />
                          {/* v1.342.0: KEY names only; the GET carries no
                              values, and the vault's keys are named apart.
                              v1.356.0 (G74): counted as "settings" (it read
                              "N env"); the names stay in the tooltip. */}
                          {envKeys.length > 0 && (
                            <Badge
                              value={`${envKeys.length} setting${envKeys.length === 1 ? "" : "s"}`}
                              tone="violet"
                              keepCase
                              data-testid={`mcp-env-keys-${s.name}`}
                              title={envKeys.join(", ")}
                            />
                          )}
                          {secretKeys.length > 0 && (
                            <Badge
                              value={`${secretKeys.length} in vault`}
                              tone="violet"
                              keepCase
                              data-testid={`mcp-secret-keys-${s.name}`}
                              title={`Kept in the encrypted vault: ${secretKeys.join(", ")}`}
                            />
                          )}
                        </div>
                        {/* v1.356.0 (G74): "Ask before each use" is a labelled
                            switch (it was an "auto-approve on/off" pill that
                            read like a status). On = agents ask before each
                            call; off = this extension runs without asking.
                            The daemon's resolver is built at boot, so the
                            change says, in sight, that it applies after a
                            restart. */}
                        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-zinc-400">
                          <PackSwitch
                            size="sm"
                            on={!s.auto_approve}
                            label={`Ask before each use of ${s.name}`}
                            busy={mcpBusy === `auto:${s.name}`}
                            testId={`mcp-ask-${s.name}`}
                            title={
                              s.auto_approve
                                ? "Off: autonomous agents may run its tools without asking. Switch on to require your approval again."
                                : "On: agents ask before each of its tools runs. Switch off to let it run without asking."
                            }
                            onChange={() => void toggleAutoApprove(s)}
                          />
                          <span>Ask before each use</span>
                          {/* v1.356.0 ship fix: the switch is per extension, but
                              "run without asking" is one shared rule — the
                              global setting, or ANY switched-on extension set
                              to run without asking, lets every extension run
                              unasked. Say so beside a switch that reads On. */}
                          <span
                            className="text-zinc-500"
                            data-testid={`mcp-ask-note-${s.name}`}
                            title={
                              autoApprove && !s.auto_approve
                                ? "Run app tools without asking is on (in Settings > Permissions, or another extension is set to run without asking), so agents do not ask for this one either."
                                : undefined
                            }
                          >
                            {autoApprove && !s.auto_approve
                              ? " · not in effect right now: app tools are set to run without asking"
                              : " · changes apply after a restart"}
                          </span>
                        </div>
                        {/* v1.356.0 (G74): the launch line is folded under
                            "Show command" (an address: "Show address"), like
                            the catalog cards; a first-time reader never meets
                            a raw command or transport name. */}
                        <details className="group mt-1" data-testid={`mcp-launch-fold-${s.name}`}>
                          <summary className="flex cursor-pointer select-none items-center gap-1 text-[11px] font-medium text-zinc-500 transition-colors hover:text-zinc-300 [&::-webkit-details-marker]:hidden">
                            <ChevronRight
                              size={12}
                              className="transition-transform duration-200 group-open:rotate-90 motion-reduce:transition-none"
                            />
                            {s.transport === "http" || s.transport === "sse" ? "Show address" : "Show command"}
                          </summary>
                          <div className="mt-1 overflow-x-auto">
                            {/* v1.343.0: an http/sse pack has no command line; its
                                row says the URL instead. v1.356.0 (G74, fix
                                round): opened, the fold reads the address
                                (an older server says "(older servers)"), never
                                "HTTP"/"SSE" — the protocol rides the title. */}
                            <code
                              className="whitespace-pre font-mono text-[11px] text-zinc-500"
                              data-testid={`mcp-launch-${s.name}`}
                              title={
                                s.transport === "http" || s.transport === "sse"
                                  ? addressLine(s.transport, s.url).title
                                  : undefined
                              }
                            >
                              {s.transport === "http" || s.transport === "sse"
                                ? addressLine(s.transport, s.url).text
                                : [s.command, ...(s.args ?? [])].join(" ")}
                            </code>
                          </div>
                        </details>
                        {/* v1.356.0 (G78): a switched-off pack says what off
                            means and that nothing is lost; no Retry. */}
                        {off && (
                          <p
                            data-testid={`mcp-off-${s.name}`}
                            className="mt-1.5 text-[12px] text-zinc-400"
                          >
                            {PACK_OFF_LINE}
                          </p>
                        )}
                        {/* v1.229.0 (audit U4): the REASON a pack holds no tools.
                            "0 tools loaded" alone read as a fact of life; the
                            daemon had the exception text all along. */}
                        {problem && (
                          <div
                            data-testid={`mcp-last-error-${s.name}`}
                            className="mt-1.5 flex flex-wrap items-center gap-2 text-[12px] text-amber-200"
                          >
                            {/* v1.256.0 (R-02): the PLAIN-WORDS cause leads when the
                                daemon classified one. "FileNotFoundError: [WinError 2]
                                The system cannot find the file specified" is accurate
                                and names nothing anyone can act on; the raw text stays
                                below it, because a bug report still needs it. */}
                            <span className="min-w-0 break-all">
                              {state === "failing"
                                ? `Failing: ${cause || "its calls fail"}`
                                : `Didn’t start: ${cause || "it gave no reason"}`}
                              {/[.!?]$/.test(cause.trim()) ? "" : "."}
                            </span>
                            {/* v1.356.0 (G77): what a broken pack costs. */}
                            <span data-testid={`mcp-consequence-${s.name}`}>{PACK_FAILING_CONSEQUENCE}</span>
                            {retryButton}
                            {/* v1.354.0: the add area's outcome offers this link
                                beside a Node.js reason; the row says it too. */}
                            {mentionsNode(`${s.reason ?? ""} ${s.fix ?? ""} ${s.last_error ?? ""}`) && (
                              <a
                                href={RUNTIME_HELP.Node.href}
                                target="_blank"
                                rel="noreferrer"
                                data-testid={`mcp-install-node-${s.name}`}
                                className="font-medium text-accent-soft underline underline-offset-2"
                              >
                                Install Node.js
                              </a>
                            )}
                            {s.fix && (
                              <span
                                data-testid={`mcp-fix-${s.name}`}
                                className="w-full text-[11px] text-amber-100/80"
                              >
                                {s.fix}
                              </span>
                            )}
                            {s.reason && s.last_error && s.reason !== s.last_error && (
                              <code className="w-full whitespace-pre-wrap break-all font-mono text-[11px] text-zinc-500">
                                {s.last_error}
                              </code>
                            )}
                            <LastOutput tail={s.stderr_tail} testId={`mcp-stderr-${s.name}`} />
                          </div>
                        )}
                        {!problem && showRetry && state !== "starting" && (
                          <div
                            data-testid={`mcp-no-tools-${s.name}`}
                            className="mt-1.5 flex flex-wrap items-center gap-2 text-[12px] text-zinc-400"
                          >
                            <span>No tools loaded.</span>
                            {retryButton}
                          </div>
                        )}
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        {/* v1.356.0 (G78): On / Off without losing it. Off
                            takes its tools out of every chat and agent at
                            once and keeps its settings and keys; on loads it
                            again with nothing typed. */}
                        <PackSwitch
                          on={!off}
                          label={packSwitchLabel(s.name)}
                          title={packSwitchTitle(!off)}
                          busy={switching}
                          testId={`mcp-switch-${s.name}`}
                          onChange={(next) => void setPackOn(s, next)}
                        />
                        {/* v1.343.0: Edit opens the inline editor below the
                            row; it is prefilled from THIS row (names only). */}
                        <button
                          type="button"
                          data-testid={`mcp-edit-${s.name}`}
                          onClick={() => setEditing((e) => (e === s.name ? null : s.name))}
                          aria-pressed={editing === s.name}
                          title={`Edit "${s.name}"`}
                          className="btn-soft btn-sm"
                        >
                          <Pencil size={12} /> Edit
                        </button>
                        <button
                          type="button"
                          onClick={() => void testServer(s.name)}
                          disabled={testing}
                          title={`Test "${s.name}" — connect now and list its tools`}
                          className="btn-soft btn-sm"
                        >
                          {testing ? (
                            <LoaderInline label="Testing…" />
                          ) : (
                            <>
                              <Radio size={12} /> Test
                            </>
                          )}
                        </button>
                        {/* v1.356.0 (G74): "Remove", the same word as the
                            catalog card and the Directory (it read Delete
                            here, Disconnect there). */}
                        <ConfirmButton
                          onConfirm={() => removeServer(s.name)}
                          label="Remove"
                          title={`Remove "${s.name}" and its saved settings (to pause it instead, switch it off)`}
                        />
                      </div>
                    </div>

                    {editing === s.name && (
                      <EditPack
                        key={s.name}
                        row={s as unknown as Record<string, unknown>}
                        onSaved={(res) => onPackEdited(s.name, res)}
                        onCancel={() => setEditing(null)}
                      />
                    )}

                    {/* loaded tool names, as chips */}
                    {names.length > 0 && (
                      <div className="mt-2.5 flex flex-wrap gap-1">
                        {names.map((n) => {
                          // v1.299.0: a quarantined tool wears amber here too;
                          // the Trust button lives in the Permissions panel.
                          const held = quarantinedOf(s).includes(n);
                          return (
                            <span
                              key={n}
                              data-quarantined={held ? "1" : undefined}
                              title={held ? "New since last load — asks until you trust it" : undefined}
                              className={`rounded-md border px-1.5 py-0.5 font-mono text-[11px] ${
                                held
                                  ? "border-amber-400/30 bg-amber-400/[0.08] text-amber-200"
                                  : "border-white/[0.06] bg-white/[0.03] text-zinc-300"
                              }`}
                            >
                              {n}
                            </span>
                          );
                        })}
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
                          // v1.342.0: a failed Test says the REAL load error,
                          // the plain cause, the next step and the pack's last
                          // output, so the user does not read "advertised no
                          // tools" for a pack that died on a missing key.
                          <div data-testid={`mcp-test-fail-${s.name}`} className="space-y-1.5">
                            <ErrorNote>{test.error || test.reason || "Test failed."}</ErrorNote>
                            {test.reason && test.reason !== test.error && (
                              <p className="text-[12px] text-amber-200">{test.reason}</p>
                            )}
                            {test.fix && (
                              <p
                                data-testid={`mcp-test-fix-${s.name}`}
                                className="text-[11px] text-amber-100/80"
                              >
                                {test.fix}
                              </p>
                            )}
                            <LastOutput
                              tail={test.stderr_tail}
                              testId={`mcp-test-stderr-${s.name}`}
                            />
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </Card>
        </div>
      </Reveal>

      {/* ------------------------------------------------------------------ */}
      {/*  Manual tool builder (advanced) — the original hand-rolled form    */}
      {/* ------------------------------------------------------------------ */}
      <Reveal>
        {/* `id` is the "Create your own" tile's target in the built-in grid. */}
        <Card title="Teach Iron Jarvis something new" icon={<Sparkles size={15} />}>
        <span id="teach" className="sr-only" />
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
      {/*  Capability requests (v1.178.0) — the other direction of the hero  */}
      {/*  above: there YOU ask for a tool, here an AGENT does. It sits      */}
      {/*  directly under it for that reason, and above "Your tools" because */}
      {/*  a pending request is work waiting on the user, not reference.     */}
      {/*  No <Reveal> wrapper: the card carries its own, so a daemon        */}
      {/*  without /capability/proposals leaves no empty gap behind.         */}
      {/* ------------------------------------------------------------------ */}
      <ProposalsCard />

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
                        {/* A saved command that pastes a parameter into a shell
                            script is an injection (v1.192.0). The page cannot
                            fix a stored record, but it must not stay quiet
                            about one — this reaches user-written and
                            model-generated tools too, not just the gallery. */}
                        {interpolatesIntoShell(tool.command) && (
                          <span
                            title="This command pastes a parameter value into a shell script, so a value containing a quote or a ';' can run commands of its own. Re-add it from Ready-made tools, or delete it."
                            className="inline-flex items-center gap-1 rounded-md border border-amber-500/30 bg-amber-500/[0.1] px-1.5 py-0.5 text-[11px] font-medium text-amber-300"
                          >
                            <ShieldAlert size={11} /> value goes into a shell
                          </span>
                        )}
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

      {/* THE TRUST MOMENT (review §6). Every Add on this page opens this —
          built-in and extension alike — so the commitment step is the same
          shape whichever depth the user picked. */}
      {pending && (
        <EnableDialog
          plan={pending.plan}
          busy={pendingBusy}
          error={pendingError}
          onCancel={() => {
            setPending(null);
            setPendingError(null);
          }}
          onEnable={(values, auto) => void confirmEnable(values, auto)}
        />
      )}
    </PageShell>
  );
}
