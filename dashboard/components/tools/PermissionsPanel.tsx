"use client";

/**
 * Who may run what, per extension (v1.216.0).
 *
 * From the review: "Fix the permission control — it is the scariest part of the
 * page… A global, restart-gated, all-future-plugins grant sits in a shopping
 * list… If you keep a global shortcut, label it like a dangerous setting:
 * 'Allow all current and future extensions to run without asking' with a
 * confirm dialog, not a casual checkbox."
 *
 * ONE CORRECTION TO THE REVIEW, because it changes what this had to be. The
 * grant is NOT default-on: `mcp_auto_approve` defaults to False
 * (`core/config.py`) and `mcp_call` sits at "ask" in the per-tool permission
 * table. What the page did was worse in a subtler way — the checkbox rendered
 * `auto_approve_effective`, which the daemon computes as
 *
 *     global OR any(server.auto_approve)          (routes/agents.py)
 *
 * so ONE extension connected with its own per-server grant made the GLOBAL
 * checkbox appear checked. The user could not tell a single-pack grant from a
 * blanket one, and unchecking the box to undo the pack's grant also wrote the
 * global flag off. That is the defect being fixed: the two facts are now shown
 * as the two facts they are.
 *
 * WHAT THIS IS NOT. It is not a new permission model — the review asked for
 * UI/UX only, and the daemon already has both switches. This panel stops
 * conflating them, defaults new extensions to ask, and puts the blast-radius
 * essay behind the confirm step instead of printing it beside a shopping list.
 */

import { useState } from "react";
import { Check, ShieldAlert, ShieldCheck } from "lucide-react";
import { Modal } from "@/components/Modal";
import { LoaderInline } from "@/components/ui";

export interface PermissionRow {
  /** Server name as the daemon knows it. */
  name: string;
  /** Per-server grant — the honest, scoped one. */
  autoApprove: boolean;
  /** Tool count, for "what it exposes". */
  tools: number;
}

export function PermissionsPanel({
  rows,
  globalOn,
  busyKey,
  onToggleServer,
  onSetGlobal,
}: {
  rows: PermissionRow[];
  /** The GLOBAL flag, on its own — never mixed with the per-server ones. */
  globalOn: boolean;
  busyKey: string | null;
  onToggleServer: (name: string, next: boolean) => void;
  onSetGlobal: (next: boolean) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const covered = rows.filter((r) => r.autoApprove).length;

  return (
    <section
      data-testid="permissions-panel"
      className="rounded-xl border border-white/[0.06] bg-white/[0.015] p-3.5"
    >
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <ShieldCheck size={14} className="shrink-0 text-accent-soft/80" aria-hidden />
        <h3 className="text-[12.5px] font-semibold tracking-wide text-zinc-200">
          Permissions
        </h3>
        <span className="text-[11.5px] text-zinc-500">
          {globalOn
            ? "every extension runs without asking"
            : covered === 0
              ? "agents ask before every extension call"
              : `${covered} of ${rows.length} run without asking`}
        </span>
      </div>

      {/* THE ONE LINE, not the essay (review: "Short line on this page: 'New
          extensions will ask before running.' … Do not put the blast-radius
          essay inline"). It stays here rather than linking out because the
          review asked for permissions to live on this page. */}
      <p className="mb-3 text-[11.5px] leading-relaxed text-zinc-500">
        New extensions ask before running. Chat is unaffected — arming a tool
        there is already your approval.
      </p>

      {rows.length === 0 ? (
        <p className="text-[12px] text-zinc-600">
          Nothing connected yet. Enable an extension and it appears here.
        </p>
      ) : (
        <ul className="space-y-1" data-testid="permission-rows">
          {rows.map((r) => {
            const on = globalOn || r.autoApprove;
            const forced = globalOn && !r.autoApprove;
            return (
              <li
                key={r.name}
                className="flex flex-wrap items-center gap-2 rounded-lg border border-white/[0.05] bg-white/[0.015] px-2.5 py-1.5"
              >
                <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-zinc-200">
                  {r.name}
                </span>
                <span className="shrink-0 text-[11px] tabular-nums text-zinc-500">
                  {r.tools} tool{r.tools === 1 ? "" : "s"}
                </span>
                <button
                  type="button"
                  onClick={() => onToggleServer(r.name, !r.autoApprove)}
                  disabled={busyKey === `auto:${r.name}` || forced}
                  data-testid={`perm-${r.name}`}
                  title={
                    forced
                      ? "The global switch below is on, so this runs without asking regardless"
                      : on
                        ? `Agents run ${r.name}'s tools without asking. Click to require approval.`
                        : `Agents ask before each ${r.name} call. Click to allow without asking.`
                  }
                  className={`shrink-0 rounded-md border px-2 py-0.5 text-[11px] font-medium transition-colors disabled:opacity-50 ${
                    on
                      ? "border-amber-400/30 bg-amber-400/[0.08] text-amber-200/90"
                      : "border-white/10 text-zinc-400 hover:border-white/25 hover:text-zinc-200"
                  }`}
                >
                  {busyKey === `auto:${r.name}` ? (
                    "…"
                  ) : on ? (
                    <>Allowed{forced ? " (global)" : ""}</>
                  ) : (
                    "Asks first"
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {/* THE GLOBAL, labelled as what it is. Not a checkbox in a list. */}
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t hairline pt-3">
        <ShieldAlert
          size={13}
          className={`shrink-0 ${globalOn ? "text-amber-300" : "text-zinc-600"}`}
          aria-hidden
        />
        <span className="min-w-0 flex-1 text-[11.5px] leading-relaxed text-zinc-500">
          Allow <span className="text-zinc-300">all current and future</span>{" "}
          extensions to run without asking
        </span>
        <button
          type="button"
          onClick={() => (globalOn ? onSetGlobal(false) : setConfirming(true))}
          disabled={busyKey === "global"}
          data-testid="perm-global"
          aria-pressed={globalOn}
          className={`shrink-0 rounded-md border px-2.5 py-1 text-[11.5px] font-medium transition-colors disabled:opacity-50 ${
            globalOn
              ? "border-amber-400/40 bg-amber-400/[0.12] text-amber-200"
              : "border-white/10 text-zinc-400 hover:border-white/25 hover:text-zinc-200"
          }`}
        >
          {busyKey === "global" ? "…" : globalOn ? "On" : "Off"}
        </button>
      </div>

      {/* THE ESSAY, at the confirm step — where a warning is read instead of
          skimmed past. Turning it OFF needs no confirmation: narrowing a
          permission is never the dangerous direction. */}
      {confirming && (
        <Modal
          label="Allow every extension to run without asking"
          onClose={() => setConfirming(false)}
          className="w-full max-w-md"
          testId="perm-global-confirm"
        >
          <header className="flex shrink-0 items-center gap-2 border-b hairline px-4 py-3">
            <ShieldAlert size={15} className="text-amber-300" aria-hidden />
            <h2 className="text-[13px] font-semibold tracking-wide text-zinc-100">
              Allow every extension to run without asking?
            </h2>
          </header>
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 text-[12.5px] leading-relaxed text-zinc-300">
            <p>
              This is a <span className="text-amber-200/90">blanket</span> grant.
              It covers every extension connected now{" "}
              <span className="text-amber-200/90">and every one you connect
              later</span>, including ones whose tools you have not seen yet.
            </p>
            <p>
              Autonomous agents will run those tools without stopping to ask —
              reading and writing files, reaching the network, or opening a
              browser, depending on what each extension exposes.
            </p>
            <p className="text-zinc-400">
              It takes effect after the next Iron Jarvis restart, and turning it
              back off makes every extension ask again. Chat is unaffected:
              arming a tool there is already your approval.
            </p>
            <p className="text-zinc-400">
              If you only trust one extension, close this and use its own
              &ldquo;Asks first&rdquo; switch instead.
            </p>
          </div>
          <footer className="flex shrink-0 items-center justify-end gap-2 border-t hairline px-4 py-3">
            <button
              type="button"
              onClick={() => setConfirming(false)}
              className="btn-ghost py-1.5 text-xs"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => {
                setConfirming(false);
                onSetGlobal(true);
              }}
              data-testid="perm-global-confirm-yes"
              className="inline-flex items-center gap-1.5 rounded-xl border border-amber-400/40 bg-amber-400/[0.12] px-3 py-1.5 text-xs font-medium text-amber-200 transition-colors hover:bg-amber-400/20"
            >
              <Check size={13} /> Allow all extensions
            </button>
          </footer>
        </Modal>
      )}
    </section>
  );
}
