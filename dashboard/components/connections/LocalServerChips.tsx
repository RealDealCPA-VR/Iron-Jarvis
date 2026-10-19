"use client";

/**
 * The "LM Studio on this PC" / "llama.cpp" presses over an endpoint form
 * (v1.355.0, G72; presets in lib/localServerPresets). Drawn as the calm
 * secondary action every other Connections button wears (CALM_ACTION: no
 * border, no fill until hovered) — no new card, no icon.
 *
 * `useFetchModelsSoon(container)` is how a chip "runs the existing Fetch
 * available models": after the press's state lands, it clicks the picker's
 * OWN button inside `container`. React runs a child's effects before its
 * parent's, so by then the picker has already reset itself for the new
 * address, and the click fetches with the address the chip just filled — the
 * same request a person pressing the link would send. No second fetch path.
 */

import { useEffect, useState, type RefObject } from "react";
import { CALM_ACTION } from "@/components/connections/endpointChip";
import {
  FETCH_MODELS_SELECTOR,
  LOCAL_SERVER_PRESETS,
  type LocalServerPreset,
} from "@/lib/localServerPresets";

export function useFetchModelsSoon(container: RefObject<HTMLElement | null>): () => void {
  const [asked, setAsked] = useState(0);
  useEffect(() => {
    if (asked === 0) return;
    container.current?.querySelector<HTMLButtonElement>(FETCH_MODELS_SELECTOR)?.click();
  }, [asked, container]);
  return () => setAsked((n) => n + 1);
}

export function LocalServerChips({
  onPick,
}: {
  onPick: (preset: LocalServerPreset) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Fill in a model server on this PC"
      data-testid="local-server-chips"
      className="flex flex-wrap items-center gap-1.5"
    >
      {LOCAL_SERVER_PRESETS.map((p) => (
        <button
          key={p.id}
          type="button"
          data-testid={`local-server-${p.id}`}
          onClick={() => onPick(p)}
          title={`Fill in ${p.baseUrl} and list the models it has loaded`}
          className={CALM_ACTION}
        >
          {p.label}
        </button>
      ))}
    </div>
  );
}
