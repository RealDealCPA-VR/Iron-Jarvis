"use client";

/**
 * The THREAD RAIL (v1.214.0) — the Agents module's left card.
 *
 * Reported verbatim: the left pane should be "a new fixed full length and
 * scrollable left card that is the height length of the app below the very top
 * pane", holding the threads, showing "the image of the related agent or
 * agents (layered as they are now)", with the roster and new-agent controls
 * collapsed into "a small icon button on the bottom left" that opens a modal.
 *
 * Three consequences, and each one is a decision rather than a coat of paint:
 *
 *   * THE RAIL IS THE THREAD LIST. It used to be the ROSTER — a column of
 *     agent faces — with the threads in a second 16rem rail beside the
 *     conversation. Two rails is one too many for a module with one subject:
 *     the thing you pick, dozens of times a day, is a CONVERSATION. Agents are
 *     picked rarely and configured rarer still, so they move behind the icon
 *     at the foot of this card.
 *
 *   * IT IS FULL HEIGHT, AND IT SCROLLS ITSELF. The card fills the app below
 *     the title bar and its list is the only thing inside it that scrolls, so
 *     the header (how many threads) and the footer (the agents icon) are
 *     always on screen. A long thread list used to push the roster's gear —
 *     the ONLY door to agent configuration since v1.179.0 — off the bottom of
 *     the page.
 *
 *   * THE FACES ARE THE AGENTS' OWN. `FaceStack` draws each panel with the
 *     portraits and faces every other surface uses, layered exactly as the old
 *     initial-dots were.
 *
 * The icon is a BUTTON, not a disclosure: it opens `AgentsModal`, which is a
 * real dialog attached to the page rather than a card revealed into a column.
 * That is the whole point of the change — see `components/Modal.tsx` for why
 * the old surface could not simply be made bigger.
 */

import { Check, Plus, Trash2 } from "lucide-react";
import { ErrorNote } from "@/components/ui";
import { ModuleTitle } from "@/components/PageHeader";
import { timeAgo } from "@/lib/format";
import { FaceStack } from "./FaceStack";
import { GearFace } from "./RosterStrip";
import type { ThreadRow } from "./identity";

export function ThreadRail({
  threads,
  selectedId,
  onSelect,
  onNew,
  pendingDelete,
  onArmDelete,
  onConfirmDelete,
  avatarByKey,
  error,
  onOpenAgents,
  agentCount,
  pickedName,
  agentsOpen = false,
  title,
  titleHint,
}: {
  threads: ThreadRow[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  /** The thread whose delete is armed (one click arms, the next confirms). */
  pendingDelete: string | null;
  onArmDelete: (id: string) => void;
  onConfirmDelete: (id: string) => void;
  /** participantKey → token-signed portrait URL, from the page's roster. */
  avatarByKey?: Map<string, string | null>;
  error?: string | null;
  /** Open the agents room. Omitted on a daemon with no roster to show. */
  onOpenAgents?: () => void;
  agentCount?: number;
  /** Who the page is working with — named beside the icon so the selection
   *  does not vanish along with the roster it used to be made in. */
  pickedName?: string | null;
  agentsOpen?: boolean;
  /** The MODULE's name, rendered top-left inside this card. Omitted on the
   *  older-daemon page, which still has a `PageHeader` above the flow — two
   *  <h1>s on one page is not a tidier header, it is a broken outline. */
  title?: string;
  /** Its description, shown on hover/focus/tap (see ModuleTitle). */
  titleHint?: string;
}) {
  return (
    // `card-surface` directly rather than <Card>: this rail is a flex COLUMN
    // with its own header, one scrolling region and a footer, and Card's props
    // (title/right/pad) describe a different shape — it also takes no
    // data-* attributes, and the layout tests need to address the card itself.
    <section
      data-testid="agents-thread-rail"
      className="card-surface flex h-full min-h-0 flex-col overflow-hidden"
    >
      {/* THE MODULE'S NAME, TOP LEFT, INSIDE THIS CARD (v1.214.3). Reported:
          "the title Agents should be on the top left inside the card of the
          threads and the chat box pushed up so it looks more clean." It used
          to be a `PageHeader` spanning the conversation column, which pushed
          the transcript down by a heading's height on every visit and left the
          rail starting below nothing. With the title in here the two columns
          begin on the same line and the conversation gets that space back.
          `ModuleTitle` is the SAME component the other 37 pages use, at a
          smaller size — the hover/focus/tap popover and its a11y wiring have
          one implementation, not two. */}
      {title && (
        <div className="flex shrink-0 items-start justify-between gap-2 px-3 pt-2.5">
          <ModuleTitle
            title={title}
            hint={titleHint}
            className="text-[15px] font-semibold tracking-tight text-zinc-50"
            iconSize={11}
          />
        </div>
      )}
      <div
        className={`flex shrink-0 items-center justify-between border-b hairline px-3 pb-2 ${
          title ? "pt-1.5" : "pt-2"
        }`}
      >
        <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
          Round-table · {threads.length}
        </span>
        <button
          type="button"
          onClick={onNew}
          className="btn-ghost px-2 py-1 text-[12px]"
          title="Start a new agent thread"
        >
          <Plus size={13} /> New
        </button>
      </div>

      {/* THE ONLY SCROLLING PART. `min-h-0` is load-bearing: a flex child's
          default `min-height:auto` refuses to shrink below its content, so
          without it the list grows the card and the footer leaves the screen —
          which is the exact failure this rail was built to end. */}
      <div className="max-h-[50vh] min-h-0 flex-1 space-y-0.5 overflow-y-auto p-1.5 md:max-h-none">
        {error && <ErrorNote>{error}</ErrorNote>}
        {threads.length === 0 && !error && (
          <p className="px-2 py-3 text-[11.5px] leading-relaxed text-zinc-600">
            No threads yet. Start one and pick who sits at the table.
          </p>
        )}
        {threads.map((t) => {
          const active = t.id === selectedId;
          return (
            <div
              key={t.id}
              className={`group/thread relative rounded-xl border transition-colors ${
                active
                  ? "border-accent/25 bg-accent/[0.08]"
                  : "border-transparent hover:bg-white/[0.04]"
              }`}
            >
              <button
                type="button"
                onClick={() => onSelect(t.id)}
                aria-current={active ? "true" : undefined}
                className="w-full px-2.5 py-2 pr-8 text-left"
                title={t.title || "Agent thread"}
              >
                <span
                  className={`block truncate text-[13px] ${
                    active ? "text-accent-soft" : "text-zinc-200"
                  }`}
                >
                  {t.title || "Agent thread"}
                </span>
                <span className="mt-1.5 flex items-center gap-2">
                  <FaceStack participants={t.participants} avatarByKey={avatarByKey} />
                  <span className="min-w-0 truncate text-[11px] text-zinc-500">
                    {t.message_count} msg{t.message_count === 1 ? "" : "s"} ·{" "}
                    {timeAgo(t.updated_at)}
                  </span>
                </span>
              </button>
              {pendingDelete === t.id ? (
                <button
                  type="button"
                  onClick={() => onConfirmDelete(t.id)}
                  aria-label="Confirm delete"
                  title="Click again to delete"
                  className="absolute right-1.5 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-md bg-rose-500/15 text-rose-300"
                >
                  <Check size={13} />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => onArmDelete(t.id)}
                  aria-label={`Delete ${t.title || "thread"}`}
                  title="Delete this thread"
                  className="absolute right-1.5 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-md text-zinc-500 opacity-0 transition-opacity hover:bg-white/[0.06] hover:text-rose-300 focus-visible:opacity-100 group-hover/thread:opacity-100"
                >
                  <Trash2 size={13} />
                </button>
              )}
            </div>
          );
        })}
      </div>

      {/* THE ICON, BOTTOM LEFT. Outside the scroll region on purpose: agent
          configuration lives behind this and nowhere else, so it must not be
          something a long list can carry away.
          The gear-with-a-face is the drawing the user asked for by name in
          v1.178.0 and it still earns its place — a plain cog reads as
          "settings for this page", a cog with eyes reads as "one of these",
          which is what the button actually opens. */}
      {onOpenAgents && (
        <div className="flex shrink-0 items-center gap-2 border-t hairline p-1.5">
          <button
            type="button"
            onClick={onOpenAgents}
            data-testid="roster-gear"
            aria-haspopup="dialog"
            aria-expanded={agentsOpen}
            title="Agents — customize any of them, or create a new one"
            className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl border transition-colors ${
              agentsOpen
                ? "border-accent/50 bg-accent/[0.10] text-accent-soft"
                : "border-white/[0.07] bg-white/[0.02] text-zinc-500 hover:border-accent/40 hover:bg-white/[0.05] hover:text-accent-soft"
            }`}
          >
            <GearFace size={22} />
            {/* The button's job in full, for a screen reader that gets no
                tooltip: the gear is aria-hidden, so without this the control
                has no accessible name at all. */}
            <span className="sr-only">
              Agents — the roster, portraits and faces, and creating a new local
              or remote agent
            </span>
          </button>
          <span className="min-w-0 flex-1 leading-tight">
            <span className="block truncate text-[11.5px] text-zinc-400">
              Agents{typeof agentCount === "number" ? ` · ${agentCount}` : ""}
            </span>
            {/* THE SELECTION SURVIVES THE MOVE. It used to be visible as a
                highlighted row in the roster rail; with the roster behind a
                door, the page's pick would otherwise be a state with nothing
                on screen to show it. */}
            <span
              data-testid="rail-picked"
              className="block truncate text-[10.5px] text-zinc-600"
            >
              {pickedName ? `working with ${pickedName}` : "nobody picked yet"}
            </span>
          </span>
        </div>
      )}
    </section>
  );
}

export default ThreadRail;
