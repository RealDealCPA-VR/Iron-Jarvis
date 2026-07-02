"use client";

import { useState } from "react";
import { Plug, FlaskConical, Settings2, Power, CheckCircle2, Plus, Save } from "lucide-react";
import { post, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import type { Integration, IntegrationTestResult } from "@/lib/types";
import {
  Card,
  Badge,
  Dot,
  OfflineHint,
  Empty,
  SkeletonRows,
  ErrorNote,
  SuccessNote,
  LoaderInline,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

function IntegrationCard({
  integ,
  onChanged,
}: {
  integ: Integration;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<"enable" | "configure" | "test" | null>(null);
  const [showConfig, setShowConfig] = useState(false);
  const [config, setConfig] = useState("{\n  \n}");
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [test, setTest] = useState<IntegrationTestResult | null>(null);

  async function toggleEnable() {
    setBusy("enable");
    setError(null);
    try {
      await post(`/integrations/${encodeURIComponent(integ.id)}/enable`, {
        enabled: !integ.enabled,
      });
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function saveConfig() {
    setBusy("configure");
    setError(null);
    setNote(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(config || "{}");
    } catch {
      setError("Config must be valid JSON.");
      setBusy(null);
      return;
    }
    try {
      await post(`/integrations/${encodeURIComponent(integ.id)}/configure`, { config: parsed });
      setNote("Configuration saved.");
      setShowConfig(false);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function runTest() {
    setBusy("test");
    setError(null);
    setTest(null);
    try {
      const res = await post<IntegrationTestResult>(
        `/integrations/${encodeURIComponent(integ.id)}/test`,
      );
      setTest(res);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card hover>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Dot on={integ.enabled} />
            <h3 className="truncate text-sm font-semibold text-zinc-100">
              {integ.display_name}
            </h3>
          </div>
          <div className="mt-0.5 font-mono text-[11px] text-zinc-600">{integ.id}</div>
        </div>
        <Badge value={integ.kind} tone="violet" />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Badge value={integ.enabled ? "enabled" : "disabled"} tone={integ.enabled ? "green" : "slate"} />
        <Badge value={integ.configured ? "configured" : "unconfigured"} tone={integ.configured ? "cyan" : "amber"} />
      </div>

      {integ.required_secrets.length > 0 && (
        <div className="mt-3">
          <div className="text-[11px] uppercase tracking-[0.1em] text-zinc-400">
            Required secrets
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {integ.required_secrets.map((s) => (
              <span
                key={s}
                className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-0.5 font-mono text-[11px] text-zinc-300"
              >
                {s}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        <button onClick={toggleEnable} disabled={busy !== null} className="btn-ghost px-3 py-1.5 text-xs">
          {busy === "enable" ? (
            <LoaderInline />
          ) : (
            <>
              <Power size={13} /> {integ.enabled ? "Disable" : "Enable"}
            </>
          )}
        </button>
        <button
          onClick={() => setShowConfig((v) => !v)}
          disabled={busy !== null}
          className="btn-ghost px-3 py-1.5 text-xs"
        >
          <Settings2 size={13} /> Configure
        </button>
        <button onClick={runTest} disabled={busy !== null} className="btn-ghost px-3 py-1.5 text-xs">
          {busy === "test" ? (
            <LoaderInline />
          ) : (
            <>
              <FlaskConical size={13} /> Test
            </>
          )}
        </button>
      </div>

      {showConfig && (
        <div className="mt-3 space-y-2">
          <label className="block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
            Config (JSON)
          </label>
          <textarea
            aria-label="Config (JSON)"
            value={config}
            onChange={(e) => setConfig(e.target.value)}
            rows={5}
            spellCheck={false}
            className="field resize-y font-mono text-xs"
          />
          <button onClick={saveConfig} disabled={busy === "configure"} className="btn-accent px-3 py-1.5 text-xs">
            {busy === "configure" ? <LoaderInline label="Saving…" /> : "Save config"}
          </button>
        </div>
      )}

      {test && (
        <div className="mt-3">
          {test.ok ? (
            <SuccessNote>
              <span className="inline-flex items-center gap-1.5">
                <CheckCircle2 size={14} /> {test.detail || "Connection OK."}
              </span>
            </SuccessNote>
          ) : (
            <ErrorNote>{test.detail || "Test failed."}</ErrorNote>
          )}
        </div>
      )}
      {note && (
        <div className="mt-3">
          <SuccessNote>{note}</SuccessNote>
        </div>
      )}
      {error && (
        <div className="mt-3">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/*  Add a custom REST integration                                              */
/* -------------------------------------------------------------------------- */

function AddIntegrationForm({
  onCancel,
  onAdded,
}: {
  onCancel: () => void;
  onAdded: (name: string) => void;
}) {
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [description, setDescription] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = name.trim() !== "" && baseUrl.trim() !== "" && !busy;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !baseUrl.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const body: {
        name: string;
        base_url: string;
        description?: string;
        auth_token?: string;
      } = { name: name.trim(), base_url: baseUrl.trim() };
      if (description.trim()) body.description = description.trim();
      if (token.trim()) body.auth_token = token.trim();
      await post("/integrations", body);
      // Success: hand the name up so the page can note it, reload and close.
      onAdded(name.trim());
    } catch (err) {
      // Keep the form (and its values) open so the user can fix and retry.
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <form onSubmit={submit} className="space-y-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
          <Plug size={15} className="text-accent-soft" /> New REST integration
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <label
              htmlFor="add-integ-name"
              className="block text-[11px] uppercase tracking-[0.1em] text-zinc-400"
            >
              Name
            </label>
            <input
              id="add-integ-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Acme CRM"
              autoComplete="off"
              autoFocus
              className="field text-sm"
            />
          </div>
          <div className="space-y-1.5">
            <label
              htmlFor="add-integ-url"
              className="block text-[11px] uppercase tracking-[0.1em] text-zinc-400"
            >
              Base URL
            </label>
            <input
              id="add-integ-url"
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.example.com"
              autoComplete="off"
              className="field font-mono text-sm"
            />
          </div>
        </div>

        <div className="space-y-1.5">
          <label
            htmlFor="add-integ-desc"
            className="block text-[11px] uppercase tracking-[0.1em] text-zinc-400"
          >
            Description <span className="text-zinc-600">(optional)</span>
          </label>
          <input
            id="add-integ-desc"
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What this connector is for"
            autoComplete="off"
            className="field text-sm"
          />
        </div>

        <div className="space-y-1.5">
          <label
            htmlFor="add-integ-token"
            className="block text-[11px] uppercase tracking-[0.1em] text-zinc-400"
          >
            Bearer token <span className="text-zinc-600">(optional)</span>
          </label>
          <input
            id="add-integ-token"
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="••••••••"
            autoComplete="off"
            className="field font-mono text-sm"
          />
          <p className="text-[11px] leading-relaxed text-zinc-500">
            Stored encrypted in the vault; leave blank for public APIs.
          </p>
        </div>

        {error && <ErrorNote>{error}</ErrorNote>}

        <div className="flex items-center gap-2">
          <button type="submit" disabled={!canSubmit} className="btn-accent px-3 py-1.5 text-xs">
            {busy ? (
              <LoaderInline label="Adding…" />
            ) : (
              <>
                <Save size={14} /> Add integration
              </>
            )}
          </button>
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="btn-ghost px-3 py-1.5 text-xs"
          >
            Cancel
          </button>
        </div>
      </form>
    </Card>
  );
}

export default function IntegrationsPage() {
  const { data, error, loading, reload } = useApi<{ integrations: Integration[] }>(
    "/integrations",
  );
  const offline = error && error.status === 0;
  const integrations = data?.integrations ?? [];
  const [showAdd, setShowAdd] = useState(false);
  const [added, setAdded] = useState<string | null>(null);

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Integrations"
          subtitle="Enable, configure and test external connectors. Configure stores connector settings; Test pings the live service."
          actions={
            <button
              onClick={() => {
                setShowAdd((v) => !v);
                setAdded(null);
              }}
              className="btn-accent px-3 py-1.5 text-xs"
            >
              <Plus size={14} /> Add integration
            </button>
          }
        />
      </Reveal>

      <Reveal>
        <p className="flex items-center gap-2 text-xs text-zinc-600">
          <Plug size={13} />
          Custom integrations connect any REST API — add a base URL (plus an optional bearer
          token) and health-check it any time with the Test button.
        </p>
      </Reveal>

      {showAdd && (
        <Reveal>
          <AddIntegrationForm
            onCancel={() => setShowAdd(false)}
            onAdded={(name) => {
              setShowAdd(false);
              setAdded(name);
              reload();
            }}
          />
        </Reveal>
      )}

      {added && (
        <Reveal>
          <SuccessNote>
            Added{" "}
            <span className="font-medium text-emerald-100">{added}</span> — find it in the list
            below and use Test to health-check it.
          </SuccessNote>
        </Reveal>
      )}

      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      {loading && !data ? (
        <Reveal>
          <Card>
            <SkeletonRows rows={4} />
          </Card>
        </Reveal>
      ) : integrations.length === 0 ? (
        <Reveal>
          <Card>
            <Empty icon={<Plug size={24} />}>No integrations registered.</Empty>
          </Card>
        </Reveal>
      ) : (
        <Reveal>
          <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
            {integrations.map((integ) => (
              <IntegrationCard key={integ.id} integ={integ} onChanged={reload} />
            ))}
          </div>
        </Reveal>
      )}
    </PageShell>
  );
}
