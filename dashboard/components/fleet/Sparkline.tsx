"use client";

/** Viewbox geometry. `preserveAspectRatio="none"` stretches this to any box. */
const W = 100;
const H = 30;
const PAD = 2; // keeps the stroke off the top/bottom edge

/**
 * Turn a series into polyline `points` strings — one per contiguous run of
 * non-null readings.
 *
 * A `null` is a GAP, not a zero: an unreachable scrape window or a counter
 * reset means "we don't know", so the line BREAKS instead of diving to the
 * floor. Joining across a null would draw a plunge that never happened, which
 * is exactly the fabrication this whole surface exists to avoid.
 *
 * Runs of one reading come back as a single `x,y` pair (no space) — the caller
 * renders those as a dot, since a one-point polyline draws nothing.
 */
export function toSegments(points: (number | null)[], w = W, h = H): string[] {
  const vals = points.filter((p): p is number => p != null && Number.isFinite(p));
  if (points.length < 2 || vals.length === 0) return [];

  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min;
  const top = PAD;
  const bottom = h - PAD;
  // A flat series has no span to scale against — centre it rather than divide by zero.
  const y = (v: number) => (span === 0 ? h / 2 : bottom - ((v - min) / span) * (bottom - top));

  const segments: string[] = [];
  let run: string[] = [];
  points.forEach((p, i) => {
    if (p == null || !Number.isFinite(p)) {
      if (run.length) segments.push(run.join(" "));
      run = [];
      return;
    }
    const x = (i / (points.length - 1)) * w;
    run.push(`${x.toFixed(2)},${y(p).toFixed(2)}`);
  });
  if (run.length) segments.push(run.join(" "));
  return segments;
}

/**
 * A dependency-free inline sparkline. Scales to its container, auto-fits to the
 * min/max of the readings it was given, and breaks the line at every gap.
 */
export function Sparkline({
  points,
  className = "",
}: {
  points: (number | null)[];
  className?: string;
}) {
  const segments = toSegments(points);
  if (segments.length === 0) return null; // no data is drawn as no data

  const gaps = points.filter((p) => p == null || !Number.isFinite(p)).length;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      aria-hidden="true"
      className={`h-8 w-full overflow-visible text-accent ${className}`}
    >
      <title>
        {`${points.length - gaps} readings${gaps ? `, ${gaps} unavailable (shown as gaps)` : ""}`}
      </title>
      {segments.map((seg, i) => (
        <polyline
          key={i}
          // An isolated reading between two gaps is drawn as a zero-length
          // segment: with a round cap that paints a dot, so a lone sample is
          // visible instead of silently absent. Doubling the point here rather
          // than using <circle> keeps it ROUND — preserveAspectRatio="none"
          // scales x and y differently, which would squash a real circle into a
          // dash (vectorEffect corrects strokes, not fills).
          points={seg.includes(" ") ? seg : `${seg} ${seg}`}
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          // Without this the non-uniform scale squashes the stroke itself.
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </svg>
  );
}
