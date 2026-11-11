"use client";

/**
 * v1.474.0 (Hand off): quiet lines in a Build › Code session.
 *
 * - In a session whose coding tool started helpers: one line per helper —
 *   its tool, its title, how it stands — and Open.
 * - In a helper: "Helper for <the session that started it>" and Open.
 *
 * A helper is an ordinary Code session (its own cards, its own Stop); these
 * lines only make the link between the two easy to follow. Nothing here acts
 * on a helper.
 */

import { helperStateWords, type CodeHelper, type CodeSessionView } from "@/lib/buildApp";

const OPEN =
  "shrink-0 rounded-full px-2.5 py-0.5 text-[12px] text-zinc-300 shadow-[0_0_0_0.5px_rgb(var(--white)/0.16)] transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60";

function stateTone(state: CodeHelper["state"]): string {
  if (state === "waiting") return "text-tone-warn";
  if (state === "failed") return "text-tone-danger";
  return "text-zinc-500";
}

export function HelperLines({
  helpers,
  handedFrom,
  onOpen,
}: {
  helpers: CodeHelper[];
  handedFrom: CodeSessionView["handedFrom"];
  onOpen?: (id: string) => void;
}) {
  if (!handedFrom && helpers.length === 0) return null;
  return (
    <div data-testid="code-helpers" className="mb-2 space-y-1 px-1">
      {handedFrom ? (
        <div data-testid="code-handed-from" className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <p className="min-w-0 text-[12px] text-zinc-500">
            Helper for {handedFrom.label ? `${handedFrom.label}'s session ` : "the session "}
            &ldquo;{handedFrom.title || "untitled"}&rdquo;
          </p>
          {onOpen ? (
            <button type="button" data-testid="code-handed-from-open" className={OPEN} onClick={() => onOpen(handedFrom.id)}>
              Open
            </button>
          ) : null}
        </div>
      ) : null}
      {helpers.length ? (
        <p className="text-[12px] text-zinc-500">
          {helpers.length === 1 ? "1 helper" : `${helpers.length} helpers`} this session&apos;s tool started:
        </p>
      ) : null}
      {helpers.map((h) => (
        <div key={h.id} data-testid={`code-helper-${h.id}`} className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <p className="min-w-0 text-[12px] text-zinc-400">
            {h.label ? `${h.label} · ` : ""}
            {h.title || "Helper"}
            {" · "}
            <span data-testid={`code-helper-state-${h.id}`} className={stateTone(h.state)}>
              {helperStateWords(h.state)}
            </span>
          </p>
          {onOpen ? (
            <button type="button" data-testid={`code-helper-open-${h.id}`} className={OPEN} onClick={() => onOpen(h.id)}>
              Open
            </button>
          ) : null}
        </div>
      ))}
    </div>
  );
}
