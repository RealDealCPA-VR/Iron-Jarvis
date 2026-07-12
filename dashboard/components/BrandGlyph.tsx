"use client";

// Real brand marks for the Marketplace, bundled at build time from Simple Icons
// (import-only — no logos are hand-drawn here) so nothing phones a logo CDN and
// the marks work fully offline, in keeping with the local-first ethos. Imported
// per-icon so only the marks we use ship in the bundle. Any connector without a
// brand mark (or a brand not in the set, e.g. Slack / OneDrive) falls back to its
// emoji glyph.
import {
  siGithub,
  siPostgresql,
  siSentry,
  siPuppeteer,
  siGmail,
  siNotion,
  siGooglemaps,
  siGoogledrive,
  siDropbox,
  siBox,
  siBrave,
  siStripe,
} from "simple-icons";

interface BrandIcon {
  title: string;
  hex: string; // brand color, no leading "#"
  path: string; // single 24x24 SVG path
}

/** Connector id (see connectors/catalog.py) → its Simple Icons brand mark. */
const BRAND: Record<string, BrandIcon> = {
  github: siGithub,
  postgres: siPostgresql,
  sentry: siSentry,
  puppeteer: siPuppeteer,
  gmail: siGmail,
  notion: siNotion,
  google_maps: siGooglemaps,
  google_drive: siGoogledrive,
  dropbox: siDropbox,
  box: siBox,
  brave_search: siBrave,
  stripe: siStripe,
};

/** Whether a connector has a real brand mark (vs. the emoji fallback). */
export function hasBrandMark(id: string): boolean {
  return id in BRAND;
}

/**
 * A brand color too dark to read on the dark UI (GitHub #181717, Notion #000000,
 * …) is rendered near-white instead; brighter brand colors render as-is.
 */
function displayFill(hex: string): string {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return Number.isNaN(lum) || lum < 0.4 ? "#e8e8ea" : `#${h}`;
}

/**
 * The connector's real brand mark on a softly glowing tile, in its brand color.
 * Falls back to the emoji `glyph` for connectors with no brand mark (the emoji
 * tile keeps the prior look, incl. the emerald tint when connected). Purely
 * presentational.
 */
export function BrandGlyph({
  id,
  glyph,
  connected = false,
}: {
  id: string;
  glyph: string;
  connected?: boolean;
}) {
  const brand = BRAND[id];

  if (!brand) {
    return (
      <span
        aria-hidden="true"
        className="relative grid h-12 w-12 shrink-0 place-items-center"
      >
        <span
          className={`absolute inset-0 rounded-2xl blur-[10px] transition-colors ${
            connected ? "bg-emerald-400/20" : "bg-accent/15"
          }`}
        />
        <span
          className={`relative grid h-12 w-12 place-items-center rounded-2xl border text-2xl leading-none ${
            connected
              ? "border-emerald-400/30 bg-emerald-400/[0.06]"
              : "border-white/[0.1] bg-white/[0.03]"
          }`}
        >
          {glyph}
        </span>
      </span>
    );
  }

  const fill = displayFill(brand.hex);
  return (
    <span
      aria-hidden="true"
      className="relative grid h-12 w-12 shrink-0 place-items-center"
    >
      <span
        className="absolute inset-0 rounded-2xl blur-[10px]"
        style={{ backgroundColor: fill, opacity: 0.16 }}
      />
      <span className="relative grid h-12 w-12 place-items-center rounded-2xl border border-white/[0.1] bg-white/[0.04]">
        <svg
          role="img"
          viewBox="0 0 24 24"
          className="h-6 w-6"
          fill={fill}
          aria-label={`${brand.title} logo`}
        >
          <path d={brand.path} />
        </svg>
      </span>
    </span>
  );
}
