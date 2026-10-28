"use client";

/**
 * v1.403.0: THE CODING-SESSION TEAM on the screen (lib/sessionTeam).
 *
 * The user's decision: "Build the coding-session team: Lead plans and checks
 * but never edits files, Workers do the code, Reviewer checks every change.
 * If no worker can take a piece, the lead may do it and must say so."
 *
 * Two pieces live here:
 *
 *  - TeamControl: the box's **Team** chip ("Team" alone, "Team · 3" with a
 *    team) and its panel — the TEAM LEAD (the session's own model, named in
 *    the model chip's own words, not changed here), up to two WORKERS and an
 *    optional REVIEWER, each picked from the same provider-grouped model list
 *    "Try on…" uses (lib/tryOnMenu), each with its state in words. A pick makes
 *    (or reuses) the model's team member (`POST /team/members/from-model`),
 *    then sets the session's team (`PATCH /build/sessions/{id} {team}`);
 *    "No team" sets it back to working alone. "Use this team for new sessions
 *    in this folder" saves it as the folder's default
 *    (`PUT /build/folders/team`). A refusal is one plain sentence in the panel.
 *    An Iron Jarvis without teams (its `GET /build/folders/team` answers 404)
 *    shows no Team control at all.
 *
 *  - TeamReplyLine: a team reply's quiet line ("Team: lead Opus 5.5 · workers
 *    glm-5.3-flash, Qwen3.8-27B · reviewer Codex") and, in the warn tone and
 *    always in sight, every time the lead did a piece itself ("The team lead
 *    did this itself: <reason>") — never folded away.
 *
 * The panel is PORTALED to the page and fixed above its chip, always inside
 * the window (lib/buildAttempts `tryOnMenuPosition`, the "Try on…" menu's
 * rule): the session's columns clip what overflows them, and a phone has 16 px
 * either side. A disclosure, not a form: every button is `type="button"`;
 * Escape closes the model list first, then the panel (the focus back on the
 * chip); a press outside closes it.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { ChevronRight, TriangleAlert, Users, X } from "lucide-react";
import { ApiError } from "@/lib/api";
import { composerChipClass, fitIconChip, fitShows, fitWords } from "@/lib/composerChips";
import { tryOnMenuPosition } from "@/lib/buildAttempts";
import { matchTryOn, placeWord, tryOnGroups, type TryOnGroup, type TryOnModel } from "@/lib/tryOnMenu";
import { KIND_BADGE, MODEL_FILTER_MAX } from "@/components/chat/ModelMenu";
import { workerModelName } from "@/lib/buildHelpers";
import { isPhone } from "@/components/harness/TryOnModels";
import { patchSession, type BuildSessionView } from "@/lib/buildSessions";
import {
  LEAD_DUTY,
  MAX_WORKERS,
  REVIEWER_DUTY,
  REVIEWER_WORDS,
  TEAM_LEAD_WORDS,
  WORKERS_WORDS,
  WORKER_DUTY,
  decodeReplyTeam,
  getFolderTeam,
  leadDidLines,
  memberFromModel,
  memberStateWarn,
  memberStateWords,
  piecesWords,
  putFolderTeam,
  sameTeam,
  teamChipWords,
  teamCount,
  teamLineWords,
  teamNamesOf,
  teamPatch,
  type SessionTeam,
  type TeamMemberView,
  type TeamNames,
} from "@/lib/sessionTeam";
import type { CatalogRow } from "@/lib/answeredModel";
import type { TryOnRow } from "@/lib/buildAttempts";

type Place = ReturnType<typeof tryOnMenuPosition>;
type Part = "worker" | "reviewer";

/** The chip's tooltip while the session works alone. */
export const TEAM_SOLO_TITLE =
  "Team: give this session workers that write the code and a reviewer that checks every change, led by this session's model";

/** A 404: an Iron Jarvis that has no session teams. */
function missing(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404;
}

/** A refusal's one sentence (the route's own `detail`). */
function refusal(e: unknown): string {
  return e instanceof Error && e.message ? e.message : "Iron Jarvis could not change the team.";
}

/** A member in the model menu's words (the catalog's label, else the
 *  daemon's label, else its name). */
export function memberWords(m: TeamMemberView, models: readonly CatalogRow[]): string {
  return workerModelName(m.provider, m.model, models) || m.label || m.name;
}

/** The members a view lists — the team's own, or (a view that keeps only the
 *  names) the names with nothing known about them. */
function membersOf(team: SessionTeam | null, names: TeamNames | null): { workers: TeamMemberView[]; reviewer: TeamMemberView | null } {
  if (team) return { workers: team.workers, reviewer: team.reviewer };
  const blank = (name: string): TeamMemberView => ({ name, provider: "", model: "", label: "", state: "unknown" });
  return {
    workers: (names?.workers ?? []).map(blank),
    reviewer: names?.reviewer ? blank(names.reviewer) : null,
  };
}

export function TeamControl({
  threadId,
  folder,
  noFolder,
  team,
  teamSetup,
  leadWords,
  models,
  localOnly,
  disabled,
  onView,
}: {
  threadId: string;
  /** The session's folder ("" for a session with no folder). */
  folder: string;
  noFolder: boolean;
  /** The view's team (with each member's state), or null. */
  team: SessionTeam | null;
  /** The view's setup `team` (names only) — read when the view keeps no team. */
  teamSetup: TeamNames | null;
  /** The team lead: the session's own model, in the model chip's words. */
  leadWords: string;
  models: readonly (TryOnRow & CatalogRow)[];
  localOnly: boolean;
  disabled?: boolean;
  /** The session view the daemon answered a change with. */
  onView: (v: BuildSessionView) => void;
}) {
  /** Does this Iron Jarvis have session teams? Unknown until its folder's
   *  team is read once; a 404 hides the control for good. */
  const [avail, setAvail] = useState<"unknown" | "yes" | "no">("unknown");
  const [folderTeam, setFolderTeam] = useState<TeamNames | null>(null);
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState<Place | null>(null);
  const [picking, setPicking] = useState<Part | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const chipRef = useRef<HTMLButtonElement | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const addRef = useRef<{ worker: HTMLButtonElement | null; reviewer: HTMLButtonElement | null }>({
    worker: null,
    reviewer: null,
  });
  const doneRef = useRef<HTMLButtonElement | null>(null);
  /** v1.403.0 review: the folder's one button (use it / stop using it). */
  const folderRef = useRef<HTMLButtonElement | null>(null);

  // Read once per folder: the folder's saved team, and whether teams exist.
  useEffect(() => {
    let alive = true;
    getFolderTeam(folder)
      .then((t) => {
        if (!alive) return;
        setAvail("yes");
        setFolderTeam(t);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        // Only a 404 is an Iron Jarvis without teams; another refusal (or no
        // answer) still has the control — its folder's team is just unknown.
        setAvail(missing(e) || !(e instanceof ApiError) ? "no" : "yes");
      });
    return () => {
      alive = false;
    };
  }, [folder]);

  const current: TeamNames | null = teamNamesOf(team) ?? teamSetup ?? null;
  const { workers, reviewer } = membersOf(team, team ? null : current);
  const count = teamCount(current);
  const words = teamChipWords(count);
  const title = current
    ? teamLineWords({
        lead: leadWords || team?.lead?.label || "",
        workers: workers.map((w) => memberWords(w, models)),
        reviewer: reviewer ? memberWords(reviewer, models) : "",
      })
    : TEAM_SOLO_TITLE;
  const groups = useMemo(() => tryOnGroups(models, localOnly), [models, localOnly]);

  const measure = useCallback(() => {
    const chip = chipRef.current;
    if (!chip) return;
    const r = chip.getBoundingClientRect();
    const de = document.documentElement;
    const next = tryOnMenuPosition(
      { left: r.left, top: r.top },
      { width: de.clientWidth || window.innerWidth, height: de.clientHeight || window.innerHeight },
    );
    setPlace((prev) =>
      prev &&
      prev.left === next.left &&
      prev.bottom === next.bottom &&
      prev.width === next.width &&
      prev.maxHeight === next.maxHeight
        ? prev
        : next,
    );
  }, []);

  useLayoutEffect(() => {
    if (open) measure();
  }, [open, measure]);

  // Each opening starts with the model list closed and no old refusal.
  const [openedFor, setOpenedFor] = useState(false);
  if (open !== openedFor) {
    setOpenedFor(open);
    if (open) {
      setPicking(null);
      setError("");
    }
  }

  // The focus goes into the panel once per opening (it is at the page's end).
  useEffect(() => {
    if (!open) return;
    boxRef.current
      ?.querySelector<HTMLElement>("button:not([disabled]), input:not([disabled])")
      ?.focus({ preventScroll: true });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node | null;
      if (t && (boxRef.current?.contains(t) || chipRef.current?.contains(t))) return;
      setOpen(false);
    };
    const onScroll = (e: Event) => {
      if (e.target instanceof Node && boxRef.current?.contains(e.target)) return;
      measure();
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open, measure]);

  const close = () => {
    setOpen(false);
    chipRef.current?.focus();
  };

  /** After a change took away the control that had the focus (a picked
   *  model, a removed member, "No team", the folder's button): to the control
   *  that takes its place, else Done — never out of the panel (review: "No
   *  team" and the folder's buttons left it on the page's body). Done once
   *  the change is DRAWN and the panel is no longer busy (review: a timer
   *  raced React's commit, and on a loaded machine found the old button,
   *  still disabled, and fell back to Done). */
  const focusNext = useRef<(() => HTMLButtonElement | null) | null>(null);
  // A change that ends before the busy state is ever drawn still moves the
  // focus: the asking itself is a change the effect sees.
  const [focusAsk, setFocusAsk] = useState(0);
  const refocusTo = (target: () => HTMLButtonElement | null) => {
    focusNext.current = target;
    setFocusAsk((n) => n + 1);
  };
  const refocus = (part: Part) => refocusTo(() => addRef.current[part]);
  useEffect(() => {
    if (busy || !focusNext.current) return;
    const target = focusNext.current;
    focusNext.current = null;
    const el = target();
    (el && !el.disabled && el.isConnected ? el : doneRef.current)?.focus({ preventScroll: true });
  }, [busy, focusAsk]);

  /** The model list closed (Escape, Cancel): its Add button stays where it
   *  was, so the focus goes back to it at once. */
  const stopPicking = () => {
    const part = picking;
    setPicking(null);
    if (!part) return;
    const add = addRef.current[part];
    (add && !add.disabled && add.isConnected ? add : doneRef.current)?.focus({ preventScroll: true });
  };

  const onPanelKey = (e: KeyboardEvent<HTMLDivElement>) => {
    // An IME composing a word owns its keys (its Escape drops the word).
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      if (picking) {
        stopPicking();
        return;
      }
      close();
      return;
    }
    if (e.key !== "Tab") return;
    // The panel is on the page's top layer — the end of the page in tab
    // order: Shift+Tab from its first control goes back to the chip, Tab from
    // its last moves on from the chip (the "Try on…" menu's rule).
    const items = Array.from(
      e.currentTarget.querySelectorAll<HTMLElement>("input:not([disabled]), button:not([disabled])"),
    );
    if (!items.length) return;
    const at = document.activeElement;
    if (e.shiftKey && at === items[0]) {
      e.preventDefault();
      chipRef.current?.focus();
    } else if (!e.shiftKey && at === items[items.length - 1]) {
      chipRef.current?.focus();
    }
  };

  /** Set the session's team; a 404 means this Iron Jarvis has none. */
  async function save(next: TeamNames | null) {
    const v = await patchSession(threadId, teamPatch(next));
    if (v) onView(v);
  }

  async function run(job: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await job();
    } catch (e) {
      if (missing(e)) {
        setOpen(false);
        setAvail("no");
        return;
      }
      setError(refusal(e));
    } finally {
      setBusy(false);
    }
  }

  const pick = (m: TryOnModel, part: Part) =>
    run(async () => {
      const sameModel = (x: TeamMemberView) => x.provider === m.provider && x.model === m.model;
      const label = m.label || m.model;
      if (workers.some(sameModel)) {
        setError(`${label} is already a worker.`);
        return;
      }
      if (reviewer && sameModel(reviewer)) {
        setError(`${label} is already the reviewer.`);
        return;
      }
      const made = await memberFromModel(m.provider, m.model, part);
      const names = current ?? { workers: [], reviewer: "" };
      if (names.workers.includes(made.name)) {
        setError(`${label} is already a worker.`);
        return;
      }
      // Review: a worker pick whose member is the reviewer too (a view that
      // keeps only the names cannot tell before the member is known).
      if (names.reviewer === made.name) {
        setError(`${label} is already the reviewer.`);
        return;
      }
      const next: TeamNames =
        part === "worker"
          ? { workers: [...names.workers, made.name].slice(0, MAX_WORKERS), reviewer: names.reviewer }
          : { workers: names.workers, reviewer: made.name };
      await save(next);
      setPicking(null);
      refocus(part);
    });

  const removeWorker = (name: string) =>
    run(async () => {
      const names = current;
      if (!names) return;
      const left = names.workers.filter((w) => w !== name);
      // No worker left: the session works alone (a reviewer checks workers).
      await save(left.length ? { workers: left, reviewer: names.reviewer } : null);
      refocus("worker");
    });

  const removeReviewer = () =>
    run(async () => {
      if (!current) return;
      await save({ workers: current.workers, reviewer: "" });
      refocus("reviewer");
    });

  const noTeam = () =>
    run(async () => {
      await save(null);
      refocus("worker");
    });

  const saveFolder = (names: TeamNames | null) =>
    run(async () => {
      setFolderTeam(await putFolderTeam(folder, names));
      refocusTo(() => folderRef.current);
    });

  if (avail !== "yes") return null;

  const memberRow = (m: TeamMemberView, part: Part) => {
    const w = memberWords(m, models);
    const warn = memberStateWarn(m.state);
    return (
      <li
        key={m.name}
        data-testid={part === "worker" ? "harness-team-worker" : "harness-team-reviewer"}
        data-name={m.name}
        data-state={m.state}
        title={[m.name, m.provider, m.model].filter(Boolean).join(" · ")}
        className="flex min-w-0 items-center gap-2 rounded-lg bg-white/[0.04] px-2 py-1 text-[12px]"
      >
        <span className="min-w-0 truncate text-zinc-200">{w}</span>
        <span
          data-testid="harness-team-state"
          className={`shrink-0 text-[11px] ${warn ? "text-tone-warn" : "text-zinc-500"}`}
        >
          {memberStateWords(m.state)}
        </span>
        <button
          type="button"
          data-testid={part === "worker" ? "harness-team-remove-worker" : "harness-team-remove-reviewer"}
          aria-label={`Remove ${w} from the team`}
          title={`Remove ${w} from the team`}
          disabled={busy || disabled}
          onClick={() => void (part === "worker" ? removeWorker(m.name) : removeReviewer())}
          className="ml-auto inline-grid h-5 w-5 shrink-0 place-items-center rounded-full text-zinc-500 hover:bg-white/[0.1] hover:text-zinc-200 disabled:opacity-40"
        >
          <X size={11} aria-hidden />
        </button>
      </li>
    );
  };

  const addButton = (part: Part, label: string, can: boolean) => (
    <button
      type="button"
      ref={(el) => {
        addRef.current[part] = el;
      }}
      data-testid={part === "worker" ? "harness-team-add-worker" : "harness-team-add-reviewer"}
      aria-expanded={picking === part}
      disabled={busy || disabled || !can}
      onClick={() => setPicking((p) => (p === part ? null : part))}
      className="mt-1 rounded-full px-2.5 py-1 text-[12px] text-accent-soft transition-colors hover:bg-accent/10 disabled:cursor-not-allowed disabled:opacity-40"
    >
      {label}
    </button>
  );

  const heading = (text: string, note: string) => (
    <p className="flex items-baseline gap-1.5 text-[12px] font-medium text-zinc-300">
      {text}
      <span className="text-[11px] font-normal text-zinc-500">{note}</span>
    </p>
  );

  const folderDefault = (() => {
    if (noFolder || !folder) return null;
    const inUse = !!current && sameTeam(current, folderTeam);
    if (inUse || (!current && folderTeam)) {
      return (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-white/[0.06] pt-2">
          <p data-testid="harness-team-folder-note" className="text-[12px] text-zinc-400">
            {inUse ? "New sessions in this folder start with this team." : "New sessions in this folder start with a team."}
          </p>
          <button
            type="button"
            ref={folderRef}
            data-testid="harness-team-folder-clear"
            disabled={busy || disabled}
            onClick={() => void saveFolder(null)}
            className="rounded-full px-2 py-0.5 text-[12px] text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-200 disabled:opacity-40"
          >
            Stop using it for new sessions
          </button>
        </div>
      );
    }
    if (!current) return null;
    return (
      <div className="border-t border-white/[0.06] pt-2">
        <button
          type="button"
          ref={folderRef}
          data-testid="harness-team-folder-default"
          disabled={busy || disabled}
          onClick={() => void saveFolder(current)}
          className="rounded-full bg-white/[0.06] px-3 py-1 text-left text-[12px] text-zinc-200 hover:bg-white/[0.1] disabled:opacity-40"
        >
          Use this team for new sessions in this folder
        </button>
      </div>
    );
  })();

  const panel = open ? (
    <div
      ref={boxRef}
      data-testid="harness-team-panel"
      role="group"
      aria-label="This session's team"
      aria-busy={busy}
      onKeyDown={onPanelKey}
      style={{
        position: "fixed",
        left: place?.left ?? 16,
        bottom: place?.bottom ?? 96,
        width: place?.width ?? 320,
        maxHeight: place?.maxHeight,
      }}
      className="z-[60] flex flex-col rounded-xl border border-white/10 bg-zinc-900 p-2 shadow-xl shadow-black/40"
    >
      <div className="grid min-h-0 gap-3 overflow-y-auto overscroll-contain px-1 pb-1">
        <section data-testid="harness-team-lead">
          {heading(TEAM_LEAD_WORDS, "this session's own model")}
          <p data-testid="harness-team-lead-model" className="mt-0.5 truncate text-[13px] text-zinc-200">
            {leadWords || team?.lead?.label || "This session's model"}
          </p>
          <p className="text-[12px] leading-5 text-zinc-500">{LEAD_DUTY}</p>
        </section>

        <section data-testid="harness-team-workers">
          {heading(WORKERS_WORDS, `up to ${MAX_WORKERS}`)}
          <p className="text-[12px] leading-5 text-zinc-500">{WORKER_DUTY}</p>
          {workers.length ? <ul className="mt-1 grid gap-1">{workers.map((w) => memberRow(w, "worker"))}</ul> : null}
          {workers.length < MAX_WORKERS ? addButton("worker", "Add a worker", true) : null}
          {picking === "worker" ? (
            <TeamModelPicker
              part="worker"
              groups={groups}
              localOnly={localOnly}
              busy={busy}
              onPick={(m) => void pick(m, "worker")}
              onCancel={stopPicking}
            />
          ) : null}
        </section>

        <section data-testid="harness-team-reviewer-part">
          {heading(REVIEWER_WORDS, "optional")}
          <p className="text-[12px] leading-5 text-zinc-500">{REVIEWER_DUTY}</p>
          {reviewer ? <ul className="mt-1 grid gap-1">{memberRow(reviewer, "reviewer")}</ul> : null}
          {!reviewer ? addButton("reviewer", "Add a reviewer", workers.length > 0) : null}
          {!reviewer && !workers.length ? (
            <p data-testid="harness-team-reviewer-needs" className="text-[11px] text-zinc-500">
              Add a worker first: the reviewer checks the workers&apos; changes.
            </p>
          ) : null}
          {picking === "reviewer" ? (
            <TeamModelPicker
              part="reviewer"
              groups={groups}
              localOnly={localOnly}
              busy={busy}
              onPick={(m) => void pick(m, "reviewer")}
              onCancel={stopPicking}
            />
          ) : null}
        </section>

        {error ? (
          <p data-testid="harness-team-error" role="alert" className="text-[12px] leading-5 text-tone-warn">
            {error}
          </p>
        ) : null}

        {folderDefault}
      </div>
      <div className="flex shrink-0 items-center justify-between gap-1 pt-1">
        {current ? (
          <button
            type="button"
            data-testid="harness-team-none"
            disabled={busy || disabled}
            onClick={() => void noTeam()}
            title="Work alone again: this session's model does every piece itself"
            className="rounded-full px-2.5 py-1 text-[12px] text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-200 disabled:opacity-40"
          >
            No team
          </button>
        ) : (
          <span />
        )}
        <button
          type="button"
          ref={doneRef}
          data-testid="harness-team-done"
          onClick={close}
          className="rounded-full bg-white/[0.08] px-3 py-1 text-[12px] text-zinc-200 hover:bg-white/[0.14]"
        >
          Done
        </button>
      </div>
    </div>
  ) : null;

  return (
    <div className="flex items-center">
      <button
        ref={chipRef}
        type="button"
        data-testid="harness-team"
        data-count={count}
        aria-expanded={open}
        aria-haspopup="true"
        aria-label={words}
        title={title}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={`${composerChipClass(count > 0)}${count > 0 ? "" : ` ${fitIconChip("team")}`}`}
      >
        <Users size={14} aria-hidden className="shrink-0" />
        <span className={fitWords("team")}>{words}</span>
        {/* With a team, a people icon alone would tell it by hue: the chip
            keeps the count, as the "Try on…" chip does. */}
        {count > 0 ? <span className={`hidden ${fitShows("team")}`}>{count}</span> : null}
      </button>
      {panel && typeof document !== "undefined" ? createPortal(panel, document.body) : null}
    </div>
  );
}

/** The provider-grouped model list a worker or the reviewer is picked from
 *  (the "Try on…" list's groups, one model at a time): a find box, then one
 *  row per provider opening its models under it. */
function TeamModelPicker({
  part,
  groups,
  localOnly,
  busy,
  onPick,
  onCancel,
}: {
  part: Part;
  groups: readonly TryOnGroup[];
  localOnly: boolean;
  busy: boolean;
  onPick: (m: TryOnModel) => void;
  onCancel: () => void;
}) {
  const [filter, setFilter] = useState("");
  const [sub, setSub] = useState<string | null>(null);
  const filterRef = useRef<HTMLInputElement | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const found = useMemo(() => matchTryOn(groups, filter, MODEL_FILTER_MAX), [groups, filter]);

  // The focus goes in once, when the list opens: the find box on a wide
  // window, the first provider on a phone (review: a focused box there pops
  // the keyboard over the list — the "Try on…" list's rule).
  useLayoutEffect(() => {
    if (isPhone()) rootRef.current?.querySelector<HTMLElement>('[data-testid="harness-team-group"]')?.focus({ preventScroll: true });
    else filterRef.current?.focus({ preventScroll: true });
  }, []);

  const modelButton = (m: TryOnModel, where?: string) => {
    const badge = KIND_BADGE[m.place] ?? KIND_BADGE.api;
    return (
      <button
        key={`${m.provider}::${m.model}`}
        type="button"
        data-testid="harness-team-model"
        data-provider={m.provider}
        data-model={m.model}
        disabled={busy}
        onClick={() => onPick(m)}
        title={m.title}
        className="flex w-full min-w-0 items-center gap-2 rounded-md px-2 py-1 text-left text-[12px] text-zinc-200 hover:bg-white/[0.06] disabled:opacity-40"
      >
        <span className="min-w-0 truncate">{m.label}</span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          {where ? <span className="max-w-[7rem] truncate text-[10px] text-zinc-500">{where}</span> : null}
          <span data-testid="harness-team-place" data-place={m.place} title={badge.title} className={`text-[10px] ${badge.cls}`}>
            {placeWord(m.place)}
          </span>
        </span>
      </button>
    );
  };

  const typed = filter.trim();
  return (
    <div
      ref={rootRef}
      data-testid="harness-team-picker"
      role="group"
      aria-label={part === "worker" ? "Pick a model for a worker" : "Pick a model for the reviewer"}
      className="mt-1 rounded-lg border border-white/[0.06] bg-black/10 p-1"
    >
      {groups.length ? (
        <input
          ref={filterRef}
          data-testid="harness-team-filter"
          value={filter}
          onChange={(e) => {
            setFilter(e.target.value);
            setSub(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.preventDefault();
          }}
          placeholder="Type to find a model…"
          aria-label="Find a model"
          className="mb-1 w-full rounded-lg border border-white/10 bg-black/20 px-2 py-1 text-[12px] text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-accent/40"
        />
      ) : null}
      <div className="max-h-[240px] overflow-y-auto overscroll-contain">
        {!groups.length ? (
          <p data-testid="harness-team-picker-empty" className="px-1 py-1 text-[12px] text-zinc-500">
            {localOnly ? "No model on this PC or your network is connected." : "No model is connected."}
          </p>
        ) : typed ? (
          found.hits.length ? (
            <>
              {found.hits.map(({ group, model }) => modelButton(model, group.label))}
              {found.more > 0 ? (
                <p className="px-2 py-1 text-[11px] text-zinc-500">{found.more} more — type more to narrow it down.</p>
              ) : null}
            </>
          ) : (
            <p className="px-2 py-1.5 text-[12px] text-zinc-500">No model matches.</p>
          )
        ) : (
          groups.map((g) => {
            const expanded = sub === g.id;
            return (
              <div key={g.id}>
                <button
                  type="button"
                  data-testid="harness-team-group"
                  data-group={g.id}
                  aria-expanded={expanded}
                  onClick={() => setSub(expanded ? null : g.id)}
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[12px] text-zinc-300 hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
                >
                  <span className="min-w-0 truncate">{g.label}</span>
                  <span className="ml-auto shrink-0 text-[10px] text-zinc-600">{g.models.length}</span>
                  <ChevronRight
                    size={12}
                    aria-hidden
                    className={`shrink-0 text-zinc-500 transition-transform ${expanded ? "rotate-90" : ""}`}
                  />
                </button>
                {expanded ? (
                  <div role="group" aria-label={`${g.label} models`} className="pl-2">
                    {g.models.map((m) => modelButton(m))}
                  </div>
                ) : null}
              </div>
            );
          })
        )}
      </div>
      <div className="flex justify-end pt-1">
        <button
          type="button"
          data-testid="harness-team-picker-cancel"
          onClick={onCancel}
          className="rounded-full px-2.5 py-0.5 text-[12px] text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-200"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

/** A team reply's quiet line, and — in the warn tone, never folded away —
 *  every time the lead did a piece itself. Nothing for a reply of a session
 *  that worked alone. */
export function TeamReplyLine({ raw }: { raw: unknown }) {
  const t = decodeReplyTeam(raw);
  if (!t) return null;
  const line = teamLineWords(t);
  const pieces = piecesWords(t.pieces);
  return (
    <div data-testid="harness-team-line" className="mb-2 grid gap-0.5">
      <p
        data-testid="harness-team-members"
        title={[line, pieces].filter(Boolean).join("\n")}
        className="flex min-w-0 items-start gap-1.5 break-words text-[12px] leading-5 text-zinc-500"
      >
        <Users size={12} aria-hidden className="mt-1 shrink-0" />
        <span className="min-w-0">{line}</span>
      </p>
      {leadDidLines(t).map((words, i) => (
        <p
          key={i}
          data-testid="harness-team-lead-did"
          className="flex min-w-0 items-start gap-1.5 break-words text-[12px] leading-5 text-tone-warn"
        >
          <TriangleAlert size={12} aria-hidden className="mt-1 shrink-0" />
          <span className="min-w-0">{words}</span>
        </p>
      ))}
    </div>
  );
}
