"use client";

/**
 * The project drawer's calm parts (v1.329.0, calm chat W4 F6).
 *
 * The drawer used to be a stack of bordered cards: a PROJECT card, a
 * Workspace card, the Directory card with a big accent "Open terminal here"
 * button (which did nothing from chat), and Files / Knowledge / Tasks /
 * Board / Media as bordered chips in half-pixel type that repeated the top
 * bar's own tabs. Now it reads like the chat beside it:
 *
 *   * plain SECTIONS with a quiet label (the chat list's group heading style)
 *     and a hairline under each, never a box;
 *   * ghost controls (transparent at rest, a soft fill on hover), the same
 *     language as the composer's chips;
 *   * Files / Knowledge as plain text TABS in whole-pixel type, a real
 *     tablist; Tasks / Board / Media are NOT repeated here, the top bar
 *     already holds them;
 *   * the terminal action is a quiet ghost button that really opens a Build
 *     terminal in the folder (POST /terminals, then Build focused on it).
 *
 * Theme tokens only (`zinc-*`, `accent`, `white` overlays, `tone-*`,
 * `hairline`), so the dark Marks and the light Daylight Mark both read right.
 */

import { useRef, useState, type ReactNode } from "react";
import { ApiError, post } from "@/lib/api";

/** A section's quiet label: the chat list's group heading, word for word. */
export const PANEL_LABEL =
  "text-[11px] font-medium uppercase tracking-[0.06em] text-zinc-500";

/** A ghost control in the drawer: transparent at rest, filled on hover, a
 *  ring only for a keyboard user. */
export const PANEL_GHOST =
  "inline-flex h-7 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-2 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-40";

/** The project picker as a ghost select: no field box, the text lines up with
 *  the section label (the negative margin cancels its own padding). */
export const PANEL_SELECT =
  "-mx-2 block w-[calc(100%+1rem)] cursor-pointer rounded-lg bg-transparent px-2 py-1.5 text-[13px] text-zinc-100 transition-colors hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50";

/**
 * A wrapper for a child that still draws its own card (the folder picker and
 * the folder's file list are shared with Build, which keeps its cards). It
 * takes the card's edge, fill, shadow and blur away and lets the child's own
 * 16px padding line up with the drawer's (the drawer body is px-4, so -mx-4
 * cancels it exactly and nothing overflows).
 */
export const UNBOX_CHILD =
  "-mx-4 [&>div]:rounded-none [&>div]:border-0 [&>div]:bg-transparent [&>div]:shadow-none [&>div]:backdrop-filter-none";

/** One plain section: a quiet label (with optional ghost actions on its right)
 *  and a hairline under it. */
export function PanelSection({
  label,
  right,
  children,
  testId,
  className = "",
}: {
  label: ReactNode;
  right?: ReactNode;
  children?: ReactNode;
  testId?: string;
  className?: string;
}) {
  return (
    <section data-testid={testId} className={`shrink-0 border-b hairline py-3 ${className}`}>
      <div className="flex min-h-[28px] flex-wrap items-center gap-x-2 gap-y-1">
        <h3 className={PANEL_LABEL}>{label}</h3>
        {right ? (
          <div className="ml-auto flex flex-wrap items-center justify-end gap-0.5">{right}</div>
        ) : null}
      </div>
      {children ? <div className="mt-1">{children}</div> : null}
    </section>
  );
}

export interface PanelTab<T extends string> {
  value: T;
  label: string;
}

/**
 * Plain text tabs, a real tablist: role=tab, aria-selected, aria-controls,
 * roving tabindex. Arrow keys, Home and End move focus (wrapping); a click,
 * Enter or Space selects (manual activation, like the top bar's views).
 */
export function PanelTabs<T extends string>({
  label,
  tabs,
  value,
  onSelect,
  panelId,
  idPrefix,
}: {
  label: string;
  tabs: ReadonlyArray<PanelTab<T>>;
  value: T;
  onSelect: (v: T) => void;
  panelId: string;
  idPrefix: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  function onKeyDown(e: React.KeyboardEvent<HTMLButtonElement>, i: number) {
    const n = tabs.length;
    let next = -1;
    if (e.key === "ArrowRight") next = (i + 1) % n;
    else if (e.key === "ArrowLeft") next = (i - 1 + n) % n;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = n - 1;
    if (next < 0) return;
    e.preventDefault();
    refs.current[next]?.focus();
  }
  return (
    <div
      role="tablist"
      aria-label={label}
      className="flex shrink-0 items-center gap-1 border-b hairline py-2"
    >
      {tabs.map((t, i) => {
        const selected = t.value === value;
        return (
          <button
            key={t.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            id={`${idPrefix}-${t.value}`}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={panelId}
            tabIndex={selected ? 0 : -1}
            onClick={() => onSelect(t.value)}
            onKeyDown={(e) => onKeyDown(e, i)}
            className={`rounded-md px-1.5 py-1 text-[13px] transition-colors first:-ml-1.5 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 ${
              selected ? "font-medium text-zinc-100" : "text-zinc-500 hover:text-zinc-200"
            }`}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

/** Where Build opens on a given pane. */
export function terminalHref(id: string): string {
  return `/terminals?focus=${encodeURIComponent(id)}`;
}

function defaultNavigate(href: string) {
  // A full load of Build, not a client push: this is a press that leaves the
  // chat, and the drawer has no router of its own (it renders in tests and
  // portals without one).
  window.location.assign(href);
}

/**
 * Open a NEW Build terminal in `folder` and take the user to it. The press is
 * the consent (a new shell, nothing typed into it); the daemon's own refusal
 * (pane cap, a folder it cannot start in) comes back as one plain sentence.
 */
export function useOpenTerminal(
  folder: string | null,
  navigate: (href: string) => void = defaultNavigate,
): { open: () => Promise<void>; busy: boolean; error: string | null } {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busyRef = useRef(false);
  async function open() {
    if (!folder || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      const info = await post<{ id?: string }>("/terminals", { cwd: folder });
      if (!info?.id) throw new Error("The terminal did not start.");
      navigate(terminalHref(info.id));
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 0
          ? "Iron Jarvis is not answering, so no terminal was opened."
          : e instanceof Error
            ? e.message
            : String(e),
      );
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  return { open, busy, error };
}
