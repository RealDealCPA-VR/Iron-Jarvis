"use client";

/**
 * Stable / Fresh on the Updates page (v1.405.0) — the desktop app only (a
 * browser tab has no bridge, and an older desktop shell without
 * `update.channels` would ignore the setting, so the choice is not drawn
 * there).
 *
 * A choice saves `update_channel` through the one settings writer and then asks
 * the desktop for a check, so the new channel is read at once. "Keep this
 * version off Stable" asks the daemon to run the Stable workflow with
 * `hold=<this PC's version>` through the user's own signed-in GitHub CLI — two
 * presses — or shows the command and the workflow's page when it cannot.
 */

import { useEffect, useRef, useState } from "react";
import { ShieldCheck, Zap, PauseCircle, ExternalLink } from "lucide-react";
import { get, post, put, ApiError } from "@/lib/api";
import { Code, ErrorNote, SuccessNote, SectionLabel } from "@/components/ui";
import {
  CHANNEL_WORDS,
  UPDATE_CHANNEL_KEY,
  aheadOfStable,
  aheadWords,
  asChannel,
  asHoldView,
  stableHasIt,
  versionWords,
  type ChannelVersions,
  type HoldView,
  type UpdateChannel,
} from "@/lib/updateChannel";

export interface ChannelBridge {
  channels: () => Promise<ChannelVersions | null>;
  check: () => Promise<unknown>;
}

function message(err: unknown): string {
  return err instanceof ApiError ? err.message : String((err as Error)?.message ?? err);
}

const ORDER: UpdateChannel[] = ["stable", "fresh"];

export function UpdateChannelChoice({
  bridge,
  current,
  onChecked,
}: {
  bridge: ChannelBridge;
  /** The version this PC runs (from the update state), if known. */
  current?: string | null;
  /** The check's answer, handed back so the card above shows it. */
  onChecked?: (state: unknown) => void;
}) {
  const [versions, setVersions] = useState<ChannelVersions | null>(null);
  const [channel, setChannel] = useState<UpdateChannel | null>(null);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    bridge
      .channels()
      .then((v) => {
        if (!alive.current || !v) return;
        setVersions(v);
        setChannel((c) => c ?? asChannel(v.channel));
      })
      .catch(() => {});
    return () => {
      alive.current = false;
    };
  }, [bridge]);

  const pc = current ?? versions?.current ?? null;

  async function choose(next: UpdateChannel) {
    if (saving || next === channel) return;
    setSaving(true);
    setError("");
    setNote("");
    try {
      await put("/settings/values", { values: { [UPDATE_CHANNEL_KEY]: next } });
      if (!alive.current) return;
      setChannel(next);
      setNote(`This PC is on ${CHANNEL_WORDS[next].label} now. Checking for updates…`);
      try {
        const s = await bridge.check();
        if (alive.current && onChecked) onChecked(s);
      } catch {
        /* the check's own outcome is the card above's to say */
      } finally {
        if (alive.current) setNote(`This PC is on ${CHANNEL_WORDS[next].label} now.`);
      }
    } catch (err) {
      if (alive.current) setError(`The channel was not changed: ${message(err)}`);
    } finally {
      if (alive.current) setSaving(false);
    }
  }

  const ahead = channel === "stable" && pc && versions?.stable && aheadOfStable(pc, versions.stable);

  return (
    <div className="space-y-3" data-testid="update-channel">
      <SectionLabel>Update channel</SectionLabel>
      <div role="radiogroup" aria-label="Update channel" className="grid gap-2 sm:grid-cols-2">
        {ORDER.map((c) => {
          const on = channel === c;
          const Icon = c === "stable" ? ShieldCheck : Zap;
          return (
            <button
              key={c}
              type="button"
              role="radio"
              aria-checked={on}
              data-testid={`update-channel-${c}`}
              disabled={saving || channel === null}
              onClick={() => choose(c)}
              className={`flex items-start gap-2.5 rounded-xl border px-3 py-2.5 text-left transition-colors disabled:opacity-60 ${
                on
                  ? "border-accent/40 bg-accent/[0.08]"
                  : "border-white/[0.08] bg-white/[0.015] hover:border-white/20"
              }`}
            >
              <Icon size={15} className={`mt-0.5 shrink-0 ${on ? "text-accent-soft" : "text-zinc-500"}`} aria-hidden />
              <span className="min-w-0">
                <span className="block text-[14px] font-medium text-zinc-100">
                  {CHANNEL_WORDS[c].label}
                  {versions && (
                    <span className="ml-1.5 font-normal text-zinc-500" data-testid={`update-channel-${c}-version`}>
                      · offers {versionWords(versions[c])}
                    </span>
                  )}
                </span>
                <span className="block text-[12px] leading-relaxed text-zinc-400">{CHANNEL_WORDS[c].sentence}</span>
              </span>
            </button>
          );
        })}
      </div>

      <p className="text-[12px] text-zinc-500" data-testid="update-channel-pc">
        This PC runs {versionWords(pc)}.
      </p>

      {ahead && (
        <p className="text-[13px] leading-relaxed text-zinc-300" data-testid="update-channel-ahead">
          {aheadWords(String(pc), String(versions?.stable))}
        </p>
      )}

      {note && <SuccessNote>{note}</SuccessNote>}
      {error && <ErrorNote>{error}</ErrorNote>}

      {pc && <HoldThisVersion version={pc} stable={versions?.stable ?? null} />}

      <p className="text-[11px] leading-relaxed text-zinc-600">
        The day&apos;s wait is time only: Iron Jarvis cannot see whether another PC ran a version. You can hold a
        version back.
      </p>
    </div>
  );
}

/** "Keep this version off Stable" — two presses; the command when the app cannot ask. */
function HoldThisVersion({ version, stable }: { version: string; stable: string | null }) {
  const [view, setView] = useState<HoldView | null>(null);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState("");
  const [error, setError] = useState("");

  if (stableHasIt(version, stable)) {
    return (
      <p className="text-[12px] leading-relaxed text-zinc-500" data-testid="update-hold-already">
        {versionWords(version)} is already on Stable, so it cannot be held back now: Stable never moves to an
        older version.
      </p>
    );
  }

  async function first() {
    setBusy(true);
    setError("");
    setDone("");
    try {
      const v = asHoldView(await get<unknown>(`/update/stable-hold?version=${encodeURIComponent(version)}`));
      if (!v) throw new Error("Iron Jarvis did not answer about holding a version back.");
      setView(v);
      setArmed(v.can_run);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  async function second() {
    setBusy(true);
    setError("");
    try {
      const res = await post<{ message?: string }>("/update/stable-hold", { version, confirm: true });
      setDone(res?.message || `Asked GitHub to keep ${versionWords(version)} off Stable.`);
      setArmed(false);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2" data-testid="update-hold">
      {!done && (
        <div className="flex flex-wrap items-center gap-2">
          {armed ? (
            <>
              <button
                type="button"
                data-testid="update-hold-confirm"
                onClick={second}
                disabled={busy}
                className="btn-ghost text-sm text-amber-200 disabled:opacity-50"
              >
                <PauseCircle size={14} /> Press again to keep {versionWords(version)} off Stable
              </button>
              <button
                type="button"
                onClick={() => setArmed(false)}
                disabled={busy}
                className="text-[12px] text-zinc-400 hover:text-zinc-200"
              >
                Cancel
              </button>
            </>
          ) : (
            <button
              type="button"
              data-testid="update-hold-start"
              onClick={first}
              disabled={busy}
              className="btn-ghost text-sm disabled:opacity-50"
            >
              <PauseCircle size={14} /> Keep this version off Stable
            </button>
          )}
        </div>
      )}
      {view && !view.can_run && (
        <div className="space-y-1.5 text-[12px] leading-relaxed text-zinc-400" data-testid="update-hold-by-hand">
          <p>{view.reason} To keep {versionWords(version)} off Stable yourself, run this in a terminal:</p>
          <Code className="block whitespace-pre-wrap break-all">{view.command}</Code>
          {view.actions_url && (
            <p>
              Or open{" "}
              <a
                href={view.actions_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-accent-soft hover:underline"
              >
                the Stable channel on GitHub <ExternalLink size={11} aria-hidden />
              </a>
              , press Run workflow and type {String(version).replace(/^v/, "")} under hold.
            </p>
          )}
        </div>
      )}
      {done && <SuccessNote>{done}</SuccessNote>}
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}
