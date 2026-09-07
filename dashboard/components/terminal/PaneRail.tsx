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
 *
 * WHAT IT ALSO OWNS (v1.238.0). Per-pane CAPABILITIES, for the same reason
 * renaming moved here in v1.219.0: this is the only surface that lists every
 * pane, so it is the only one where you can set five of them without visiting
 * five panes. Only Browser is enforced in these ships, and the popover says so
 * in its own copy rather than shipping four checkboxes that look live and gate
 * nothing — which is the v1.218.0 lesson written as a rule.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Check, Pencil, Plus, SlidersHorizontal, X } from "lucide-react";

import { get, patch } from "@/lib/api";
import {
  PaneDot,
  stateWord,
  type PaneDisplay,
} from "@/components/terminal/PaneState";
import type { BrowserStatus } from "@/lib/types";

/**
 * A pane's Capabilities (v1.238.0, D20). Deliberately declared HERE rather than
 * imported: the popover is the only consumer, and every field is optional, so a
 * `PaneCapabilities` from `lib/types` assigns to it without this file having to
 * be edited in lock-step with that one.
 */
export interface RailPaneCapabilities {
  files?: boolean;
  shell?: boolean;
  browser?: boolean;
  extensions?: boolean;
  memory?: boolean;
}

export type RailCapabilityKey = keyof RailPaneCapabilities;

/** The five boxes, in D20's order. */
export const PANE_CAPABILITIES: {
  key: RailCapabilityKey;
  label: string;
  hint: string;
}[] = [
  { key: "files", label: "Files", hint: "read and write in this pane's folder" },
  { key: "shell", label: "Shell", hint: "run commands in this pane" },
  { key: "browser", label: "Browser", hint: "drive your browser through Jarvis" },
  { key: "extensions", label: "Extensions", hint: "use this install's extensions" },
  { key: "memory", label: "Memory", hint: "read this install's memory" },
];

/** The one capability these five ships actually gate on. */
export const ENFORCED_CAPABILITY: RailCapabilityKey = "browser";

/** The two `browser_access` words under which a pane's Browser box can be live. */
const BROWSER_ACCESS_LIVE = ["read_only", "interactive"];

/** Every key present and boolean, so a PATCH body is never half a map. */
export function fullCapabilities(
  ...layers: (RailPaneCapabilities | null | undefined)[]
): Required<RailPaneCapabilities> {
  const out: Required<RailPaneCapabilities> = {
    files: false,
    shell: false,
    browser: false,
    extensions: false,
    memory: false,
  };
  for (const layer of layers) {
    if (!layer) continue;
    for (const cap of PANE_CAPABILITIES) {
      const v = layer[cap.key];
      if (typeof v === "boolean") out[cap.key] = v;
    }
  }
  return out;
}

/**
 * What a capability box says about itself — the WORD, never colour alone.
 *
 * `access` is the live `config.browser_access`; `""` means the install setting
 * has not answered yet, and `failed` means asking for it did not work. Both are
 * unavailable, because a Browser box that renders live on an unknown setting is
 * the v1.218.0 lie in miniature.
 *
 * `on` is THIS PANE'S recorded value, and it is a parameter because from
 * v1.238.0 an unticked Browser box is not decoration — the daemon's chat gate
 * and its outward MCP grant both refuse a pane that has not been ticked. A
 * single word "enforced" printed under both a ticked and an unticked box
 * described the SETTING and left the user to guess what it did to the pane in
 * front of them, which is the same distance between copy and code the v1.218.0
 * lesson is about. So the word names the effect on this pane, in the direction
 * the box is currently pointing.
 */
export function capabilityStatus(
  key: RailCapabilityKey,
  access: string,
  failed: boolean,
  on = false,
): { word: string; available: boolean; offForInstall: boolean } {
  if (key !== ENFORCED_CAPABILITY) {
    return {
      word: "recorded, not enforced yet — nothing gates on it",
      available: true,
      offForInstall: false,
    };
  }
  if (failed) {
    return { word: "install setting unreadable", available: false, offForInstall: false };
  }
  if (!access) {
    return { word: "checking this install", available: false, offForInstall: false };
  }
  if (!BROWSER_ACCESS_LIVE.includes(access)) {
    return { word: "off for this install", available: false, offForInstall: true };
  }
  return {
    word: on
      ? "enforced — this pane may use your browser"
      : "enforced — this pane gets no browser tools",
    available: true,
    offForInstall: false,
  };
}

export interface RailPane {
  id: string;
  /** The pane's human handle, else its shell's name. */
  label: string;
  state: PaneDisplay;
  /** The CLI's own name — "Claude Code", "Grok CLI", "Pi" — not its id.
   *  Empty when the pane is a plain shell. */
  cli?: string | null;
  /** The pane's folder — the second line, and often the real identifier. */
  cwd?: string | null;
  /** Terminal output arrived that this browser has not shown. */
  unseen?: boolean;
  /** The pane's hidden chat layer is holding an approval. */
  chatApproval?: boolean;
  /** Server-side per-pane Capabilities (D20). Absent = the daemon has not been
   *  asked yet, which renders as five unticked boxes — the safe default and
   *  exactly how an old `terminals.json` snapshot restores. */
  capabilities?: RailPaneCapabilities | null;
}

export function PaneRail({
  panes,
  focusedId,
  onFocus,
  onClose,
  onRename,
  onNew,
  busy = false,
  footer,
}: {
  panes: RailPane[];
  focusedId: string | null;
  onFocus: (id: string) => void;
  onClose: (id: string) => void;
  /** Commit a new name for a pane. "" clears it back to the shell's name. */
  onRename: (id: string, name: string) => void;
  onNew: () => void;
  busy?: boolean;
  /** The layout switch, supplied by the page so the rail owns no modes. */
  footer?: React.ReactNode;
}) {
  const blocked = panes.filter((p) => p.state === "blocked");

  // RENAMING FROM THE RAIL (v1.219.0). The name was editable in the pane
  // header, which is the one place you are already looking at that pane — so
  // naming the OTHER four meant visiting each one. The rail is where you see
  // them all, so it is where naming them belongs.
  //
  // A single click must keep selecting the pane: that is what the rail is for,
  // and the click-to-edit the header uses would fight it. So renaming has its
  // own two doors — the pencil that appears on hover, and a double-click on the
  // name for anyone who expects that — and neither steals the plain click.
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  const begin = useCallback((p: RailPane) => {
    setDraft(p.label);
    setEditing(p.id);
  }, []);
  const commit = useCallback(
    (id: string, previous: string) => {
      setEditing(null);
      const next = draft.trim();
      if (next !== previous) onRename(id, next);
    },
    [draft, onRename],
  );

  // CAPABILITIES, PER PANE (v1.238.0, D20). The rail is the only surface that
  // lists every pane, and per-pane configuration already lives here since
  // renaming moved in v1.219.0 — so this is where the five boxes belong.
  //
  // The plain click still SELECTS: the popover has its own affordance, exactly
  // as rename does, and neither steals the row.
  //
  // The install-wide `browser_access` is read WHEN A POPOVER OPENS rather than
  // polled. Build already runs several pollers and this answer is only ever
  // looked at while the panel is on screen; asking once, at the moment the user
  // is standing there, is both live and free the rest of the time.
  const [capsOpen, setCapsOpen] = useState<string | null>(null);
  const [access, setAccess] = useState("");
  const [accessFailed, setAccessFailed] = useState(false);
  const [capsError, setCapsError] = useState<string | null>(null);
  // Optimistic, per pane, so a box responds to the click; the daemon is the
  // truth and the next list refresh replaces it. A refused PATCH puts it back.
  const [overlay, setOverlay] = useState<Record<string, RailPaneCapabilities>>({});

  const openCaps = useCallback((id: string) => {
    setCapsOpen(id);
    setCapsError(null);
    setAccessFailed(false);
    setAccess("");
    get<BrowserStatus>("/browser/status")
      .then((s) => setAccess(String(s?.access || "off")))
      .catch(() => setAccessFailed(true));
    // RE-READ THE PANE, every open. `panes` comes from the Build page, which
    // fetches /terminals once on mount and never polls — so after a tick, a
    // close and a reopen, `p.capabilities` is the value from page load and the
    // popover would render a box the daemon has already changed. Showing a
    // stale grant is the worst half of this surface's job done wrong: the user
    // reads "no Browser" off a pane whose running harness is holding a token
    // that resolves to browser:true. There is no GET /terminals/{id}, so the
    // list route answers and the one row is lifted into the overlay.
    get<{ terminals?: { id?: string; capabilities?: RailPaneCapabilities | null }[] }>(
      "/terminals",
    )
      .then((r) => {
        const row = (r?.terminals || []).find((t) => t?.id === id);
        if (!row) return;
        setOverlay((o) => ({ ...o, [id]: fullCapabilities(row.capabilities) }));
      })
      .catch(() => {
        // The props are still the best answer available, and the boxes render
        // from them. Nothing is invented and nothing is claimed.
      });
  }, []);

  const closeCaps = useCallback(() => {
    setCapsOpen(null);
    setCapsError(null);
    // The overlay is DROPPED on close and re-seeded from the daemon on the next
    // open (see `openCaps`), so it can never become a long-lived second copy of
    // the truth that quietly diverges from the pane.
    setOverlay({});
  }, []);

  const toggleCap = useCallback(
    async (p: RailPane, key: RailCapabilityKey) => {
      const before = fullCapabilities(p.capabilities, overlay[p.id]);
      const next = { ...before, [key]: !before[key] };
      setOverlay((o) => ({ ...o, [p.id]: next }));
      setCapsError(null);
      try {
        // ONLY THE KEY THAT CHANGED. `TerminalSession.update_capabilities` is a
        // real partial merge, and sending the whole five-key map made this
        // client's cached idea of the other four AUTHORITATIVE: with a stale
        // `before` — which is what a reopened popover had — ticking Shell
        // shipped `browser: false` alongside it and revoked, silently, a
        // capability the user had granted and the daemon had stored. One key is
        // the only body that can say what the click meant.
        const body = await patch<{ capabilities?: RailPaneCapabilities | null }>(
          `/terminals/${p.id}`,
          { capabilities: { [key]: next[key] } },
        );
        // The daemon answers with the pane's whole merged map. Adopt it: it is
        // the truth, and it is how a value changed by another surface between
        // the read and the write stops being wrong on screen.
        if (body && body.capabilities) {
          const merged = fullCapabilities(body.capabilities);
          setOverlay((o) => ({ ...o, [p.id]: merged }));
        }
      } catch {
        setOverlay((o) => ({ ...o, [p.id]: before }));
        setCapsError("Could not save that — the pane still has the capabilities it had.");
      }
    },
    [overlay],
  );

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
                {editing === p.id ? (
                  <>
                    <PaneDot state={p.state} />
                    <input
                      ref={inputRef}
                      autoFocus
                      data-testid="rail-rename-input"
                      aria-label={`Rename pane ${p.label}`}
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onBlur={() => commit(p.id, p.label)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") commit(p.id, p.label);
                        // Escape ABANDONS. A rename you cannot back out of is
                        // one people stop starting.
                        if (e.key === "Escape") setEditing(null);
                      }}
                      className="field min-w-0 flex-1 py-0.5 font-mono text-[11.5px]"
                    />
                  </>
                ) : (
                <button
                  type="button"
                  onClick={() => onFocus(p.id)}
                  onDoubleClick={() => begin(p)}
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
                    </span>
                    {/* The CLI on its OWN line, by the name the Launch menu
                        used. The first cut appended the daemon's id — "claude",
                        "grok" — beside the state, which is the internal key,
                        not what the user picked. */}
                    {p.cli ? (
                      <span
                        data-testid={`rail-cli-${p.id}`}
                        className="block truncate text-[10px] text-zinc-500"
                      >
                        {p.cli}
                      </span>
                    ) : null}
                  </span>
                </button>
                )}

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
                    onClick={() => (capsOpen === p.id ? closeCaps() : openCaps(p.id))}
                    title="Capabilities for this pane"
                    aria-label={`Capabilities for ${p.label}`}
                    aria-expanded={capsOpen === p.id}
                    data-testid={`rail-caps-${p.id}`}
                    className={`grid h-4 w-4 place-items-center rounded transition-colors hover:bg-white/[0.08] hover:text-zinc-300 focus:opacity-100 group-hover:opacity-100 ${
                      capsOpen === p.id
                        ? "text-accent-soft opacity-100"
                        : "text-zinc-700 opacity-0"
                    }`}
                  >
                    <SlidersHorizontal size={10} />
                  </button>
                  <button
                    type="button"
                    onClick={() => begin(p)}
                    title="Rename this pane"
                    aria-label={`Rename ${p.label}`}
                    data-testid={`rail-rename-${p.id}`}
                    className="grid h-4 w-4 place-items-center rounded text-zinc-700 opacity-0 transition-colors hover:bg-white/[0.08] hover:text-zinc-300 focus:opacity-100 group-hover:opacity-100"
                  >
                    <Pencil size={10} />
                  </button>
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

                {capsOpen === p.id ? (
                  <>
                    <button
                      aria-hidden
                      tabIndex={-1}
                      onClick={closeCaps}
                      className="fixed inset-0 z-30 cursor-default"
                    />
                    <div
                      role="dialog"
                      aria-label={`Capabilities for ${p.label}`}
                      data-testid={`rail-caps-panel-${p.id}`}
                      className="absolute right-1 top-full z-40 mt-1 w-72 rounded-xl border border-white/10 bg-ink-900/95 p-2 shadow-card backdrop-blur"
                    >
                      <p className="px-1 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-500">
                        Capabilities
                      </p>
                      <div className="space-y-1">
                        {PANE_CAPABILITIES.map((cap) => {
                          const on = fullCapabilities(p.capabilities, overlay[p.id])[cap.key];
                          const status = capabilityStatus(cap.key, access, accessFailed, on);
                          // WITHDRAWING A GRANT IS ALWAYS ALLOWED. Only the
                          // off->on direction waits on the install setting; a
                          // box that is ON but unavailable was still the user's
                          // recorded choice, and locking it left them having to
                          // re-enable Browser install-wide in order to take it
                          // away from one pane.
                          const clickable = status.available || on;
                          return (
                            <button
                              key={cap.key}
                              type="button"
                              role="checkbox"
                              aria-checked={on}
                              disabled={!clickable}
                              data-testid={`rail-cap-${p.id}-${cap.key}`}
                              onClick={() => toggleCap(p, cap.key)}
                              className={`flex w-full items-start gap-2 rounded-lg border px-2 py-1.5 text-left transition-colors ${
                                !status.available && !on
                                  ? "cursor-not-allowed border-white/[0.06] bg-white/[0.02] opacity-70"
                                  : on
                                    ? "border-accent/30 bg-accent/[0.06]"
                                    : "border-white/[0.06] bg-white/[0.02] hover:border-white/15"
                              }`}
                            >
                              <span
                                className={`mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded border ${
                                  on
                                    ? "border-accent/50 bg-accent text-ink-950"
                                    : "border-white/15 bg-transparent text-transparent"
                                }`}
                              >
                                <Check size={11} strokeWidth={3} />
                              </span>
                              <span className="min-w-0 flex-1">
                                <span className="block text-[11.5px] font-medium text-zinc-200">
                                  {cap.label}
                                </span>
                                <span className="block text-[10px] leading-relaxed text-zinc-500">
                                  {cap.hint}
                                </span>
                                {/* THE WORD, always. Never colour alone, and
                                    never merely unticked: an unavailable box has
                                    to SAY what makes it unavailable. */}
                                <span
                                  data-testid={`rail-cap-word-${p.id}-${cap.key}`}
                                  className={`block text-[10px] ${
                                    status.available ? "text-zinc-500" : "text-amber-300"
                                  }`}
                                >
                                  {status.word}
                                </span>
                                {/* D20 example, said out loud: global off plus
                                    pane checked equals unavailable. */}
                                {on && !status.available ? (
                                  <span
                                    data-testid={`rail-cap-conflict-${p.id}-${cap.key}`}
                                    className="block text-[10px] leading-relaxed text-amber-200"
                                  >
                                    Ticked for this pane, but unavailable — the install setting wins. Untick it here if you want the grant gone.
                                  </span>
                                ) : null}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                      {capabilityStatus(ENFORCED_CAPABILITY, access, accessFailed).offForInstall ? (
                        <p className="mt-1.5 px-1 text-[10px] leading-relaxed text-amber-200">
                          Browser is off for this whole install.{" "}
                          <Link
                            href="/computeruse"
                            className="text-accent-soft underline-offset-2 hover:underline"
                          >
                            Open the Browser page
                          </Link>{" "}
                          to turn it on.
                        </p>
                      ) : null}
                      <p
                        data-testid={`rail-caps-footnote-${p.id}`}
                        className="mt-1.5 border-t border-white/[0.06] px-1 pt-1.5 text-[10px] leading-relaxed text-zinc-500"
                      >
                        Only <span className="text-zinc-300">Browser</span> is enforced today. A pane
                        starts unticked, and an unticked Browser box means this pane&rsquo;s chat and
                        anything it launches get no browser tools. Files, Shell, Extensions and Memory
                        are recorded here and shown to you; nothing gates on them yet.
                      </p>
                      {capsError ? (
                        <p
                          role="alert"
                          className="mt-1.5 rounded-lg border border-amber-500/25 bg-amber-500/10 px-2 py-1 text-[10px] leading-relaxed text-amber-200"
                        >
                          {capsError}
                        </p>
                      ) : null}
                    </div>
                  </>
                ) : null}
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
