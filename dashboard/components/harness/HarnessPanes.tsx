"use client";

/**
 * The IJ harness session's RIGHT PANES (v1.369.0, plan Phase 6): plan,
 * terminal, changes, browser, files — tabs over one box, each the app's OWN
 * piece:
 *
 *  - plan: `PlanChecklist`, fed by the running turn's `plan_update` calls,
 *    else the newest one a reply kept, else the plan the session keeps (one
 *    the user approved in plan mode). v1.384.0: the RUNNING turn's plan is
 *    here only from `xl` (beside the conversation; below it, under the
 *    reply), and a plan that appears behind another tab marks this one;
 *  - terminal: a NORMAL Build terminal in the session's folder, made through
 *    the terminals API and drawn by the page's own TerminalPane (the pane
 *    host, v1.243.0) — it is in the Terminals view's rail too;
 *  - changes: ONE scrolling review (`ChangesReview`, v1.405.0) of
 *    `POST /chat/changes` with the session's own `build:<id>` journal: every
 *    file's diff inline, Undo, Reviewed marks, Open in VS Code / Cursor;
 *  - browser: a preview of a page on this PC or the user's network, and the
 *    paired browser named (BrowserPreview);
 *  - files: `FilesPanel` (calm) on the session's folder;
 *  - timeline (v1.390.0): the reply on one time axis — the model you talk
 *    to, each step, each helper with its model (HarnessTimeline). Mounted
 *    only on its tab, so the running reply is read only while it is shown;
 *  - session (v1.404.0): one look at the session — where it works, what
 *    answers it, how full the conversation is, the last attachment, what it
 *    changed and what runs in the background (HarnessSessionRail). Mounted
 *    only on its tab, so its reads run only while it is shown.
 *
 * The terminal tab, once opened, stays MOUNTED and is hidden with
 * `visibility` (never `display:none`, never an unmount — the Build terminal
 * rules); behind another tab its terminal parks in the host's lot.
 */

import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import {
  ChartGantt,
  FileDiff,
  FolderOpen,
  Globe,
  ListChecks,
  Loader2,
  Plus,
  RefreshCw,
  SquareMenu,
  SquareTerminal,
  type LucideIcon,
} from "lucide-react";
import { PlanChecklist } from "@/components/chat/PlanChecklist";
import { fetchTurnChangeSet, type ChangeUndoState, type FileChange } from "@/components/chat/ChangedFiles";
import { ChangesReview } from "@/components/harness/ChangesReview";
import { FilesPanel } from "@/components/terminal/FilesPanel";
import { BrowserPreview } from "@/components/harness/BrowserPreview";
import { BranchView } from "@/components/harness/HarnessBranch";
import { HarnessTimeline, type LiveTimelineHandle } from "@/components/harness/HarnessTimeline";
import { SessionRail } from "@/components/harness/HarnessSessionRail";
import type { HelperView } from "@/lib/buildHelpers";
import type { CatalogRow } from "@/lib/answeredModel";
import type { BuildAccount } from "@/lib/buildAccounts";
import { ConfirmButton } from "@/components/ui";
import { Markdown } from "@/components/Markdown";
import type { PlanView } from "@/lib/planChecklist";
import { replyPlan } from "@/lib/buildContext";
import {
  addressesIn,
  planFromMessages,
  planFromSession,
  readSessionTerminal,
  writeSessionTerminal,
  type BuildMessage,
  type BuildSessionView,
} from "@/lib/buildSessions";
import type { TerminalInfo } from "@/lib/types";

/** What the page lends the panes for the session's terminal. */
export interface SessionTerminalApi {
  /** The live terminal with this id, or null (closed, or never opened). */
  find: (id: string) => TerminalInfo | null;
  /** Open a NEW Build terminal in `cwd` (the page's own add path). */
  open: (cwd: string) => Promise<TerminalInfo | null>;
  /** Draw the terminal with the page's own TerminalPane. `onClose` is the
   *  pane's own close (×): the tab asks first, like the rail's dialog. */
  render: (info: TerminalInfo, opts: { parked: boolean; onClose: () => void }) => ReactNode;
  /** End the terminal (the page's own close path). */
  close: (id: string) => void;
}

export type PaneTab = "plan" | "timeline" | "terminal" | "changes" | "browser" | "files" | "session";

/**
 * v1.401.2 (the user: the six words "seem a bit overwhelming … a small icon
 * instead so it a cleaner look and if a mouse is hovering over it then the
 * word describing it briefly pops up or stays up if the mouse is held there
 * steady, but if no mouse hovering then no word"): each tab is an ICON; its
 * word is its accessible name and a small tip that shows while the pointer
 * rests on it (after a short pause, so a passing pointer shows nothing) or
 * while it has keyboard focus — never otherwise.
 */
export const PANE_TABS: readonly { value: PaneTab; label: string; icon: LucideIcon }[] = [
  { value: "plan", label: "Plan", icon: ListChecks },
  // v1.390.0 (the long-jobs plan's Phase 1, decision 9): beside Plan.
  { value: "timeline", label: "Timeline", icon: ChartGantt },
  { value: "terminal", label: "Terminal", icon: SquareTerminal },
  { value: "changes", label: "Changes", icon: FileDiff },
  { value: "browser", label: "Browser", icon: Globe },
  { value: "files", label: "Files", icon: FolderOpen },
  // v1.404.0 (the user: "add a sessions rail … next to the other icons"):
  // the session at a glance, the last attachment included.
  { value: "session", label: "Session", icon: SquareMenu },
];

const TAB_KEY = "ij.build.pane";

/** v1.399.0 (night shift N2): open the NEXT session shown on this pane tab —
 *  Last night's "Changes" opens the task's session on its Changes pane, whose
 *  Branch view is a live branch's first view (the branch's whole diff). */
export function choosePaneNext(tab: PaneTab): void {
  try {
    window.localStorage.setItem(TAB_KEY, tab);
  } catch {
    /* the session opens on its usual tab */
  }
}

function readTab(): PaneTab {
  try {
    const v = window.localStorage.getItem(TAB_KEY);
    return PANE_TABS.some((t) => t.value === v) ? (v as PaneTab) : "plan";
  } catch {
    return "plan";
  }
}

/**
 * The newest plan a reply KEPT (harness gaps, fix 5), newest reply first:
 * its saved `plan` (the line's own checklist since v1.374.0) read through
 * `replyPlan` — the SAME reading the reply's own "Plan · N of M done" uses,
 * so the two can never disagree — else, for a line an older daemon wrote,
 * the plan rebuilt from its `plan_update` step's detail.
 *
 * THE BUG THIS ENDS (the user's screenshot, v1.388.1): the pane read ONLY
 * the step detail, whose input is cut at 300 characters (chat_turn.
 * step_detail) — any plan longer than that failed to parse — and a long
 * reply's steps after its first 60 keep no detail at all. So the Plan tab
 * said "No plan yet" beside a reply that said "Plan · 0 of 11 done".
 */
export function keptPlan(messages: readonly BuildSessionView["messages"][number][]): PlanView | null {
  return newestKept(messages)?.plan ?? null;
}

/** `keptPlan`, with WHEN the reply that kept it was written (its `at`). */
function newestKept(
  messages: readonly BuildSessionView["messages"][number][],
): { plan: PlanView; at: string } | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i];
    if (m.role !== "assistant") continue;
    const at = typeof m.at === "string" ? m.at : "";
    const saved = replyPlan(m.plan, !!m.error);
    if (saved) return { plan: saved, at };
    const fromStep = planFromMessages([m]);
    if (fromStep) return { plan: fromStep, at };
  }
  return null;
}

/** Milliseconds of a daemon stamp (ISO; one with no zone is UTC, the
 *  daemon's clock — lib/format `normalizeIso`'s rule), else null. */
function stampMs(iso: string | null | undefined): number | null {
  let s = typeof iso === "string" ? iso.trim() : "";
  if (!s) return null;
  if (/[T ]\d{2}:\d{2}/.test(s) && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) s = `${s.replace(" ", "T")}Z`;
  const t = Date.parse(s);
  return Number.isNaN(t) ? null : t;
}

/**
 * The plan to show: the running turn's, else the NEWEST of the plan a reply
 * kept (`keptPlan`) and the session's approved one.
 *
 * Harness gaps review: the first cut took ANY reply's plan before the approved
 * one, so a plan approved after an older reply kept its own (plan mode, the
 * model writes a new plan, Allow) never showed — the tab kept the old reply's
 * steps. A reply's plan wins only when that reply was written at or after the
 * approval: the approving turn's own reply (it is written when the turn ends,
 * after the approval landed mid-turn) or a later one — so the pane still says
 * what the reply beside it says. A stamp that cannot be read keeps the reply's
 * plan, as before.
 */
export function panePlan(view: BuildSessionView, livePlan: PlanView | null): PlanView | null {
  if (livePlan) return livePlan;
  const kept = newestKept(view.messages);
  const approved = planFromSession(view.plan);
  if (kept && approved) {
    const approvedAt = stampMs(view.plan?.approvedAt);
    const keptAt = stampMs(kept.at);
    if (approvedAt !== null && keptAt !== null && approvedAt > keptAt) return approved;
  }
  return kept?.plan ?? approved;
}

/** Below `xl` (the panes stacked under the conversation) a RUNNING reply's
 *  plan is drawn under the reply; this pane says where it is (v1.384.0). */
export const LIVE_PLAN_ELSEWHERE = "While it works, the plan is under the reply.";

function PlanPane({ view, livePlan }: { view: BuildSessionView; livePlan: PlanView | null }) {
  const plan = panePlan(view, livePlan);
  return (
    <div data-testid="pane-plan" className="space-y-3">
      {livePlan ? (
        // v1.384.0 — ONE PLACE for a running reply's plan: here from `xl`
        // (this pane sits beside the conversation), under the reply below it
        // (`HarnessPlan.LivePlan`). CSS only.
        <>
          <div data-testid="pane-plan-live" className="hidden xl:block">
            <PlanChecklist plan={livePlan} />
          </div>
          <p data-testid="pane-plan-elsewhere" className="text-[13px] text-zinc-400 xl:hidden">
            {LIVE_PLAN_ELSEWHERE}
          </p>
        </>
      ) : plan ? (
        <PlanChecklist plan={plan} />
      ) : (
        <p className="text-[13px] text-zinc-400">
          No plan yet. While the session works it keeps its step list here; in plan mode it writes one for
          you to approve first.
        </p>
      )}
      {view.plan?.text ? (
        <details className="group">
          <summary className="cursor-pointer text-[12px] text-zinc-500 hover:text-zinc-300">
            The plan you approved
          </summary>
          <div className="mt-2 text-[13px] leading-relaxed text-zinc-300">
            <Markdown content={view.plan.text} />
          </div>
        </details>
      ) : null}
    </div>
  );
}

function SessionTerminal({
  view,
  api,
  visible,
}: {
  view: BuildSessionView;
  api: SessionTerminalApi;
  visible: boolean;
}) {
  const [termId, setTermId] = useState<string | null>(() => readSessionTerminal(view.id));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The pane's × asks first (the rail's "Close this terminal?"): a live
  // shell is not closed by one press.
  const [closing, setClosing] = useState(false);
  const info = termId ? api.find(termId) : null;

  async function open() {
    setBusy(true);
    setError(null);
    try {
      const made = await api.open(view.folder);
      if (made) {
        writeSessionTerminal(view.id, made.id);
        setTermId(made.id);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!info) {
    return (
      <div data-testid="pane-terminal-empty" className="space-y-2">
        <p className="text-[13px] text-zinc-400">
          {view.noFolder && !view.folder
            ? "This session's folder is made with your first message; a terminal can open there after that."
            : `A terminal in ${view.noFolder ? "this session's own folder" : view.folderName}, beside the session. It is a normal Build terminal: it shows in Build too.`}
        </p>
        <button
          type="button"
          data-testid="pane-terminal-open"
          onClick={() => void open()}
          disabled={busy || !view.folder}
          className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[13px] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:opacity-50"
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
          Open a terminal here
        </button>
        {error ? (
          <p role="alert" className="text-[12px] text-tone-danger">
            {error}
          </p>
        ) : null}
      </div>
    );
  }
  return (
    <div data-testid="pane-terminal" className="flex h-full min-h-[320px] flex-col gap-2">
      {closing ? (
        <div data-testid="pane-terminal-close" className="flex flex-wrap items-center gap-2 px-1 text-[13px] text-zinc-300">
          <span className="min-w-0 flex-1 break-all">Close this terminal? It ends the live shell in {info.cwd}.</span>
          <ConfirmButton
            onConfirm={() => {
              setClosing(false);
              api.close(info.id);
            }}
            label="Close terminal"
            confirmLabel="Confirm close"
            title="End this shell session"
          />
          <button
            type="button"
            onClick={() => setClosing(false)}
            className="rounded-md px-2 py-1 text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
          >
            Cancel
          </button>
        </div>
      ) : null}
      <div className="min-h-0 flex-1">{api.render(info, { parked: !visible, onClose: () => setClosing(true) })}</div>
    </div>
  );
}

/** v1.375.0: a session on its own branch shows two views of its changes —
 *  what this session changed (the undo journal, with Undo) and the whole
 *  BRANCH against its base, committed and not, with its buttons. */
function ChangesScope({
  scope,
  onScope,
}: {
  scope: "session" | "branch";
  onScope: (s: "session" | "branch") => void;
}) {
  const opt = (value: "session" | "branch", label: string) => (
    <button
      type="button"
      data-testid={`changes-scope-${value}`}
      aria-pressed={scope === value}
      onClick={() => onScope(value)}
      className={`rounded-md px-1.5 py-0.5 text-[12px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 ${
        scope === value ? "font-medium text-zinc-100" : "text-zinc-500 hover:text-zinc-200"
      }`}
    >
      {label}
    </button>
  );
  return (
    <div data-testid="changes-scope" className="flex items-center gap-1">
      {opt("session", "This session")}
      {opt("branch", "Branch")}
    </div>
  );
}

function ChangesPane({
  view,
  refreshKey,
  undoFor,
  onUndo,
  onSessionChanged,
  onSessionGone,
  initialScope,
}: {
  view: BuildSessionView;
  refreshKey: number;
  /** v1.404.0: the Session tab's "See changes" opens "This session" — the
   *  count it showed — even on a live branch. */
  initialScope?: "session";
  undoFor: (path: string) => ChangeUndoState | null;
  onUndo: (actionId: string, path: string) => Promise<void>;
  onSessionChanged?: () => void;
  onSessionGone?: () => void;
}) {
  // v1.378.0 (part 2, Phase 7, the walkthrough): a session on its own branch
  // opens on the Branch view — the branch IS that session's work, and its
  // Commit / Push / Create PR buttons are there (the walkthrough pressed
  // Changes, then Branch, every time). "This session" is one press away.
  // (Review: only while the branch is live work. A removed worktree's Branch
  // view is one sentence and a merged branch's diff against its base is
  // empty, so those keep the old first view: what the session changed.)
  const [scope, setScope] = useState<"session" | "branch">(() =>
    initialScope === "session" ? "session" : view.git && !view.git.removed && !view.git.merged ? "branch" : "session",
  );
  if (view.git && scope === "branch") {
    return (
      <div data-testid="pane-changes" className="space-y-2">
        <ChangesScope scope={scope} onScope={setScope} />
        <BranchView
          sessionId={view.id}
          git={view.git}
          refreshKey={refreshKey}
          onChanged={onSessionChanged}
          onArchived={onSessionGone}
        />
      </div>
    );
  }
  return (
    <SessionChanges
      view={view}
      refreshKey={refreshKey}
      undoFor={undoFor}
      onUndo={onUndo}
      scopeBar={view.git ? <ChangesScope scope={scope} onScope={setScope} /> : null}
    />
  );
}

function SessionChanges({
  view,
  refreshKey,
  undoFor,
  onUndo,
  scopeBar,
}: {
  view: BuildSessionView;
  refreshKey: number;
  undoFor: (path: string) => ChangeUndoState | null;
  onUndo: (actionId: string, path: string) => Promise<void>;
  scopeBar: ReactNode;
}) {
  const [changes, setChanges] = useState<FileChange[] | null>(null);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [round, setRound] = useState(0);
  const since = view.messages.find((m) => typeof m.at === "string")?.at ?? null;

  useEffect(() => {
    let live = true;
    if (!since) {
      setChanges([]);
      return;
    }
    fetchTurnChangeSet({ since, sessionId: view.sessionId })
      .then((set) => {
        if (!live) return;
        setChanges(set.changes);
        setMore(set.more);
        setError(null);
      })
      .catch((e) => {
        if (live) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      live = false;
    };
  }, [since, view.sessionId, refreshKey, round]);

  return (
    <div data-testid="pane-changes" className="space-y-2">
      {scopeBar}
      <div className="flex items-center gap-2">
        <p className="min-w-0 flex-1 text-[12px] text-zinc-500">What this session changed on disk.</p>
        <button
          type="button"
          aria-label="Read the changes again"
          title="Read the changes again"
          onClick={() => setRound((n) => n + 1)}
          className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
        >
          <RefreshCw size={13} />
        </button>
      </div>
      {error ? (
        <p role="alert" className="text-[12px] text-tone-danger">
          {error}
        </p>
      ) : changes === null ? (
        <p className="flex items-center gap-1.5 text-[12px] text-zinc-500">
          <Loader2 size={12} className="animate-spin" /> Reading the changes…
        </p>
      ) : changes.length === 0 ? (
        <p data-testid="pane-changes-none" className="text-[13px] text-zinc-400">
          No files changed yet.
        </p>
      ) : (
        // v1.405.0: every file's diff inline in one scroll (no press per
        // file, no dialog), Undo through the SAME handler as before.
        <ChangesReview
          changes={changes}
          sessionId={view.id}
          more={more}
          hint
          undoFor={undoFor}
          onUndo={onUndo}
          onUndone={() => setRound((n) => n + 1)}
        />
      )}
    </div>
  );
}

export function HarnessPanes({
  view,
  livePlan,
  terminal,
  refreshKey,
  undoFor,
  onUndo,
  onSessionChanged,
  onSessionGone,
  pageSteps = 0,
  timeline = null,
  models,
  account = null,
  messages,
  modelWords = "",
  helpers = [],
  busy = false,
}: {
  view: BuildSessionView;
  livePlan: PlanView | null;
  /** v1.404.0 (the Session tab): the conversation as shown — the question
   *  being sent included, so its attachment is the last one at once. */
  messages?: readonly BuildMessage[];
  /** v1.404.0: the model as the box's chip names it. */
  modelWords?: string;
  /** v1.404.0: the running reply's helper agents. */
  helpers?: readonly HelperView[];
  /** v1.404.0: a reply is running. */
  busy?: boolean;
  /** v1.390.0: the running reply's Timeline (HarnessSession folds it). */
  timeline?: LiveTimelineHandle | null;
  /** v1.390.0: the model list, for the Timeline's model names. */
  models?: readonly CatalogRow[];
  /** v1.393.0: the session's account (the cached snapshot's row), whose
   *  current usage window the Timeline's header says. */
  account?: BuildAccount | null;
  terminal: SessionTerminalApi;
  /** Bumped when a turn ends, so the changes are read again. */
  refreshKey: number;
  /** v1.376.0: the running turn's finished steps in the agent's own browser
   *  (lib/agentBrowser finishedPageSteps) — the Browser pane reads what the
   *  agent saw again each time it grows. */
  pageSteps?: number;
  undoFor: (path: string) => ChangeUndoState | null;
  onUndo: (actionId: string, path: string) => Promise<void>;
  /** v1.375.0: the Branch view changed the session (merged, pushed). */
  onSessionChanged?: () => void;
  /** v1.375.0: the Branch view removed the worktree (the session is archived). */
  onSessionGone?: () => void;
}) {
  const [tab, setTab] = useState<PaneTab>("plan");
  const [terminalOpened, setTerminalOpened] = useState(false);
  const baseId = useId();
  useEffect(() => {
    const saved = readTab();
    setTab(saved);
    if (saved === "terminal") setTerminalOpened(true);
  }, []);
  // v1.384.0: a running reply's plan APPEARS while the user is on another
  // tab — the tab stays (never switched for them); the Plan tab is MARKED
  // (a dot, with words for a screen reader) until they open it.
  const [planNew, setPlanNew] = useState(false);
  const hadPlan = useRef(!!livePlan);
  const hasPlan = !!livePlan;
  useEffect(() => {
    if (hasPlan && !hadPlan.current && tab !== "plan") setPlanNew(true);
    hadPlan.current = hasPlan;
  }, [hasPlan, tab]);
  // v1.404.0: the Changes view the Session tab's "See changes" asks for
  // (null: the Changes tab's own first view).
  const [changesScope, setChangesScope] = useState<"session" | null>(null);
  const choose = useCallback((t: PaneTab) => {
    setTab(t);
    if (t === "terminal") setTerminalOpened(true);
    if (t === "plan") setPlanNew(false);
    try {
      window.localStorage.setItem(TAB_KEY, t);
    } catch {
      /* the choice lasts this visit */
    }
  }, []);
  const seen = addressesIn(view.messages);

  return (
    <div data-testid="harness-panes" className="flex h-full min-h-0 flex-col gap-2">
      <div
        role="tablist"
        aria-label="Session views"
        data-testid="harness-pane-tabs"
        className="flex shrink-0 flex-wrap items-center gap-x-1 gap-y-1 px-1"
      >
        {PANE_TABS.map((t) => {
          const selected = tab === t.value;
          const Icon = t.icon;
          return (
            <button
              key={t.value}
              type="button"
              role="tab"
              id={`${baseId}-tab-${t.value}`}
              data-testid={`harness-pane-tab-${t.value}`}
              aria-selected={selected}
              aria-controls={`${baseId}-panel`}
              tabIndex={selected ? 0 : -1}
              onClick={() => {
                setChangesScope(null);
                choose(t.value);
              }}
              onKeyDown={(e) => {
                if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
                e.preventDefault();
                const i = PANE_TABS.findIndex((x) => x.value === t.value);
                const next = PANE_TABS[(i + (e.key === "ArrowRight" ? 1 : -1) + PANE_TABS.length) % PANE_TABS.length];
                setChangesScope(null);
                choose(next.value);
                document.getElementById(`${baseId}-tab-${next.value}`)?.focus();
              }}
              className={`group relative inline-grid h-8 w-8 place-items-center rounded-md transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 ${
                selected ? "bg-white/[0.08] text-zinc-100" : "text-zinc-500 hover:bg-white/[0.04] hover:text-zinc-200"
              }`}
            >
              <Icon size={16} aria-hidden strokeWidth={selected ? 2.25 : 1.75} />
              {/* The word, SHOWN only while the pointer rests here (after
                  300 ms, so a passing pointer shows nothing) or the tab has
                  keyboard focus. Faded, never removed: it is the tab's name
                  for a screen reader (with "(a new plan)" below), so no
                  aria-label repeats it. */}
              <span
                data-testid={`harness-pane-tip-${t.value}`}
                className="pointer-events-none absolute left-1/2 top-full z-30 mt-1 -translate-x-1/2 whitespace-nowrap rounded-md border border-white/10 bg-ink-900 px-2 py-0.5 text-[12px] text-zinc-200 opacity-0 shadow-lg shadow-black/30 transition-opacity duration-150 group-hover:opacity-100 group-hover:delay-300 group-focus-visible:opacity-100"
              >
                {t.label}
              </span>
              {t.value === "plan" && planNew && !selected ? (
                // v1.384.0: from xl only — below it the plan is under the
                // reply, where the user already is.
                <span data-testid="harness-plan-new" className="hidden xl:inline">
                  <span aria-hidden className="absolute right-1 top-1 inline-block h-1.5 w-1.5 rounded-full bg-accent" />
                  <span className="sr-only"> (a new plan)</span>
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
      <div
        id={`${baseId}-panel`}
        role="tabpanel"
        aria-labelledby={`${baseId}-tab-${tab}`}
        className="relative min-h-0 flex-1 overflow-y-auto px-1"
      >
        {tab === "plan" ? <PlanPane view={view} livePlan={livePlan} /> : null}
        {tab === "timeline" ? <HarnessTimeline view={view} live={timeline} models={models} account={account} /> : null}
        {tab === "changes" ? (
          <ChangesPane
            view={view}
            refreshKey={refreshKey}
            undoFor={undoFor}
            onUndo={onUndo}
            onSessionChanged={onSessionChanged}
            onSessionGone={onSessionGone}
            initialScope={changesScope ?? undefined}
          />
        ) : null}
        {tab === "session" ? (
          <SessionRail
            view={view}
            messages={messages ?? view.messages}
            modelWords={modelWords}
            account={account}
            helpers={helpers}
            busy={busy}
            refreshKey={refreshKey}
            onOpenChanges={() => {
              setChangesScope("session");
              choose("changes");
            }}
          />
        ) : null}
        {tab === "browser" ? (
          <BrowserPreview threadId={view.id} seen={seen} refreshKey={refreshKey} pageSteps={pageSteps} />
        ) : null}
        {tab === "files" ? (
          <FilesPanel
            variant="calm"
            folder={view.folder || null}
            onOpenTerminal={() => choose("terminal")}
            noFolderText={
              view.noFolder
                ? "This session's folder is made with your first message, so it has no files yet."
                : "This session has no folder."
            }
          />
        ) : null}
        {/* The terminal stays mounted once opened; behind another tab it is
            hidden with visibility and its terminal parks (TerminalPane
            `parked`), never display:none and never an unmount. */}
        {terminalOpened ? (
          <div
            data-testid="pane-terminal-layer"
            className={tab === "terminal" ? "h-full" : "pointer-events-none absolute inset-0"}
            style={{ visibility: tab === "terminal" ? "visible" : "hidden" }}
            aria-hidden={tab === "terminal" ? undefined : true}
          >
            <SessionTerminal view={view} api={terminal} visible={tab === "terminal"} />
          </div>
        ) : null}
      </div>
    </div>
  );
}
