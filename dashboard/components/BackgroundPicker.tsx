"use client";

/**
 * Pick an animated background for a theme, and manage the ones on this PC
 * (v1.410.0). Settings → Appearance → Make your own (components/ThemeMaker).
 *
 * Picking one downloads THAT file only (the daemon fetches it from the
 * installed version's release and checks it — backgrounds/store.py); a
 * background already on this PC is used as it is. `DownloadedBackgrounds`
 * lists what is kept here, with its size, and Delete frees the space (a theme
 * that used it simply shows its plain colours).
 */

import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { Check, Download } from "lucide-react";
import { ConfirmButton } from "@/components/ui";
import { BACKGROUNDS, backgroundEntry, sizeWords } from "@/lib/backgroundCatalog";
import {
  BACKGROUNDS_EVENT,
  deleteBackground,
  downloadBackground,
  listBackgrounds,
  type BackgroundList,
} from "@/lib/backgrounds";
import type { BackgroundMotion, PaletteBackground, ThemeVars } from "@/lib/themePalette";

export const STILL_WHEN =
  "It holds still on battery power, when Windows is set to show less motion, and whenever the window is hidden or minimised.";

/** The daemon's list, re-read on every change; null while unknown or unreachable. */
export function useBackgroundList(): { list: BackgroundList | null; refresh: () => void } {
  const [list, setList] = useState<BackgroundList | null>(null);
  const refresh = useCallback(() => {
    listBackgrounds()
      .then(setList)
      .catch(() => setList(null));
  }, []);
  useEffect(() => {
    refresh();
    window.addEventListener(BACKGROUNDS_EVENT, refresh);
    return () => window.removeEventListener(BACKGROUNDS_EVENT, refresh);
  }, [refresh]);
  return { list, refresh };
}

function varsStyle(vars: ThemeVars | undefined): CSSProperties {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(vars ?? {})) out[`--${k}`] = v;
  return out as CSSProperties;
}

const MOTIONS: { id: BackgroundMotion; label: string }[] = [
  { id: "animated", label: "Animated" },
  { id: "still", label: "Still" },
];

export function BackgroundPicker({
  value,
  onChange,
  vars,
}: {
  value: PaletteBackground | null;
  onChange: (next: PaletteBackground | null) => void;
  /** The draft's colours, so the swatches follow them. */
  vars?: ThemeVars;
}) {
  const { list } = useBackgroundList();
  const [busy, setBusy] = useState<string>("");
  const [error, setError] = useState("");
  const stateOf = (id: string) => list?.backgrounds.find((b) => b.id === id)?.state ?? "absent";

  const pick = async (id: string | null) => {
    setError("");
    if (id === null) return onChange(null);
    const motion = value?.motion ?? "animated";
    if (stateOf(id) === "ready") return onChange({ id, motion });
    setBusy(id);
    try {
      await downloadBackground(id);
      onChange({ id, motion });
    } catch (e) {
      const words = e instanceof Error && e.message ? e.message : "The download did not finish.";
      setError(`${backgroundEntry(id)?.name ?? "That background"} could not be downloaded: ${words}`);
    } finally {
      setBusy("");
    }
  };

  return (
    <div data-testid="background-picker" className="space-y-2">
      <div role="radiogroup" aria-label="Animated background" className="grid grid-cols-3 gap-1.5 sm:grid-cols-6">
        <button
          type="button"
          role="radio"
          aria-checked={value === null}
          data-testid="background-choice-none"
          onClick={() => pick(null)}
          className={`flex flex-col items-stretch gap-1 rounded-lg border p-1 text-left text-[11px] ${
            value === null ? "border-accent/60 text-accent-soft" : "border-white/10 text-zinc-400 hover:border-white/20"
          }`}
        >
          <span className="h-10 rounded-md border border-white/10 bg-ink-950" style={varsStyle(vars)} aria-hidden />
          <span className="truncate px-0.5">None</span>
        </button>
        {BACKGROUNDS.map((b) => {
          const on = value?.id === b.id;
          const here = stateOf(b.id) === "ready";
          return (
            <button
              key={b.id}
              type="button"
              role="radio"
              aria-checked={on}
              disabled={!!busy}
              data-testid={`background-choice-${b.id}`}
              title={`${b.blurb}. ${here ? "On this PC" : `Downloads ${sizeWords(b.size)} when picked`}.`}
              onClick={() => pick(b.id)}
              className={`flex flex-col items-stretch gap-1 rounded-lg border p-1 text-left text-[11px] disabled:opacity-60 ${
                on ? "border-accent/60 text-accent-soft" : "border-white/10 text-zinc-400 hover:border-white/20"
              }`}
            >
              <span
                aria-hidden
                className="h-10 rounded-md border border-white/10"
                style={{ ...varsStyle(vars), background: b.preview }}
              />
              <span className="flex items-center gap-1 truncate px-0.5">
                {b.name}
                {busy === b.id ? (
                  <span className="text-zinc-500">…</span>
                ) : here ? (
                  <Check size={11} aria-label="On this PC" className="text-tone-success" />
                ) : (
                  <Download size={11} aria-label="Downloads when picked" className="text-zinc-500" />
                )}
              </span>
            </button>
          );
        })}
      </div>
      {busy && (
        <p role="status" className="text-[11px] text-zinc-500">
          Downloading {backgroundEntry(busy)?.name}…
        </p>
      )}
      {error && (
        <p role="alert" data-testid="background-error" className="text-[12px] text-tone-danger">
          {error}
        </p>
      )}
      {value && (
        <div className="flex flex-wrap items-center gap-2">
          <div role="radiogroup" aria-label="Background motion" data-testid="background-motion" className="flex gap-1">
            {MOTIONS.map((m) => (
              <button
                key={m.id}
                type="button"
                role="radio"
                aria-checked={value.motion === m.id}
                onClick={() => onChange({ ...value, motion: m.id })}
                className={`rounded-lg border px-2.5 py-1 text-[12px] ${
                  value.motion === m.id
                    ? "border-accent/50 bg-accent/[0.12] text-accent-soft"
                    : "border-white/10 text-zinc-400 hover:border-white/20 hover:text-zinc-200"
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>
      )}
      <p className="text-[11px] leading-relaxed text-zinc-500">
        Each one is downloaded from Iron Jarvis&rsquo;s own release when you pick it and kept on this PC
        until you delete it. It runs sealed off: it cannot reach the internet or the rest of the app.{" "}
        {STILL_WHEN}
      </p>
    </div>
  );
}

/** What is kept on this PC, with sizes, and Delete. Nothing when none is. */
export function DownloadedBackgrounds() {
  const { list } = useBackgroundList();
  const [error, setError] = useState("");
  const kept = (list?.backgrounds ?? []).filter((b) => b.state !== "absent");
  if (!list || kept.length === 0) return null;
  return (
    <div data-testid="downloaded-backgrounds" className="space-y-1.5">
      <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">
        Backgrounds on this PC · {sizeWords(list.total_bytes)}
      </div>
      <ul className="space-y-1">
        {kept.map((b) => (
          <li
            key={b.id}
            data-testid={`downloaded-background-${b.id}`}
            className="flex items-center gap-2 rounded-lg border border-white/[0.07] px-2 py-1"
          >
            <span className="min-w-0 flex-1 truncate text-[12px] text-zinc-300">
              {backgroundEntry(b.id)?.name ?? b.name}
              <span className="ml-1.5 text-zinc-500">{sizeWords(b.bytes)}</span>
              {b.state === "outdated" && (
                <span className="ml-1.5 text-zinc-500">· updates the next time it is used</span>
              )}
            </span>
            <ConfirmButton
              label="Delete"
              title={`Delete the ${backgroundEntry(b.id)?.name ?? b.name} background from this PC`}
              onConfirm={() => {
                setError("");
                deleteBackground(b.id).catch((e) =>
                  setError(e instanceof Error && e.message ? e.message : "It could not be deleted."),
                );
              }}
            />
          </li>
        ))}
      </ul>
      {error && (
        <p role="alert" className="text-[12px] text-tone-danger">
          {error}
        </p>
      )}
    </div>
  );
}
