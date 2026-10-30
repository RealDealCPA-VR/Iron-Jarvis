/**
 * THE ANIMATED BACKGROUNDS (v1.410.0) — the only background thing the app ships.
 *
 * The backgrounds themselves are five small HTML files in the repository
 * (`backgrounds/<id>/index.html`, written for Iron Jarvis). None is in the
 * installer or this bundle: the daemon downloads the one the user picks from
 * the INSTALLED version's tag (`backgroundSourceUrl`), checks it against
 * `sha256` here, keeps it until the user deletes it, and serves it sealed off
 * (no network, sandboxed iframe — components/BackgroundLayer.tsx).
 *
 * Twin: `src/iron_jarvis/backgrounds/catalog.py`. `tests/test_backgrounds_v1410.py`
 * pins the two equal and every size + sha256 to the file in the repository, so
 * editing a background without updating both catalogs fails CI.
 *
 * `preview` is a CSS background drawn from the theme's own variables, so the
 * small swatch in Settings follows the colours being picked.
 */

export const BACKGROUND_REPO = "RealDealCPA-VR/Iron-Jarvis";

export interface BackgroundEntry {
  id: string;
  name: string;
  /** One line about the look. */
  blurb: string;
  /** Bytes of the file (what a pick downloads). */
  size: number;
  sha256: string;
  /** A CSS `background` value — the swatch on the picker. */
  preview: string;
}

const A = "rgb(var(--accent-rgb)";
const D = "rgb(var(--accent-deep-rgb)";
const G = "rgb(var(--ink-950))";

export const BACKGROUNDS: BackgroundEntry[] = [
  {
    id: "aurora",
    name: "Aurora",
    blurb: "Soft bands of colour drifting slowly",
    size: 9191,
    sha256: "effec7a0c0aa13c9ee35ae0fc942f0cfb47be210a50b43b400e037caf9a2971b",
    preview: `radial-gradient(80% 45% at 30% 30%, ${A} / 0.55), transparent 70%), radial-gradient(70% 40% at 75% 65%, ${D} / 0.5), transparent 70%), ${G}`,
  },
  {
    id: "particles",
    name: "Drift",
    blurb: "A quiet field of drifting points",
    size: 8343,
    sha256: "18293ecc9405301c8747ab5325d242148ba04882350bb151aca14f9d364234cd",
    preview: `radial-gradient(circle at 20% 30%, ${A} / 0.8) 0 2px, transparent 3px), radial-gradient(circle at 60% 20%, ${A} / 0.7) 0 2px, transparent 3px), radial-gradient(circle at 75% 70%, ${D} / 0.8) 0 2px, transparent 3px), radial-gradient(circle at 35% 75%, ${A} / 0.6) 0 2px, transparent 3px), ${G}`,
  },
  {
    id: "waves",
    name: "Waves",
    blurb: "Thin lines rolling slowly across",
    size: 7624,
    sha256: "354305421dac7c977573e84887f6d1faad58e869cdc72e4b40855871d843175d",
    preview: `repeating-linear-gradient(170deg, transparent 0 9px, ${A} / 0.35) 9px 10px), ${G}`,
  },
  {
    id: "grid-beams",
    name: "Grid",
    blurb: "A faint grid with beams of light",
    size: 9400,
    sha256: "75242e1c3a0590ba442d1e31fb52d15945fca90fb89df8bdd63ab3e3a008d176",
    preview: `linear-gradient(90deg, transparent 45%, ${A} / 0.5) 50%, transparent 55%), repeating-linear-gradient(0deg, ${A} / 0.18) 0 1px, transparent 1px 12px), repeating-linear-gradient(90deg, ${A} / 0.18) 0 1px, transparent 1px 12px), ${G}`,
  },
  {
    id: "reactor",
    name: "Reactor",
    blurb: "A calm glow with slowly turning rings",
    size: 9059,
    sha256: "a938adbdfc4abfa3dab7963870bfd666243201095434dcf50b092b183acc26d3",
    preview: `radial-gradient(circle at 65% 60%, ${A} / 0.6) 0 8%, transparent 9% 14%, ${A} / 0.4) 15% 16%, transparent 17% 24%, ${D} / 0.4) 25% 26%, transparent 40%), ${G}`,
  },
];

const BY_ID = new Map(BACKGROUNDS.map((b) => [b.id, b]));

export function backgroundEntry(id: string | null | undefined): BackgroundEntry | null {
  return (id && BY_ID.get(id)) || null;
}

export function isBackgroundId(id: unknown): id is string {
  return typeof id === "string" && BY_ID.has(id);
}

/** Where the daemon downloads `id` from for app version `version`: the file
 *  as it is at that release's tag, never a branch. */
export function backgroundSourceUrl(id: string, version: string): string {
  return `https://raw.githubusercontent.com/${BACKGROUND_REPO}/v${version}/backgrounds/${id}/index.html`;
}

/** "8 KB" — sizes in Settings. */
export function sizeWords(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}
