"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";

/**
 * The failed turn's Retry (v1.312.0, chat-retry-only-when-default-down).
 *
 * While the provider the turn ran on is cooling down, a press could only be
 * refused at once with the same words — so the button says when it will work
 * ("Retry in 23s"), is disabled until then, and counts down ON ITS OWN. The
 * health poll is slower than a second, so the number it reports is turned
 * into a deadline here and ticked locally; a fresh poll value resets it.
 *
 * When that provider is known down or still cooling down it also offers
 * "Choose another model…", which only opens the model menu: the page names
 * no model and never switches (v1.162.0) — the user picks, then retries.
 */
export function RetryTurnButton({
  cooldownS,
  onRetry,
  provider,
  down,
  onChooseModel,
}: {
  /** Seconds left on the provider's cooldown when health last reported; 0 = none. */
  cooldownS: number;
  onRetry: () => void;
  /** The provider the turn ran on (the pick, else the default) — for the title. */
  provider: string;
  /** That provider is KNOWN down (health said so; unknown is not down). */
  down: boolean;
  /** Opens the model menu. The page suggests no model and switches nothing. */
  onChooseModel: () => void;
}) {
  const [deadline, setDeadline] = useState(() =>
    cooldownS > 0 ? Date.now() + cooldownS * 1000 : 0,
  );
  const [now, setNow] = useState(() => Date.now());
  // A new reading from /health is the truth — restart the countdown from it.
  useEffect(() => {
    setDeadline(cooldownS > 0 ? Date.now() + cooldownS * 1000 : 0);
    setNow(Date.now());
  }, [cooldownS]);
  const left = deadline > now ? Math.ceil((deadline - now) / 1000) : 0;
  useEffect(() => {
    if (left <= 0) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [left > 0]); // eslint-disable-line react-hooks/exhaustive-deps

  // "Choose another model…" is decided by the SAME local countdown as Retry
  // (v1.312.0): read from the polled number instead, it outlived the wait by
  // up to one health poll — Retry back on, and still a nudge to switch.
  const choose =
    down || left > 0 ? (
      <button
        type="button"
        onClick={onChooseModel}
        title={`${provider} ${down ? "is not reachable" : "is cooling down"} — pick a model for this chat, then press Retry`}
        className="btn-ghost shrink-0 py-1.5 text-[13px]"
      >
        Choose another model…
      </button>
    ) : null;

  if (left > 0)
    return (
      <>
        <button
          type="button"
          disabled
          title="This model is cooling down — Retry comes back when the wait ends, or choose another model"
          className="btn-ghost shrink-0 py-1.5 text-[13px] disabled:opacity-60"
        >
          <RefreshCw size={14} aria-hidden="true" /> Retry in {left}s
        </button>
        {choose}
      </>
    );
  return (
    <>
      <button
        type="button"
        onClick={onRetry}
        title="Re-send the last message"
        className="btn-ghost shrink-0 py-1.5 text-[13px]"
      >
        <RefreshCw size={14} /> Retry
      </button>
      {choose}
    </>
  );
}
