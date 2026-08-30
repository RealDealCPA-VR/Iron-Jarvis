"use client";

/**
 * FaceStack — the thread's panel, as the agents' own faces (v1.214.0).
 *
 * The thread rail has shown a layered stack since the round-table shipped, but
 * the things it layered were `AgentAvatar` dots: a coloured circle with the
 * first LETTER of the name in it. Every other surface in the app draws the
 * agent — the roster, the kanban board, the round-table transcript, the
 * @-mention popover — so the one list you scan to choose a conversation was
 * the only place an agent did not look like itself. Reported as wanting the
 * left pane to "show the image of the related agent or agents (layered as they
 * are now)": the layering was right, the contents were not.
 *
 * So this is the same overlapping strip, drawing `AgentFace` — which means a
 * stored PORTRAIT where one exists, the chosen face where one is set, and the
 * name-derived face otherwise, resolved by the same precedence as everywhere
 * else. The identity is the component's; this file only owns the arrangement.
 *
 * The portrait lookup is passed IN, keyed by participant key, because the page
 * already holds the roster it comes from. Fetching a second opinion here would
 * be one more list that can disagree with the rail beside it.
 */

import AgentFace from "./AgentFace";
import type { Participant } from "./identity";

export function FaceStack({
  participants,
  avatarByKey,
  max = 5,
  size = 20,
}: {
  participants: Participant[];
  /** participantKey → stored portrait URL (already token-signed), or null. */
  avatarByKey?: Map<string, string | null>;
  max?: number;
  size?: number;
}) {
  const shown = participants.slice(0, max);
  const extra = participants.length - shown.length;
  return (
    <span className="flex items-center -space-x-1.5">
      {shown.map((p) => (
        // The ring is on the WRAPPER, not the face: `AgentFace` renders either
        // an <svg> or an <img> depending on whether a portrait is stored, and
        // a ring that only lands on one of the two would make a panel of mixed
        // agents read as two different components.
        <span
          key={p.key}
          className="grid shrink-0 place-items-center rounded-full bg-ink-900 ring-2 ring-ink-900"
          style={{ width: size, height: size }}
        >
          <AgentFace
            name={p.name}
            mood="idle"
            size={size}
            avatarUrl={avatarByKey?.get(p.key) ?? undefined}
            title={p.role ? `${p.name} — ${p.role}` : p.name}
          />
        </span>
      ))}
      {extra > 0 && (
        <span
          className="grid shrink-0 place-items-center rounded-full border border-white/10 bg-ink-800 text-[9px] font-semibold text-zinc-400 ring-2 ring-ink-900"
          style={{ width: size, height: size }}
          title={`${extra} more on this panel`}
        >
          +{extra}
        </span>
      )}
    </span>
  );
}

export default FaceStack;
