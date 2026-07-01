"use client";

// Multi-terminal workspace: a tiled grid of live xterm.js terminals on the
// left/center, and a directory tree on the right for picking a project folder
// to open a terminal in. xterm is dynamically imported (no SSR).

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { Loader2, Plus, SquareTerminal } from "lucide-react";
import { ApiError, del, get, post } from "@/lib/api";
import type { ModelOption, Shell, TerminalInfo } from "@/lib/types";
import { Card, OfflineHint, ErrorNote, Spinner, ConfirmButton } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { DirectoryTree } from "@/components/terminal/DirectoryTree";

// xterm only runs in the browser — never during SSR / `next build`.
const TerminalPane = dynamic(
  () => import("@/components/terminal/TerminalPane").then((m) => m.TerminalPane),
  {
    ssr: false,
    loading: () => (
      <div className="grid h-full place-items-center text-zinc-600">
        <Loader2 size={18} className="animate-spin" />
      </div>
    ),
  },
);

export default function TerminalsPage() {
  const [terminals, setTerminals] = useState<TerminalInfo[]>([]);
  const [shells, setShells] = useState<Shell[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]); // per-pane AI picker
  const [shell, setShell] = useState<string>("");
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  // A terminal whose close was requested (from the pane's X) and is awaiting a
  // confirm — killing a live shell is irreversible, so we gate it.
  const [pendingClose, setPendingClose] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Re-attach to existing sessions + load the shell list on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [terms, sh, mods] = await Promise.all([
          get<{ terminals: TerminalInfo[] }>("/terminals"),
          get<{ shells: Shell[] }>("/terminals/shells").catch(() => ({ shells: [] })),
          get<{ models: ModelOption[] }>("/models").catch(() => ({ models: [] })),
        ]);
        if (cancelled) return;
        const alive = terms.terminals.filter((t) => t.alive);
        setTerminals(alive);
        setFocusedId(alive[0]?.id ?? null);
        setShells(sh.shells);
        setShell(sh.shells[0]?.name ?? "");
        setModels(mods.models);
        setOffline(false);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 0) setOffline(true);
        else setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const addTerminal = useCallback(
    async (cwd?: string | null) => {
      setBusy(true);
      setError(null);
      try {
        const info = await post<TerminalInfo>("/terminals", {
          cwd: cwd ?? undefined,
          shell: shell || undefined,
        });
        setTerminals((prev) => [...prev, info]);
        setFocusedId(info.id);
        setOffline(false);
      } catch (e) {
        if (e instanceof ApiError && e.status === 0) setOffline(true);
        else setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [shell],
  );

  const closeTerminal = useCallback((id: string) => {
    // Optimistically remove the pane (its WS unmounts), then kill server-side.
    setTerminals((prev) => prev.filter((t) => t.id !== id));
    setFocusedId((cur) => (cur === id ? null : cur));
    del(`/terminals/${id}`).catch(() => {
      /* already gone / offline — the pane is removed regardless */
    });
  }, []);

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Terminals"
          subtitle="Live shell sessions, tiled. Pick a project folder on the right and open a terminal there, or hit + to add one."
          actions={
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-2 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                <SquareTerminal size={13} className="text-accent-soft/70" />
                Shell
              </label>
              <select
                aria-label="Shell"
                value={shell}
                onChange={(e) => setShell(e.target.value)}
                disabled={shells.length === 0}
                className="field w-auto py-1.5 text-[13px]"
              >
                {shells.length === 0 && <option value="">default</option>}
                {shells.map((s) => (
                  <option key={s.name} value={s.name}>
                    {s.name}
                  </option>
                ))}
              </select>
              <button
                onClick={() => addTerminal(selectedPath)}
                disabled={busy}
                className="btn-accent py-1.5 text-[13px]"
              >
                {busy ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Plus size={14} />
                )}
                New terminal
              </button>
            </div>
          }
        />
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint detail="Terminals and the directory tree both need it running." />
        </Reveal>
      )}
      {error && (
        <Reveal>
          <ErrorNote>{error}</ErrorNote>
        </Reveal>
      )}

      <Reveal>
        <div className="flex flex-col gap-5 lg:flex-row">
          {/* Terminals workspace (left / center) */}
          <div className="min-w-0 flex-1">
            {loading ? (
              <Card>
                <Spinner label="Attaching to sessions…" />
              </Card>
            ) : (
              <div className="grid grid-cols-1 gap-4 2xl:grid-cols-2">
                {terminals.map((t) => (
                  <div key={t.id} className="relative h-[360px]">
                    <TerminalPane
                      info={t}
                      focused={focusedId === t.id}
                      onFocus={() => setFocusedId(t.id)}
                      onClose={() => setPendingClose(t.id)}
                      models={models}
                    />
                    {pendingClose === t.id && (
                      <div className="absolute inset-0 z-20 grid place-items-center rounded-2xl bg-black/70 backdrop-blur-sm">
                        <div className="w-[min(20rem,90%)] rounded-2xl border border-white/10 bg-ink-850/95 p-5 text-center shadow-card">
                          <div className="text-sm font-semibold text-zinc-100">
                            Close this terminal?
                          </div>
                          <p className="mt-1 break-all text-[12px] text-zinc-500">
                            Ends the live shell session in {t.cwd}.
                          </p>
                          <div className="mt-4 flex items-center justify-center gap-2">
                            <ConfirmButton
                              onConfirm={() => {
                                closeTerminal(t.id);
                                setPendingClose(null);
                              }}
                              label="Close terminal"
                              confirmLabel="Confirm close"
                              title="End this shell session"
                            />
                            <button
                              type="button"
                              onClick={() => setPendingClose(null)}
                              className="btn-ghost py-1 text-xs"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                ))}

                {/* The prominent "+" tile to add a new terminal. */}
                <button
                  data-add-terminal
                  onClick={() => addTerminal(selectedPath)}
                  disabled={busy}
                  className="group grid h-[360px] place-items-center rounded-2xl border-2 border-dashed border-white/[0.1] bg-white/[0.01] text-zinc-500 transition-colors hover:border-accent/40 hover:bg-accent/[0.04] hover:text-accent-soft disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <div className="flex flex-col items-center gap-3">
                    <span className="grid h-14 w-14 place-items-center rounded-2xl border border-white/10 bg-white/[0.03] transition-colors group-hover:border-accent/40 group-hover:bg-accent/10">
                      {busy ? (
                        <Loader2 size={26} className="animate-spin" />
                      ) : (
                        <Plus size={26} />
                      )}
                    </span>
                    <div className="text-center">
                      <div className="text-sm font-medium">Add terminal</div>
                      <div className="mt-0.5 max-w-[16rem] text-[11px] text-zinc-400">
                        {selectedPath
                          ? `opens in ${selectedPath}`
                          : "opens in your home directory"}
                      </div>
                    </div>
                  </div>
                </button>
              </div>
            )}
          </div>

          {/* Directory tree (right) */}
          <div className="w-full shrink-0 lg:w-80 xl:w-96">
            <div className="lg:sticky lg:top-0 lg:h-[calc(100vh-9rem)]">
              <DirectoryTree
                selectedPath={selectedPath}
                onSelect={setSelectedPath}
                onOpenTerminal={(p) => addTerminal(p)}
              />
            </div>
          </div>
        </div>
      </Reveal>
    </PageShell>
  );
}
