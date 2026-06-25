"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Play } from "lucide-react";
import { post, ApiError } from "@/lib/api";
import type { SessionView } from "@/lib/types";
import { ErrorNote, LoaderInline } from "./ui";
import { VoiceInput, appendDictation } from "./VoiceInput";

const AGENT_TYPES = ["builder", "supervisor", "planner", "researcher", "reviewer"];

export function NewSessionForm({ onCreated }: { onCreated?: () => void }) {
  const router = useRouter();
  const [task, setTask] = useState("");
  const [agentType, setAgentType] = useState("builder");
  const [provider, setProvider] = useState("");
  const [wait, setWait] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!task.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const session = await post<SessionView>("/sessions", {
        task: task.trim(),
        agent_type: agentType,
        provider: provider.trim() || undefined,
        wait,
      });
      setTask("");
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
          value={task}
          onChange={(e) => setTask(e.target.value)}
          rows={3}
          placeholder="Describe what the agent should do… or dictate with the mic"
          className="field resize-y"
        />
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
          <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-500">
            Provider
          </label>
          <input
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            placeholder="default"
            className="field"
          />
        </div>
      </div>

      <div className="flex items-center justify-between">
        <label className="flex items-center gap-2 text-sm text-zinc-400">
          <input
            type="checkbox"
            checked={wait}
            onChange={(e) => setWait(e.target.checked)}
            className="h-4 w-4 accent-[#22d3ee]"
          />
          Wait for completion
        </label>
        <button type="submit" disabled={busy || !task.trim()} className="btn-accent">
          {busy ? <LoaderInline label="Running…" /> : <><Play size={14} /> Run session</>}
        </button>
      </div>

      {error && <ErrorNote>{error}</ErrorNote>}
    </form>
  );
}
