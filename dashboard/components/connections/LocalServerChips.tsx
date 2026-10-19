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
 *
 * A second chip pressed while the first one's fetch is still out (fix round):
 * the picker's reset for the new address is QUEUED in that same commit, so its
 * button still carries `disabled` from the fetch in flight when this effect
 * runs — and a click on a disabled button dispatches nothing. So a disabled
 * button is pressed again on a later frame, once the picker has re-rendered
 * with loading off, for at most `FETCH_RETRY_FRAMES` frames (a press that
 * cannot land by then is dropped; the user can press the link). The effect's
 * cleanup cancels a pending retry, so a newer press never doubles up.
 */

import { useEffect, useState, type RefObject } from "react";
import { CALM_ACTION } from "@/components/connections/endpointChip";
import {
  FETCH_MODELS_SELECTOR,
  LOCAL_SERVER_PRESETS,
  type LocalServerPreset,
} from "@/lib/localServerPresets";

/** How many frames a chip waits for the picker's button to come back on. */
export const FETCH_RETRY_FRAMES = 30;

export function useFetchModelsSoon(container: RefObject<HTMLElement | null>): () => void {
  const [asked, setAsked] = useState(0);
  useEffect(() => {
    if (asked === 0) return;
    let frame = 0;
    let left = FETCH_RETRY_FRAMES;
    const press = () => {
      frame = 0;
      const button = container.current?.querySelector<HTMLButtonElement>(FETCH_MODELS_SELECTOR);
      if (!button) return;
      if (button.disabled) {
        if (left-- > 0) frame = requestAnimationFrame(press);
        return;
      }
      button.click();
    };
    press();
    return () => {
      if (frame) cancelAnimationFrame(frame);
    };
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
