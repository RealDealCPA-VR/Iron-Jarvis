"use client";

import { useState } from "react";
import Link from "next/link";
import {
  Bot,
  Wrench,
  Rocket,
  Plus,
  Cpu,
  ArrowUpRight,
  ShieldCheck,
  Server,
  Play,
  Pencil,
  Save,
  X,
  Power,
  Globe,
  CheckCircle2,
} from "lucide-react";
import { post, patch, del, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import type { AgentsResponse, DynamicAgent, SessionView, ModelOption } from "@/lib/types";
import {
  Card,
  Badge,
  StatusDot,
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
import { shortId } from "@/lib/format";

/* -------------------------------------------------------------------------- */
/*  Tool metadata (friendly labels for the built-ins; unknowns fall back to    */
/*  the raw name + the tool's own description from GET /tools)                  */
/* -------------------------------------------------------------------------- */

/** Fallback tool names when GET /tools hasn't loaded (offline / cold). */
const KNOWN_TOOLS = [
  "read_file",
  "write_file",
  "edit_file",
  "list_files",
  "grep",
  "shell",
  "memory_write",
  "memory_read",
  "memory_search",
  "skill_search",
  "skill_load",
  "secret_list",
  "secret_set",
  "integration_list",
  "integration_test",
  "file_search",
  "notify",
  "ltm_search",
  "ltm_append",
  "create_agent",
  "list_agents",
  "spawn_agent",
  "delegate",
];

/**
 * Friendly "what this lets the agent do" labels for the tool checkboxes.
 * Unknown names simply fall back to the raw tool name + its own description.
 */
const TOOL_LABELS: Record<string, { label: string; hint: string }> = {
  read_file: { label: "Read files", hint: "Open and read file contents" },
  write_file: { label: "Write files", hint: "Create new files on disk" },
  edit_file: { label: "Edit files", hint: "Change existing files" },
  list_files: { label: "Browse folders", hint: "See what's inside a directory" },
  grep: { label: "Search inside files", hint: "Find text across many files" },
  shell: { label: "Run commands", hint: "Execute terminal commands" },
  memory_write: { label: "Save session notes", hint: "Jot things down while working" },
  memory_read: { label: "Read session notes", hint: "Look up notes it saved" },
  memory_search: { label: "Search session notes", hint: "Find a note from earlier" },
  skill_search: { label: "Find skills", hint: "Look for a matching playbook" },
  skill_load: { label: "Use skills", hint: "Follow a skill's playbook" },
  secret_list: { label: "See stored key names", hint: "Names only — never the values" },
  secret_set: { label: "Store keys", hint: "Save API keys to the vault" },
  integration_list: { label: "See connected services", hint: "List what's hooked up" },
  integration_test: { label: "Test connections", hint: "Check a service is working" },
  file_search: { label: "Find files on disk", hint: "Search your drive for files" },
  notify: { label: "Send you notifications", hint: "Ping you via Slack, Telegram, etc." },
  ltm_search: { label: "Search long-term memory", hint: "Recall things from past sessions" },
  ltm_append: { label: "Save to long-term memory", hint: "Remember things for next time" },
  create_agent: { label: "Create new agents", hint: "Define brand-new helpers" },
  list_agents: { label: "See available agents", hint: "Know who it can call on" },
  spawn_agent: { label: "Start helper agents", hint: "Kick off another agent on a task" },
  delegate: { label: "Hand work to other agents", hint: "Assign a task, get the result back" },
  web_search: { label: "Search the web", hint: "Look things up online" },
};

/** A stable key for a {provider, model} pair used as the <select> value. */
const modelKey = (m: ModelOption) => `${m.provider}|${m.model}`;

/* -------------------------------------------------------------------------- */
/*  Local types (lib/types.ts is intentionally untouched)                      */
/* -------------------------------------------------------------------------- */

/** One tool as advertised by GET /tools (base.py::spec). */
interface ToolSpec {
  name: string;
  description: string;
  input_schema?: unknown;
}

/** One custom (user/agent-authored) tool from GET /tools/custom. */
interface CustomToolLite {
  name: string;
}

/** A picker entry — friendly label when we know the tool, raw name otherwise. */
interface ToolEntry {
  name: string;
  label: string;
  hint: string;
  /** Whether we have a hand-written friendly label (drives the mono styling). */
  friendly: boolean;
}

/** Dynamic-agent row may carry its config when the daemon includes it. */
type DynamicAgentFull = DynamicAgent & {
  system_prompt?: string;
  tools?: string[];
};

type RemoteKind = "http-task" | "openai-chat" | "openai-responses";
/** The two OpenAI dialects both carry a model id and parse an LLM reply;
 *  they differ only in the request field (`messages` vs `input`). */
const OPENAI_KINDS: RemoteKind[] = ["openai-chat", "openai-responses"];

/** A registered remote agent (GET /agents/remote). The token is NEVER returned. */
interface RemoteAgent {
  name: string;
  base_url: string;
  kind: RemoteKind;
  model?: string | null;
  timeout_s?: number | null;
  enabled?: boolean;
  /** Server hint that a bearer token is on file (value never leaves the daemon). */
  has_token?: boolean;
  description?: string;
}

function entryFor(name: string, description?: string): ToolEntry {
  const meta = TOOL_LABELS[name];
  return {
    name,
    label: meta?.label ?? name,
    hint: meta?.hint ?? description ?? "",
    friendly: Boolean(meta),
  };
}

/* -------------------------------------------------------------------------- */
/*  Tool picker — live from GET /tools + GET /tools/custom, grouped             */
/* -------------------------------------------------------------------------- */

function ToolPicker({
  builtin,
  custom,
  value,
  onToggle,
}: {
  builtin: ToolEntry[];
  custom: ToolEntry[];
  value: string[];
  onToggle: (name: string) => void;
}) {
  const renderEntry = (t: ToolEntry) => {
    const on = value.includes(t.name);
    return (
      <label
        key={t.name}
        className={`flex cursor-pointer items-start gap-2 rounded-lg border px-2 py-1.5 transition-colors ${
          on
            ? "border-accent/40 bg-accent/[0.1]"
            : "border-transparent hover:bg-white/[0.04]"
        }`}
      >
        <input
          type="checkbox"
          checked={on}
          onChange={() => onToggle(t.name)}
          className="mt-0.5 h-3 w-3 shrink-0 accent-[#22d3ee]"
        />
        <span className="min-w-0">
          <span className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
            <span
              className={`text-[11px] font-medium leading-tight ${
                t.friendly ? "" : "font-mono"
              } ${on ? "text-accent-soft" : "text-zinc-300"}`}
            >
              {t.label}
            </span>
            {t.friendly && (
              <span className="rounded bg-white/[0.06] px-1 font-mono text-[9px] leading-4 text-zinc-500">
                {t.name}
              </span>
            )}
          </span>
          {t.hint && (
            <span className="mt-0.5 block text-[10px] leading-tight text-zinc-500">
              {t.hint}
            </span>
          )}
        </span>
      </label>
    );
  };

  return (
    <div className="max-h-64 space-y-2.5 overflow-y-auto rounded-xl border border-white/[0.06] bg-ink-900/50 p-2.5">
      {custom.length > 0 && (
        <div className="mb-1">
          <SectionLabel>Custom tools · {custom.length}</SectionLabel>
        </div>
      )}
      {custom.length > 0 && (
        <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">{custom.map(renderEntry)}</div>
      )}
      {custom.length > 0 && (
        <div className="pt-1">
          <SectionLabel>Built-in tools · {builtin.length}</SectionLabel>
        </div>
      )}
      <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">{builtin.map(renderEntry)}</div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Dynamic-agent row — Edit (system prompt / tools) + Delete                   */
/* -------------------------------------------------------------------------- */

function DynamicAgentRow({
  agent,
  builtin,
  custom,
  onChanged,
}: {
  agent: DynamicAgentFull;
  builtin: ToolEntry[];
  custom: ToolEntry[];
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [prompt, setPrompt] = useState(agent.system_prompt ?? "");
  const [tools, setTools] = useState<string[]>(agent.tools ?? []);
  // Only PATCH the tool set if the user actually touched it — otherwise a row
  // that never carried its tools would blank them on save.
  const [toolsDirty, setToolsDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggleTool(t: string) {
    setToolsDirty(true);
    setTools((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]));
  }

  function startEdit() {
    setPrompt(agent.system_prompt ?? "");
    setTools(agent.tools ?? []);
    setToolsDirty(false);
    setError(null);
    setEditing(true);
  }

  async function save() {
    setBusy(true);
    setError(null);
    // Send only what the user changed: an empty prompt keeps the current one,
    // and tools ride along only when the picker was touched.
    const body: Record<string, unknown> = {};
    if (prompt.trim()) body.system_prompt = prompt.trim();
    if (toolsDirty) body.tools = tools;
    try {
      await patch(`/agents/${encodeURIComponent(agent.name)}`, body);
      setEditing(false);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    try {
      await del(`/agents/${encodeURIComponent(agent.name)}`);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <li className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Bot size={14} className="text-violet-300" />
        <span className="text-sm font-semibold text-zinc-100">{agent.name}</span>
        {agent.model && (
          <span className="inline-flex items-center gap-1 rounded-md border border-accent/30 bg-accent/[0.08] px-1.5 py-0.5 font-mono text-[10px] text-accent-soft">
            <Cpu size={10} />
            {agent.provider ? `${agent.provider} · ${agent.model}` : agent.model}
          </span>
        )}
        <span className="ml-auto flex items-center gap-2">
          {!editing && (
            <button
              type="button"
              onClick={startEdit}
              title={`Edit "${agent.name}"`}
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-xs font-medium text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft"
            >
              <Pencil size={13} /> Edit
            </button>
          )}
          <ConfirmButton
            onConfirm={remove}
            label="Delete"
            title={`Delete agent "${agent.name}"`}
          />
        </span>
      </div>
      {agent.description && !editing && (
        <p className="mt-0.5 pl-6 text-xs text-zinc-500">{agent.description}</p>
      )}

      {editing && (
        <div className="mt-3 space-y-3 border-t hairline pt-3">
          <div>
            <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
              System prompt
            </label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              placeholder={
                agent.system_prompt
                  ? "Edit the system prompt…"
                  : "Leave blank to keep the current prompt…"
              }
              className="field resize-y"
            />
          </div>
          <div>
            <div className="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
              <Wrench size={12} /> Tools{tools.length ? ` · ${tools.length}` : ""}
              {!toolsDirty && (
                <span className="font-normal normal-case tracking-normal text-zinc-600">
                  · unchanged
                </span>
              )}
            </div>
            <ToolPicker
              builtin={builtin}
              custom={custom}
              value={tools}
              onToggle={toggleTool}
            />
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={save}
              disabled={busy}
              className="btn-accent py-1.5 text-xs"
            >
              {busy ? <LoaderInline label="Saving…" /> : <><Save size={14} /> Save changes</>}
            </button>
            <button
              type="button"
              onClick={() => {
                setEditing(false);
                setError(null);
              }}
              className="btn-ghost py-1.5 text-xs"
            >
              <X size={14} /> Cancel
            </button>
          </div>
        </div>
      )}
      {error && <div className="mt-2"><ErrorNote>{error}</ErrorNote></div>}
    </li>
  );
}

/* -------------------------------------------------------------------------- */
/*  Remote-agent row — Test / Run / enable toggle / Delete                      */
/* -------------------------------------------------------------------------- */

function RemoteAgentRow({ agent, onChanged }: { agent: RemoteAgent; onChanged: () => void }) {
  const enabled = agent.enabled !== false;
  const [busy, setBusy] = useState<null | "test" | "run" | "toggle">(null);
  const [test, setTest] = useState<{ ok: boolean; detail: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runOpen, setRunOpen] = useState(false);
  const [task, setTask] = useState("");
  const [result, setResult] = useState<string | null>(null);

  async function runTest() {
    setBusy("test");
    setError(null);
    setTest(null);
    try {
      const r = await post<{ ok?: boolean; detail?: string }>(
        `/agents/remote/${encodeURIComponent(agent.name)}/test`,
      );
      setTest({ ok: r.ok !== false, detail: r.detail ?? (r.ok !== false ? "Reachable." : "Unreachable.") });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function run(e: React.FormEvent) {
    e.preventDefault();
    if (!task.trim()) return;
    setBusy("run");
    setError(null);
    setResult(null);
    try {
      const r = await post<{ ok?: boolean; result?: string; reply?: string; detail?: string }>(
        `/agents/remote/${encodeURIComponent(agent.name)}/run`,
        { task: task.trim() },
      );
      const text = r.result ?? r.reply ?? r.detail ?? "(no output)";
      if (r.ok === false) setError(text);
      else setResult(text);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function toggle() {
    setBusy("toggle");
    setError(null);
    try {
      // POST /agents/remote upserts by name. We resend the known config with
      // the flipped flag and OMIT the token so the stored one is preserved.
      await post("/agents/remote", {
        name: agent.name,
        base_url: agent.base_url,
        kind: agent.kind,
        model: agent.model ?? "",
        timeout_s: agent.timeout_s ?? undefined,
        enabled: !enabled,
      });
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    try {
      await del(`/agents/remote/${encodeURIComponent(agent.name)}`);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <li className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-2">
        {OPENAI_KINDS.includes(agent.kind as RemoteKind) ? (
          <Globe size={14} className="text-emerald-300" />
        ) : (
          <Server size={14} className="text-sky-300" />
        )}
        <span className="text-sm font-semibold text-zinc-100">{agent.name}</span>
        <Badge value={agent.kind} tone="cyan" />
        {OPENAI_KINDS.includes(agent.kind as RemoteKind) && agent.model && (
          <span className="inline-flex items-center gap-1 rounded-md border border-accent/30 bg-accent/[0.08] px-1.5 py-0.5 font-mono text-[10px] text-accent-soft">
            <Cpu size={10} /> {agent.model}
          </span>
        )}
        {!enabled && (
          <span className="rounded-md border border-zinc-500/25 bg-zinc-500/10 px-1.5 py-0.5 text-[10px] font-medium text-zinc-400">
            disabled
          </span>
        )}
      </div>
      <div className="mt-1 overflow-x-auto pl-6">
        <code className="whitespace-pre font-mono text-[11px] text-zinc-500">
          {agent.base_url}
        </code>
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={runTest}
          disabled={busy !== null}
          className="btn-ghost py-1.5 text-xs"
        >
          {busy === "test" ? <LoaderInline label="Testing…" /> : <><CheckCircle2 size={14} /> Test</>}
        </button>
        <button
          type="button"
          onClick={() => {
            setRunOpen((v) => !v);
            setResult(null);
            setError(null);
          }}
          disabled={!enabled}
          title={enabled ? "Send this agent a task" : "Enable the agent to run it"}
          className="btn-ghost py-1.5 text-xs"
        >
          <Play size={14} /> Run
        </button>
        <button
          type="button"
          onClick={toggle}
          disabled={busy !== null}
          title={enabled ? "Disable this remote agent" : "Enable this remote agent"}
          className="btn-ghost py-1.5 text-xs"
        >
          {busy === "toggle" ? (
            <LoaderInline label="…" />
          ) : (
            <>
              <Power size={14} /> {enabled ? "Disable" : "Enable"}
            </>
          )}
        </button>
        <ConfirmButton
          onConfirm={remove}
          label="Delete"
          title={`Remove remote agent "${agent.name}"`}
          className="ml-auto"
        />
      </div>

      {runOpen && (
        <form onSubmit={run} className="mt-2.5 space-y-2">
          <textarea
            value={task}
            onChange={(e) => setTask(e.target.value)}
            rows={2}
            placeholder="What should this remote agent do?"
            className="field resize-y"
          />
          <button
            type="submit"
            disabled={busy !== null || !task.trim()}
            className="btn-accent py-1.5 text-xs"
          >
            {busy === "run" ? <LoaderInline label="Running…" /> : <><Rocket size={14} /> Send task</>}
          </button>
        </form>
      )}

      {result && (
        <div className="mt-2.5 overflow-x-auto rounded-lg border border-white/[0.06] bg-ink-900/60 px-3 py-2.5">
          <pre className="whitespace-pre-wrap break-words font-mono text-[12px] text-zinc-300">
            {result}
          </pre>
        </div>
      )}
      {test && (
        <div className="mt-2.5">
          {test.ok ? <SuccessNote>{test.detail}</SuccessNote> : <ErrorNote>{test.detail}</ErrorNote>}
        </div>
      )}
      {error && <div className="mt-2.5"><ErrorNote>{error}</ErrorNote></div>}
    </li>
  );
}

/* -------------------------------------------------------------------------- */
/*  Page                                                                       */
/* -------------------------------------------------------------------------- */

export default function AgentsPage() {
  const { data, error, loading, reload } = useApi<AgentsResponse>("/agents");
  const { data: modelsData } = useApi<{ models: ModelOption[] }>("/models");
  const { data: toolsData } = useApi<{ tools: ToolSpec[] }>("/tools");
  const { data: customToolsData } = useApi<{ tools: CustomToolLite[] }>("/tools/custom");
  const { data: remoteData, reload: reloadRemote } = useApi<{
    remotes?: RemoteAgent[];
    agents?: RemoteAgent[];
  }>("/agents/remote");

  const offline = error && error.status === 0;
  const builtin = data?.builtin ?? [];
  const dynamic = (data?.dynamic ?? []) as DynamicAgentFull[];
  const allNames = [...builtin, ...dynamic.map((d) => d.name)];
  const models = modelsData?.models ?? [];
  const remotes = remoteData?.remotes ?? remoteData?.agents ?? [];

  // Merge GET /tools (all registered tools + descriptions) with GET /tools/custom
  // (which names are custom) into two labelled groups for the picker. Falls back
  // to the built-in name list when /tools hasn't loaded (offline).
  const specs = toolsData?.tools ?? [];
  const customNames = new Set((customToolsData?.tools ?? []).map((t) => t.name));
  const builtinTools: ToolEntry[] =
    specs.length > 0
      ? specs.filter((s) => !customNames.has(s.name)).map((s) => entryFor(s.name, s.description))
      : KNOWN_TOOLS.map((n) => entryFor(n));
  const customTools: ToolEntry[] = specs
    .filter((s) => customNames.has(s.name))
    .map((s) => entryFor(s.name, s.description));

  // Create form
  const [name, setName] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [description, setDescription] = useState("");
  const [model, setModel] = useState(""); // "provider|model"
  const [tools, setTools] = useState<string[]>([]);
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createOk, setCreateOk] = useState<string | null>(null);

  // Spawn form
  const [spawnAgent, setSpawnAgent] = useState("");
  const [task, setTask] = useState("");
  const [spawnBusy, setSpawnBusy] = useState(false);
  const [spawnError, setSpawnError] = useState<string | null>(null);
  const [spawned, setSpawned] = useState<SessionView | null>(null);

  // Remote register form
  const [rName, setRName] = useState("");
  const [rBaseUrl, setRBaseUrl] = useState("");
  const [rKind, setRKind] = useState<RemoteKind>("http-task");
  const [rModel, setRModel] = useState("");
  const [rToken, setRToken] = useState("");
  const [rTimeout, setRTimeout] = useState("60");
  const [rBusy, setRBusy] = useState(false);
  const [rError, setRError] = useState<string | null>(null);
  const [rOk, setROk] = useState<string | null>(null);

  function toggleTool(t: string) {
    setTools((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]));
  }

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !systemPrompt.trim()) return;
    setCreateBusy(true);
    setCreateError(null);
    setCreateOk(null);
    const [provider, modelName] = model ? model.split("|") : ["", ""];
    try {
      await post("/agents", {
        name: name.trim(),
        system_prompt: systemPrompt.trim(),
        tools,
        description: description.trim(),
        provider,
        model: modelName,
      });
      setCreateOk(`Agent "${name.trim()}" created.`);
      setName("");
      setSystemPrompt("");
      setDescription("");
      setModel("");
      setTools([]);
      reload();
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setCreateBusy(false);
    }
  }

  async function spawn(e: React.FormEvent) {
    e.preventDefault();
    const agent = spawnAgent || allNames[0];
    if (!agent || !task.trim()) return;
    setSpawnBusy(true);
    setSpawnError(null);
    setSpawned(null);
    try {
      const session = await post<SessionView>(`/agents/${encodeURIComponent(agent)}/spawn`, {
        task: task.trim(),
        wait: false, // non-blocking — the result card links to the LIVE session
      });
      setSpawned(session);
      setTask("");
    } catch (err) {
      setSpawnError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSpawnBusy(false);
    }
  }

  async function registerRemote(e: React.FormEvent) {
    e.preventDefault();
    if (!rName.trim() || !rBaseUrl.trim()) return;
    setRBusy(true);
    setRError(null);
    setROk(null);
    try {
      await post("/agents/remote", {
        name: rName.trim(),
        base_url: rBaseUrl.trim(),
        kind: rKind,
        model: OPENAI_KINDS.includes(rKind) ? rModel.trim() : "",
        token: rToken.trim(), // stored encrypted, never returned
        timeout_s: Number(rTimeout) || 60,
        enabled: true,
      });
      setROk(`Remote agent "${rName.trim()}" registered.`);
      setRName("");
      setRBaseUrl("");
      setRModel("");
      setRToken("");
      setRTimeout("60");
      setRKind("http-task");
      reloadRemote();
    } catch (err) {
      setRError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setRBusy(false);
    }
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Agents"
          subtitle="Built-in and custom agents. Create a dynamic agent with a system prompt and tool set, then spawn it on a task."
        />
      </Reveal>
      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <Reveal>
        <div className="grid gap-6 lg:grid-cols-2">
          <Card title={`Built-in · ${builtin.length}`} icon={<ShieldCheck size={15} />}>
            {loading && !data ? (
              <SkeletonRows rows={3} />
            ) : builtin.length === 0 ? (
              <Empty icon={<Bot size={22} />}>No built-in agents.</Empty>
            ) : (
              <div className="flex flex-wrap gap-2">
                {builtin.map((b) => (
                  <Badge key={b} value={b} tone="cyan" />
                ))}
              </div>
            )}
          </Card>

          <Card title={`Dynamic · ${dynamic.length}`} icon={<Cpu size={15} />}>
            {loading && !data ? (
              <SkeletonRows rows={3} />
            ) : dynamic.length === 0 ? (
              <Empty icon={<Bot size={22} />}>No custom agents yet — create one below.</Empty>
            ) : (
              <ul className="space-y-2">
                {dynamic.map((d) => (
                  <DynamicAgentRow
                    key={d.name}
                    agent={d}
                    builtin={builtinTools}
                    custom={customTools}
                    onChanged={reload}
                  />
                ))}
              </ul>
            )}
          </Card>
        </div>
      </Reveal>

      {/* ------------------------------------------------------------------ */}
      {/*  Remote agents — reach an agent you run elsewhere                   */}
      {/* ------------------------------------------------------------------ */}
      <Reveal>
        <Card title={`Remote agents${remotes.length ? ` · ${remotes.length}` : ""}`} icon={<Server size={15} />}>
          <p className="mb-4 text-sm text-zinc-400">
            Reach an agent you run elsewhere — your Hermes on another machine, an
            OpenAI-compatible endpoint, etc. Register it once, then test and run it
            like any local agent.
          </p>

          <div className="grid gap-6 lg:grid-cols-2">
            {/* Register form */}
            <form onSubmit={registerRemote} className="space-y-3.5">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Name
                  </label>
                  <input
                    value={rName}
                    onChange={(e) => setRName(e.target.value)}
                    placeholder="my-hermes"
                    className="field"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Kind
                  </label>
                  <select
                    aria-label="Remote kind"
                    value={rKind}
                    onChange={(e) => setRKind(e.target.value as RemoteKind)}
                    className="field"
                  >
                    <option value="http-task">http-task (Hermes / task API)</option>
                    <option value="openai-chat">openai-chat (OpenAI chat/completions)</option>
                    <option value="openai-responses">openai-responses (OpenAI Responses API)</option>
                  </select>
                </div>
              </div>
              <div>
                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  Base URL
                </label>
                <input
                  value={rBaseUrl}
                  onChange={(e) => setRBaseUrl(e.target.value)}
                  placeholder="http://192.168.1.20:8080"
                  autoComplete="off"
                  className="field font-mono text-xs"
                />
              </div>
              {OPENAI_KINDS.includes(rKind) && (
                <div>
                  <label className="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    <Cpu size={12} /> Model
                  </label>
                  <input
                    value={rModel}
                    onChange={(e) => setRModel(e.target.value)}
                    placeholder="e.g. gpt-4o-mini / llama3"
                    autoComplete="off"
                    className="field font-mono text-xs"
                  />
                </div>
              )}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Bearer token
                  </label>
                  <input
                    type="password"
                    value={rToken}
                    onChange={(e) => setRToken(e.target.value)}
                    placeholder="optional"
                    autoComplete="off"
                    className="field font-mono text-xs"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Timeout (s)
                  </label>
                  <input
                    type="number"
                    min={1}
                    value={rTimeout}
                    onChange={(e) => setRTimeout(e.target.value)}
                    placeholder="60"
                    className="field"
                  />
                </div>
              </div>
              <p className="text-[11px] leading-relaxed text-zinc-500">
                The token is stored encrypted and never shown again.
              </p>
              <button
                type="submit"
                disabled={rBusy || !rName.trim() || !rBaseUrl.trim()}
                className="btn-accent w-full"
              >
                {rBusy ? <LoaderInline label="Registering…" /> : <><Plus size={14} /> Register remote</>}
              </button>
              {rOk && <SuccessNote>{rOk}</SuccessNote>}
              {rError && <ErrorNote>{rError}</ErrorNote>}
            </form>

            {/* Registered remotes */}
            <div>
              <div className="mb-2.5">
                <SectionLabel>Registered{remotes.length ? ` · ${remotes.length}` : ""}</SectionLabel>
              </div>
              {remotes.length === 0 ? (
                <Empty icon={<Server size={22} />}>
                  No remote agents yet — register one on the left.
                </Empty>
              ) : (
                <ul className="space-y-2.5">
                  {remotes.map((r) => (
                    <RemoteAgentRow key={r.name} agent={r} onChanged={reloadRemote} />
                  ))}
                </ul>
              )}
            </div>
          </div>
        </Card>
      </Reveal>

      <Reveal>
        <div className="grid gap-6 lg:grid-cols-2">
          <Card title="Create agent" icon={<Plus size={15} />}>
            <form onSubmit={create} className="space-y-3.5">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Name
                  </label>
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="my-agent"
                    className="field"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Description
                  </label>
                  <input
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="optional"
                    className="field"
                  />
                </div>
              </div>
              <div>
                <label className="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  <Cpu size={12} /> Model
                </label>
                <select
                  aria-label="Model"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  className="field"
                >
                  <option value="">Default (from config)</option>
                  {models.map((m) => (
                    <option key={modelKey(m)} value={modelKey(m)}>
                      {m.provider} · {m.model}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  System prompt
                </label>
                <textarea
                  value={systemPrompt}
                  onChange={(e) => setSystemPrompt(e.target.value)}
                  rows={3}
                  placeholder="You are a specialized agent that…"
                  className="field resize-y"
                />
              </div>
              <div>
                <div className="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  <Wrench size={12} /> Tools{tools.length ? ` · ${tools.length}` : ""}
                </div>
                <ToolPicker
                  builtin={builtinTools}
                  custom={customTools}
                  value={tools}
                  onToggle={toggleTool}
                />
              </div>
              <button
                type="submit"
                disabled={createBusy || !name.trim() || !systemPrompt.trim()}
                className="btn-accent w-full"
              >
                {createBusy ? <LoaderInline label="Creating…" /> : <><Plus size={14} /> Create agent</>}
              </button>
              {createOk && <SuccessNote>{createOk}</SuccessNote>}
              {createError && <ErrorNote>{createError}</ErrorNote>}
            </form>
          </Card>

          <Card title="Spawn agent" icon={<Rocket size={15} />}>
            <form onSubmit={spawn} className="space-y-3.5">
              <div>
                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  Agent
                </label>
                <select
                  aria-label="Agent"
                  value={spawnAgent}
                  onChange={(e) => setSpawnAgent(e.target.value)}
                  className="field"
                >
                  {allNames.length === 0 && <option value="">(no agents)</option>}
                  {builtin.length > 0 && (
                    <optgroup label="Built-in">
                      {builtin.map((b) => (
                        <option key={b} value={b}>
                          {b}
                        </option>
                      ))}
                    </optgroup>
                  )}
                  {dynamic.length > 0 && (
                    <optgroup label="Dynamic">
                      {dynamic.map((d) => (
                        <option key={d.name} value={d.name}>
                          {d.name}
                        </option>
                      ))}
                    </optgroup>
                  )}
                </select>
              </div>
              <div>
                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  Task
                </label>
                <textarea
                  value={task}
                  onChange={(e) => setTask(e.target.value)}
                  rows={3}
                  placeholder="What should this agent do?"
                  className="field resize-y"
                />
              </div>
              <button
                type="submit"
                disabled={spawnBusy || !task.trim() || allNames.length === 0}
                className="btn-accent w-full"
              >
                {spawnBusy ? <LoaderInline label="Spawning…" /> : <><Rocket size={14} /> Spawn</>}
              </button>
              {spawnError && <ErrorNote>{spawnError}</ErrorNote>}
              {spawned && (
                <Link
                  href={`/sessions/${spawned.id}`}
                  className="group block rounded-xl border border-accent/25 bg-accent/[0.06] px-3.5 py-3 transition-colors hover:bg-accent/[0.1]"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex min-w-0 items-center gap-2">
                      <StatusDot status={spawned.status} />
                      <span className="truncate text-sm text-zinc-100">{spawned.task}</span>
                    </span>
                    <Badge value={spawned.status} />
                  </div>
                  <div className="mt-1 flex items-center gap-2 pl-4 text-[11px] text-zinc-500">
                    <span className="font-mono">{shortId(spawned.id)}</span>
                    <span>·</span>
                    <span>{spawned.agent_type}</span>
                    <ArrowUpRight
                      size={12}
                      className="text-zinc-600 opacity-0 transition-opacity group-hover:opacity-100"
                    />
                  </div>
                </Link>
              )}
            </form>
          </Card>
        </div>
      </Reveal>
    </PageShell>
  );
}
