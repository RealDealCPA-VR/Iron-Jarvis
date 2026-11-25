"use client";

/**
 * v1.577.0: one extension's own settings, under its Edit form on the Tools
 * page. Saved with PUT /mcp/servers/{name}/policy (only what changed), and
 * used from the next call: nothing restarts.
 *
 * - Who asks before its tools run (`approval`).
 * - Trust its new tools (`trust_all`): tools a later version adds are not
 *   held back (a tool the safety scan flagged still is).
 * - Always include these tools (`pinned_tools`): they join every chat the
 *   extension is part of, before any picking.
 * - Start when first used (`lazy`).
 */

import { useMemo, useState } from "react";
import { ApiError, put } from "@/lib/api";
import { ErrorNote, LoaderInline } from "@/components/ui";

export const APPROVAL_CHOICES: { value: string; label: string; hint: string }[] = [
  { value: "", label: "As set for the chat", hint: "The chat's own approval setting decides, as before." },
  { value: "ask", label: "Ask before every tool", hint: "Iron Jarvis asks before each use of any of its tools." },
  {
    value: "writes",
    label: "Ask only before changes",
    hint: "Tools that only read run without asking; a tool that can change something asks each time.",
  },
  {
    value: "server",
    label: "The extension asks in its own window",
    hint: "For an extension with its own approvals: Iron Jarvis does not ask as well.",
  },
];

export interface ExtensionPolicy {
  approval?: string;
  trust_all?: boolean;
  pinned_tools?: string[];
  lazy?: boolean;
}

/** Only the fields that differ from what is saved (omitted = keep). */
export function policyPatch(saved: ExtensionPolicy, now: ExtensionPolicy): ExtensionPolicy {
  const out: ExtensionPolicy = {};
  if ((now.approval ?? "") !== (saved.approval ?? "")) out.approval = now.approval ?? "";
  if (!!now.trust_all !== !!saved.trust_all) out.trust_all = !!now.trust_all;
  if (!!now.lazy !== !!saved.lazy) out.lazy = !!now.lazy;
  const a = [...(saved.pinned_tools ?? [])].sort().join("\n");
  const b = [...(now.pinned_tools ?? [])].sort().join("\n");
  if (a !== b) out.pinned_tools = [...(now.pinned_tools ?? [])];
  return out;
}

export function ExtensionSettings({
  name,
  policy,
  toolNames,
  locked,
  onSaved,
}: {
  name: string;
  policy: ExtensionPolicy | undefined;
  toolNames: string[];
  locked?: boolean;
  onSaved?: (p: ExtensionPolicy) => void;
}) {
  const [saved, setSaved] = useState<ExtensionPolicy>(() => ({ ...(policy ?? {}) }));
  const [now, setNow] = useState<ExtensionPolicy>(() => ({ ...(policy ?? {}) }));
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const pins = useMemo(() => new Set(now.pinned_tools ?? []), [now.pinned_tools]);
  const shown = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return toolNames.filter((t) => !f || t.toLowerCase().includes(f)).slice(0, 60);
  }, [toolNames, filter]);
  const changes = policyPatch(saved, now);
  const dirty = Object.keys(changes).length > 0;

  function togglePin(tool: string) {
    setDone(false);
    const next = new Set(pins);
    if (next.has(tool)) next.delete(tool);
    else next.add(tool);
    setNow((p) => ({ ...p, pinned_tools: toolNames.filter((t) => next.has(t)) }));
  }

  async function save() {
    if (busy || !dirty) return;
    setBusy(true);
    setError(null);
    try {
      const res = await put<{ policy?: ExtensionPolicy }>(
        `/mcp/servers/${encodeURIComponent(name)}/policy`,
        changes,
      );
      const next = { ...now, ...(res?.policy ?? {}) };
      setSaved(next);
      setNow(next);
      setDone(true);
      onSaved?.(next);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const choice = APPROVAL_CHOICES.find((c) => c.value === (now.approval ?? "")) ?? APPROVAL_CHOICES[0];

  return (
    <div
      data-testid={`extension-settings-${name}`}
      className="mt-2 rounded-xl border border-white/10 bg-white/[0.02] p-3 text-[12px] text-zinc-300"
    >
      <div className="mb-2 text-[13px] font-medium text-zinc-200">How Iron Jarvis uses it</div>
      <label className="block">
        <span className="mb-1 block text-zinc-400">Who asks before its tools run</span>
        <select
          data-testid={`extension-approval-${name}`}
          className="w-full rounded-lg border border-white/10 bg-black/30 px-2 py-1"
          value={now.approval ?? ""}
          disabled={!!locked}
          onChange={(e) => {
            setDone(false);
            setNow((p) => ({ ...p, approval: e.target.value }));
          }}
        >
          {APPROVAL_CHOICES.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
        <span className="mt-1 block text-zinc-500">
          {locked ? "This extension is locked: Iron Jarvis asks before each use of it." : choice.hint}
        </span>
      </label>
      <label className="mt-2 flex items-start gap-2">
        <input
          type="checkbox"
          data-testid={`extension-trust-${name}`}
          checked={!!now.trust_all}
          disabled={!!locked}
          onChange={(e) => {
            setDone(false);
            setNow((p) => ({ ...p, trust_all: e.target.checked }));
          }}
        />
        <span>
          Trust its new tools
          <span className="block text-zinc-500">
            Tools a later version adds work like the ones you saw when you added it. A tool the
            safety check flagged still waits for you.
          </span>
        </span>
      </label>
      <label className="mt-2 flex items-start gap-2">
        <input
          type="checkbox"
          data-testid={`extension-lazy-${name}`}
          checked={!!now.lazy}
          onChange={(e) => {
            setDone(false);
            setNow((p) => ({ ...p, lazy: e.target.checked }));
          }}
        />
        <span>
          Start when first used
          <span className="block text-zinc-500">
            After it has started once, it starts only when a chat or task first uses it.
          </span>
        </span>
      </label>
      {toolNames.length > 0 && (
        <div className="mt-2">
          <span className="mb-1 block text-zinc-400">
            Always include these tools ({pins.size} chosen)
          </span>
          <span className="mb-1 block text-zinc-500">
            Chosen tools are offered in every chat this extension is part of. Iron Jarvis can also
            look up its other tools when a task needs them.
          </span>
          {toolNames.length > 12 && (
            <input
              data-testid={`extension-pin-filter-${name}`}
              className="mb-1 w-full rounded-lg border border-white/10 bg-black/30 px-2 py-1"
              placeholder="Find a tool"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          )}
          <div className="flex max-h-40 flex-wrap gap-1 overflow-y-auto">
            {shown.map((t) => (
              <button
                key={t}
                type="button"
                data-testid={`extension-pin-${name}-${t}`}
                aria-pressed={pins.has(t)}
                onClick={() => togglePin(t)}
                className={
                  "rounded-md border px-1.5 py-0.5 font-mono text-[11px] " +
                  (pins.has(t)
                    ? "border-accent/40 bg-accent/15 text-zinc-100"
                    : "border-white/10 bg-white/[0.03] text-zinc-400")
                }
              >
                {t}
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          data-testid={`extension-settings-save-${name}`}
          disabled={!dirty || busy}
          onClick={save}
          className="rounded-lg border border-accent/30 bg-accent/15 px-3 py-1 text-zinc-100 disabled:opacity-40"
        >
          Save
        </button>
        {busy && <LoaderInline />}
        {done && !dirty && <span className="text-zinc-500">Saved. It applies to the next use.</span>}
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}
