"use client";

// The chat list's project filter (v1.329.0, calm chat W6 H1).
//
// Inside a project chat the chat page's list could show every chat (grouped,
// that project first) or only that project's chats. That switch used to be an
// uppercase "ALL CHATS" label plus a small underlined "Only <project>" link,
// stacked under the sidebar's own "CHATS" heading: three headings before the
// first row, where every other page shows "Chats" and a search. It is now one
// quiet pair of ghosts under the search, in sentence case, the one in effect
// filled like the open chat's row: "All chats" and "Only <project>".
//
// It changes the LIST only (the v1.315.0 rule): the open chat, its project and
// the composer stay as they are. Both buttons are always there, so the state
// is said by which one is filled (`aria-pressed`), not by a third label.
// Theme tokens only.

export default function ThreadRailFilter({
  projectName,
  only,
  onChange,
}: {
  projectName: string;
  /** The list shows only this project's chats. */
  only: boolean;
  onChange: (only: boolean) => void;
}) {
  const chip = (on: boolean) =>
    [
      "inline-flex h-6 min-w-0 items-center rounded-[8px] px-2 text-[12px] transition-colors",
      "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60",
      on
        ? "bg-white/[0.07] text-zinc-200"
        : "text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-200",
    ].join(" ");
  return (
    <div
      role="group"
      aria-label="Which chats to show"
      data-testid="thread-rail-filter"
      data-only={only ? "true" : "false"}
      className="mt-1.5 flex min-w-0 items-center gap-1"
    >
      <button
        type="button"
        data-testid="thread-rail-filter-all"
        aria-pressed={!only}
        onClick={() => onChange(false)}
        className={`${chip(!only)} shrink-0`}
        title="Show every saved chat here. The open chat and its project stay as they are."
      >
        All chats
      </button>
      <button
        type="button"
        data-testid="thread-rail-filter-only"
        aria-pressed={only}
        onClick={() => onChange(true)}
        className={chip(only)}
        title={`Show only the chats in ${projectName}`}
      >
        <span className="min-w-0 truncate">Only {projectName}</span>
      </button>
    </div>
  );
}
