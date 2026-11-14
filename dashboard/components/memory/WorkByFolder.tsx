"use client";

/**
 * "Your work, by folder" (v1.491.0, vision plan leap 3, Phase 1): every
 * folder Iron Jarvis has work on record for, and what was done there.
 *
 * One read of the folder list when the section mounts (and again, a few
 * times, while a long first build is still going); a folder's own work is
 * read when the user opens it. Read-only. An older daemon (404) or a daemon
 * that cannot answer draws nothing new: the failed-read line, never an empty
 * list that reads as "you have done nothing".
 *
 * v1.492.0 (Phase 2): a piece of work shows its RECAP (written by a model
 * after the work went quiet, checked against the work's own record), and an
 * opened folder has a two-press "Forget this folder"; forgotten folders are
 * listed at the foot with "Remember again".
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ChevronRight, FolderClosed } from "lucide-react";
import { get, post } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { failureOf, type FailureLike } from "@/lib/loadFailed";
import { plainText } from "@/lib/plainText";
import { outcomeWords, statusWords, toneTextClass } from "@/lib/statusWords";
import {
  FORGET_CONFIRM,
  FORGET_LABEL,
  FORGET_TITLE,
  FORGOTTEN_ABOUT,
  FORGOTTEN_HEADING,
  RECAP_ABOUT,
  RECAP_STALE,
  REMEMBER_LABEL,
  WORK_ABOUT,
  WORK_BUILDING,
  WORK_EMPTY,
  WORK_FOLDERS_PATH,
  WORK_FORGET_PATH,
  WORK_HEADING,
  WORK_REMEMBER_PATH,
  countWords,
  decodeEntries,
  decodeFolders,
  filesWords,
  forgotWords,
  kindWords,
  workEntriesPath,
  type ForgottenFolder,
  type WorkFolder,
  type WorkItem,
} from "@/lib/workRecord";
import { LoadFailedLine } from "@/components/LoadFailedLine";
import { ConfirmButton } from "@/components/ui";

/** Reads again this often while the first build is still going. */
export const WORK_BUILD_REREAD_MS = 4000;
/** And at most this many times. */
export const WORK_BUILD_REREADS = 30;

type FolderWork = { state: "loading" } | { state: "failed"; error: FailureLike } | { state: "ready"; entries: WorkItem[]; total: number };

export function WorkByFolder() {
  const [folders, setFolders] = useState<WorkFolder[] | null>(null);
  const [building, setBuilding] = useState(false);
  const [foldersTotal, setFoldersTotal] = useState(0);
  const [failed, setFailed] = useState<FailureLike | null>(null);
  const [hidden, setHidden] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [work, setWork] = useState<Record<string, FolderWork>>({});
  const [forgotten, setForgotten] = useState<ForgottenFolder[]>([]);
  // What the last Forget / Remember press did, in words (or why it could not).
  const [note, setNote] = useState<{ words: string; failed: boolean } | null>(null);
  const workRef = useRef<Record<string, FolderWork>>({});
  const rereads = useRef(0);
  const alive = useRef(true);

  const read = useCallback(() => {
    Promise.resolve()
      .then(() => get<unknown>(WORK_FOLDERS_PATH))
      .then((raw) => {
        if (!alive.current) return;
        const got = decodeFolders(raw);
        setFolders(got.folders);
        setBuilding(got.building);
        setFoldersTotal(got.foldersTotal);
        setForgotten(got.forgotten);
        setFailed(null);
      })
      .catch((e: unknown) => {
        if (!alive.current) return;
        // An older Iron Jarvis has no work record: show nothing at all.
        // (Read off the failure's own status, never `instanceof`: many tests
        // mock lib/api whole.)
        const failure = failureOf(e);
        if (failure.status === 404) setHidden(true);
        else setFailed(failure);
      });
  }, []);

  useEffect(() => {
    alive.current = true;
    read();
    return () => {
      alive.current = false;
    };
  }, [read]);

  useEffect(() => {
    if (!building || rereads.current >= WORK_BUILD_REREADS) return;
    const t = setTimeout(() => {
      rereads.current += 1;
      read();
    }, WORK_BUILD_REREAD_MS);
    return () => clearTimeout(t);
  }, [building, folders, read]);

  const readFolder = useCallback((folder: string) => {
    // What is already shown stays on screen while it is read again.
    if (workRef.current[folder]?.state !== "ready") {
      workRef.current = { ...workRef.current, [folder]: { state: "loading" } };
      setWork(workRef.current);
    }
    const settle = (next: FolderWork) => {
      if (!alive.current) return;
      workRef.current = { ...workRef.current, [folder]: next };
      setWork(workRef.current);
    };
    Promise.resolve()
      .then(() => get<unknown>(workEntriesPath(folder)))
      .then((raw) => {
        const got = decodeEntries(raw);
        settle({ state: "ready", entries: got.entries, total: got.total });
      })
      .catch((e: unknown) => settle({ state: "failed", error: failureOf(e) }));
  }, []);

  const toggleFolder = useCallback(
    (folder: string, isOpen: boolean) => {
      setOpen(isOpen ? null : folder);
      // A folder's work is read each time it is opened (the record grows
      // while a first build is going), never twice at once.
      if (!isOpen && workRef.current[folder]?.state !== "loading") readFolder(folder);
    },
    [readFolder],
  );

  const forget = useCallback(
    async (f: WorkFolder) => {
      try {
        const res = await post<{ removed?: number }>(WORK_FORGET_PATH, { folder: f.folder });
        if (!alive.current) return;
        setNote({ words: forgotWords(f.name, typeof res?.removed === "number" ? res.removed : 0), failed: false });
        setOpen(null);
        read();
      } catch (e) {
        if (!alive.current) return;
        setNote({ words: failureOf(e).message || "That folder could not be forgotten. Try again.", failed: true });
      }
    },
    [read],
  );

  const remember = useCallback(
    async (f: ForgottenFolder) => {
      try {
        await post<unknown>(WORK_REMEMBER_PATH, { folder: f.folder });
        if (!alive.current) return;
        setNote({ words: `Remembering ${f.name} again.`, failed: false });
        read();
      } catch (e) {
        if (!alive.current) return;
        setNote({ words: failureOf(e).message || "That could not be changed. Try again.", failed: true });
      }
    },
    [read],
  );

  if (hidden) return null;

  return (
    <section className="mt-6" aria-labelledby="work-by-folder-heading" data-testid="work-by-folder">
      <h2 id="work-by-folder-heading" className="text-[15px] font-medium text-zinc-100">
        {WORK_HEADING}
      </h2>
      <p className="mt-1 max-w-2xl text-[13px] text-zinc-500">{WORK_ABOUT}</p>
      <p className="mt-1 max-w-2xl text-[13px] text-zinc-500" data-testid="work-recap-about">
        {RECAP_ABOUT}
      </p>
      {/* Always mounted, so a screen reader hears the words when they come. */}
      <p
        role="status"
        data-testid="work-forget-note"
        className={`text-[13px] ${note ? "py-1" : ""} ${note?.failed ? "text-tone-danger" : "text-zinc-400"}`}
      >
        {note?.words ?? ""}
      </p>

      {failed ? (
        <LoadFailedLine thing="Your work record" error={failed} onRetry={read} testId="work-by-folder-failed" />
      ) : folders === null ? (
        <p className="py-2 text-sm text-zinc-500">Reading your work record…</p>
      ) : folders.length === 0 ? (
        <p className="py-2 text-sm text-zinc-500" data-testid="work-by-folder-empty">
          {building ? WORK_BUILDING : WORK_EMPTY}
        </p>
      ) : (
        <>
          {building && (
            <p className="py-1 text-[13px] text-zinc-500" role="status" data-testid="work-by-folder-building">
              {WORK_BUILDING}
            </p>
          )}
          <ul className="mt-2 divide-y divide-white/5">
            {folders.map((f) => {
              const isOpen = open === f.folder;
              const mine = work[f.folder];
              return (
                <li key={f.folder} data-testid="work-folder">
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded px-1 py-2 text-left hover:bg-white/5 focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent"
                    aria-expanded={isOpen}
                    onClick={() => toggleFolder(f.folder, isOpen)}
                  >
                    <ChevronRight
                      aria-hidden
                      className={`h-4 w-4 shrink-0 text-zinc-500 transition-transform ${isOpen ? "rotate-90" : ""}`}
                    />
                    <FolderClosed aria-hidden className="h-4 w-4 shrink-0 text-zinc-500" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm text-zinc-100">{f.name}</span>
                      <span className="block truncate text-[12px] text-zinc-500" title={f.folder}>
                        {f.folder}
                      </span>
                    </span>
                    <span className="shrink-0 text-[12px] text-zinc-500">
                      {countWords(f.count)}
                      {f.lastAt ? `, last ${timeAgo(f.lastAt)}` : ""}
                    </span>
                  </button>
                  {isOpen && (
                    <div className="pb-3 pl-8">
                      {!mine || mine.state === "loading" ? (
                        <p className="py-1 text-sm text-zinc-500">Reading…</p>
                      ) : mine.state === "failed" ? (
                        <LoadFailedLine
                          thing="This folder's work"
                          error={mine.error}
                          onRetry={() => readFolder(f.folder)}
                        />
                      ) : (
                        <FolderWorkList entries={mine.entries} total={mine.total} />
                      )}
                      <div className="pt-2">
                        <ConfirmButton
                          variant="calm"
                          label={FORGET_LABEL}
                          confirmLabel={FORGET_CONFIRM}
                          title={FORGET_TITLE}
                          testId="work-forget"
                          onConfirm={() => forget(f)}
                        />
                      </div>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          {foldersTotal > folders.length && (
            <p className="pt-2 text-[12px] text-zinc-500" data-testid="work-by-folder-more">
              Showing the {folders.length} most recently used of {foldersTotal} folders.
            </p>
          )}
        </>
      )}
      {!failed && forgotten.length > 0 && (
        <div className="mt-4" data-testid="work-forgotten">
          <h3 className="text-[13px] font-medium text-zinc-300">
            {FORGOTTEN_HEADING} ({forgotten.length})
          </h3>
          <p className="text-[12px] text-zinc-500">{FORGOTTEN_ABOUT}</p>
          <ul className="mt-1 space-y-1">
            {forgotten.map((f) => (
              <li key={f.folder} className="flex flex-wrap items-baseline gap-x-3 text-[13px]">
                <span className="min-w-0 truncate text-zinc-300" title={f.folder}>
                  {f.folder}
                </span>
                <button
                  type="button"
                  className="shrink-0 text-accent underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent"
                  data-testid="work-remember"
                  onClick={() => void remember(f)}
                >
                  {REMEMBER_LABEL}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function FolderWorkList({ entries, total }: { entries: WorkItem[]; total: number }) {
  if (entries.length === 0) {
    return <p className="py-1 text-sm text-zinc-500">Nothing on record in this folder any more.</p>;
  }
  return (
    <>
      <ul className="space-y-2">
        {entries.map((e) => {
          // Said only when the work did not simply finish.
          const ended =
            e.outcome === "failed" || e.outcome === "cancelled" ? statusWords(e.outcome) : outcomeWords(e.outcome);
          const files = filesWords(e);
          return (
            <li key={e.id} data-testid="work-item">
              <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
                {e.link ? (
                  <Link href={e.link} className="text-zinc-100 underline-offset-2 hover:underline">
                    {e.title}
                  </Link>
                ) : (
                  <span className="text-zinc-100">{e.title}</span>
                )}
                <span className="text-[12px] text-zinc-500">
                  {kindWords(e.kind, e.tool)}
                  {e.updatedAt ? `, ${timeAgo(e.updatedAt)}` : ""}
                  {e.localOnly ? ", Local only" : ""}
                  {e.archived ? ", archived" : ""}
                </span>
                {ended && <span className={`text-[12px] ${toneTextClass(ended.tone)}`}>{ended.word}</span>}
              </div>
              {files && <p className="text-[12px] text-zinc-500">{files}</p>}
              {e.note && <p className="text-[12px] text-zinc-500">{plainText(e.note)}</p>}
              {e.recap && (
                <div className="mt-1 border-l border-white/10 pl-2" data-testid="work-recap">
                  <ul className="space-y-0.5 text-[12px] text-zinc-400">
                    {e.recap.lines.map((line, i) => (
                      <li key={i}>{plainText(line)}</li>
                    ))}
                  </ul>
                  {!e.recap.current && (
                    <p className="text-[12px] text-zinc-500" data-testid="work-recap-stale">
                      {RECAP_STALE}
                    </p>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {total > entries.length && (
        <p className="pt-2 text-[12px] text-zinc-500">
          Showing the newest {entries.length} of {total}.
        </p>
      )}
    </>
  );
}
