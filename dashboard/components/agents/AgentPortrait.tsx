"use client";

/**
 * AgentPortrait — the portrait controls for ANY agent (v1.214.0).
 *
 * WHAT CHANGED. Upload / Generate / Remove existed since v1.171.0, but only
 * inside `DynamicRow` — so a portrait was something only an agent the user had
 * CREATED could have. The daemon never had that limit: storage is
 * `<home>/avatars/<slug>.png` keyed by the bare agent name, and
 * `POST /agents/{name}/avatar` has always said so in its own comment ("Works
 * for BUILTIN names and dynamic slugs alike"). The restriction was one
 * component's location. Reported: "every agent should be customizable
 * including the predefined agents … with the ability for the user to choose a
 * custom image for any of the agents."
 *
 * So this is the row, extracted, taking a NAME rather than a dynamic-agent
 * record — which is all it ever needed. Built-in, yours and remote agents now
 * reach one implementation, and there is no second copy to drift.
 *
 * THE UPLOAD GOES THROUGH THE CROPPER. A picked file is never posted directly:
 * `PortraitCropper` returns a square PNG and only then does the POST happen.
 * The size check stays HERE and on the raw file, before any decoding — a 40 MB
 * photo must be refused with a plain line, not decoded into a canvas first.
 * (The cropper's output is square at 512px, comfortably under the cap, so the
 * check is about the pick, not the result.)
 */

import { useState } from "react";
import { Trash2, Upload, Wand2 } from "lucide-react";
import { API_BASE, ApiError, del, ijToken, post } from "@/lib/api";
import { ErrorNote, LoaderInline } from "@/components/ui";
import AgentFace, { type FaceOverride } from "./AgentFace";
import { PortraitCropper } from "./PortraitCropper";

/** Upload cap on the PICKED file — mirrors the daemon's 2MB decoded limit so
 *  an oversized pick fails here with a plain line instead of a 413. */
export const AVATAR_MAX_BYTES = 2 * 1024 * 1024;

/** <img> can't send the Authorization header — the token rides as `?token=`,
 *  the pattern every media surface in the app uses. `rev` busts the browser
 *  cache after a write, since the URL itself never changes. */
export function avatarSrc(rel: string, rev: number | string): string {
  const token = ijToken();
  return `${API_BASE}${rel}?v=${encodeURIComponent(String(rev))}${
    token ? `&token=${encodeURIComponent(token)}` : ""
  }`;
}

export function AgentPortrait({
  name,
  avatar,
  face,
  onChanged,
  compact = false,
}: {
  /** The BARE agent name — the key the daemon stores portraits under, for
   *  every kind (roster rows serve `_avatar_url(bare)` for all three). */
  name: string;
  /** The stored portrait's serve path, or null/undefined when none is stored. */
  avatar?: string | null;
  /** The chosen face, drawn when no portrait is stored. */
  face?: FaceOverride | null;
  /** A write landed — the caller refetches whatever list it owns. */
  onChanged: () => void;
  compact?: boolean;
}) {
  // `rev` ONLY busts the <img> cache. Whether a portrait EXISTS always comes
  // from the daemon via `avatar`, so a failed write can never leave this row
  // pretending one is stored (the v1.171.0 rule, kept).
  const [rev, setRev] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** The picked file, waiting on the crop. Null = no cropper on screen. */
  const [pending, setPending] = useState<File | null>(null);
  const avatarUrl = avatar ? avatarSrc(avatar, rev) : undefined;

  function pick(file: File) {
    if (file.size > AVATAR_MAX_BYTES) {
      setError("portrait too large — 2 MB max");
      return;
    }
    setError(null);
    setPending(file);
  }

  async function store(imageB64: string) {
    setPending(null);
    setBusy(true);
    setError(null);
    try {
      await post(`/agents/${encodeURIComponent(name)}/avatar`, { image_b64: imageB64 });
      setRev((v) => v + 1);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      await post(`/agents/${encodeURIComponent(name)}/avatar`, { generate: true });
      setRev((v) => v + 1);
      onChanged();
    } catch (err) {
      // The daemon's honest 409 ("no image model is connected — …") lands here
      // as plain text — shown as-is, never swapped for a placeholder.
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await del(`/agents/${encodeURIComponent(name)}/avatar`);
      setRev((v) => v + 1);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const btn =
    "inline-flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft disabled:opacity-50";

  return (
    <>
      <div
        data-testid={`avatar-row-${name}`}
        className="flex flex-wrap items-center gap-2"
      >
        <AgentFace
          name={name}
          mood="idle"
          size={compact ? 24 : 28}
          avatarUrl={avatarUrl}
          face={face}
        />
        <span className="text-[11px] text-zinc-500">Portrait</span>
        <span className="ml-auto flex items-center gap-1.5">
          <label
            className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft"
            title={`Upload a portrait for "${name}" (PNG/JPEG/WebP, 2 MB max) — you choose the square`}
          >
            <Upload size={12} /> Upload
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp"
              className="hidden"
              aria-label={`Upload a portrait for ${name}`}
              disabled={busy}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) pick(f);
                // Cleared so picking the SAME file again still fires `change`
                // — otherwise a cancelled crop could not be retried.
                e.target.value = "";
              }}
            />
          </label>
          <button
            type="button"
            onClick={generate}
            disabled={busy}
            title={`Generate a portrait for "${name}" with the connected image model`}
            className={btn}
          >
            {busy ? <LoaderInline label="…" /> : <><Wand2 size={12} /> Generate</>}
          </button>
          {avatar && (
            <button
              type="button"
              onClick={remove}
              disabled={busy}
              title={`Remove the stored portrait of "${name}" (back to the drawn face)`}
              className={`${btn} hover:border-rose-400/40 hover:text-rose-300`}
            >
              <Trash2 size={12} /> Remove
            </button>
          )}
        </span>
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}
      {pending && (
        // `key` on the file so picking a DIFFERENT file remounts the cropper
        // rather than leaving the previous image's pan/zoom in place.
        <PortraitCropper
          key={`${pending.name}:${pending.size}:${pending.lastModified}`}
          file={pending}
          agentName={name}
          onCancel={() => setPending(null)}
          onCropped={(b64) => void store(b64)}
        />
      )}
    </>
  );
}

export default AgentPortrait;
