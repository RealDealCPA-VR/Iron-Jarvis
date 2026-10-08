"use client";

/**
 * MAKE YOUR OWN THEME (v1.317.0) — Settings → Appearance.
 *
 * A few picks (a main colour, an optional second colour, a background feel,
 * dark / light / Match Windows) become a full palette in BOTH versions
 * (lib/themePalette.ts), previewed side by side with the app's own classes —
 * the preview box sets the palette's variables on itself, so what it shows is
 * what the app will look like, not a drawing of it. Readability is checked
 * with the same rules the built-in themes are tested against, and anything
 * the maker had to adjust is said in a sentence.
 *
 * Palettes are kept on this PC (like the theme choice). Saving applies it;
 * every other theme row (the title bar, the drawer, Ctrl K) picks it up.
 */

import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { Check, Palette, Pencil, Plus } from "lucide-react";
import { ConfirmButton, Field } from "@/components/ui";
import { parseHex } from "@/lib/color";
import { DEFAULT_THEME, applyTheme } from "@/lib/theme";
import {
  MAX_NAME,
  MAX_PALETTES,
  MODES,
  PALETTES_EVENT,
  PALETTES_KEY,
  PRESETS,
  SURFACES,
  SWATCHES,
  accentHex,
  deletePalette,
  generatePalette,
  loadPalettes,
  makePalette,
  readabilityProblems,
  upsertPalette,
  type CustomPalette,
  type PaletteMode,
  type Scheme,
  type Surface,
  type ThemeVars,
} from "@/lib/themePalette";

interface Draft {
  id?: string;
  name: string;
  accent: string;
  second: string | null;
  surface: Surface;
  mode: PaletteMode;
}

const BLANK: Draft = { name: "", accent: "#22d3ee", second: null, surface: "cool", mode: "system" };

/** The palette's variables as inline CSS on the preview box. */
function varsStyle(vars: ThemeVars): CSSProperties {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(vars)) out[`--${k}`] = v;
  return out as CSSProperties;
}

function usePalettes(): CustomPalette[] {
  const [list, setList] = useState<CustomPalette[]>([]);
  useEffect(() => {
    const sync = () => setList(loadPalettes());
    sync();
    const onStorage = (e: StorageEvent) => {
      if (e.key === null || e.key === PALETTES_KEY) sync();
    };
    window.addEventListener(PALETTES_EVENT, sync);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(PALETTES_EVENT, sync);
      window.removeEventListener("storage", onStorage);
    };
  }, []);
  return list;
}

function useActiveTheme(): string {
  const [active, setActive] = useState("");
  useEffect(() => {
    const html = document.documentElement;
    const sync = () => setActive(html.dataset.theme || DEFAULT_THEME);
    sync();
    const mo = new MutationObserver(sync);
    mo.observe(html, { attributes: true, attributeFilter: ["data-theme"] });
    return () => mo.disconnect();
  }, []);
  return active;
}

/** One version of the palette, drawn with the app's own classes. */
function Preview({ scheme, vars }: { scheme: Scheme; vars: ThemeVars }) {
  return (
    <div
      data-testid={`theme-preview-${scheme}`}
      style={varsStyle(vars)}
      className="overflow-hidden rounded-xl border border-white/10 bg-ink-950 p-2.5"
    >
      <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-zinc-500">
        {scheme === "dark" ? "Dark version" : "Light version"}
      </div>
      <div className="space-y-1.5 rounded-lg border border-white/10 bg-ink-850 p-2.5">
        <div className="text-[13px] font-semibold text-zinc-100">Weekly report</div>
        <p className="text-[12px] leading-snug text-zinc-400">
          Three files ready. <span className="text-accent-soft underline">Open folder</span>
        </p>
        <p className="text-[11px] text-zinc-600">Updated 2 min ago</p>
        <div className="flex flex-wrap items-center gap-1">
          <span className="rounded-md border border-tone-success/30 bg-tone-success/10 px-1.5 py-0.5 text-[10px] text-tone-success">
            Done
          </span>
          <span className="rounded-md border border-tone-danger/30 bg-tone-danger/10 px-1.5 py-0.5 text-[10px] text-tone-danger">
            Failed
          </span>
          <span className="rounded-md border border-tone-warn/30 bg-tone-warn/10 px-1.5 py-0.5 text-[10px] text-tone-warn">
            Waiting
          </span>
        </div>
        <span className="inline-flex rounded-lg bg-accent px-2.5 py-1 text-[11px] font-semibold text-ink-950">
          Send it
        </span>
      </div>
    </div>
  );
}

function Choice<T extends string>({
  label,
  value,
  options,
  onChange,
  testId,
}: {
  label: string;
  value: T;
  options: { id: T; label: string; hint?: string }[];
  onChange: (v: T) => void;
  testId: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} data-testid={testId} className="flex flex-wrap gap-1">
      {options.map((o) => {
        const on = o.id === value;
        return (
          <button
            key={o.id}
            type="button"
            role="radio"
            aria-checked={on}
            title={o.hint}
            onClick={() => onChange(o.id)}
            className={`rounded-lg border px-2.5 py-1 text-[12px] transition-colors ${
              on
                ? "border-accent/50 bg-accent/[0.12] text-accent-soft"
                : "border-white/10 text-zinc-400 hover:border-white/20 hover:text-zinc-200"
            }`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** A colour well, the hex beside it, and the quick picks. */
function ColourPick({
  label,
  value,
  onChange,
  testId,
}: {
  label: string;
  value: string;
  onChange: (hex: string) => void;
  testId: string;
}) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const bad = !parseHex(text);
  return (
    <div data-testid={testId} className="space-y-1.5">
      <div className="flex items-center gap-2">
        <input
          type="color"
          aria-label={`${label} — colour picker`}
          value={parseHex(value) ? value : "#22d3ee"}
          onChange={(e) => onChange(e.target.value)}
          className="h-9 w-12 cursor-pointer rounded-lg border border-white/10 bg-transparent p-0.5"
        />
        <input
          aria-label={`${label} — hex code`}
          value={text}
          spellCheck={false}
          onChange={(e) => {
            const v = e.target.value.trim();
            setText(v);
            const hex = v.startsWith("#") ? v : `#${v}`;
            if (parseHex(hex)) onChange(hex.toLowerCase());
          }}
          className={`field w-28 font-mono text-[12px] ${bad ? "border-tone-danger/50" : ""}`}
        />
        {bad && <span className="text-[11px] text-tone-danger">Use six hex digits, like #22d3ee</span>}
      </div>
      <div className="flex flex-wrap gap-1" role="group" aria-label={`${label} — quick picks`}>
        {SWATCHES.map((s) => (
          <button
            key={s}
            type="button"
            aria-label={`Use ${s}`}
            title={s}
            onClick={() => onChange(s)}
            style={{ background: s }}
            className={`h-5 w-5 rounded-md border ${
              s === value.toLowerCase() ? "border-zinc-100 ring-1 ring-zinc-100" : "border-white/10"
            }`}
          />
        ))}
      </div>
    </div>
  );
}

function Editor({
  initial,
  onDone,
}: {
  initial: Draft;
  onDone: (saved: CustomPalette | null) => void;
}) {
  const [d, setD] = useState<Draft>(initial);
  const [error, setError] = useState("");
  const set = (patch: Partial<Draft>) => setD((prev) => ({ ...prev, ...patch }));
  const valid = !!parseHex(d.accent) && (d.second === null || !!parseHex(d.second));
  const gen = useMemo(
    () => (valid ? generatePalette({ accent: d.accent, second: d.second, surface: d.surface }) : null),
    [valid, d.accent, d.second, d.surface],
  );
  const problems = gen
    ? [...readabilityProblems(gen.vars.dark, "dark"), ...readabilityProblems(gen.vars.light, "light")]
    : [];

  const save = () => {
    if (!valid) return;
    const p = makePalette({ ...d, name: d.name || "My theme" });
    if (!upsertPalette(p)) {
      setError(`You can keep up to ${MAX_PALETTES} themes on this PC — delete one first.`);
      return;
    }
    applyTheme(p.id);
    onDone(p);
  };

  return (
    <div data-testid="theme-maker-editor" className="space-y-3 rounded-xl border border-white/10 bg-white/[0.02] p-3">
      <div>
        <div className="mb-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">Start from</div>
        <div className="flex flex-wrap gap-1" data-testid="theme-maker-presets">
          {PRESETS.map((p) => (
            <button
              key={p.from}
              type="button"
              onClick={() =>
                set({ accent: p.spec.accent, second: p.spec.second ?? null, surface: p.spec.surface })
              }
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2 py-1 text-[12px] text-zinc-300 hover:border-white/20"
            >
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: p.spec.accent }} aria-hidden />
              {p.name}
            </button>
          ))}
        </div>
      </div>

      <Field label="Name">
        <input
          className="field w-full text-sm"
          value={d.name}
          maxLength={MAX_NAME}
          placeholder="My theme"
          onChange={(e) => set({ name: e.target.value })}
        />
      </Field>

      <div>
        <div className="mb-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">Main colour</div>
        <ColourPick label="Main colour" value={d.accent} onChange={(accent) => set({ accent })} testId="theme-maker-accent" />
      </div>

      <div className="space-y-1.5">
        <label className="flex items-center gap-2 text-[12px] text-zinc-300">
          <input
            type="checkbox"
            checked={d.second !== null}
            onChange={(e) => set({ second: e.target.checked ? "#b91c1c" : null })}
          />
          Add a second colour
          <span className="text-[11px] text-zinc-500">— used for background glows</span>
        </label>
        {d.second !== null && (
          <ColourPick
            label="Second colour"
            value={d.second}
            onChange={(second) => set({ second })}
            testId="theme-maker-second"
          />
        )}
      </div>

      <div>
        <div className="mb-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">Background</div>
        <Choice label="Background" value={d.surface} options={SURFACES} onChange={(surface) => set({ surface })} testId="theme-maker-surface" />
      </div>

      <div>
        <div className="mb-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">Show</div>
        <Choice label="Show" value={d.mode} options={MODES} onChange={(mode) => set({ mode })} testId="theme-maker-mode" />
        <p className="mt-1 text-[11px] text-zinc-500">
          Both versions are made for you. &ldquo;Match Windows&rdquo; switches between them with your
          Windows light or dark setting.
        </p>
      </div>

      {gen && (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
          <Preview scheme="dark" vars={gen.vars.dark} />
          <Preview scheme="light" vars={gen.vars.light} />
        </div>
      )}

      {gen && (
        <div data-testid="theme-maker-readability" className="space-y-1 text-[12px]">
          {problems.length === 0 ? (
            <div className="flex items-center gap-1.5 text-tone-success">
              <Check size={13} aria-hidden /> Easy to read in both versions
            </div>
          ) : (
            <div className="text-tone-warn">Some text may be hard to read: {problems.join(", ")}.</div>
          )}
          {gen.notes.map((n) => (
            <p key={n} className="text-zinc-500">
              {n}
            </p>
          ))}
        </div>
      )}

      {error && (
        <p role="alert" className="text-[12px] text-tone-danger">
          {error}
        </p>
      )}

      <div className="flex items-center gap-2">
        <button type="button" className="btn-accent px-3 py-1.5 text-xs" disabled={!valid} onClick={save}>
          {initial.id ? "Save and use" : "Save and use this theme"}
        </button>
        <button type="button" className="btn-ghost px-3 py-1.5 text-xs" onClick={() => onDone(null)}>
          Cancel
        </button>
      </div>
    </div>
  );
}

export function ThemeMaker() {
  const palettes = usePalettes();
  const active = useActiveTheme();
  const [editing, setEditing] = useState<Draft | null>(null);

  // Ctrl K's "Make your own theme" lands on /settings#appearance.
  useEffect(() => {
    if (typeof window === "undefined" || window.location.hash !== "#appearance") return;
    document.getElementById("appearance")?.scrollIntoView({ block: "start" });
  }, []);

  const remove = (p: CustomPalette) => {
    if (active === p.id) applyTheme(DEFAULT_THEME);
    deletePalette(p.id);
  };

  return (
    <div data-testid="theme-maker" className="mt-4 space-y-2.5 border-t border-white/[0.06] pt-4">
      <div className="flex items-center justify-between">
        <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">Your themes</div>
        {!editing && (
          <button
            type="button"
            data-testid="theme-maker-new"
            onClick={() => setEditing({ ...BLANK })}
            disabled={palettes.length >= MAX_PALETTES}
            className="inline-flex items-center gap-1 rounded-lg border border-accent/30 bg-accent/[0.08] px-2.5 py-1 text-[12px] font-medium text-accent-soft hover:bg-accent/[0.14] disabled:opacity-50"
          >
            <Plus size={13} aria-hidden /> Make your own
          </button>
        )}
      </div>

      {palettes.length === 0 && !editing && (
        <p className="text-[12px] leading-relaxed text-zinc-500">
          Pick your own colours — Iron Jarvis makes a dark and a light version and keeps every word
          readable.
        </p>
      )}

      {palettes.length > 0 && (
        <ul className="space-y-1.5" data-testid="theme-maker-list">
          {palettes.map((p) => {
            const on = active === p.id;
            return (
              <li
                key={p.id}
                data-testid={`theme-row-${p.id}`}
                className="flex items-center gap-2 rounded-lg border border-white/[0.07] px-2 py-1.5"
              >
                <span className="flex shrink-0 overflow-hidden rounded-md border border-white/10" aria-hidden>
                  <span className="h-5 w-3" style={{ background: `rgb(${p.vars.dark["ink-950"]})` }} />
                  <span className="h-5 w-3" style={{ background: accentHex(p, "dark") }} />
                  <span className="h-5 w-3" style={{ background: `rgb(${p.vars.light["ink-950"]})` }} />
                  <span className="h-5 w-3" style={{ background: accentHex(p, "light") }} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] text-zinc-200">{p.name}</span>
                  <span className="block text-[11px] text-zinc-500">
                    {MODES.find((m) => m.id === p.mode)?.label}
                  </span>
                </span>
                {on ? (
                  <span className="inline-flex items-center gap-1 text-[11px] text-tone-success">
                    <Check size={12} aria-hidden /> In use
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => applyTheme(p.id)}
                    className="rounded-lg border border-white/10 px-2 py-0.5 text-[12px] text-zinc-300 hover:border-white/20"
                  >
                    Use
                  </button>
                )}
                <button
                  type="button"
                  aria-label={`Edit ${p.name}`}
                  title="Edit"
                  onClick={() =>
                    setEditing({ id: p.id, name: p.name, accent: p.accent, second: p.second, surface: p.surface, mode: p.mode })
                  }
                  className="rounded-md p-1 text-zinc-400 hover:text-zinc-100"
                >
                  <Pencil size={13} aria-hidden />
                </button>
                <ConfirmButton label="Delete" onConfirm={() => remove(p)} title={`Delete ${p.name}`} />
              </li>
            );
          })}
        </ul>
      )}

      {editing && <Editor key={editing.id ?? "new"} initial={editing} onDone={() => setEditing(null)} />}

      {!editing && palettes.length > 0 && (
        <p className="flex items-center gap-1.5 text-[11px] text-zinc-500">
          <Palette size={12} aria-hidden /> Your themes also appear in the title bar and in Ctrl K.
        </p>
      )}
    </div>
  );
}
