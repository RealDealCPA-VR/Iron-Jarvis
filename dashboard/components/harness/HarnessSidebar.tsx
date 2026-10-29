"use client";

/**
 * The IJ harness view's LEFT PANE (v1.369.0, plan Phase 6).
 *
 *  - New session: in the folder last worked in, or one picked here (the
 *    Folders picker, or a pasted path). A folder Iron Jarvis cannot use is
 *    refused by the daemon in words, shown under the box. v1.379.0: the
 *    folder is OPTIONAL ("Start with no folder", or Start with the box
 *    empty): such a session keeps its files in a folder of its own under the
 *    chat files folder, made with its first message — one line says where.
 *    All of them share one plain "No folder" group.
 *  - Sessions grouped by folder (GET /build/sessions, newest first). A group
 *    is named by its DISPLAY name; renaming it changes the display name only
 *    (a folder that is a Jarvis project's root shows and renames the
 *    PROJECT's name — the daemon's one store). Each group's "+" starts a new
 *    session in that folder.
 *  - Terminals: the live Build terminals with the state dots Build draws
 *    (GET /terminals/activity, polled by CodeView). Pressing one opens it
 *    in Build (v1.406.0: Build is the terminals; this list is in Chat › Code).
 */

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Check, Columns2, FolderOpen, FolderX, Loader2, Moon, Pencil, Plus, Search, SquareTerminal, Sunrise, X } from "lucide-react";
import { filterFolders } from "@/lib/buildNext";
import { CODING_SESSIONS_LIST } from "@/lib/buildWords";
import { DirectoryTree } from "@/components/terminal/DirectoryTree";
import { PaneDot, stateWord } from "@/components/terminal/PaneState";
import type { RailPane } from "@/components/terminal/PaneRail";
import { formatAge } from "@/lib/threadStatus";
import { noFolderLine, type BuildFolder } from "@/lib/buildSessions";
import { fetchFolderGit, folderGitLine, type FolderGit } from "@/lib/buildGit";
import { SESSION_DRAG_TYPE } from "@/lib/codeSplit";

/** v1.406.0 part 7: the "Open beside" button's words. */
export const OPEN_BESIDE_TITLE = "Open beside the session in front (Shift+Enter on a row; or drag the row to the right edge)";

/** v1.375.0: what New session sends beside the folder. */
export interface StartExtra {
  /** Work on a new branch in its own worktree (true) or in the folder
   *  directly (false). Absent: the daemon decides (a branch when it can). */
  branch?: boolean;
}

const ROW =
  "flex w-full min-w-0 items-center gap-2 rounded-[10px] px-2.5 py-1.5 text-left text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60";
const GHOST =
  "grid h-6 w-6 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:opacity-40";

/** A session row's dot: working (accent), waiting on you (warn), quiet. */
function SessionDot({ running, waiting }: { running: boolean; waiting: boolean }) {
  const cls = waiting ? "bg-tone-warn animate-pulse" : running ? "bg-accent animate-pulse" : "bg-zinc-600";
  return <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${cls}`} />;
}

/** How long New session waits after the folder changes before it asks the
 *  daemon whether the folder is in a git repository. */
export const FOLDER_GIT_DEBOUNCE_MS = 300;

function NewSessionForm({
  defaultFolder,
  onStart,
  onClose,
  workRoot,
  startWithNoFolder = false,
}: {
  defaultFolder: string | null;
  /** v1.379.0 review: the session in front has no folder — the form opens
   *  that way (no folder in the box, the folder list closed). */
  startWithNoFolder?: boolean;
  onStart: (folder: string, extra?: StartExtra) => Promise<string | null>;
  onClose: () => void;
  /** v1.379.0: where a session with no folder keeps its files. */
  workRoot: string;
}) {
  const [folder, setFolder] = useState(defaultFolder ?? "");
  const [picking, setPicking] = useState(!defaultFolder && !startWithNoFolder);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  // v1.375.0 (part 2, Phase 4): is the folder in a git repository? Then the
  // session works on its own branch by default — the switch says so and
  // turns it off ("work in the folder directly").
  const [gitInfo, setGitInfo] = useState<{ folder: string; info: FolderGit | null } | null>(null);
  const [onBranch, setOnBranch] = useState(true);
  useEffect(() => {
    const f = folder.trim();
    if (!f) {
      setGitInfo(null);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      void fetchFolderGit(f).then((info) => {
        if (live) setGitInfo({ folder: f, info });
      });
    }, FOLDER_GIT_DEBOUNCE_MS);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [folder]);
  const info = gitInfo && gitInfo.folder === folder.trim() ? gitInfo.info : null;
  const showSwitch = !!info && info.repo && info.canBranch;
  const gitLine = folderGitLine(info);

  async function begin(f: string) {
    setBusy(true);
    setProblem(null);
    // `branch` rides only when the switch was shown for THIS folder; else the
    // daemon decides (a branch when the folder can have one). v1.379.0: an
    // empty folder is a session with NO folder (no branch: no repository).
    const err = await (f && showSwitch ? onStart(f, { branch: onBranch }) : onStart(f));
    setBusy(false);
    if (err) setProblem(err);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    await begin(folder.trim());
  }

  return (
    <form
      data-testid="harness-new-session-form"
      onSubmit={submit}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
      className="space-y-2 rounded-xl bg-white/[0.03] p-2"
    >
      <label htmlFor="harness-new-folder" className="block text-[12px] text-zinc-500">
        Folder to work in (optional)
      </label>
      <input
        id="harness-new-folder"
        data-testid="harness-new-folder"
        // v1.377.0: the form opens ready to type (New session, Ctrl+N).
        autoFocus
        value={folder}
        onChange={(e) => setFolder(e.target.value)}
        placeholder="Paste a folder path"
        className="w-full rounded-lg border hairline bg-transparent px-2 py-1.5 font-mono text-[12px] text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-accent/50"
      />
      {showSwitch ? (
        <label data-testid="harness-new-branch" className="flex items-start gap-2 text-[12px] text-zinc-300">
          <input
            type="checkbox"
            data-testid="harness-new-branch-switch"
            checked={onBranch}
            onChange={(e) => setOnBranch(e.target.checked)}
            className="mt-0.5 accent-accent"
          />
          <span className="min-w-0">
            Work on a new branch
            <span className="block text-[11px] text-zinc-500">
              {onBranch
                ? `A worktree beside the repository, on its own ij/ branch from ${info?.branch || "the current commit"}. Your checkout is not touched.`
                : "Off: the session works in the folder directly, on the branch checked out there."}
            </span>
          </span>
        </label>
      ) : gitLine ? (
        <p data-testid="harness-new-git-note" className="text-[11px] text-zinc-500">
          {gitLine}
        </p>
      ) : null}
      {/* v1.379.0: a session needs no folder (Claude Code's own rule: attach
          one, or not) — this line says where such a session keeps its files. */}
      <p data-testid="harness-new-no-folder-line" className="break-words text-[11px] leading-snug text-zinc-500">
        {noFolderLine(workRoot)}
      </p>
      {problem ? (
        <p role="alert" className="text-[12px] text-tone-danger">
          {problem}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-1.5">
        <button type="submit" data-testid="harness-new-start" disabled={busy} className="btn-accent text-xs">
          {busy ? <Loader2 size={12} className="animate-spin" /> : null}
          Start
        </button>
        <button
          type="button"
          onClick={() => setPicking((v) => !v)}
          aria-expanded={picking}
          className="rounded-md px-2 py-1 text-[12px] text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-100"
        >
          {picking ? "Hide folders" : "Choose a folder"}
        </button>
        <button
          type="button"
          data-testid="harness-new-no-folder"
          disabled={busy}
          onClick={() => void begin("")}
          className="rounded-md px-2 py-1 text-[12px] text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-100 disabled:opacity-40"
        >
          Start with no folder
        </button>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="ml-auto grid h-6 w-6 place-items-center rounded-md text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-200"
        >
          <X size={13} />
        </button>
      </div>
      {picking ? (
        <div className="max-h-72 overflow-y-auto">
          <DirectoryTree
            variant="calm"
            selectedPath={folder || null}
            onSelect={(p) => setFolder(p)}
            hideAction
            hideHeaderCollapse
            showProjects
          />
        </div>
      ) : null}
    </form>
  );
}

function FolderGroup({
  folder,
  selectedId,
  besideId = null,
  onSelect,
  onOpenBeside,
  onNew,
  onRename,
}: {
  folder: BuildFolder;
  selectedId: string | null;
  besideId?: string | null;
  onSelect: (id: string) => void;
  onOpenBeside?: (id: string) => void;
  onNew: (folder: string) => void;
  onRename: (path: string, name: string) => Promise<string | null>;
}) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (renaming !== null) inputRef.current?.focus();
  }, [renaming !== null]); // eslint-disable-line react-hooks/exhaustive-deps

  async function save() {
    const name = (renaming ?? "").trim();
    setRenaming(null);
    if (!name || name === folder.name) return;
    const err = await onRename(folder.path, name);
    setProblem(err);
  }

  return (
    <section
      data-testid="harness-folder-group"
      data-path={folder.path}
      data-no-folder={folder.noFolder ? "true" : undefined}
      className="space-y-0.5"
    >
      <div className="group/head flex min-w-0 items-center gap-1 px-1">
        {folder.noFolder ? (
          // v1.379.0: the sessions started with no folder — one plain group,
          // nothing to rename, and its "+" starts another one.
          <>
            <FolderX size={12} aria-hidden className="shrink-0 text-zinc-500" />
            <span
              data-testid="harness-folder-name"
              title="Sessions with no folder: each keeps its files in a folder of its own"
              className="min-w-0 flex-1 truncate text-[12px] font-medium text-zinc-400"
            >
              {folder.name}
            </span>
            <button
              type="button"
              data-testid="harness-folder-new"
              aria-label="New session with no folder"
              title="New session with no folder"
              onClick={() => onNew("")}
              className={GHOST}
            >
              <Plus size={13} />
            </button>
          </>
        ) : renaming !== null ? (
          <>
            <input
              ref={inputRef}
              aria-label={folder.isProject ? "Project name" : "Folder name in your coding sessions"}
              data-testid="harness-folder-rename-input"
              value={renaming}
              onChange={(e) => setRenaming(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void save();
                else if (e.key === "Escape") setRenaming(null);
              }}
              onBlur={() => void save()}
              className="min-w-0 flex-1 rounded-md border hairline bg-transparent px-1.5 py-0.5 text-[12px] text-zinc-200 outline-none focus:border-accent/50"
            />
            <button type="button" aria-label="Save the name" onMouseDown={(e) => e.preventDefault()} onClick={() => void save()} className={GHOST}>
              <Check size={12} />
            </button>
          </>
        ) : (
          <>
            <FolderOpen size={12} aria-hidden className="shrink-0 text-zinc-500" />
            <span
              data-testid="harness-folder-name"
              title={folder.isProject ? `${folder.path} (a project)` : folder.path}
              className="min-w-0 flex-1 truncate text-[12px] font-medium text-zinc-400"
            >
              {folder.name}
            </span>
            <button
              type="button"
              data-testid="harness-folder-rename"
              aria-label={folder.isProject ? `Rename the project ${folder.name}` : `Rename ${folder.name} in your coding sessions`}
              title={
                folder.isProject
                  ? "Rename the project (the folder on disk keeps its name)"
                  : "Rename how this folder shows in your coding sessions (the folder on disk keeps its name)"
              }
              onClick={() => setRenaming(folder.name)}
              className={`${GHOST} [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover/head:opacity-100 focus-visible:opacity-100`}
            >
              <Pencil size={11} />
            </button>
            <button
              type="button"
              data-testid="harness-folder-new"
              aria-label={`New session in ${folder.name}`}
              title={`New session in ${folder.name}`}
              onClick={() => onNew(folder.path)}
              className={GHOST}
            >
              <Plus size={13} />
            </button>
          </>
        )}
      </div>
      {problem ? (
        <p role="alert" className="px-2 text-[12px] text-tone-danger">
          {problem}
        </p>
      ) : null}
      <ul className="space-y-0.5">
        {folder.sessions.map((s) => {
          const current = s.id === selectedId;
          // v1.406.0 part 7: the session open in the OTHER column of a split.
          const beside = !current && !!besideId && s.id === besideId;
          return (
            <li key={s.id} className="group/row relative">
              <button
                type="button"
                data-testid={`harness-session-row-${s.id}`}
                aria-current={current ? "true" : undefined}
                data-beside={beside ? "true" : undefined}
                title={s.branch ? `On its own branch, ${s.branch}` : undefined}
                onClick={() => onSelect(s.id)}
                // v1.406.0 part 7: Shift+Enter on a row opens it beside; the
                // row can be dragged to the view's right edge to do the same.
                onKeyDown={(e) => {
                  if (!onOpenBeside || e.key !== "Enter" || !e.shiftKey) return;
                  e.preventDefault();
                  onOpenBeside(s.id);
                }}
                draggable={onOpenBeside ? true : undefined}
                onDragStart={
                  onOpenBeside
                    ? (e) => {
                        try {
                          e.dataTransfer.setData(SESSION_DRAG_TYPE, s.id);
                          e.dataTransfer.effectAllowed = "move";
                        } catch {
                          /* a browser that will not carry it: the button still opens beside */
                        }
                      }
                    : undefined
                }
                className={`${ROW} ${onOpenBeside ? "pr-8" : ""} ${
                  current
                    ? "bg-white/[0.07] text-zinc-100"
                    : beside
                      ? "bg-white/[0.04] text-zinc-100"
                      : "text-zinc-300 hover:bg-white/[0.04] hover:text-zinc-100"
                }`}
              >
                <SessionDot running={s.running} waiting={s.waiting} />
                <span className="min-w-0 flex-1 truncate">{s.title}</span>
                <span className="shrink-0 text-[11px] text-zinc-600">
                  {s.waiting ? "needs you" : s.running ? "working" : formatAge(s.updatedAt)}
                </span>
              </button>
              {onOpenBeside && !current && !beside ? (
                <button
                  type="button"
                  data-testid={`harness-open-beside-${s.id}`}
                  aria-label={`Open ${s.title} beside`}
                  title={OPEN_BESIDE_TITLE}
                  onClick={() => onOpenBeside(s.id)}
                  className={`${GHOST} absolute right-1 top-1/2 -translate-y-1/2 opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100`}
                >
                  <Columns2 size={13} aria-hidden />
                </button>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function HarnessSidebar({
  folders,
  listError,
  selectedId,
  besideId = null,
  onSelect,
  onOpenBeside,
  onStart,
  onRename,
  defaultFolder,
  terminals,
  onOpenTerminal,
  newRequest = 0,
  searchRequest = 0,
  workRoot = "",
  startWithNoFolder = false,
  tonightOpen = false,
  onTonight,
  lastNightOpen = false,
  onLastNight,
}: {
  /** v1.398.0 (night shift N1): Tonight is the view in front. */
  tonightOpen?: boolean;
  /** v1.398.0: open the Tonight list (absent: no Tonight row). */
  onTonight?: () => void;
  /** v1.399.0 (night shift N2): Last night is the view in front. */
  lastNightOpen?: boolean;
  /** v1.399.0: open Last night (absent: no Last night row). */
  onLastNight?: () => void;
  /** v1.379.0 review: the session in front has no folder (New session opens
   *  with none). */
  startWithNoFolder?: boolean;
  /** v1.377.0: Ctrl+N — each change opens New session. */
  newRequest?: number;
  /** v1.379.0: "/resume" — each change puts the caret in the session search. */
  searchRequest?: number;
  /** v1.379.0: where a session with no folder keeps its files. */
  workRoot?: string;
  /** null while the first read is out. */
  folders: BuildFolder[] | null;
  listError: string | null;
  selectedId: string | null;
  /** v1.406.0 part 7: the session open in the other column of a split. */
  besideId?: string | null;
  onSelect: (id: string) => void;
  /** v1.406.0 part 7: open a session BESIDE the one in front (absent: no
   *  "Open beside" on the rows). */
  onOpenBeside?: (id: string) => void;
  /** Start a session in `folder`; resolves to the daemon's refusal, or null.
   *  v1.375.0: `extra.branch` — New session's branch switch, when shown. */
  onStart: (folder: string, extra?: StartExtra) => Promise<string | null>;
  onRename: (path: string, name: string) => Promise<string | null>;
  defaultFolder: string | null;
  terminals: RailPane[];
  onOpenTerminal: (id: string) => void;
}) {
  const [creating, setCreating] = useState(false);
  const [busyFolder, setBusyFolder] = useState<string | null>(null);
  const [groupProblem, setGroupProblem] = useState<string | null>(null);
  // v1.377.0 (Phase 6): Ctrl+N opens New session (lib/harnessKeys).
  useEffect(() => {
    if (newRequest > 0) setCreating(true);
  }, [newRequest]);
  // v1.377.0: search the sessions by title and folder name (the chat list's
  // search as the model: a quiet box, Escape clears it first).
  const [query, setQuery] = useState("");
  // v1.379.0: "/resume" (Claude Code's own name) — the caret in that box.
  const searchRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (searchRequest > 0) searchRef.current?.focus();
  }, [searchRequest]);
  const shown = folders ? filterFolders(folders, query) : null;
  const total = folders ? folders.reduce((n, f) => n + f.sessions.length, 0) : 0;

  async function startIn(folder: string) {
    setBusyFolder(folder);
    setGroupProblem(null);
    const err = await onStart(folder);
    setBusyFolder(null);
    if (err) setGroupProblem(err);
  }

  return (
    // Harness gaps (fix 4): the view's everyday name (lib/buildWords).
    <nav aria-label={CODING_SESSIONS_LIST} data-testid="harness-sidebar" className="flex h-full min-h-0 flex-col gap-2">
      {creating ? (
        <NewSessionForm
          defaultFolder={startWithNoFolder ? "" : defaultFolder}
          startWithNoFolder={startWithNoFolder}
          workRoot={workRoot}
          onStart={async (folder, extra) => {
            const err = await (extra ? onStart(folder, extra) : onStart(folder));
            if (!err) setCreating(false);
            return err;
          }}
          onClose={() => setCreating(false)}
        />
      ) : (
        <button
          type="button"
          data-testid="harness-new-session"
          onClick={() => setCreating(true)}
          className="flex h-8 w-full shrink-0 items-center gap-2 rounded-[10px] px-2.5 text-left text-[13px] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
        >
          <Plus size={14} className="shrink-0" />
          New session
        </button>
      )}
      {onTonight ? (
        // v1.398.0 (night shift N1): the night's queue, one row under New
        // session (the session rows' own look).
        <button
          type="button"
          data-testid="harness-tonight"
          aria-current={tonightOpen ? "page" : undefined}
          onClick={onTonight}
          className={`${ROW} shrink-0 ${tonightOpen ? "bg-white/[0.07] text-zinc-100" : "text-zinc-300 hover:bg-white/[0.04] hover:text-zinc-100"}`}
        >
          <Moon size={14} className="shrink-0" aria-hidden />
          Tonight
        </button>
      ) : null}
      {onLastNight ? (
        // v1.399.0 (night shift N2): the morning's summary, beside Tonight.
        <button
          type="button"
          data-testid="harness-last-night"
          aria-current={lastNightOpen ? "page" : undefined}
          onClick={onLastNight}
          className={`${ROW} shrink-0 ${lastNightOpen ? "bg-white/[0.07] text-zinc-100" : "text-zinc-300 hover:bg-white/[0.04] hover:text-zinc-100"}`}
        >
          <Sunrise size={14} className="shrink-0" aria-hidden />
          Last night
        </button>
      ) : null}

      {total > 0 ? (
        // `isolate` + `z-[1]` (the v1.313.0 rule): the icon paints OVER the
        // field, not under its fill.
        <div className="relative isolate shrink-0">
          <Search
            size={12}
            aria-hidden="true"
            className="pointer-events-none absolute left-2.5 top-1/2 z-[1] -translate-y-1/2 text-zinc-500"
          />
          <input
            ref={searchRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              // Escape clears the box first (and is not the reply's Stop).
              if (e.key === "Escape" && query) {
                e.preventDefault();
                e.stopPropagation();
                setQuery("");
              }
            }}
            placeholder="Search sessions…"
            aria-label="Search sessions"
            data-testid="harness-session-search"
            className="field w-full py-1.5 pl-8 text-[12px]"
          />
        </div>
      ) : null}
      <div data-testid="harness-session-groups" className="min-h-0 flex-1 space-y-3 overflow-y-auto">
        {listError ? (
          <p role="alert" className="px-2 text-[12px] text-tone-danger">
            {listError}
          </p>
        ) : folders === null || shown === null ? (
          <p className="flex items-center gap-1.5 px-2 text-[12px] text-zinc-500">
            <Loader2 size={12} className="animate-spin" /> Reading your sessions…
          </p>
        ) : folders.length === 0 ? (
          <p data-testid="harness-no-sessions" className="px-2 text-[13px] text-zinc-500">
            No sessions yet. Start one in a folder to build there, or with no folder.
          </p>
        ) : shown.length === 0 ? (
          <p data-testid="harness-no-session-match" className="px-2 text-[12px] leading-relaxed text-zinc-500">
            No sessions match “{query.trim()}”.
          </p>
        ) : (
          shown.map((f) => (
            <FolderGroup
              key={f.noFolder ? "\u0000no-folder" : f.path}
              folder={f}
              selectedId={selectedId}
              besideId={besideId}
              onSelect={onSelect}
              onOpenBeside={onOpenBeside}
              onNew={(path) => void startIn(path)}
              onRename={onRename}
            />
          ))
        )}
        {busyFolder ? (
          <p className="flex items-center gap-1.5 px-2 text-[12px] text-zinc-500">
            <Loader2 size={12} className="animate-spin" /> Starting a session…
          </p>
        ) : null}
        {groupProblem ? (
          <p role="alert" className="px-2 text-[12px] text-tone-danger">
            {groupProblem}
          </p>
        ) : null}
      </div>

      <section
        data-testid="harness-terminals"
        aria-label="Terminals"
        className="max-h-[40%] shrink-0 space-y-0.5 overflow-y-auto border-t hairline pt-2"
      >
        <h3 className="flex items-center gap-1.5 px-2 text-[12px] font-medium text-zinc-400">
          <SquareTerminal size={12} aria-hidden /> Terminals
        </h3>
        {terminals.length === 0 ? (
          <p className="px-2 text-[12px] text-zinc-600">No terminals open.</p>
        ) : (
          terminals.map((t) => {
            const word = stateWord(t.state);
            return (
              <button
                key={t.id}
                type="button"
                data-testid={`harness-terminal-row-${t.id}`}
                onClick={() => onOpenTerminal(t.id)}
                title={t.cwd ? `${t.label} · ${t.cwd} · open in Build` : `${t.label} · open in Build`}
                className={`${ROW} text-zinc-300 hover:bg-white/[0.04] hover:text-zinc-100`}
              >
                <PaneDot state={t.state} />
                <span className="min-w-0 flex-1 truncate">{t.label}</span>
                {word ? <span className="shrink-0 text-[11px] text-zinc-600">{word}</span> : null}
              </button>
            );
          })
        )}
      </section>
    </nav>
  );
}
