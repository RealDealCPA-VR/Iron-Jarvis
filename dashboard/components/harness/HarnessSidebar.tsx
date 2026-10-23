"use client";

/**
 * The IJ harness view's LEFT PANE (v1.369.0, plan Phase 6).
 *
 *  - New session: in the folder last worked in, or one picked here (the
 *    Folders picker, or a pasted path). A folder Iron Jarvis cannot use is
 *    refused by the daemon in words, shown under the box.
 *  - Sessions grouped by folder (GET /build/sessions, newest first). A group
 *    is named by its DISPLAY name; renaming it changes the display name only
 *    (a folder that is a Jarvis project's root shows and renames the
 *    PROJECT's name — the daemon's one store). Each group's "+" starts a new
 *    session in that folder.
 *  - Terminals: the live Build terminals with the state dots the Terminals
 *    view draws (GET /terminals/activity, polled by the page). Pressing one
 *    opens it in the Terminals view, through the page's own focus path.
 */

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Check, FolderOpen, Loader2, Pencil, Plus, SquareTerminal, X } from "lucide-react";
import { DirectoryTree } from "@/components/terminal/DirectoryTree";
import { PaneDot, stateWord } from "@/components/terminal/PaneState";
import type { RailPane } from "@/components/terminal/PaneRail";
import { formatAge } from "@/lib/threadStatus";
import type { BuildFolder } from "@/lib/buildSessions";
import { fetchFolderGit, folderGitLine, type FolderGit } from "@/lib/buildGit";

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
}: {
  defaultFolder: string | null;
  onStart: (folder: string, extra?: StartExtra) => Promise<string | null>;
  onClose: () => void;
}) {
  const [folder, setFolder] = useState(defaultFolder ?? "");
  const [picking, setPicking] = useState(!defaultFolder);
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

  async function submit(e: FormEvent) {
    e.preventDefault();
    const f = folder.trim();
    if (!f) {
      setProblem("Pick or paste a folder first.");
      return;
    }
    setBusy(true);
    setProblem(null);
    // `branch` rides only when the switch was shown for THIS folder; else the
    // daemon decides (a branch when the folder can have one).
    const err = await (showSwitch ? onStart(f, { branch: onBranch }) : onStart(f));
    setBusy(false);
    if (err) setProblem(err);
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
        Folder to work in
      </label>
      <input
        id="harness-new-folder"
        data-testid="harness-new-folder"
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
  onSelect,
  onNew,
  onRename,
}: {
  folder: BuildFolder;
  selectedId: string | null;
  onSelect: (id: string) => void;
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
    <section data-testid="harness-folder-group" data-path={folder.path} className="space-y-0.5">
      <div className="group/head flex min-w-0 items-center gap-1 px-1">
        {renaming !== null ? (
          <>
            <input
              ref={inputRef}
              aria-label={folder.isProject ? "Project name" : "Folder name in Build"}
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
              aria-label={folder.isProject ? `Rename the project ${folder.name}` : `Rename ${folder.name} in Build`}
              title={
                folder.isProject
                  ? "Rename the project (the folder on disk keeps its name)"
                  : "Rename how this folder shows in Build (the folder on disk keeps its name)"
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
          return (
            <li key={s.id}>
              <button
                type="button"
                data-testid={`harness-session-row-${s.id}`}
                aria-current={current ? "true" : undefined}
                title={s.branch ? `On its own branch, ${s.branch}` : undefined}
                onClick={() => onSelect(s.id)}
                className={`${ROW} ${current ? "bg-white/[0.07] text-zinc-100" : "text-zinc-300 hover:bg-white/[0.04] hover:text-zinc-100"}`}
              >
                <SessionDot running={s.running} waiting={s.waiting} />
                <span className="min-w-0 flex-1 truncate">{s.title}</span>
                <span className="shrink-0 text-[11px] text-zinc-600">
                  {s.waiting ? "needs you" : s.running ? "working" : formatAge(s.updatedAt)}
                </span>
              </button>
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
  onSelect,
  onStart,
  onRename,
  defaultFolder,
  terminals,
  onOpenTerminal,
}: {
  /** null while the first read is out. */
  folders: BuildFolder[] | null;
  listError: string | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
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

  async function startIn(folder: string) {
    setBusyFolder(folder);
    setGroupProblem(null);
    const err = await onStart(folder);
    setBusyFolder(null);
    if (err) setGroupProblem(err);
  }

  return (
    <nav aria-label="IJ harness sessions" data-testid="harness-sidebar" className="flex h-full min-h-0 flex-col gap-2">
      {creating ? (
        <NewSessionForm
          defaultFolder={defaultFolder}
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

      <div data-testid="harness-session-groups" className="min-h-0 flex-1 space-y-3 overflow-y-auto">
        {listError ? (
          <p role="alert" className="px-2 text-[12px] text-tone-danger">
            {listError}
          </p>
        ) : folders === null ? (
          <p className="flex items-center gap-1.5 px-2 text-[12px] text-zinc-500">
            <Loader2 size={12} className="animate-spin" /> Reading your sessions…
          </p>
        ) : folders.length === 0 ? (
          <p data-testid="harness-no-sessions" className="px-2 text-[13px] text-zinc-500">
            No sessions yet. Start one in a folder to build there.
          </p>
        ) : (
          folders.map((f) => (
            <FolderGroup
              key={f.path}
              folder={f}
              selectedId={selectedId}
              onSelect={onSelect}
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
                title={t.cwd ? `${t.label} · ${t.cwd} · open in the Terminals view` : `${t.label} · open in the Terminals view`}
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
