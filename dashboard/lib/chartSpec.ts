/**
 * The ```chart fence (v1.325.0): what the model may write, and the ONE strict
 * reader of it.
 *
 * THIS IS A TWO-PARTY AGREEMENT. `CHART_BLOCK` in the daemon's
 * `daemon/chat_turn.py` tells the model to put numbers that read better as a
 * picture in a fenced block named `chart`, holding ONLY JSON of this shape:
 *
 *   {"type": "bar" | "line" | "pie", "title": "...", "unit": "...",
 *    "labels": ["Q1", "Q2"], "series": [{"name": "Revenue", "values": [1, 2]}]}
 *
 * and a backend test reads THIS FILE to check that every type and key the
 * prompt names is spelled the same here. Rename one side and the model writes
 * a fence nobody draws — which fails silently, as a plain code block.
 *
 * STRICT ON PURPOSE. A chart is the model's numbers drawn as fact, so anything
 * the reader would have to GUESS at is refused (`null` → the ordinary code
 * block, where the user can still read what was written): numbers written as
 * strings ("1,234"), a value count that does not match the labels, unknown
 * keys, NaN/Infinity, a pie with two series or a negative slice. A
 * half-written fence while the reply is still streaming is just invalid JSON,
 * so it shows as code until the closing brace lands.
 */

import { rowsToCsv } from "@/lib/tableData";

export const CHART_FENCE = "chart";
export const CHART_TYPES = ["bar", "line", "pie"] as const;
export type ChartType = (typeof CHART_TYPES)[number];

export interface ChartSeries {
  name: string;
  values: number[];
}

export interface ChartSpec {
  type: ChartType;
  title?: string;
  unit?: string;
  labels: string[];
  series: ChartSeries[];
}

export const CHART_MAX_LABELS = 50;
export const CHART_MAX_SERIES = 6;
export const CHART_MAX_TITLE = 120;
export const CHART_MAX_UNIT = 24;
export const CHART_MAX_NAME = 120;

const TOP_KEYS = new Set(["type", "title", "unit", "labels", "series"]);
const SERIES_KEYS = new Set(["name", "values"]);

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function optionalText(v: unknown, max: number): string | undefined | null {
  if (v === undefined) return undefined;
  if (typeof v !== "string") return null;
  const t = v.trim();
  if (t.length > max) return null;
  return t || undefined;
}

/** The fence's text → a chart, or null when anything would need guessing. */
export function parseChartSpec(raw: string): ChartSpec | null {
  let data: unknown;
  try {
    data = JSON.parse((raw ?? "").trim());
  } catch {
    return null;
  }
  if (!isPlainObject(data)) return null;
  for (const key of Object.keys(data)) if (!TOP_KEYS.has(key)) return null;

  const type = data.type;
  if (typeof type !== "string" || !(CHART_TYPES as readonly string[]).includes(type)) return null;

  const title = optionalText(data.title, CHART_MAX_TITLE);
  const unit = optionalText(data.unit, CHART_MAX_UNIT);
  if (title === null || unit === null) return null;

  const labels = data.labels;
  if (!Array.isArray(labels) || labels.length < 1 || labels.length > CHART_MAX_LABELS) return null;
  if (!labels.every((l) => typeof l === "string")) return null;

  const series = data.series;
  if (!Array.isArray(series) || series.length < 1 || series.length > CHART_MAX_SERIES) return null;
  const out: ChartSeries[] = [];
  for (const s of series) {
    if (!isPlainObject(s)) return null;
    for (const key of Object.keys(s)) if (!SERIES_KEYS.has(key)) return null;
    if (typeof s.name !== "string" || s.name.length > CHART_MAX_NAME) return null;
    const values = s.values;
    if (!Array.isArray(values) || values.length !== labels.length) return null;
    if (!values.every((v) => typeof v === "number" && Number.isFinite(v))) return null;
    out.push({ name: s.name.trim(), values: values as number[] });
  }

  if (type === "pie") {
    if (out.length !== 1) return null;
    const vals = out[0].values;
    if (vals.some((v) => v < 0)) return null;
    if (!vals.some((v) => v > 0)) return null; // all zero: nothing to divide
  }

  const spec: ChartSpec = { type: type as ChartType, labels: labels as string[], series: out };
  if (title) spec.title = title;
  if (unit) spec.unit = unit;
  return spec;
}

/**
 * The chart's numbers as CSV: one row per label, one column per series. The
 * first header cell is "Label". Values are written as plain numbers (no unit,
 * no thousands separators) so a spreadsheet reads them as numbers.
 */
export function chartToCsv(spec: ChartSpec): string {
  const header: string[] = ["Label", ...spec.series.map((s, i) => s.name || `Series ${i + 1}`)];
  const rows: (string | number)[][] = spec.labels.map((label, r) => [
    label,
    ...spec.series.map((s) => s.values[r]),
  ]);
  return rowsToCsv([header, ...rows]);
}

/**
 * Axis ticks on "nice" numbers (1, 2, 2.5, 5 × 10^k) covering [min, max].
 * Returns at least two ticks; a flat range is widened so it has height.
 */
export function niceTicks(min: number, max: number, target = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
  if (min > max) [min, max] = [max, min];
  if (min === max) {
    if (min === 0) return [0, 1];
    const pad = Math.abs(min) * 0.5;
    min -= pad;
    max += pad;
  }
  const span = max - min;
  const rough = span / Math.max(1, target);
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / mag;
  const nice = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10;
  const step = nice * mag;
  const start = Math.floor(min / step + 1e-9) * step;
  const end = Math.ceil(max / step - 1e-9) * step;
  const ticks: number[] = [];
  // Round each tick to the step's precision so 0.1 + 0.2 reads 0.3.
  const decimals = Math.max(0, -Math.floor(Math.log10(step)) + 1);
  for (let v = start, i = 0; v <= end + step / 2 && i < 100; v += step, i++) {
    ticks.push(Number(v.toFixed(decimals)));
  }
  if (ticks.length < 2) ticks.push(Number((start + step).toFixed(decimals)));
  return ticks;
}
