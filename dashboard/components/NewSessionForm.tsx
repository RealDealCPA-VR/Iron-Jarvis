"use client";

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Play, Cpu, PlugZap, ArrowRight, Paperclip } from "lucide-react";
import { post, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import type { SessionView, ModelOption, Health } from "@/lib/types";
import { ErrorNote, LoaderInline } from "./ui";
import { VoiceInput, appendDictation } from "./VoiceInput";

const AGENT_TYPES = ["builder", "supervisor", "planner", "researcher", "reviewer"];

/** Capability-spanning starter prompts shown when the task box is empty. */
const EXAMPLE_TASKS = [
  "Summarize this PDF and draft a follow-up email",
  "Read a spreadsheet and chart the totals",
  "Research a topic and write a markdown brief",
  "Search the web for a question and summarize the top results",
  "Schedule a daily 9am status check",
];

/** A stable "provider|model" key used as the <select> value. */
const optKey = (m: ModelOption) => `${m.provider}|${m.model}`;

/** Read a File as raw base64 (FileReader gives a data: URL — strip the prefix). */
function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("could not read file"));
    reader.onload = () => {
      const res = String(reader.result);
      const comma = res.indexOf(",");
      resolve(comma >= 0 ? res.slice(comma + 1) : res);
    };
    reader.readAsDataURL(file);
  });
}

/**
 * Public entry point. The inner form reads `useSearchParams`, which would force
 * the whole consuming page out of static prerendering unless it sits inside a
 * Suspense boundary — so we own that boundary here instead of touching the page.
 */
export function NewSessionForm(props: { onCreated?: () => void }) {
  return (
    <Suspense fallback={null}>
      <NewSessionFormInner {...props} />
    </Suspense>
  );
}

function NewSessionFormInner({ onCreated }: { onCreated?: () => void }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { data: modelsData } = useApi<{ models: ModelOption[] }>("/models");
  const { data: health } = useApi<Health>("/health");

  const [task, setTask] = useState("");
  const [agentType, setAgentType] = useState("builder");
  const [choice, setChoice] = useState(""); // "provider|model" or "" => default
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Deep-link support: /sessions?new=1&task=<encoded>&agent=<type>. Used by the
  // command palette ("New session") and the Templates page ("Run").
  const taskRef = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    const presetTask = searchParams.get("task");
    if (presetTask) setTask(presetTask);
    const presetAgent = searchParams.get("agent");
    if (presetAgent && AGENT_TYPES.includes(presetAgent)) setAgentType(presetAgent);
    // Preselect the saved provider|model (the <select> value is this exact key).
    const presetModel = searchParams.get("model");
    if (presetModel) setChoice(presetModel);
    if (searchParams.get("new") || presetTask) {
      // Focus (and place the caret at the end of) the task box on arrival.
      requestAnimationFrame(() => {
        const el = taskRef.current;
        if (!el) return;
        el.focus();
        const len = el.value.length;
        el.setSelectionRange(len, len);
      });
    }
    // Run once on mount; the deep-link is consumed immediately.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // File attach
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [attaching, setAttaching] = useState(false);
  const [attachedName, setAttachedName] = useState<string | null>(null);

  const models = useMemo(() => modelsData?.models ?? [], [modelsData]);

  // Which providers are actually available (from /health)?
  const availability = useMemo(() => {
    const map = new Map<string, boolean>();
    for (const p of health?.providers ?? []) map.set(p.provider, p.available);
    return map;
  }, [health]);

  const isAvailable = (m: ModelOption) => availability.get(m.provider) ?? false;

  // True when no *real* (non-mock) provider is connected — mock still works.
  const onlyMock = useMemo(() => {
    const reals = (health?.providers ?? []).filter(
      (p) => p.provider !== "mock" && p.class !== "mock",
    );
    return reals.length === 0 || reals.every((p) => !p.available);
  }, [health]);

  async function onAttach(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file
    if (!file) return;
    setAttaching(true);
    setError(null);
    try {
      const content_b64 = await readAsBase64(file);
      const res = await post<{ path: string; name: string; bytes: number }>(
        "/documents/upload",
        { filename: file.name, content_b64 },
      );
      setAttachedName(res.name);
      // Append a note so the agent can read it with read_document.
      setTask((t) => `${t}${t.trim() ? "\n\n" : ""}(Attached file at ${res.path})`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setAttaching(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!task.trim()) return;
    setBusy(true);
    setError(null);
    const [provider, model] = choice ? choice.split("|") : ["", ""];
    try {
      // wait:false — the session starts and we jump to its detail page so the
      // user watches it run live (and can cancel it).
      const session = await post<SessionView>("/sessions", {
        task: task.trim(),
        agent_type: agentType,
        provider: provider || undefined,
        model: model || undefined,
        wait: false,
      });
      setTask("");
      setAttachedName(null);
      onCreated?.();
      if (session?.id) router.push(`/sessions/${session.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3.5">
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <label className="block text-[11px] uppercase tracking-[0.1em] text-zinc-500">
            Task
          </label>
          <VoiceInput
            size="sm"
            onTranscript={(chunk) => setTask((p) => appendDictation(p, chunk))}
          />
        </div>
        <textarea
          ref={taskRef}
          value={task}
          onChange={(e) => setTask(e.target.value)}
          rows={3}
          placeholder="Describe what the agent should do… or dictate with the mic"
          className="field resize-y"
        />

        {/* Starter prompts — only while the box is empty */}
        {!task.trim() && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {EXAMPLE_TASKS.map((ex) => (
              <button
                key={ex}
                type="button"
                onClick={() => setTask(ex)}
                className="rounded-full border border-white/10 bg-white/[0.03] px-2.5 py-1 text-[11px] text-zinc-400 transition-colors hover:border-accent/30 hover:text-accent-soft"
              >
                {ex}
              </button>
            ))}
          </div>
        )}

        {/* Optional file attach */}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            ref={fileRef}
            type="file"
            className="hidden"
            onChange={onAttach}
          />
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={attaching}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-[11px] text-zinc-400 transition-colors hover:border-accent/30 hover:text-accent-soft disabled:opacity-50"
          >
            {attaching ? (
              <LoaderInline label="Uploading…" />
            ) : (
              <>
                <Paperclip size={12} /> Attach file
              </>
            )}
          </button>
          {attachedName && (
            <span className="text-[11px] text-emerald-300">Attached {attachedName}</span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-500">
            Agent type
          </label>
          <select
            value={agentType}
            onChange={(e) => setAgentType(e.target.value)}
            className="field"
          >
            {AGENT_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-500">
            <Cpu size={12} /> Model
          </label>
          <select
            value={choice}
            onChange={(e) => setChoice(e.target.value)}
            className="field"
          >
            <option value="">
              {health
                ? `Default · ${health.default_provider} / ${health.default_model}`
                : "Default"}
            </option>
            {models.map((m) => {
              const avail = isAvailable(m);
              return (
                <option key={optKey(m)} value={optKey(m)} disabled={!avail}>
                  {m.provider} · {m.model}
                  {avail ? "" : " — not connected"}
                </option>
              );
            })}
          </select>
        </div>
      </div>

      {onlyMock && (
        <Link
          href="/connections"
          className="flex items-center gap-2 rounded-xl border border-amber-500/20 bg-amber-500/[0.05] px-3 py-2 text-[11px] text-amber-100/80 transition-colors hover:bg-amber-500/[0.1]"
        >
          <PlugZap size={13} className="shrink-0 text-amber-300" />
          <span>
            Running on the built-in offline model.{" "}
            <span className="font-medium text-accent-soft">Connect a real model</span>
          </span>
          <ArrowRight size={12} className="ml-auto shrink-0 text-amber-300/70" />
        </Link>
      )}

      <div className="flex items-center justify-end">
        <button type="submit" disabled={busy || !task.trim()} className="btn-accent">
          {busy ? <LoaderInline label="Starting…" /> : <><Play size={14} /> Run session</>}
        </button>
      </div>

      {error && <ErrorNote>{error}</ErrorNote>}
    </form>
  );
}
