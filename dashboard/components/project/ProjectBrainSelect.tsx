"use client";

/**
 * The project page's "Memory profile" choice (v1.406.0), beside its model:
 * follow the app's profile (""), none in this project ("off"), or one profile.
 * The page saves the choice through its own `savePatch({brain_profile})`.
 *
 * Reads `GET /brain` for the list and the app's own choice; an older Iron
 * Jarvis (404, or an answer with no profiles list) renders nothing.
 */

import { useEffect, useState } from "react";
import { Library } from "lucide-react";
import { get } from "@/lib/api";
import { PROJECT_OFF, decodeBrainState, type BrainState } from "@/lib/brainProfiles";

export function ProjectBrainSelect({
  value,
  onChange,
  disabled = false,
}: {
  /** The project's `brain_profile`: "" follows the app, "off", or an id. */
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
}) {
  const [state, setState] = useState<BrainState | null>(null);

  useEffect(() => {
    let live = true;
    get<unknown>("/brain")
      .then((raw) => {
        if (live) setState(decodeBrainState(raw));
      })
      .catch(() => {
        if (live) setState(null);
      });
    return () => {
      live = false;
    };
  }, []);

  if (!state) return null;
  const appName = state.profiles.find((p) => p.id === state.selected)?.name ?? "none";
  const known = value === "" || value === PROJECT_OFF || state.profiles.some((p) => p.id === value);

  return (
    <div className="flex min-w-0 items-center gap-2">
      <label
        htmlFor="project-brain-profile"
        className="flex shrink-0 items-center gap-1.5 text-[12px] text-zinc-500"
      >
        <Library size={12} className="shrink-0" aria-hidden="true" />
        Memory profile
      </label>
      <select
        id="project-brain-profile"
        data-testid="project-brain-profile"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        title="Which memory base this project's chats and tasks search first and save to."
        className="field w-auto min-w-0 max-w-[16rem] py-1 text-sm"
      >
        <option value="">Same as the app ({appName})</option>
        <option value={PROJECT_OFF}>None in this project</option>
        {!known && <option value={value}>A profile that was deleted</option>}
        {state.profiles.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </div>
  );
}
