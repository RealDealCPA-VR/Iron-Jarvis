"use client";

/**
 * The once-only "Keep this chat on this PC" line above the message box
 * (v1.455.0, readiness Wave G, item 25; the rule is lib/localOnlyOffer).
 * Quiet: no box, no colour of alarm; the press calls the page's own Local
 * only toggle, so it is exactly the chip's choice.
 */

import { useEffect, useState } from "react";
import { House } from "lucide-react";
import {
  LOCAL_ONLY_OFFER_DISMISS,
  LOCAL_ONLY_OFFER_KEEP,
  LOCAL_ONLY_OFFER_LINE,
  markOfferSeen,
  readOfferSeen,
  shouldOfferLocalOnly,
} from "@/lib/localOnlyOffer";
import type { LocalOnlyRow } from "@/lib/localOnly";

export function LocalOnlyOffer({
  attachedCount,
  localOnly,
  models,
  onKeep,
}: {
  attachedCount: number;
  localOnly: boolean;
  models: readonly (LocalOnlyRow & { provider?: string })[];
  onKeep: () => void;
}) {
  const [seen] = useState(() => readOfferSeen());
  const [shownHere, setShownHere] = useState(false);
  const [answered, setAnswered] = useState(false);
  const show = shouldOfferLocalOnly({ attachedCount, localOnly, models, seen, shownHere, answered });

  useEffect(() => {
    if (show && !shownHere) {
      setShownHere(true);
      markOfferSeen();
    }
  }, [show, shownHere]);
  // The files went (sent or removed): this chat's line is done.
  useEffect(() => {
    if (attachedCount <= 0 && shownHere) setAnswered(true);
  }, [attachedCount, shownHere]);

  if (!show) return null;
  return (
    <div
      data-testid="local-only-offer"
      className="flex w-full min-w-0 flex-wrap items-center gap-x-2 gap-y-1 px-4 pt-2 text-[12px] text-zinc-400"
    >
      <House size={12} className="shrink-0 text-tone-success" aria-hidden />
      <span className="min-w-0">{LOCAL_ONLY_OFFER_LINE}</span>
      <button
        type="button"
        data-testid="local-only-offer-keep"
        onClick={() => {
          setAnswered(true);
          markOfferSeen();
          onKeep();
        }}
        className="rounded-full px-2 py-0.5 text-[12px] font-medium text-tone-success hover:bg-white/[0.05]"
      >
        {LOCAL_ONLY_OFFER_KEEP}
      </button>
      <button
        type="button"
        data-testid="local-only-offer-dismiss"
        onClick={() => {
          setAnswered(true);
          markOfferSeen();
        }}
        className="rounded-full px-2 py-0.5 text-[12px] text-zinc-500 hover:bg-white/[0.05] hover:text-zinc-300"
      >
        {LOCAL_ONLY_OFFER_DISMISS}
      </button>
    </div>
  );
}
