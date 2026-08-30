"use client";

/**
 * Adding a tool is a trust moment, so it gets a step (v1.216.0).
 *
 * From the review: "Clicking Add on 'Files & folders' is a trust moment.
 * Modal or slide-over: what the agent will be able to do · folder picker
 * before enable, not after · runtime check · which agents get it · 'Ask every
 * time' vs 'allow' · one primary button: Enable. Same for zip / list dir /
 * open URL. Opening a browser or zipping a folder is not the same as DNS
 * lookup. Equal-looking + Add buttons flatten risk."
 *
 * WHAT THIS DIALOG IS NOT: a second form. Everything it asks was already asked
 * SOMEWHERE — the placeholder values were an inline panel under the card, the
 * approval posture was a page-level checkbox, and who-gets-it was a sentence
 * in a paragraph. They are gathered here so the answer to "what am I agreeing
 * to?" is on one screen at the moment of agreeing, instead of spread across a
 * page the user has already scrolled past.
 *
 * It is deliberately the SAME dialog for a built-in and an extension. The
 * review's point is that the two grids look like two products; making the
 * commitment step identical is the strongest way to say they are one model
 * with different depth.
 */

import { useState } from "react";
import { FolderOpen, ShieldAlert, Users } from "lucide-react";
import { Modal } from "@/components/Modal";
import { ErrorNote, LoaderInline } from "@/components/ui";
import { RiskChips, SourceChip } from "./chips";
import {
  CAPABILITY_LABEL,
  RUNTIME_HELP,
  type Capability,
} from "./meta";

export interface EnablePlan {
  /** "built-in" or "extension" — drives the wording, not the behaviour. */
  kind: "builtin" | "extension";
  /** What the user sees it called. */
  title: string;
  /** The registered/technical id, shown small. */
  id: string;
  summary: string;
  caps: Capability[];
  /** Runtime the pack needs, if any. */
  needs?: string;
  official?: boolean;
  /** Values to collect before enabling — a path placeholder, an API key. */
  fields?: { key: string; label: string; hint?: string; kind: "path" | "text" }[];
  /** Extensions can be granted "run without asking" at the moment of enabling;
   *  built-in suite tools have no such flag (they are argv commands the daemon
   *  runs under the normal tool permission). */
  offerAutoApprove?: boolean;
}

export function EnableDialog({
  plan,
  busy,
  error,
  onCancel,
  onEnable,
}: {
  plan: EnablePlan;
  busy: boolean;
  error?: string | null;
  onCancel: () => void;
  onEnable: (values: Record<string, string>, autoApprove: boolean) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  // ASK EVERY TIME IS THE DEFAULT, and it is the default here rather than
  // somewhere in settings, because this is where the user is deciding. The
  // review: "Default ask each time for new plugins."
  const [auto, setAuto] = useState(false);
  const fields = plan.fields ?? [];
  const ready = fields.every((f) => (values[f.key] ?? "").trim().length > 0);
  const help = plan.needs ? RUNTIME_HELP[plan.needs] : undefined;

  return (
    <Modal
      label={`Enable ${plan.title}`}
      onClose={onCancel}
      busy={busy}
      className="w-full max-w-lg"
      testId="enable-dialog"
    >
      <header className="flex shrink-0 items-start gap-3 border-b hairline px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-[14px] font-semibold tracking-wide text-zinc-100">
            Enable {plan.title}
          </h2>
          <p className="mt-0.5 font-mono text-[11px] text-zinc-500">{plan.id}</p>
        </div>
        {plan.kind === "extension" && (
          <SourceChip official={Boolean(plan.official)} />
        )}
      </header>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        <p className="text-[13px] leading-relaxed text-zinc-400">{plan.summary}</p>

        {/* WHAT THE AGENT WILL BE ABLE TO DO — in words, not only as chips. */}
        <section className="rounded-xl border border-white/[0.06] bg-white/[0.015] p-3">
          <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wide text-zinc-500">
            What this lets agents do
          </h3>
          {plan.caps.length === 0 ? (
            <p className="text-[12.5px] text-zinc-400">
              Nothing on your machine — it works entirely inside the conversation.
            </p>
          ) : (
            <ul className="space-y-1">
              {plan.caps.map((c) => (
                <li key={c} className="text-[12.5px] text-zinc-300">
                  · {CAPABILITY_LABEL[c]}
                </li>
              ))}
            </ul>
          )}
          <div className="mt-2">
            <RiskChips caps={plan.caps} />
          </div>
        </section>

        {/* FOLDER / VALUE PICKER — before enabling, not after. */}
        {fields.length > 0 && (
          <section className="space-y-2.5">
            <h3 className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
              {fields.some((f) => f.kind === "path") ? "Where it may work" : "Details it needs"}
            </h3>
            {fields.map((f) => (
              <label key={f.key} className="block">
                <span className="mb-1 flex items-center gap-1.5 text-[12px] text-zinc-300">
                  {f.kind === "path" && (
                    <FolderOpen size={12} className="text-accent-soft/80" aria-hidden />
                  )}
                  {f.label}
                </span>
                <input
                  value={values[f.key] ?? ""}
                  onChange={(e) =>
                    setValues((v) => ({ ...v, [f.key]: e.target.value }))
                  }
                  placeholder={f.kind === "path" ? "C:\\Users\\you\\Documents" : "value"}
                  data-testid={`enable-field-${f.key}`}
                  className="field w-full px-2.5 py-1.5 font-mono text-[12px]"
                />
                {f.hint && (
                  <span className="mt-1 block text-[11px] text-zinc-500">{f.hint}</span>
                )}
              </label>
            ))}
          </section>
        )}

        {/* RUNTIME CHECK — stated, and never claimed as detected. */}
        {plan.needs && (
          <section className="flex items-start gap-2.5 rounded-xl border border-amber-400/20 bg-amber-400/[0.04] px-3 py-2.5">
            <ShieldAlert size={14} className="mt-0.5 shrink-0 text-amber-200/90" aria-hidden />
            <p className="text-[12px] leading-relaxed text-zinc-300">
              This extension runs through{" "}
              <span className="font-medium text-amber-200/90">{plan.needs}</span>.{" "}
              {help?.how}{" "}
              {help && (
                <a
                  href={help.href}
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent-soft underline underline-offset-2"
                >
                  Install {help.label}
                </a>
              )}
              . If it is missing, enabling still works and the pack will report
              it cannot start.
            </p>
          </section>
        )}

        {/* WHO GETS IT. */}
        <section className="flex items-start gap-2.5 text-[12px] leading-relaxed text-zinc-400">
          <Users size={14} className="mt-0.5 shrink-0 text-zinc-500" aria-hidden />
          <span>
            Every agent in this fleet can use it — built-in agents, the ones you
            created, and remote agents. Chat arms it per turn, as it always has.
          </span>
        </section>

        {/* ASK VS ALLOW — one question, at the moment it is being decided. */}
        {plan.offerAutoApprove && (
          <fieldset className="space-y-1.5">
            <legend className="mb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-500">
              When an agent uses it
            </legend>
            <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-white/[0.06] bg-white/[0.015] px-3 py-2 text-[12.5px]">
              <input
                type="radio"
                name="posture"
                checked={!auto}
                onChange={() => setAuto(false)}
                data-testid="enable-ask"
                className="mt-0.5 h-3.5 w-3.5 accent-accent"
              />
              <span>
                <span className="font-medium text-zinc-200">Ask me each time</span>
                <span className="mt-0.5 block text-[11.5px] text-zinc-500">
                  Recommended. You approve the first calls and can switch this
                  off once you trust it.
                </span>
              </span>
            </label>
            <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-white/[0.06] bg-white/[0.015] px-3 py-2 text-[12.5px]">
              <input
                type="radio"
                name="posture"
                checked={auto}
                onChange={() => setAuto(true)}
                data-testid="enable-allow"
                className="mt-0.5 h-3.5 w-3.5 accent-accent"
              />
              <span>
                <span className="font-medium text-zinc-200">
                  Run without asking
                </span>
                <span className="mt-0.5 block text-[11.5px] text-zinc-500">
                  Applies to this extension only, and takes effect after the next
                  restart.
                </span>
              </span>
            </label>
          </fieldset>
        )}

        {error && <ErrorNote>{error}</ErrorNote>}
      </div>

      <footer className="flex shrink-0 items-center justify-end gap-2 border-t hairline px-4 py-3">
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="btn-ghost py-1.5 text-xs"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => onEnable(values, auto)}
          disabled={busy || !ready}
          data-testid="enable-confirm"
          className="btn-accent py-1.5 text-xs"
        >
          {busy ? <LoaderInline label="Enabling…" /> : "Enable"}
        </button>
      </footer>
    </Modal>
  );
}
