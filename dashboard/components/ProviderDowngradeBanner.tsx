"use client";

import { useState } from "react";
import { AlertTriangle, X } from "lucide-react";
import Link from "next/link";
import { useEvents } from "@/lib/useEvents";
import type { IJEvent } from "@/lib/types";

/** The one /events type the banner reads (v1.311.0). */
export const PROVIDER_DOWNGRADE_EVENT_TYPES: readonly string[] = ["provider.downgraded"];

/** What the banner says about one `provider.downgraded` event, or null. */
export interface DowngradeNotice {
  /** "mock" = the offline mock really answered; "refused" = nothing answered. */
  kind: "mock" | "refused";
  title: string;
  detail: string;
}

function sentence(text: string): string {
  const t = text.trim().replace(/[.\s]+$/, "");
  return t ? `${t.charAt(0).toUpperCase()}${t.slice(1)}.` : "";
}

/**
 * The banner's one decision (v1.329.0, calm chat J1), pure so it is pinned.
 *
 * The router publishes `provider.downgraded` in two very different cases, and
 * the banner used to read neither apart: it said "Output came from the mock
 * model" for both, which was FALSE on every refusal (`used: "none"`: the
 * v1.162.0 rule refuses instead of answering with the mock).
 *
 * - `used: "mock"` (the mock-trap: the default is still the mock while a real
 *   model is connected): the mock REALLY answered, so the old truth stays.
 * - `used: "none"` from an interactive turn (no session: the chat lanes, the
 *   browser sidebar, a one-shot) renders NOTHING. The surface that asked
 *   already shows the refusal where the user is standing, with its Retry and
 *   its model menu; a second notice repeating it at the top of the page was
 *   the loud box the closing audit found.
 * - `used: "none"` from a RUN (an agent, a mission, a schedule: the event
 *   carries its session id) says plainly that nothing answered, naming the
 *   endpoint by its label. That run may be on a page the user is not looking
 *   at, so this line is how they learn it, and the link to Connections stays.
 */
export function downgradeNotice(e: IJEvent | null | undefined): DowngradeNotice | null {
  if (!e || e.type !== "provider.downgraded") return null;
  const p = (e.payload ?? {}) as Record<string, unknown>;
  const used = String(p.used ?? "");
  const reason = String(p.reason ?? "");
  if (used === "none") {
    const sid = String(e.session_id ?? "");
    if (!sid || sid === "chat") return null;
    // The name exactly as the user gave it (a label is never re-cased).
    const name = String(p.label || p.requested || "the chosen model");
    const why = reason.trim().replace(/[.\s]+$/, "");
    return {
      kind: "refused",
      title: "Nothing answered a background job.",
      detail: why ? `${name}: ${why}.` : `${name} did not answer.`,
    };
  }
  return {
    kind: "mock",
    title: "Output came from the mock model.",
    detail: sentence(reason || "a session ran on the offline mock model instead of a real provider"),
  };
}

/**
 * One calm line under the title bar when a `provider.downgraded` event needs
 * saying (see `downgradeNotice`). Tone tokens only (the danger tone for a run
 * nothing answered, the warn tone for a mock answer), a hairline, a quiet link
 * to Connections, dismissible; the next event that needs saying shows again.
 */
export function ProviderDowngradeBanner() {
  // v1.311.0 (wave 3): only the one type it reads. It sits on every page,
  // and every other frame re-rendered the page's tree for nothing.
  const { events } = useEvents(40, { types: PROVIDER_DOWNGRADE_EVENT_TYPES });
  const [dismissedTs, setDismissedTs] = useState<string | null>(null);

  // The newest event that has something to say: a chat refusal (said in the
  // chat) neither shows nor hides a notice that was already up.
  let latest: IJEvent | undefined;
  let notice: DowngradeNotice | null = null;
  for (const e of events) {
    const n = downgradeNotice(e);
    if (n) {
      latest = e;
      notice = n;
      break;
    }
  }
  if (!latest || !notice || latest.ts === dismissedTs) return null;
  const tone = notice.kind === "refused" ? "text-tone-danger" : "text-tone-warn";

  return (
    <div
      role="status"
      data-testid="provider-downgrade"
      data-kind={notice.kind}
      className="flex items-start gap-2 rounded-xl border border-white/[0.06] px-3 py-2 text-[13px] leading-5"
    >
      <AlertTriangle size={14} aria-hidden="true" className={`mt-[3px] shrink-0 ${tone}`} />
      <p className="min-w-0 flex-1 break-words text-zinc-400">
        <span className={`font-medium ${tone}`}>{notice.title}</span> {notice.detail}{" "}
        <Link
          href="/connections"
          className="whitespace-nowrap text-zinc-300 underline-offset-2 transition-colors hover:text-zinc-100 hover:underline"
        >
          Open Connections
        </Link>
      </p>
      <button
        type="button"
        aria-label="Dismiss"
        onClick={() => setDismissedTs(latest!.ts)}
        className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
      >
        <X size={14} />
      </button>
    </div>
  );
}
