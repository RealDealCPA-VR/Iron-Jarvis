"use client";

import { useState } from "react";
import { KeyRound, Plus, ShieldCheck, EyeOff } from "lucide-react";
import { post, del, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import type { SecretMeta } from "@/lib/types";
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
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { timeAgo } from "@/lib/format";

const KINDS = ["api_key", "oauth", "token", "password", "generic"];

export default function SecretsPage() {
  const { data, error, loading, reload } = useApi<{ secrets: SecretMeta[] }>("/secrets");
  const offline = error && error.status === 0;
  const secrets = data?.secrets ?? [];

  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [kind, setKind] = useState("generic");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !value.trim()) return;
    setBusy(true);
    setFormError(null);
    setOk(null);
    try {
      await post("/secrets", {
        name: name.trim(),
        value,
        kind,
        description: description.trim(),
      });
      setOk(`Secret "${name.trim()}" stored.`);
      setName("");
      setValue("");
      setDescription("");
      setKind("generic");
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(secretName: string) {
    setOk(null);
    try {
      await del(`/secrets/${encodeURIComponent(secretName)}`);
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Secrets"
          subtitle="Encrypted credential store. Values are write-only — they are never returned by the API or shown here."
        />
      </Reveal>
      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <Reveal>
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-1">
            <Card title="Add secret" icon={<Plus size={15} />}>
              <form onSubmit={submit} className="space-y-3.5">
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Name
                  </label>
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="OPENAI_API_KEY"
                    className="field font-mono"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Value <span className="text-zinc-600">(write-only)</span>
                  </label>
                  <input
                    type="password"
                    value={value}
                    onChange={(e) => setValue(e.target.value)}
                    placeholder="••••••••••••"
                    className="field font-mono"
                    autoComplete="off"
                  />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                      Kind
                    </label>
                    <select
                      aria-label="Secret kind"
                      value={kind}
                      onChange={(e) => setKind(e.target.value)}
                      className="field"
                    >
                      {KINDS.map((k) => (
                        <option key={k} value={k}>
                          {k}
                        </option>
                      ))}
                    </select>
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
                <button
                  type="submit"
                  disabled={busy || !name.trim() || !value.trim()}
                  className="btn-accent w-full"
                >
                  {busy ? <LoaderInline label="Storing…" /> : <><ShieldCheck size={14} /> Store secret</>}
                </button>
                {ok && <SuccessNote>{ok}</SuccessNote>}
                {formError && <ErrorNote>{formError}</ErrorNote>}
              </form>
            </Card>
          </div>

          <div className="lg:col-span-2">
            <Card
              title={`Stored secrets${secrets.length ? ` · ${secrets.length}` : ""}`}
              icon={<KeyRound size={15} />}
              right={
                <span className="flex items-center gap-1.5 text-[11px] text-zinc-500">
                  <EyeOff size={13} /> values never shown
                </span>
              }
            >
              {loading && !data ? (
                <SkeletonRows rows={5} />
              ) : secrets.length === 0 ? (
                <Empty
                  icon={<KeyRound size={24} />}
                  action={{ label: "Connect a model", href: "/connections" }}
                >
                  No secrets stored yet. Most people start by connecting a model.
                </Empty>
              ) : (
                <div className="-mx-1 overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b hairline text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                        <th className="px-2 py-2.5 font-medium">Name</th>
                        <th className="px-2 py-2.5 font-medium">Kind</th>
                        <th className="px-2 py-2.5 font-medium">Description</th>
                        <th className="px-2 py-2.5 font-medium">Updated</th>
                        <th className="px-2 py-2.5 font-medium" />
                      </tr>
                    </thead>
                    <tbody>
                      {secrets.map((s) => (
                        <tr
                          key={s.name}
                          className="border-b border-white/[0.04] align-middle last:border-0 hover:bg-white/[0.02]"
                        >
                          <td className="px-2 py-2.5 font-mono text-zinc-100">{s.name}</td>
                          <td className="px-2 py-2.5">
                            <Badge value={s.kind} tone="cyan" />
                          </td>
                          <td className="max-w-xs truncate px-2 py-2.5 text-zinc-400">
                            {s.description || <span className="text-zinc-600">—</span>}
                          </td>
                          <td className="px-2 py-2.5 text-zinc-500">
                            {s.updated_at ? timeAgo(s.updated_at) : "—"}
                          </td>
                          <td className="px-2 py-2.5 text-right">
                            <ConfirmButton
                              onConfirm={() => remove(s.name)}
                              label="Delete"
                              title={`Delete secret "${s.name}" — this cannot be undone`}
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>
        </div>
      </Reveal>
    </PageShell>
  );
}
