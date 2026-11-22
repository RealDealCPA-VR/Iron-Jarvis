"use client";

/**
 * 22-b (R3.4): under the newest settled Build › Code reply, Commit and Open a
 * pull request — one press each, the message ready to edit (lib/buildAppGit,
 * the daemon's routes/build_app_git.py).
 *
 *  - The message is already there when the row shows: Iron Jarvis wrote it
 *    from the session's own record (the first message and the files), never
 *    a model. Commit is the ONE main button.
 *  - A session in the folder directly lists every changed file with a tick
 *    (other programs' changes are listed too) and commits ONLY the ticked
 *    ones; what the user staged for any other file stays staged.
 *  - A session on its own branch commits everything in its own copy of the
 *    folder; the Changes tab's Branch view has Push, Merge and Discard.
 *  - Open a pull request uses the branch's own title (editable) and is
 *    offered only when one can be opened; otherwise its one quiet line says
 *    why ("Start the next session on its own branch to open a pull
 *    request.").
 *
 * Read once per settled reply (`readKey`), never polled. Nothing is drawn
 * when the folder is not a repository or there is nothing to offer.
 */

import { memo, useCallback, useEffect, useRef, useState } from "react";
import { GitBranch, Loader2 } from "lucide-react";
import { Button } from "@/components/ui";
import {
  COMMIT_LABEL,
  MESSAGE_FROM_RECORD,
  OPEN_PR_LABEL,
  TICKS_LINE,
  branchFilesLine,
  canCommit,
  commitAppGit,
  draftFor,
  getAppGit,
  hasGitWork,
  openAppPr,
  prLinkWords,
  type AppGit,
} from "@/lib/buildAppGit";
import { gitRefusal, type GitRefusal } from "@/lib/buildGit";

const FIELD =
  "w-full min-w-0 rounded-lg border hairline bg-transparent px-2 py-1.5 text-[13px] text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-accent/50";
const QUIET_BTN =
  "inline-flex h-7 items-center rounded-lg px-2 text-[13px] text-zinc-300 transition-colors hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-50";

/** How long the row waits for the reply's end to settle before it reads. */
export const READ_AFTER_MS = 400;

const STATUS_WORDS: Record<string, string> = {
  created: "new",
  modified: "changed",
  deleted: "deleted",
  renamed: "renamed",
};

export const ReplyGit = memo(function ReplyGit({
  sessionId,
  readKey,
  prUrl = "",
  onChanged,
}: {
  sessionId: string;
  /** Changes when a reply settles: the row reads the folder again. */
  readKey: string;
  /** The pull request the session's view already names ("" = none). */
  prUrl?: string;
  /** Something changed on the row (a commit, a pull request). */
  onChanged?: () => void;
}) {
  const [git, setGit] = useState<AppGit | null>(null);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState<"" | "commit" | "pr">("");
  const [note, setNote] = useState("");
  const [refusal, setRefusal] = useState<GitRefusal | null>(null);
  const [openedUrl, setOpenedUrl] = useState("");
  const edited = useRef(false);
  const titleEdited = useRef(false);
  const seq = useRef(0);
  const draftSeq = useRef(0);

  const read = useCallback(async () => {
    const n = ++seq.current;
    try {
      const g = await getAppGit(sessionId);
      if (n !== seq.current) return;
      setGit(g);
      if (g) {
        setTicked(new Set(g.files.filter((f) => f.ticked).map((f) => f.rel)));
        if (!edited.current) setMessage(g.draft);
        if (!titleEdited.current) setTitle(g.prDraft.title);
      }
    } catch {
      // A folder that cannot be read draws nothing (the Changes tab says why).
      if (n === seq.current) setGit(null);
    }
  }, [sessionId]);

  useEffect(() => {
    edited.current = false;
    titleEdited.current = false;
    setNote("");
    setRefusal(null);
    // Read once the reply's end has settled (its last rows land in a burst;
    // opened on a scratch daemon, each one asked again, one after another).
    const t = window.setTimeout(() => void read(), READ_AFTER_MS);
    return () => window.clearTimeout(t);
  }, [read, readKey]);

  async function toggle(rel: string) {
    const next = new Set(ticked);
    if (next.has(rel)) next.delete(rel);
    else next.add(rel);
    setTicked(next);
    if (edited.current) return;
    // The message lists the ticked files: written again for them (the
    // record's draft, no model), unless the user has edited it.
    const n = ++draftSeq.current;
    try {
      const m = await draftFor(sessionId, Array.from(next));
      if (n === draftSeq.current && !edited.current) setMessage(m);
    } catch {
      /* the message stays as it is */
    }
  }

  async function commit() {
    if (!git || busy) return;
    setBusy("commit");
    setRefusal(null);
    setNote("");
    try {
      const done = await commitAppGit(sessionId, message, git.kind === "folder" ? Array.from(ticked) : null);
      edited.current = false;
      setNote(`Committed ${done.sha}: ${done.subject}${done.note ? ` ${done.note}` : ""}`);
      onChanged?.();
      await read();
    } catch (e) {
      setRefusal(gitRefusal(e, "The commit did not go through."));
    } finally {
      setBusy("");
    }
  }

  async function openPr() {
    if (!git || busy) return;
    setBusy("pr");
    setRefusal(null);
    setNote("");
    try {
      const made = await openAppPr(sessionId, title, git.prDraft.body);
      setOpenedUrl(made.url);
      setNote(made.number ? `Pull request #${made.number} is open.` : "The pull request is open.");
      onChanged?.();
      await read();
    } catch (e) {
      setRefusal(gitRefusal(e, "The pull request was not opened."));
    } finally {
      setBusy("");
    }
  }

  const link = openedUrl || git?.prUrl || prUrl;
  if (!git || !hasGitWork(git, link)) {
    return note ? (
      <p role="status" data-testid="code-git-note" className="mt-3 break-words text-[12px] text-zinc-400">
        {note}
      </p>
    ) : null;
  }
  const toCommit = git.uncommitted > 0;
  const gh = git.kind === "branch" ? git.status?.gh ?? null : git.gh;
  const ghReady = !!gh && gh.installed && gh.signedIn === true;
  // A pull request once there are commits the main branch lacks (files left
  // unticked do not hold it back: they are not in it).
  const prPossible = git.canPr && !link && git.ahead > 0;
  const commitReady = canCommit(git, ticked, message);

  return (
    <div
      data-testid="code-git"
      data-kind={git.kind}
      role="group"
      aria-label="Commit and pull request"
      className="mt-3 grid min-w-0 gap-2 rounded-xl bg-white/[0.03] p-3"
    >
      {toCommit ? (
        <>
          <p className="flex min-w-0 items-start gap-1.5 text-[12px] text-zinc-400">
            <GitBranch size={12} aria-hidden className="mt-[3px] shrink-0" />
            <span data-testid="code-git-files-line" className="min-w-0 break-words">
              {git.kind === "branch" ? branchFilesLine(git.uncommitted, git.branch) : TICKS_LINE}
            </span>
          </p>
          {git.kind === "folder" ? (
            <ul data-testid="code-git-files" aria-label="Changed files" className="max-h-48 min-w-0 space-y-0.5 overflow-y-auto">
              {git.files.map((f) => (
                <li key={f.rel} className="min-w-0">
                  <label className="flex min-w-0 items-start gap-2 text-[12px] text-zinc-300">
                    <input
                      type="checkbox"
                      data-testid="code-git-tick"
                      data-rel={f.rel}
                      checked={ticked.has(f.rel)}
                      disabled={!!busy}
                      onChange={() => void toggle(f.rel)}
                      className="mt-0.5 shrink-0 accent-accent"
                    />
                    <span className="min-w-0 font-mono text-[11.5px] [overflow-wrap:anywhere]">{f.rel}</span>
                    <span className="shrink-0 text-zinc-500">{STATUS_WORDS[f.status] ?? f.status}</span>
                  </label>
                </li>
              ))}
              {git.more > 0 ? (
                <li className="text-[12px] text-zinc-500">and {git.more} more, not listed (not committed from here)</li>
              ) : null}
            </ul>
          ) : null}
          <textarea
            data-testid="code-git-message"
            aria-label="Commit message"
            value={message}
            onChange={(e) => {
              edited.current = true;
              setMessage(e.target.value);
            }}
            rows={3}
            disabled={!!busy}
            className={`${FIELD} resize-y font-mono`}
          />
          <p className="text-[11px] text-zinc-500">{MESSAGE_FROM_RECORD}</p>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              data-testid="code-git-commit"
              disabled={!!busy || !commitReady}
              onClick={() => void commit()}
            >
              {busy === "commit" ? <Loader2 size={13} className="animate-spin" aria-label="Working" /> : null}
              {COMMIT_LABEL}
            </Button>
            {git.kind === "folder" && git.onMain && git.prNote ? (
              <span data-testid="code-git-pr-note" className="min-w-0 break-words text-[12px] text-zinc-500">
                {git.prNote}
              </span>
            ) : null}
          </div>
        </>
      ) : null}

      {prPossible ? (
        ghReady ? (
          <div data-testid="code-git-pr" className="grid min-w-0 gap-1.5">
            <input
              data-testid="code-git-pr-title"
              aria-label="Pull request title"
              value={title}
              onChange={(e) => {
                titleEdited.current = true;
                setTitle(e.target.value);
              }}
              disabled={!!busy}
              className={FIELD}
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant={toCommit ? "secondary" : "primary"}
                size="sm"
                data-testid="code-git-open-pr"
                disabled={!!busy || !title.trim()}
                onClick={() => void openPr()}
              >
                {busy === "pr" ? <Loader2 size={13} className="animate-spin" aria-label="Working" /> : null}
                {OPEN_PR_LABEL}
              </Button>
              <span className="min-w-0 break-words text-[12px] text-zinc-500">
                Into {git.main}{git.pushed ? "" : `; pushes ${git.branch} first (never a force)`}.
              </span>
            </div>
          </div>
        ) : (
          <div data-testid="code-git-pr-fallback" className="space-y-1 text-[12px] text-zinc-400">
            <p className="break-words">{gh?.note || "Pull requests are made on GitHub's own page."}</p>
            {(git.kind === "branch" ? git.status?.compareUrl : git.compareUrl) ? (
              <a
                data-testid="code-git-compare"
                href={(git.kind === "branch" ? git.status?.compareUrl : git.compareUrl) || ""}
                target="_blank"
                rel="noreferrer"
                className="text-accent underline-offset-2 hover:underline"
              >
                {OPEN_PR_LABEL} on GitHub
              </a>
            ) : null}
          </div>
        )
      ) : !toCommit && !link && git.prNote ? (
        <p data-testid="code-git-pr-note" className="break-words text-[12px] text-zinc-500">
          {git.prNote}
        </p>
      ) : null}

      {link ? (
        <a
          data-testid="code-git-pr-link"
          href={link}
          target="_blank"
          rel="noreferrer"
          className="w-fit text-[12px] text-accent underline-offset-2 hover:underline"
        >
          {prLinkWords(Number(link.match(/\/pull\/(\d+)/)?.[1]) || null)}
        </a>
      ) : null}

      {note ? (
        <p role="status" data-testid="code-git-note" className="break-words text-[12px] text-zinc-300">
          {note}
        </p>
      ) : null}
      {refusal ? (
        <div role="alert" data-testid="code-git-error" className="space-y-1 text-[12px]">
          <p className="break-words text-tone-danger">{refusal.detail}</p>
          {refusal.details ? (
            <details>
              <summary className="cursor-pointer text-zinc-500 hover:text-zinc-300">Details</summary>
              <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md bg-white/[0.03] p-2 font-mono text-[11px] text-zinc-400">
                {refusal.details}
              </pre>
            </details>
          ) : null}
        </div>
      ) : null}
    </div>
  );
});

export default ReplyGit;
