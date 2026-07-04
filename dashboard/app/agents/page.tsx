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
} from "lucide-react";
import { post, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import type { AgentsResponse, SessionView, ModelOption } from "@/lib/types";
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
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { shortId } from "@/lib/format";

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

/** A stable key for a {provider, model} pair used as the <select> value. */
const modelKey = (m: ModelOption) => `${m.provider}|${m.model}`;

export default function AgentsPage() {
  const { data, error, loading, reload } = useApi<AgentsResponse>("/agents");
  const { data: modelsData } = useApi<{ models: ModelOption[] }>("/models");
  const offline = error && error.status === 0;
  const builtin = data?.builtin ?? [];
  const dynamic = data?.dynamic ?? [];
  const allNames = [...builtin, ...dynamic.map((d) => d.name)];
  const models = modelsData?.models ?? [];

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
                  <li
                    key={d.name}
                    className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <Bot size={14} className="text-violet-300" />
                      <span className="text-sm font-semibold text-zinc-100">{d.name}</span>
                      {d.model && (
                        <span className="inline-flex items-center gap-1 rounded-md border border-accent/30 bg-accent/[0.08] px-1.5 py-0.5 font-mono text-[10px] text-accent-soft">
                          <Cpu size={10} />
                          {d.provider ? `${d.provider} · ${d.model}` : d.model}
                        </span>
                      )}
                    </div>
                    {d.description && (
                      <p className="mt-0.5 pl-6 text-xs text-zinc-500">{d.description}</p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
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
                <div className="grid max-h-44 grid-cols-2 gap-1.5 overflow-y-auto rounded-xl border border-white/[0.06] bg-ink-900/50 p-2.5 sm:grid-cols-3">
                  {KNOWN_TOOLS.map((t) => {
                    const on = tools.includes(t);
                    return (
                      <label
                        key={t}
                        className={`flex cursor-pointer items-center gap-1.5 rounded-lg border px-2 py-1 text-[11px] font-mono transition-colors ${
                          on
                            ? "border-accent/40 bg-accent/[0.1] text-accent-soft"
                            : "border-transparent text-zinc-400 hover:bg-white/[0.04]"
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={on}
                          onChange={() => toggleTool(t)}
                          className="h-3 w-3 accent-[#22d3ee]"
                        />
                        {t}
                      </label>
                    );
                  })}
                </div>
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
