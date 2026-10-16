"use client";

/**
 * v1.343.0 ("add a pack in one paste"), lane K2: the inline editor a pack row
 * opens with Edit.
 *
 * Prefilled from the GET /mcp/servers row: command, args as one line, the
 * plain env KEYS as rows the user can give a new value, the secret keys as
 * "set" chips with Replace / Clear, the folder; an HTTP pack shows its URL,
 * its header names with Replace / Clear and the token as a chip. Save sends
 * PATCH /mcp/servers/{name} with ONLY the fields the user changed
 * (`editPatch`): omitted = keep, "" is a value, Clear rides `clear_secrets`
 * / `clear_headers`, a token Clear sends `auth: null`. The name is not
 * editable (it is the pack's identity).
 *
 * NO VALUE IS EVER SHOWN. The row carries key and header NAMES only; a
 * Replace box is a password field that starts empty. Nothing in this file
 * reads a value off the row.
 */

import { useState } from "react";
import { Check, X } from "lucide-react";
import { ApiError, patch } from "@/lib/api";
import { ErrorNote, LoaderInline } from "@/components/ui";
import {
  editPatch,
  seedFromRow,
  seedState,
  type EditSeed,
  type EditState,
} from "@/lib/mcpImport";

export interface EditSaved {
  reloaded?: boolean;
  note?: string | null;
  [k: string]: unknown;
}

interface NewRow {
  id: number;
  key: string;
  value: string;
}

/** A key that is set, with Replace / Clear. `replacing` shows the password
 *  box; `cleared` flips the chip's words and offers Keep. */
function SetChip({
  label,
  testId,
  value,
  cleared,
  onReplace,
  onClear,
  onKeep,
}: {
  label: string;
  testId: string;
  value: string | null;
  cleared: boolean;
  onReplace: (v: string) => void;
  onClear: () => void;
  onKeep: () => void;
}) {
  const [replacing, setReplacing] = useState(false);
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid={`${testId}-chip`}>
      <span
        className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 font-mono text-[11px] ${
          cleared
            ? "border-tone-danger/25 bg-tone-danger/[0.07] text-tone-danger line-through"
            : "border-white/[0.07] bg-white/[0.02] text-zinc-300"
        }`}
      >
        {label}
        <span className="font-sans text-zinc-500">{cleared ? "will be cleared" : "set"}</span>
      </span>
      {cleared ? (
        <button
          type="button"
          data-testid={`${testId}-keep`}
          onClick={onKeep}
          className="text-[12px] font-medium text-zinc-400 hover:text-zinc-200"
        >
          Keep
        </button>
      ) : (
        <>
          {replacing ? (
            <input
              type="password"
              autoComplete="off"
              data-testid={testId}
              value={value ?? ""}
              onChange={(e) => onReplace(e.target.value)}
              placeholder="New value"
              className="field w-56 px-2 py-1 font-mono text-[12px]"
            />
          ) : (
            <button
              type="button"
              data-testid={`${testId}-replace`}
              onClick={() => setReplacing(true)}
              className="text-[12px] font-medium text-zinc-400 hover:text-zinc-200"
            >
              Replace
            </button>
          )}
          <button
            type="button"
            data-testid={`${testId}-clear`}
            onClick={() => {
              setReplacing(false);
              onClear();
            }}
            className="text-[12px] font-medium text-zinc-400 hover:text-tone-danger"
          >
            Clear
          </button>
        </>
      )}
    </div>
  );
}

function Label({ children }: { children: string }) {
  return <span className="mb-1 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">{children}</span>;
}

export function EditPack({
  row,
  onSaved,
  onCancel,
}: {
  row: Record<string, unknown>;
  onSaved: (res: EditSaved) => void;
  onCancel: () => void;
}) {
  const [seed] = useState<EditSeed>(() => seedFromRow(row));
  const [st, setSt] = useState<EditState>(() => seedState(seed));
  const [newEnv, setNewEnv] = useState<NewRow[]>([]);
  const [newHeaders, setNewHeaders] = useState<NewRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nothing, setNothing] = useState(false);
  const ids = useState(() => ({ n: 1 }))[0];
  const name = seed.name;
  const isHttp = st.transport === "http" || st.transport === "sse";
  const set = (patchSt: Partial<EditState>) => {
    setNothing(false);
    setSt((s) => ({ ...s, ...patchSt }));
  };

  function body(): Record<string, unknown> {
    const envValues = { ...st.envValues };
    for (const r of newEnv) if (r.key.trim()) envValues[r.key.trim()] = r.value;
    const headerValues = { ...st.headerValues };
    for (const r of newHeaders) if (r.key.trim()) headerValues[r.key.trim()] = r.value;
    return editPatch(seed, { ...st, envValues, headerValues });
  }

  async function save() {
    if (busy) return;
    const b = body();
    if (Object.keys(b).length === 0) {
      setNothing(true);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await patch<EditSaved>(`/mcp/servers/${encodeURIComponent(name)}`, b);
      onSaved(res ?? {});
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const plainKeys = seed.envKeys.filter((k) => !seed.secretKeys.includes(k));

  return (
    <div
      data-testid={`edit-pack-${name}`}
      className="mt-2 rounded-xl border border-accent/20 bg-accent/[0.04] p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-medium text-zinc-200">
          Editing <span className="font-mono">{name}</span>
        </span>
        <span className="text-[11px] text-zinc-500">The name cannot change.</span>
      </div>

      <div className="mt-2 grid gap-2 sm:grid-cols-[auto_1fr]">
        <label className="sm:col-span-2">
          <Label>Server type</Label>
          <select
            data-testid={`edit-transport-${name}`}
            value={isHttp ? st.transport : "stdio"}
            onChange={(e) => set({ transport: e.target.value })}
            className="field sm:w-56"
          >
            <option value="stdio">A program this PC runs</option>
            <option value="http">Streamable HTTP</option>
            <option value="sse">SSE</option>
          </select>
        </label>
      </div>

      {isHttp ? (
        <label className="mt-2 block">
          <Label>URL</Label>
          <input
            data-testid={`edit-url-${name}`}
            value={st.url}
            onChange={(e) => set({ url: e.target.value })}
            className="field px-2 py-1 font-mono text-[12px]"
          />
        </label>
      ) : (
        <>
          <label className="mt-2 block">
            <Label>Command</Label>
            <input
              data-testid={`edit-command-${name}`}
              value={st.command}
              onChange={(e) => set({ command: e.target.value })}
              className="field px-2 py-1 font-mono text-[12px]"
            />
          </label>
          <label className="mt-2 block">
            <Label>Arguments (one line)</Label>
            <input
              data-testid={`edit-args-${name}`}
              value={st.args}
              onChange={(e) => set({ args: e.target.value })}
              className="field px-2 py-1 font-mono text-[12px]"
            />
          </label>
          <label className="mt-2 block">
            <Label>Folder (optional)</Label>
            <input
              data-testid={`edit-cwd-${name}`}
              value={st.cwd}
              onChange={(e) => set({ cwd: e.target.value })}
              placeholder="Runs in the app's folder when blank"
              className="field px-2 py-1 font-mono text-[12px]"
            />
          </label>
        </>
      )}

      {!isHttp && (
        <div className="mt-2">
          <Label>Environment variables</Label>
          <div className="space-y-1">
            {plainKeys.map((k) => (
              <label key={k} className="flex items-center gap-2">
                <span className="w-44 shrink-0 truncate font-mono text-[11px] text-zinc-400">{k}</span>
                <input
                  data-testid={`edit-env-${name}-${k}`}
                  value={st.envValues[k] ?? ""}
                  onChange={(e) => set({ envValues: { ...st.envValues, [k]: e.target.value } })}
                  placeholder="Unchanged. Type to replace the value."
                  className="field flex-1 px-2 py-1 font-mono text-[12px]"
                />
              </label>
            ))}
            {seed.secretKeys.map((k) => (
              <SetChip
                key={k}
                label={k}
                testId={`edit-secret-${name}-${k}`}
                value={st.secretValues[k] ?? null}
                cleared={st.clearSecrets.includes(k)}
                onReplace={(v) => set({ secretValues: { ...st.secretValues, [k]: v } })}
                onClear={() =>
                  set({
                    clearSecrets: [...st.clearSecrets.filter((x) => x !== k), k],
                    secretValues: { ...st.secretValues, [k]: "" },
                  })
                }
                onKeep={() => set({ clearSecrets: st.clearSecrets.filter((x) => x !== k) })}
              />
            ))}
            {newEnv.map((r) => (
              <div key={r.id} className="flex items-center gap-2">
                <input
                  data-testid={`edit-newenv-key-${name}-${r.id}`}
                  value={r.key}
                  onChange={(e) => {
                    setNothing(false);
                    setNewEnv((rows) => rows.map((x) => (x.id === r.id ? { ...x, key: e.target.value } : x)));
                  }}
                  placeholder="KEY"
                  className="field w-44 px-2 py-1 font-mono text-[12px]"
                />
                <input
                  data-testid={`edit-newenv-value-${name}-${r.id}`}
                  type="password"
                  autoComplete="off"
                  value={r.value}
                  onChange={(e) => {
                    setNothing(false);
                    setNewEnv((rows) => rows.map((x) => (x.id === r.id ? { ...x, value: e.target.value } : x)));
                  }}
                  placeholder="value"
                  className="field flex-1 px-2 py-1 font-mono text-[12px]"
                />
                <button
                  type="button"
                  onClick={() => setNewEnv((rows) => rows.filter((x) => x.id !== r.id))}
                  aria-label="Remove variable"
                  className="text-zinc-500 hover:text-zinc-300"
                >
                  <X size={14} />
                </button>
              </div>
            ))}
          </div>
          <button
            type="button"
            data-testid={`edit-env-add-${name}`}
            onClick={() => setNewEnv((rows) => [...rows, { id: ids.n++, key: "", value: "" }])}
            className="mt-1 text-[12px] font-medium text-zinc-400 hover:text-zinc-200"
          >
            + Add a variable
          </button>
          <span className="mt-1 block text-[11px] leading-relaxed text-zinc-500">
            A key that looks like a token, key, secret or password goes to the encrypted vault.
          </span>
        </div>
      )}

      {isHttp && (
        <div className="mt-2">
          <Label>Headers</Label>
          <div className="space-y-1">
            {seed.headerNames.map((h) => (
              <SetChip
                key={h}
                label={h}
                testId={`edit-header-${name}-${h}`}
                value={st.headerValues[h] ?? null}
                cleared={st.clearHeaders.includes(h)}
                onReplace={(v) => set({ headerValues: { ...st.headerValues, [h]: v } })}
                onClear={() =>
                  set({
                    clearHeaders: [...st.clearHeaders.filter((x) => x !== h), h],
                    headerValues: { ...st.headerValues, [h]: "" },
                  })
                }
                onKeep={() => set({ clearHeaders: st.clearHeaders.filter((x) => x !== h) })}
              />
            ))}
            {newHeaders.map((r) => (
              <div key={r.id} className="flex items-center gap-2">
                <input
                  data-testid={`edit-newheader-name-${name}-${r.id}`}
                  value={r.key}
                  onChange={(e) => {
                    setNothing(false);
                    setNewHeaders((rows) =>
                      rows.map((x) => (x.id === r.id ? { ...x, key: e.target.value } : x)),
                    );
                  }}
                  placeholder="Header name"
                  className="field w-44 px-2 py-1 font-mono text-[12px]"
                />
                <input
                  data-testid={`edit-newheader-value-${name}-${r.id}`}
                  type="password"
                  autoComplete="off"
                  value={r.value}
                  onChange={(e) => {
                    setNothing(false);
                    setNewHeaders((rows) =>
                      rows.map((x) => (x.id === r.id ? { ...x, value: e.target.value } : x)),
                    );
                  }}
                  placeholder="Value"
                  className="field flex-1 px-2 py-1 font-mono text-[12px]"
                />
                <button
                  type="button"
                  onClick={() => setNewHeaders((rows) => rows.filter((x) => x.id !== r.id))}
                  aria-label="Remove header"
                  className="text-zinc-500 hover:text-zinc-300"
                >
                  <X size={14} />
                </button>
              </div>
            ))}
          </div>
          <button
            type="button"
            data-testid={`edit-header-add-${name}`}
            onClick={() => setNewHeaders((rows) => [...rows, { id: ids.n++, key: "", value: "" }])}
            className="mt-1 text-[12px] font-medium text-zinc-400 hover:text-zinc-200"
          >
            + Add a header
          </button>
          <div className="mt-2">
            <Label>Bearer token</Label>
            {seed.tokenSet ? (
              <SetChip
                label="Authorization"
                testId={`edit-token-${name}`}
                value={st.token}
                cleared={st.clearToken}
                onReplace={(v) => set({ token: v })}
                onClear={() => set({ clearToken: true, token: null })}
                onKeep={() => set({ clearToken: false })}
              />
            ) : (
              <input
                data-testid={`edit-token-${name}`}
                type="password"
                autoComplete="off"
                value={st.token ?? ""}
                onChange={(e) => set({ token: e.target.value, clearToken: false })}
                placeholder="None set. Type one to send Authorization: Bearer ..."
                className="field w-72 px-2 py-1 font-mono text-[12px]"
              />
            )}
          </div>
        </div>
      )}

      {error && (
        <div className="mt-2">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}
      {nothing && (
        <p className="mt-2 text-[12px] text-zinc-500" data-testid={`edit-nothing-${name}`}>
          Nothing changed.
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          type="button"
          data-testid={`edit-save-${name}`}
          onClick={() => void save()}
          disabled={busy}
          className="btn-accent"
        >
          {busy ? (
            <LoaderInline label="Saving" />
          ) : (
            <>
              <Check size={14} /> Save and reload
            </>
          )}
        </button>
        <button
          type="button"
          data-testid={`edit-cancel-${name}`}
          onClick={onCancel}
          className="text-[12px] font-medium text-zinc-500 hover:text-zinc-300"
        >
          Cancel
        </button>
        <span className="text-[11px] text-zinc-600">Only what you changed is sent. The pack restarts after a save.</span>
      </div>
    </div>
  );
}
