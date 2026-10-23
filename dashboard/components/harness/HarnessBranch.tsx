"use client";

/**
 * The BRANCH view of a Build session's Changes pane (v1.375.0, IJ harness
 * part 2 Phase 4) — for a session that works on its own git branch in a
 * worktree beside the repository (the user's decisions 1–3).
 *
 *  - one line for the branch: its commits, the files not committed yet, and
 *    whether the remote has it;
 *  - the whole diff against its base (where they meet), committed and not,
 *    with the Changes pane's own pieces (`ChangedFiles` / `DiffView`);
 *  - the person's buttons, each a POST to the daemon (routes/build_git.py):
 *    Commit (the session's model drafts the message, the user edits it),
 *    Push (never a force), Create PR (GitHub CLI signed in; otherwise the
 *    compare link opens GitHub's own page), Merge into <base> (two presses;
 *    a refusal is said in words, nothing changed) — then "Remove the
 *    worktree" — and Discard (two presses: the worktree goes to the trash,
 *    the branch stays, the session is archived).
 *
 * A refusal shows the daemon's own sentence; git's output (masked by the
 * daemon) folds under Details. A pull request's checks are read when asked
 * and on a slow poll while this view is on screen — never a tight loop.
 */

import { useCallback, useEffect, useState } from "react";
import { GitBranch, Loader2, RefreshCw } from "lucide-react";
import { ChangedFiles, decodeChanges, type FileChange } from "@/components/chat/ChangedFiles";
import { ConfirmButton } from "@/components/ui";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import {
  PR_POLL_MS,
  commitBranch,
  createPr,
  discardBranch,
  draftCommit,
  getBranchChangesRaw,
  getBranchStatus,
  getPr,
  gitRefusal,
  mergeBranch,
  moreFiles,
  pushBranch,
  statusLine,
  type BranchStatus,
  type GitRefusal,
  type PrInfo,
  type SessionGit,
} from "@/lib/buildGit";

const ACTION =
  "inline-flex h-7 items-center gap-1 rounded-md px-2 text-[12px] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-40";
const FIELD =
  "w-full min-w-0 rounded-lg border hairline bg-transparent px-2 py-1.5 text-[13px] text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-accent/50";

type Mode = "idle" | "commit" | "pr";

export function BranchView({
  sessionId,
  git,
  refreshKey,
  onChanged,
  onArchived,
}: {
  /** The session's thread id. */
  sessionId: string;
  git: SessionGit;
  /** Bumped when a turn ends (the branch is read again). */
  refreshKey: number;
  /** The session's view changed (merged, pushed, a pull request). */
  onChanged?: () => void;
  /** The worktree was removed and the session archived. */
  onArchived?: () => void;
}) {
  const [status, setStatus] = useState<BranchStatus | null>(null);
  const [changes, setChanges] = useState<FileChange[] | null>(null);
  const [more, setMore] = useState(false);
  const [readError, setReadError] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<GitRefusal | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("idle");
  const [message, setMessage] = useState("");
  const [draftNote, setDraftNote] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [prFallback, setPrFallback] = useState(false);
  const [pr, setPr] = useState<PrInfo | null>(null);
  const [prUrl, setPrUrl] = useState(git.prUrl);
  const [merged, setMerged] = useState<{ into: string; where: string } | null>(null);
  const [round, setRound] = useState(0);

  const removed = git.removed;

  const load = useCallback(async () => {
    if (removed) return;
    try {
      const [st, raw] = await Promise.all([getBranchStatus(sessionId), getBranchChangesRaw(sessionId)]);
      setStatus(st);
      setChanges(decodeChanges(raw));
      setMore(moreFiles(raw));
      setReadError(null);
    } catch (e) {
      setReadError(gitRefusal(e, "The branch could not be read.").detail);
    }
  }, [sessionId, removed]);

  useEffect(() => {
    void load();
  }, [load, refreshKey, round]);

  const readPr = useCallback(async () => {
    try {
      const info = await getPr(sessionId);
      if (info) {
        setPr(info);
        if (info.url) setPrUrl(info.url);
      }
    } catch (e) {
      setRefusal(gitRefusal(e, "The pull request could not be read."));
    }
  }, [sessionId]);

  useEffect(() => {
    if (prUrl && !removed) void readPr();
  }, [prUrl, removed, readPr]);
  // A pull request's checks change on GitHub's own time: read slowly, only
  // while the window is on screen.
  useVisibleInterval(() => void readPr(), PR_POLL_MS, !!prUrl && !removed);

  async function act(name: string, fn: () => Promise<string | null>) {
    setBusy(name);
    setRefusal(null);
    setNote(null);
    try {
      const said = await fn();
      if (said) setNote(said);
    } catch (e) {
      setRefusal(gitRefusal(e));
    } finally {
      setBusy(null);
      setRound((n) => n + 1);
    }
  }

  async function startCommit() {
    setMode("commit");
    setMessage("");
    setDraftNote("");
    await act("draft", async () => {
      const d = await draftCommit(sessionId);
      setMessage(d.message);
      setDraftNote(d.drafted ? "" : d.note);
      return null;
    });
  }

  async function doCommit() {
    await act("commit", async () => {
      const done = await commitBranch(sessionId, message);
      setMode("idle");
      onChanged?.();
      return `Committed ${done.sha}: ${done.subject}${done.note ? ` ${done.note}` : ""}`;
    });
  }

  function startPr() {
    setRefusal(null);
    setNote(null);
    const gh = status?.gh;
    if (!gh || !gh.installed || gh.signedIn !== true) {
      setPrFallback(true);
      setMode("idle");
      return;
    }
    setPrFallback(false);
    setTitle(status?.prDraft.title || git.branch);
    setBody(status?.prDraft.body || "");
    setMode("pr");
  }

  async function doPr() {
    await act("pr", async () => {
      const made = await createPr(sessionId, title, body);
      setMode("idle");
      setPrUrl(made.url);
      onChanged?.();
      return made.number ? `Pull request #${made.number} is open.` : "The pull request is open.";
    });
  }

  const base = git.base;
  const canMerge = !!status && status.ahead > 0 && status.baseExists && !status.inBase && !!base;

  if (removed) {
    return (
      <p data-testid="pane-branch-removed" className="text-[13px] text-zinc-400">
        This session&apos;s worktree was removed. The branch {git.branch} is kept in the repository.
      </p>
    );
  }

  return (
    <div data-testid="pane-branch" className="min-w-0 space-y-2">
      <div className="flex min-w-0 items-start gap-2">
        <p
          data-testid="branch-status"
          className="flex min-w-0 flex-1 items-start gap-1.5 text-[12px] leading-relaxed text-zinc-400"
        >
          <GitBranch size={12} aria-hidden className="mt-[3px] shrink-0" />
          <span className="min-w-0 break-words">
            {status ? statusLine(status, git.branch) : readError ? "" : "Reading the branch…"}
          </span>
        </p>
        <button
          type="button"
          aria-label="Read the branch again"
          title="Read the branch again"
          onClick={() => setRound((n) => n + 1)}
          className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
        >
          <RefreshCw size={13} />
        </button>
      </div>

      {readError ? (
        <p role="alert" data-testid="branch-read-error" className="text-[12px] text-tone-danger">
          {readError}
        </p>
      ) : null}

      <div data-testid="branch-actions" className="flex flex-wrap items-center gap-1">
        <button
          type="button"
          data-testid="branch-commit"
          disabled={!!busy || !status || status.uncommitted === 0}
          title={status && status.uncommitted === 0 ? "Nothing to commit" : "Commit everything not committed yet"}
          onClick={() => void startCommit()}
          className={ACTION}
        >
          Commit
        </button>
        <button
          type="button"
          data-testid="branch-push"
          disabled={!!busy || !status || status.ahead === 0 || status.pushed}
          title={
            status && !status.remote
              ? "This repository has no remote"
              : status?.pushed
                ? "The remote has every commit"
                : `Push ${git.branch}`
          }
          onClick={() =>
            void act("push", async () => {
              const done = await pushBranch(sessionId);
              onChanged?.();
              return `Pushed ${done.branch || git.branch} to ${done.remote}.`;
            })
          }
          className={ACTION}
        >
          Push
        </button>
        <button
          type="button"
          data-testid="branch-pr"
          disabled={!!busy || !status || status.ahead === 0 || !base}
          title={base ? `A pull request into ${base}` : "This session started from a commit, not a branch"}
          onClick={startPr}
          className={ACTION}
        >
          Create PR
        </button>
        {canMerge ? (
          <ConfirmButton
            onConfirm={() =>
              act("merge", async () => {
                const done = await mergeBranch(sessionId);
                setMerged({ into: done.into, where: done.where });
                onChanged?.();
                return done.where
                  ? `Merged into ${done.into} in ${done.where}.`
                  : `Merged into ${done.into}. It is not checked out anywhere, so no folder changed.`;
              })
            }
            label={`Merge into ${base}`}
            confirmLabel="Confirm merge"
            title={`A normal merge of ${git.branch} into ${base}. Refused with nothing changed when it cannot go through cleanly.`}
          />
        ) : null}
        <ConfirmButton
          onConfirm={() =>
            act("discard", async () => {
              const done = await discardBranch(sessionId);
              onArchived?.();
              return done.note || "The worktree is in the trash.";
            })
          }
          label="Discard"
          confirmLabel="Confirm discard"
          title={`The worktree folder goes to Iron Jarvis's trash; the branch ${git.branch} stays.`}
        />
        {busy ? <Loader2 size={13} aria-label="Working" className="animate-spin text-zinc-500" /> : null}
      </div>

      {mode === "commit" ? (
        <div data-testid="branch-commit-box" className="space-y-1.5 rounded-xl bg-white/[0.03] p-2">
          {busy === "draft" ? (
            <p className="flex items-center gap-1.5 text-[12px] text-zinc-500">
              <Loader2 size={12} className="animate-spin" /> Drafting a message…
            </p>
          ) : null}
          {draftNote ? (
            <p data-testid="branch-draft-note" className="text-[12px] text-zinc-400">
              {draftNote}
            </p>
          ) : null}
          <textarea
            data-testid="branch-commit-message"
            aria-label="Commit message"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            rows={4}
            placeholder="What changed, in one line, then any detail"
            className={`${FIELD} resize-y font-mono`}
          />
          <div className="flex flex-wrap gap-1.5">
            <button
              type="button"
              data-testid="branch-commit-go"
              disabled={!!busy || !message.trim()}
              onClick={() => void doCommit()}
              className="btn-accent text-xs"
            >
              Commit
            </button>
            <button type="button" onClick={() => setMode("idle")} className={ACTION}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {mode === "pr" ? (
        <div data-testid="branch-pr-box" className="space-y-1.5 rounded-xl bg-white/[0.03] p-2">
          <input
            data-testid="branch-pr-title"
            aria-label="Pull request title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className={FIELD}
          />
          <textarea
            data-testid="branch-pr-body"
            aria-label="Pull request description"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={4}
            className={`${FIELD} resize-y`}
          />
          <div className="flex flex-wrap gap-1.5">
            <button
              type="button"
              data-testid="branch-pr-go"
              disabled={!!busy || !title.trim()}
              onClick={() => void doPr()}
              className="btn-accent text-xs"
            >
              Create pull request
            </button>
            <button type="button" onClick={() => setMode("idle")} className={ACTION}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {prFallback ? (
        <div data-testid="branch-pr-fallback" className="space-y-1 text-[12px] text-zinc-400">
          <p>{status?.gh.note || "Pull requests are made on GitHub's own page."}</p>
          {status?.compareUrl ? (
            <a
              data-testid="branch-compare-link"
              href={status.compareUrl}
              target="_blank"
              rel="noreferrer"
              className="text-accent underline-offset-2 hover:underline"
            >
              Open a pull request on GitHub
            </a>
          ) : (
            <p>This remote is not on GitHub, so there is no page to open from here.</p>
          )}
        </div>
      ) : null}

      {prUrl ? (
        <div data-testid="branch-pr-row" className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[12px]">
          <a
            data-testid="branch-pr-link"
            href={prUrl}
            target="_blank"
            rel="noreferrer"
            className="text-accent underline-offset-2 hover:underline"
          >
            {pr?.number ? `Pull request #${pr.number}` : "The pull request"}
          </a>
          {pr?.words ? (
            <span
              data-testid="branch-pr-checks"
              className={pr.checks.failed ? "text-tone-danger" : pr.checks.pending ? "text-tone-warn" : "text-zinc-400"}
            >
              {pr.words}
            </span>
          ) : null}
          <button type="button" data-testid="branch-pr-check" onClick={() => void readPr()} className={ACTION}>
            Check again
          </button>
        </div>
      ) : null}

      {note ? (
        <p role="status" data-testid="branch-note" className="break-words text-[12px] text-zinc-300">
          {note}
        </p>
      ) : null}
      {refusal ? (
        <div role="alert" data-testid="branch-error" className="space-y-1 text-[12px]">
          <p className="break-words text-tone-danger">{refusal.detail}</p>
          {refusal.details ? (
            <details>
              <summary className="cursor-pointer text-zinc-500 hover:text-zinc-300">Details</summary>
              <pre
                data-testid="branch-error-details"
                className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md bg-white/[0.03] p-2 font-mono text-[11px] text-zinc-400"
              >
                {refusal.details}
              </pre>
            </details>
          ) : null}
        </div>
      ) : null}

      {merged ? (
        <div data-testid="branch-merged" className="flex flex-wrap items-center gap-2 text-[12px] text-zinc-400">
          <span>The work is in {merged.into}. The worktree is no longer needed.</span>
          <ConfirmButton
            onConfirm={() =>
              act("remove", async () => {
                const done = await discardBranch(sessionId, true);
                onArchived?.();
                return done.note || "The worktree is in the trash.";
              })
            }
            label="Remove the worktree"
            confirmLabel="Confirm remove"
            title={`The worktree folder goes to Iron Jarvis's trash; the branch ${git.branch} stays.`}
          />
        </div>
      ) : null}

      {changes === null ? null : changes.length === 0 ? (
        <p data-testid="branch-no-changes" className="text-[13px] text-zinc-400">
          No changes against {base || "the commit it started from"} yet.
        </p>
      ) : (
        <ChangedFiles changes={changes} more={more} defaultOpen />
      )}
    </div>
  );
}
