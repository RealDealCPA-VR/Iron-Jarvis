"use client";

/**
 * The IJ harness session's RIGHT PANES (v1.369.0, plan Phase 6): plan,
 * terminal, changes, browser, files — tabs over one box, each the app's OWN
 * piece:
 *
 *  - plan: `PlanChecklist`, fed by the running turn's `plan_update` calls,
 *    else the newest one a reply kept, else the plan the session keeps (one
 *    the user approved in plan mode);
 *  - terminal: a NORMAL Build terminal in the session's folder, made through
 *    the terminals API and drawn by the page's own TerminalPane (the pane
 *    host, v1.243.0) — it is in the Terminals view's rail too;
 *  - changes: `ChangedFiles` / `DiffView` from `POST /chat/changes` with the
 *    session's own `build:<id>` journal, with Undo;
 *  - browser: a preview of a page on this PC or the user's network, and the
 *    paired browser named (BrowserPreview);
 *  - files: `FilesPanel` (calm) on the session's folder.
 *
 * The terminal tab, once opened, stays MOUNTED and is hidden with
 * `visibility` (never `display:none`, never an unmount — the Build terminal
 * rules); behind another tab its terminal parks in the host's lot.
 */

import { useCallback, useEffect, useId, useState, type ReactNode } from "react";
import { Loader2, Plus, RefreshCw } from "lucide-react";
import { PlanChecklist } from "@/components/chat/PlanChecklist";
import { ChangedFiles, fetchTurnChangeSet, type ChangeUndoState, type FileChange } from "@/components/chat/ChangedFiles";
import { FilesPanel } from "@/components/terminal/FilesPanel";
import { BrowserPreview } from "@/components/harness/BrowserPreview";
import { BranchView } from "@/components/harness/HarnessBranch";
import { ConfirmButton } from "@/components/ui";
import { Markdown } from "@/components/Markdown";
import type { PlanView } from "@/lib/planChecklist";
import {
  addressesIn,
  planFromMessages,
  planFromSession,
  readSessionTerminal,
  writeSessionTerminal,
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

export type PaneTab = "plan" | "terminal" | "changes" | "browser" | "files";

export const PANE_TABS: readonly { value: PaneTab; label: string }[] = [
  { value: "plan", label: "Plan" },
  { value: "terminal", label: "Terminal" },
  { value: "changes", label: "Changes" },
  { value: "browser", label: "Browser" },
  { value: "files", label: "Files" },
];

const TAB_KEY = "ij.build.pane";

function readTab(): PaneTab {
  try {
    const v = window.localStorage.getItem(TAB_KEY);
    return PANE_TABS.some((t) => t.value === v) ? (v as PaneTab) : "plan";
  } catch {
    return "plan";
  }
}

/** The plan to show: the running turn's, else a kept reply's, else the
 *  session's approved one. */
export function panePlan(view: BuildSessionView, livePlan: PlanView | null): PlanView | null {
  return livePlan ?? planFromMessages(view.messages) ?? planFromSession(view.plan);
}

function PlanPane({ view, livePlan }: { view: BuildSessionView; livePlan: PlanView | null }) {
  const plan = panePlan(view, livePlan);
  return (
    <div data-testid="pane-plan" className="space-y-3">
      {plan ? (
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
          A terminal in {view.folderName}, beside the session. It is a normal Build terminal: it shows in the
          Terminals view too.
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
}: {
  view: BuildSessionView;
  refreshKey: number;
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
    view.git && !view.git.removed && !view.git.merged ? "branch" : "session",
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
        <ChangedFiles
          changes={changes}
          more={more}
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
}: {
  view: BuildSessionView;
  livePlan: PlanView | null;
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
  const choose = useCallback((t: PaneTab) => {
    setTab(t);
    if (t === "terminal") setTerminalOpened(true);
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
        className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 px-1"
      >
        {PANE_TABS.map((t) => {
          const selected = tab === t.value;
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
              onClick={() => choose(t.value)}
              onKeyDown={(e) => {
                if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
                e.preventDefault();
                const i = PANE_TABS.findIndex((x) => x.value === t.value);
                const next = PANE_TABS[(i + (e.key === "ArrowRight" ? 1 : -1) + PANE_TABS.length) % PANE_TABS.length];
                choose(next.value);
                document.getElementById(`${baseId}-tab-${next.value}`)?.focus();
              }}
              className={`rounded-md px-1.5 py-1 text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 ${
                selected ? "font-medium text-zinc-100" : "text-zinc-500 hover:text-zinc-200"
              }`}
            >
              {t.label}
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
        {tab === "changes" ? (
          <ChangesPane
            view={view}
            refreshKey={refreshKey}
            undoFor={undoFor}
            onUndo={onUndo}
            onSessionChanged={onSessionChanged}
            onSessionGone={onSessionGone}
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
            noFolderText="This session has no folder."
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
