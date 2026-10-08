"use client";

/**
 * One setting, drawn from its definition in the ONE settings schema (calm UI
 * redesign S10): the control follows the type (bool → switch, enum → select,
 * number, text, a list as lines, a dict as JSON), the label and help are the
 * schema's words, and a protected setting says it always asks in chat.
 */

import { useState } from "react";
import { ShieldAlert } from "lucide-react";

export interface SchemaSetting {
  key: string;
  label: string;
  group: string;
  section: string;
  type: "bool" | "enum" | "string" | "number" | "list" | "dict";
  help?: string;
  options?: { value: unknown; label: string }[];
  tier?: "allow" | "ask" | "ask-floor";
  restart?: boolean;
  advanced?: boolean;
  aliases?: string[];
  store?: "config" | "device" | "profile";
  placeholder?: string;
  pattern?: boolean;
}

/** A list is edited as one item per line; a dict as JSON. */
export function toText(def: SchemaSetting, v: unknown): string {
  if (v === null || v === undefined) return "";
  if (def.type === "list") return Array.isArray(v) ? v.join("\n") : String(v);
  if (def.type === "dict") {
    try {
      return JSON.stringify(v, null, 2);
    } catch {
      return "";
    }
  }
  return String(v);
}

/** The value to send for what the user typed; `undefined` = not valid yet. */
export function fromText(def: SchemaSetting, text: string): unknown {
  if (def.type === "list") return text.split(/\r?\n|,/).map((s) => s.trim()).filter(Boolean);
  if (def.type === "dict") {
    if (!text.trim()) return {};
    try {
      const v = JSON.parse(text);
      return v && typeof v === "object" && !Array.isArray(v) ? v : undefined;
    } catch {
      return undefined;
    }
  }
  if (def.type === "number") {
    if (!text.trim()) return null;
    const n = Number(text);
    return Number.isFinite(n) ? n : undefined;
  }
  return text;
}

export function SettingRow({
  def,
  value,
  changed,
  highlight,
  onChange,
  providerOptions,
}: {
  def: SchemaSetting;
  value: unknown;
  changed: boolean;
  highlight?: boolean;
  onChange: (v: unknown) => void;
  /** default_provider is a choice among the providers this PC has. */
  providerOptions?: string[];
}) {
  const id = `setting-${def.key}`;
  const [draft, setDraft] = useState<string | null>(null);
  const [bad, setBad] = useState(false);
  const textValue = draft ?? toText(def, value);

  let control: React.ReactNode;
  if (def.type === "bool") {
    const on = value === true;
    control = (
      <button
        type="button"
        role="switch"
        id={`${id}-control`}
        aria-checked={on}
        aria-labelledby={`${id}-label`}
        onClick={() => onChange(!on)}
        className={`relative h-6 w-11 shrink-0 rounded-full border transition-colors ${
          on ? "border-accent/50 bg-accent/30" : "border-white/10 bg-white/[0.04]"
        }`}
      >
        <span
          className={`absolute top-1/2 h-4 w-4 -translate-y-1/2 rounded-full transition-all ${on ? "left-6 bg-accent" : "left-1 bg-zinc-500"}`}
        />
      </button>
    );
  } else if (def.type === "enum" || (def.key === "default_provider" && providerOptions?.length)) {
    const opts =
      def.type === "enum"
        ? (def.options ?? []).map((o) => ({ value: String(o.value), label: o.label || String(o.value) }))
        : Array.from(new Set([...(providerOptions ?? []), String(value ?? "")]))
            .filter(Boolean)
            .map((p) => ({ value: p, label: p === "mock" ? "Demo model (scripted)" : p }));
    control = (
      <select
        id={`${id}-control`}
        aria-labelledby={`${id}-label`}
        value={value === null || value === undefined ? "" : String(value)}
        onChange={(e) => {
          const raw = e.target.value;
          const match = (def.options ?? []).find((o) => String(o.value) === raw);
          onChange(match ? match.value : raw);
        }}
        className="field w-full py-1.5 text-[13px]"
      >
        {value === null || value === undefined || value === "" ? <option value="">(not set)</option> : null}
        {opts.map((o) => (
          <option key={o.value} value={o.value} className="bg-ink-900">
            {o.label}
          </option>
        ))}
      </select>
    );
  } else if (def.type === "list" || def.type === "dict") {
    control = (
      <textarea
        id={`${id}-control`}
        aria-labelledby={`${id}-label`}
        value={textValue}
        rows={def.type === "dict" ? 4 : 3}
        spellCheck={false}
        placeholder={def.placeholder || (def.type === "list" ? "One per line" : "{}")}
        onChange={(e) => {
          setDraft(e.target.value);
          const v = fromText(def, e.target.value);
          setBad(v === undefined);
          if (v !== undefined) onChange(v);
        }}
        className={`field w-full font-mono text-[12px] ${bad ? "border-tone-danger/60" : ""}`}
      />
    );
  } else {
    control = (
      <input
        id={`${id}-control`}
        aria-labelledby={`${id}-label`}
        type={def.type === "number" ? "number" : "text"}
        value={textValue}
        placeholder={def.placeholder || ""}
        onChange={(e) => {
          setDraft(e.target.value);
          const v = fromText(def, e.target.value);
          setBad(v === undefined);
          if (v !== undefined) onChange(v);
        }}
        className={`field w-full py-1.5 text-[13px] ${bad ? "border-tone-danger/60" : ""}`}
      />
    );
  }

  return (
    <div
      id={id}
      data-testid={id}
      data-changed={changed ? "true" : undefined}
      className={`grid scroll-mt-24 grid-cols-[minmax(0,1fr)] gap-2 rounded-xl px-3 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,18rem)] sm:items-start sm:gap-6 ${
        highlight ? "ring-2 ring-accent/50" : ""
      } ${changed ? "bg-accent/[0.04]" : ""}`}
    >
      <div className="min-w-0">
        <div id={`${id}-label`} className="flex flex-wrap items-center gap-2 text-[13px] font-medium text-zinc-100">
          {def.label}
          {def.tier === "ask-floor" && (
            <span
              title="Protected: changing it from chat always asks first"
              className="inline-flex items-center gap-1 rounded-full border border-white/10 px-1.5 py-px text-[11px] font-normal text-zinc-400"
            >
              <ShieldAlert size={10} aria-hidden /> protected
            </span>
          )}
          {def.restart && <span className="text-[11px] font-normal text-tone-warn">after a restart</span>}
          {def.store === "device" && <span className="text-[11px] font-normal text-zinc-500">this device</span>}
        </div>
        {def.help && <p className="mt-0.5 text-[12px] leading-relaxed text-zinc-500">{def.help}</p>}
        {bad && (
          <p role="alert" className="mt-1 text-[12px] text-tone-danger">
            {def.type === "dict" ? "That isn't valid JSON yet." : "That isn't a number."}
          </p>
        )}
      </div>
      <div className="w-full">{control}</div>
    </div>
  );
}
