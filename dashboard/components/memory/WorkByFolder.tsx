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
 *
 * v1.499.0 (Phase 3, clients): a folder that belongs to a client says so
 * ("Client: Hartleys", a link to its page), from the `client` the SERVER put
 * on the row; a folder with no client offers "Make this a client" in one
 * press (`POST /clients {folder}`), except where the folder rules would
 * refuse it anyway. `EntryRow` (one piece of work) is shared with the
 * client page.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ChevronRight, FolderClosed } from "lucide-react";
import { get, post } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { failureOf, type FailureLike } from "@/lib/loadFailed";
import {
  CLIENTS_PATH,
  MAKE_THIS_A_CLIENT,
  afterYes,
  chipWords,
  clientHref,
  decodeAnswer,
  mayMakeClient,
  pressFailed,
} from "@/lib/clients";
import { forgetPickUpNote } from "@/lib/pickUp";
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
import {
  PIECE_GONE_WORDS,
  PIECE_OLDER_WORDS,
  PIECE_UNREAD_WORDS,
  decodePiece,
  decodePieceEntry,
  focusedWorkId,
  sameFolder,
  workPiecePath,
} from "@/lib/workHistory";
import { LoadFailedLine } from "@/components/LoadFailedLine";
import { ConfirmButton } from "@/components/ui";

/** Reads again this often while the first build is still going. */
export const WORK_BUILD_REREAD_MS = 4000;
/** And at most this many times. */
export const WORK_BUILD_REREADS = 30;

type FolderWork = { state: "loading" } | { state: "failed"; error: FailureLike } | { state: "ready"; entries: WorkItem[]; total: number };

/** `aboveSettled` (MemorySurface): the cards above this section have filled
 *  in, so a piece brought into view before that is brought into view again. */
export function WorkByFolder({ aboveSettled = false }: { aboveSettled?: boolean } = {}) {
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
        // The pick-up card's cross note for this folder goes too (next
        // leaps 04-a): nothing of a forgotten folder stays in this browser.
        forgetPickUpNote(f.folder);
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

  // v1.499.0: one press makes this folder a client. The daemon's folder
  // rules decide (a folder for many people is refused in its own words);
  // the list is read again so the row carries its client.
  const [making, setMaking] = useState<string | null>(null);
  const makeClient = useCallback(
    async (f: WorkFolder) => {
      if (making) return;
      setMaking(f.folder);
      try {
        const res = await post<unknown>(CLIENTS_PATH, { folder: f.folder });
        if (!alive.current) return;
        const made = decodeAnswer(res);
        setNote({
          words: [afterYes(made.client?.name ?? f.name), made.note].filter(Boolean).join(" "),
          failed: false,
        });
        read();
      } catch (e) {
        if (!alive.current) return;
        setNote({ words: pressFailed(failureOf(e), "make this folder a client"), failed: true });
      } finally {
        if (alive.current) setMaking(null);
      }
    },
    [making, read],
  );

  // Next leaps 13-b: a chat reply's Decisions opens /memory?work=<id>. The
  // piece's folder is asked for once (by the piece's id: no folder in the
  // address), then opened here when the list has it, and the piece is
  // brought into view. Review 3: the answer carries the piece itself, shown
  // above the list (PIECE_OLDER_WORDS) when its folder is not among the
  // folders listed, or the piece not among its folder's newest.
  const [focus, setFocus] = useState<{ id: string; folder: string; entry: WorkItem | null } | null>(null);
  const focusedOnce = useRef(false);
  useEffect(() => {
    const id = typeof window === "undefined" ? "" : focusedWorkId(window.location.search);
    if (!id) return;
    Promise.resolve()
      .then(() => get<unknown>(workPiecePath(id)))
      .then((raw) => {
        if (!alive.current) return;
        const folder = decodePiece(raw);
        if (folder) setFocus({ id, folder, entry: decodePieceEntry(raw, id) });
        else setNote({ words: PIECE_GONE_WORDS, failed: false });
      })
      .catch((e: unknown) => {
        if (!alive.current) return;
        const failure = failureOf(e);
        setNote({ words: failure.status === 404 ? PIECE_GONE_WORDS : PIECE_UNREAD_WORDS, failed: failure.status !== 404 });
      });
  }, []);
  useEffect(() => {
    if (!focus || focusedOnce.current || !folders) return;
    const f = folders.find((x) => sameFolder(x.folder, focus.folder));
    if (!f) return;
    focusedOnce.current = true;
    setOpen(f.folder);
    if (workRef.current[f.folder]?.state !== "loading") readFolder(f.folder);
  }, [focus, folders, readFolder]);

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

  // The piece ?work= named, when no folder listed here is its folder.
  const focusUnlisted =
    focus && folders && !failed && !folders.some((x) => sameFolder(x.folder, focus.folder)) ? focus.entry : null;

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
      {focusUnlisted && <OlderPiece entry={focusUnlisted} settled={aboveSettled} />}

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
              // Only the piece's own folder marks it (or shows it above its list).
              const focusHere = focus && sameFolder(f.folder, focus.folder) ? focus : null;
              return (
                <li key={f.folder} data-testid="work-folder">
                  <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="flex min-w-0 flex-1 items-center gap-2 rounded px-1 py-2 text-left hover:bg-white/5 focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent"
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
                  {/* v1.499.0: the server's own answer to "whose folder is
                      this?" — never worked out here. */}
                  {f.client ? (
                    <Link
                      href={clientHref(f.client.id)}
                      data-testid="work-folder-client"
                      className="shrink-0 rounded-full border border-white/10 px-2 py-0.5 text-[12px] text-accent-soft underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent"
                    >
                      {chipWords(f.client.name)}
                    </Link>
                  ) : f.mayMakeClient !== false && mayMakeClient(f.folder) ? (
                    <button
                      type="button"
                      data-testid="work-folder-make-client"
                      disabled={making !== null}
                      onClick={() => void makeClient(f)}
                      className="shrink-0 rounded px-1 py-0.5 text-[12px] text-zinc-500 underline-offset-2 hover:text-accent-soft hover:underline disabled:opacity-40 focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent"
                    >
                      {MAKE_THIS_A_CLIENT}
                    </button>
                  ) : null}
                  </div>
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
                        <FolderWorkList
                          entries={mine.entries}
                          total={mine.total}
                          focusId={focusHere?.id}
                          focusEntry={focusHere?.entry ?? null}
                          settled={aboveSettled}
                        />
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

/** One piece of work, as the work record shows it (v1.499.0: shared with the
 *  client page, which lists a client's work the same way). Render it inside
 *  a `<ul>`. */
export function EntryRow({
  entry: e,
  focused = false,
  settled = false,
}: {
  entry: WorkItem;
  focused?: boolean;
  /** True once what is above it on the page has filled in: a focused row is
   *  brought into view again then (a landing made earlier ends up too high). */
  settled?: boolean;
}) {
  // Said only when the work did not simply finish.
  const ended =
    e.outcome === "failed" || e.outcome === "cancelled" ? statusWords(e.outcome) : outcomeWords(e.outcome);
  const files = filesWords(e);
  // Next leaps 13-b: the piece a chat reply's Decisions opened, brought into view.
  const ref = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (focused) ref.current?.scrollIntoView?.({ block: "center" });
  }, [focused, settled]);
  return (
    <li
      ref={ref}
      data-testid="work-item"
      data-focused={focused ? "true" : undefined}
      className={focused ? "rounded border-l-2 border-accent/60 pl-2" : undefined}
    >
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
}

/** Next leaps 13-b, review 3: the piece a Decisions press opened that the
 *  list does not hold (older than the folders or pieces listed), shown
 *  above it, marked and brought into view. */
function OlderPiece({ entry, settled }: { entry: WorkItem; settled: boolean }) {
  return (
    <div className="mb-2 mt-1" data-testid="work-piece-older">
      <p className="text-[12px] text-zinc-500">{PIECE_OLDER_WORDS}</p>
      <ul className="mt-1 space-y-2">
        <EntryRow entry={entry} focused settled={settled} />
      </ul>
    </div>
  );
}

function FolderWorkList({
  entries,
  total,
  focusId,
  focusEntry = null,
  settled = false,
}: {
  entries: WorkItem[];
  total: number;
  focusId?: string;
  focusEntry?: WorkItem | null;
  settled?: boolean;
}) {
  const older = focusId && focusEntry && !entries.some((e) => e.id === focusId) ? focusEntry : null;
  if (entries.length === 0) {
    return (
      <>
        {older && <OlderPiece entry={older} settled={settled} />}
        <p className="py-1 text-sm text-zinc-500">Nothing on record in this folder any more.</p>
      </>
    );
  }
  return (
    <>
      {older && <OlderPiece entry={older} settled={settled} />}
      <ul className="space-y-2">
        {entries.map((e) => (
          <EntryRow key={e.id} entry={e} focused={Boolean(focusId) && e.id === focusId} settled={settled} />
        ))}
      </ul>
      {total > entries.length && (
        <p className="pt-2 text-[12px] text-zinc-500">
          Showing the newest {entries.length} of {total}.
        </p>
      )}
    </>
  );
}
