"use client";

/**
 * The default model, picked from a list (v1.353.0, G57).
 *
 * Settings › Models asked for the default model in a free-text box with the
 * placeholder "claude-opus-4-8" — the user had to know a model id by heart.
 * This is the composer's catalogue (`useModels`, the same /models the chat's
 * model menu reads) scoped to the default PROVIDER, each model by its name
 * ("Opus 5.5") where the catalogue has one. "Other…" keeps the old box for an
 * id the catalogue does not list, and a saved model the catalogue does not
 * list stays shown and selected — never silently replaced. The value saved is
 * always the model id.
 *
 * The same control the full settings form (pages/LegacySettingsForm
 * DefaultModelControl, v1.314.0) has drawn since v1.314.0, lifted here so the
 * schema-driven row (SettingRow) uses it too.
 */

import { useMemo, useState } from "react";
import { useDaemon } from "@/lib/daemon";
import { useModels } from "@/lib/useModels";
import type { ModelOption } from "@/lib/types";

/** The "Other…" option's value: never a real model id. */
const OTHER_MODEL = "__other_model__";

/** A model as a person reads it: its catalogue name ("Opus 5.5"), else the
 *  endpoint's own name when it is not the id again, else the id. */
export function modelOptionText(m: Pick<ModelOption, "model" | "label" | "name">): string {
  const label = (m.label || "").trim();
  if (label && label !== m.model) return label;
  return m.model;
}

export function DefaultModelPicker({
  provider,
  value,
  onChange,
  id,
  labelledBy,
}: {
  /** The provider whose models are listed. Absent: the SAVED default's
   *  provider (/health), so the list is never empty for lack of a prop. */
  provider?: string;
  value: string;
  onChange: (v: string) => void;
  id?: string;
  labelledBy?: string;
}) {
  const { models } = useModels();
  const { health } = useDaemon();
  const scope = (provider ?? health?.default_provider ?? "").trim();
  const [other, setOther] = useState(false);
  // One option per model id: the catalogue can list one twice (a configured
  // slot and its live discovery).
  const scoped = useMemo(() => {
    const seen = new Set<string>();
    return models.filter((m) => {
      if (m.provider !== scope || seen.has(m.model)) return false;
      seen.add(m.model);
      return true;
    });
  }, [models, scope]);
  const known = scoped.some((m) => m.model === value);
  return (
    <div className="space-y-2">
      <select
        id={id}
        aria-labelledby={labelledBy}
        aria-label={labelledBy ? undefined : "Default model"}
        value={other ? OTHER_MODEL : value}
        onChange={(e) => {
          if (e.target.value === OTHER_MODEL) {
            setOther(true);
            return;
          }
          setOther(false);
          onChange(e.target.value);
        }}
        className="field w-full py-1.5 text-[13px]"
      >
        {/* The saved id stays visible even when this provider's list lacks it. */}
        {!other && !known && (
          <option value={value} className="bg-ink-900">
            {value || "Not set"}
          </option>
        )}
        {scoped.map((m) => (
          <option key={m.model} value={m.model} title={m.model} className="bg-ink-900">
            {modelOptionText(m)}
            {m.available === false ? " — not connected" : ""}
          </option>
        ))}
        <option value={OTHER_MODEL} className="bg-ink-900">
          Other… (type a model id)
        </option>
      </select>
      {other && (
        <input
          type="text"
          value={value}
          placeholder="Model id"
          onChange={(e) => onChange(e.target.value)}
          aria-label="Default model id"
          spellCheck={false}
          autoFocus
          className="field w-full py-1.5 font-mono text-[13px]"
        />
      )}
    </div>
  );
}
