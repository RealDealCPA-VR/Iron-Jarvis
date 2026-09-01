"use client";

/**
 * The pane rail — Build's spine (v1.218.0).
 *
 * v1.217.0 taught the app what each pane's agent is DOING and then put the
 * answer on the panes themselves: a chip in each header and a strip above the
 * canvas. The user's verdict on opening it was exact — "it looks the exact
 * same, no tabs to see different terminals with a status pane on the left" —
 * and they were right. The states were real; the SHAPE was not. herdr's whole
 * feel comes from the list: every agent in one column with its state, one pane
 * in focus, and nothing to hunt for. A free-form canvas of overlapping windows
 * is the thing that framing exists to replace.
 *
 * So this is the list, and Build now opens on it.
 *
 * WHY EVERY PANE STILL RENDERS. Only one pane is VISIBLE here, but none is
 * unmounted and none is `display:none` — the page stacks them all in the same
 * box and toggles `visibility`. That constraint is older than this component
 * (v1.190.0): a terminal in a zero-sized holder wraps its replay into a
 * default-sized buffer that no later fit can undo, so a hidden pane must keep
 * a real box. The rail selects; it never tears down.
 *
 * WHAT IT REFUSES TO DO. It does not reorder itself by state. Sorting the
 * blocked pane to the top is the obvious move and it is wrong: the list is
 * something you click, and a list that rearranges under the cursor while an
 * agent's state flickers costs more than the scan it saves. Blocked rows are
 * found by the amber pulse instead, and the header carries a jump for the case
 * where the list is long enough to scroll.
 */

import { Plus, X } from "lucide-react";

import {
  PaneDot,
  stateWord,
  type PaneDisplay,
} from "@/components/terminal/PaneState";

export interface RailPane {
  id: string;
  /** The pane's human handle, else its shell's name. */
  label: string;
  state: PaneDisplay;
  /** "claude" / "codex" / "pi", when something is known to be running. */
  cli?: string | null;
  /** The pane's folder — the second line, and often the real identifier. */
  cwd?: string | null;
  /** Terminal output arrived that this browser has not shown. */
  unseen?: boolean;
  /** The pane's hidden chat layer is holding an approval. */
  chatApproval?: boolean;
}

export function PaneRail({
  panes,
  focusedId,
  onFocus,
  onClose,
  onNew,
  busy = false,
  footer,
}: {
  panes: RailPane[];
  focusedId: string | null;
  onFocus: (id: string) => void;
  onClose: (id: string) => void;
  onNew: () => void;
  busy?: boolean;
  /** The layout switch, supplied by the page so the rail owns no modes. */
  footer?: React.ReactNode;
}) {
  const blocked = panes.filter((p) => p.state === "blocked");

  return (
    <div
      data-testid="pane-rail"
      className="flex h-full flex-col gap-2 overflow-hidden"
    >
      <div className="flex shrink-0 items-center justify-between px-1">
        <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-600">
          Panes
        </span>
        {blocked.length > 0 ? (
          // The count is a BUTTON, not a label: with the list unsorted, this is
          // how "never hunt for the stuck one" stays true once the rail is long
          // enough to scroll.
          <button
            type="button"
            data-testid="rail-jump-blocked"
            onClick={() => onFocus(blocked[0].id)}
            title="Go to the pane waiting on you"
            className="rounded-md border border-amber-400/30 bg-amber-400/[0.1] px-1.5 py-0.5 text-[10px] font-medium text-amber-200 transition-colors hover:bg-amber-400/[0.2]"
          >
            {blocked.length} needs you
          </button>
        ) : (
          <span className="text-[10px] text-zinc-700">{panes.length}</span>
        )}
      </div>

      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto pr-0.5">
        {panes.length === 0 ? (
          <p className="px-1 py-3 text-[11.5px] leading-relaxed text-zinc-600">
            No panes yet. Open one and it appears here with whatever is running
            inside it.
          </p>
        ) : (
          panes.map((p) => {
            const active = p.id === focusedId;
            return (
              <div
                key={p.id}
                data-testid={`rail-row-${p.id}`}
                className={`group relative flex items-center gap-2 rounded-xl border px-2 py-1.5 transition-colors ${
                  active
                    ? "border-accent/40 bg-accent/[0.07]"
                    : p.state === "blocked"
                      ? "border-amber-400/25 bg-amber-400/[0.05] hover:border-amber-400/40"
                      : "border-white/[0.05] hover:border-white/[0.12] hover:bg-white/[0.03]"
                }`}
              >
                <button
                  type="button"
                  onClick={() => onFocus(p.id)}
                  aria-current={active ? "true" : undefined}
                  title={p.cwd || undefined}
                  className="flex min-w-0 flex-1 items-center gap-2 text-left"
                >
                  <PaneDot state={p.state} />
                  <span className="min-w-0 flex-1">
                    <span
                      className={`block truncate font-mono text-[11.5px] ${
                        active ? "text-accent-soft" : "text-zinc-200"
                      }`}
                    >
                      {p.label}
                    </span>
                    <span className="flex items-center gap-1 text-[10px] text-zinc-600">
                      {/* The word, always — never colour alone. */}
                      <span
                        className={
                          p.state === "blocked"
                            ? "text-amber-300/90"
                            : p.state === "working"
                              ? "text-accent-soft/80"
                              : p.state === "done"
                                ? "text-emerald-300/80"
                                : ""
                        }
                      >
                        {stateWord(p.state)}
                      </span>
                      {p.cli ? <span className="truncate">· {p.cli}</span> : null}
                    </span>
                  </span>
                </button>

                {/* Two things the pane's own header cannot tell you from here:
                    output you have not seen, and an approval waiting in the
                    pane's HIDDEN chat layer. Both are about a pane you are not
                    looking at, which is the only kind this list is for. */}
                <span className="flex shrink-0 items-center gap-1">
                  {p.chatApproval ? (
                    <span
                      data-testid={`rail-chat-approval-${p.id}`}
                      title="An approval is waiting in this pane's chat"
                      className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400"
                    />
                  ) : null}
                  {p.unseen && !active ? (
                    <span
                      data-testid={`rail-unseen-${p.id}`}
                      title="New output you have not seen"
                      className="h-1.5 w-1.5 rounded-full bg-accent"
                    />
                  ) : null}
                  <button
                    type="button"
                    onClick={() => onClose(p.id)}
                    title="Close this pane"
                    aria-label={`Close ${p.label}`}
                    className="grid h-4 w-4 place-items-center rounded text-zinc-700 opacity-0 transition-colors hover:bg-rose-500/15 hover:text-rose-300 focus:opacity-100 group-hover:opacity-100"
                  >
                    <X size={11} />
                  </button>
                </span>
              </div>
            );
          })
        )}
      </div>

      <div className="shrink-0 space-y-1 border-t border-white/[0.06] pt-2">
        <button
          type="button"
          onClick={onNew}
          disabled={busy}
          data-testid="rail-new-pane"
          className="flex w-full items-center gap-2 rounded-xl border border-accent/25 bg-accent/[0.06] px-2 py-1.5 text-[11.5px] font-medium text-accent-soft transition-colors hover:bg-accent/[0.14] disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Plus size={13} className="shrink-0" />
          New pane
        </button>
        {footer}
      </div>
    </div>
  );
}
