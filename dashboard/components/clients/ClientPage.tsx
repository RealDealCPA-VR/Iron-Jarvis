"use client";

/**
 * One client's page, /clients/[id] (v1.499.0, vision plan leap 3, Phase 3).
 *
 * Read from `GET /clients/{id}`, which the daemon assembles from its own
 * records and which writes nothing. EVERY CLAIM NAMES ITS RECORD: why Iron
 * Jarvis asked, how each folder joined, where each preference was said or
 * typed, the recap each Decided / Open line came from (with the stale note
 * when the conversation moved on), the work each file and each name was seen
 * in. The decoders drop a claim that names no record.
 *
 * "What a chat in Hartleys' folders is told" is the daemon's own block, as a
 * cloud chat gets it (Local only lines withheld), shown word for word.
 *
 * Changes are the client's own routes: rename, add or remove a folder, keep,
 * change or forget a preference, and the two-press "Not a client". A refusal
 * is said in the route's own words.
 *
 * `ClientOfFolder` (the Project page's "Client: Hartleys") lives here too.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { del, get, patch, post } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { failureOf, type FailureLike } from "@/lib/loadFailed";
import { RECAP_STALE, countWords } from "@/lib/workRecord";
import {
  ADD_FOLDER,
  ASK_AGAIN,
  CANCEL,
  CHANGE,
  CLIENTS_HREF,
  CLIENTS_TITLE,
  DECIDED,
  DECISIONS_NOTE,
  FILES_HEADING,
  FOLDERS_HEADING,
  FORGET,
  FORGET_CONFIRM,
  LOCAL_ONLY_TAG,
  MAKE_IT_A_CLIENT_AGAIN,
  NAMED_ELSEWHERE,
  nothingToldWords,
  NOT_A_CLIENT,
  NOT_A_CLIENT_CONFIRM,
  NOT_ON_RECORD,
  PEOPLE_NAMED,
  PEOPLE_NOTE,
  PREF_PLACEHOLDER,
  PROJECTS_HEADING,
  RENAME,
  SAVE,
  STILL_OPEN,
  WHY_ASKED,
  WORK_HEADING,
  YES_A_CLIENT,
  afterNo,
  afterYes,
  alsoWritten,
  chipWords,
  clientFoldersPath,
  clientHref,
  clientOfPath,
  clientPath,
  clientPrefPath,
  clientPrefsPath,
  clientRemoveFolderPath,
  clientVerbPath,
  decisionSource,
  decodeAnswer,
  decodeClientOf,
  decodeClientPage,
  fileHowWords,
  folderHowWords,
  forgottenFolderWords,
  isAClient,
  isClientId,
  keepFor,
  moreWords,
  notAClientAbout,
  olderWords,
  possessive,
  prefEvidenceLine,
  prefsIntro,
  pressFailed,
  removeFrom,
  sinceLine,
  toldForWords,
  toldHeading,
  whatWants,
  type ClientDecision,
  type ClientPageData,
  type ClientPreference,
  type ClientRef,
} from "@/lib/clients";
import { PageHeader } from "@/components/PageHeader";
import { LoadFailedLine } from "@/components/LoadFailedLine";
import { ConfirmButton } from "@/components/ui";
import { EntryRow } from "@/components/memory/WorkByFolder";

type PageState =
  | { state: "loading" }
  | { state: "missing" }
  | { state: "failed"; error: FailureLike }
  | { state: "ready"; data: ClientPageData };

const INPUT =
  "min-w-[14rem] flex-1 rounded border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[13px] text-zinc-200 outline-none focus:border-accent/40";
const QUIET_PRESS =
  "shrink-0 rounded px-1 py-0.5 text-[12px] text-zinc-500 underline-offset-2 hover:text-zinc-200 hover:underline disabled:opacity-40";

function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={`${id}-heading`} data-testid={id} className="mt-8">
      <h2 id={`${id}-heading`} className="text-[15px] font-medium text-zinc-100">
        {title}
      </h2>
      {children}
    </section>
  );
}

export function ClientPage({ id }: { id: string }) {
  const valid = isClientId(id);
  const [page, setPage] = useState<PageState>(valid ? { state: "loading" } : { state: "missing" });
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ words: string; failed: boolean } | null>(null);
  const alive = useRef(true);
  const seq = useRef(0);

  const read = useCallback(() => {
    if (!isClientId(id)) {
      setPage({ state: "missing" });
      return;
    }
    const mine = ++seq.current;
    Promise.resolve()
      .then(() => get<unknown>(clientPath(id)))
      .then((raw) => {
        if (!alive.current || mine !== seq.current) return;
        const data = decodeClientPage(raw);
        // A page for another client than the one asked for is not this page.
        setPage(data && data.client.id === id ? { state: "ready", data } : { state: "failed", error: { status: 500 } });
      })
      .catch((e: unknown) => {
        if (!alive.current || mine !== seq.current) return;
        const f = failureOf(e);
        setPage(f.status === 404 ? { state: "missing" } : { state: "failed", error: f });
      });
  }, [id]);

  useEffect(() => {
    alive.current = true;
    read();
    return () => {
      alive.current = false;
    };
  }, [read]);

  /** One press: runs, says what it did (or the route's refusal), reads again. */
  const press = useCallback(
    async (run: () => Promise<string>, action: string): Promise<boolean> => {
      if (busy) return false;
      setBusy(true);
      setNote(null);
      try {
        const words = await run();
        if (!alive.current) return true;
        setNote({ words, failed: false });
        read();
        return true;
      } catch (e) {
        if (!alive.current) return false;
        setNote({ words: pressFailed(failureOf(e), action), failed: true });
        return false;
      } finally {
        if (alive.current) setBusy(false);
      }
    },
    [busy, read],
  );

  if (page.state === "missing") {
    return (
      <>
        <PageHeader title={CLIENTS_TITLE} />
        <p className="py-2 text-sm text-zinc-400" data-testid="client-missing">
          {NOT_ON_RECORD}{" "}
          <Link href={CLIENTS_HREF} className="text-accent underline-offset-2 hover:underline">
            Open Clients
          </Link>
        </p>
      </>
    );
  }
  if (page.state === "failed") {
    return (
      <>
        <PageHeader title={CLIENTS_TITLE} />
        <LoadFailedLine thing="This client's page" error={page.error} onRetry={read} testId="client-failed" />
      </>
    );
  }
  if (page.state === "loading") {
    return (
      <>
        <PageHeader title={CLIENTS_TITLE} />
        <p className="py-2 text-sm text-zinc-500">Reading…</p>
      </>
    );
  }

  const d = page.data;
  const c = d.client;
  const confirmed = c.status === "confirmed";

  return (
    <div data-testid="client-page" data-status={c.status}>
      <PageHeader title={c.name} />
      <Header data={d} busy={busy} press={press} />
      <p
        role="status"
        data-testid="client-note"
        className={`text-[13px] ${note ? "py-1" : ""} ${note?.failed ? "text-tone-danger" : "text-zinc-400"}`}
      >
        {note?.words ?? ""}
      </p>

      <Folders data={d} busy={busy} press={press} />
      <Preferences data={d} busy={busy} press={press} />

      <Section id="client-told" title={toldHeading(c.name)}>
        {d.told && d.toldFor && (
          <p data-testid="client-told-for" className="mt-1 text-[12px] text-zinc-500">
            {toldForWords(d.toldFor)}
          </p>
        )}
        {d.told ? (
          <pre
            data-testid="client-told-text"
            className="mt-2 max-w-3xl whitespace-pre-wrap break-words rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2 font-mono text-[12px] text-zinc-300"
          >
            {d.told}
          </pre>
        ) : (
          <p data-testid="client-told-nothing" className="py-1 text-[13px] text-zinc-500">
            {nothingToldWords(c.name, c.status, d.folders.length)}
          </p>
        )}
      </Section>

      <Decisions decisions={d.decisions} />

      <Section id="client-work" title={WORK_HEADING}>
        {d.entries.length === 0 ? (
          <p className="py-1 text-[13px] text-zinc-500">No work on record in these folders yet.</p>
        ) : (
          <>
            <ul className="mt-2 space-y-2">
              {d.entries.map((e) => (
                <EntryRow key={e.id} entry={e} />
              ))}
            </ul>
            {d.entriesTotal > d.entries.length && (
              <p className="pt-2 text-[12px] text-zinc-500">{olderWords(d.entriesTotal - d.entries.length)}</p>
            )}
          </>
        )}
      </Section>

      <Section id="client-files" title={FILES_HEADING}>
        {d.files.length === 0 ? (
          <p className="py-1 text-[13px] text-zinc-500">No files on record.</p>
        ) : (
          <>
            <ul className="mt-2 space-y-1">
              {d.files.map((f) => (
                <li key={`${f.path}|${f.entryId}`} data-testid="client-file" className="text-[13px]">
                  <span className="text-zinc-200" title={f.path || undefined}>
                    {f.name}
                  </span>
                  <span className="text-[12px] text-zinc-500">
                    {f.folderName ? ` · ${f.folderName}` : ""}
                    {fileHowWords(f.how) ? ` · ${fileHowWords(f.how)}` : ""}
                    {f.at ? `, ${timeAgo(f.at)}` : ""}
                    {" · from "}
                  </span>
                  <Origin title={f.title} link={f.link} />
                </li>
              ))}
            </ul>
            {d.filesTotal > d.files.length && (
              <p className="pt-2 text-[12px] text-zinc-500">{moreWords(d.filesTotal - d.files.length)}</p>
            )}
          </>
        )}
      </Section>

      <Section id="client-people" title={PEOPLE_NAMED}>
        <p className="mt-1 max-w-2xl text-[12px] text-zinc-500">{PEOPLE_NOTE}</p>
        {d.people.length === 0 ? (
          <p className="py-1 text-[13px] text-zinc-500">No names found yet.</p>
        ) : (
          <ul className="mt-2 space-y-1">
            {d.people.map((p) => (
              <li key={`${p.how}:${p.name}`} data-testid="client-person" className="text-[13px]">
                <span className="text-zinc-200">{p.name}</span>
                <span className="text-[12px] text-zinc-500"> · Named in: </span>
                {p.seenIn.map((s, i) => (
                  <span key={s.entryId} className="text-[12px]">
                    {i > 0 ? ", " : ""}
                    <Origin title={s.title} link={s.link} />
                  </span>
                ))}
              </li>
            ))}
          </ul>
        )}
      </Section>

      {d.namedElsewhere.length > 0 && (
        <Section id="client-named-elsewhere" title={NAMED_ELSEWHERE}>
          <ul className="mt-2 space-y-1">
            {d.namedElsewhere.map((n) => (
              <li key={n.entryId} className="text-[13px]">
                <Origin title={n.title} link={n.link} />
                {n.folderName && <span className="text-[12px] text-zinc-500"> · {n.folderName}</span>}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {d.projects.length > 0 && (
        <Section id="client-projects" title={PROJECTS_HEADING}>
          <ul className="mt-2 space-y-1">
            {d.projects.map((p) => (
              <li key={p.id} className="text-[13px]">
                <Link href={p.href} className="text-zinc-100 underline-offset-2 hover:underline">
                  {p.name}
                </Link>
                {p.root && (
                  <span className="text-[12px] text-zinc-500" title={p.root}>
                    {" "}
                    · {p.root}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {confirmed && (
        <section className="mt-10 border-t border-white/5 pt-4" data-testid="client-footer">
          <p className="max-w-2xl text-[13px] text-zinc-500">{notAClientAbout(c.name)}</p>
          <div className="pt-2">
            <ConfirmButton
              variant="calm"
              label={NOT_A_CLIENT}
              confirmLabel={NOT_A_CLIENT_CONFIRM}
              testId="client-not-a-client"
              onConfirm={() =>
                void press(async () => {
                  // The one press that may take a CONFIRMED client away.
                  const res = decodeAnswer(await post<unknown>(clientVerbPath(c.id, "decline"), { expect: "confirmed" }));
                  return res.note || afterNo(c.name);
                }, "change that")
              }
            />
          </div>
        </section>
      )}
    </div>
  );
}

type Press = (run: () => Promise<string>, action: string) => Promise<boolean>;

/** A link to the work a claim came from (plain words when it has no page). */
function Origin({ title, link }: { title: string; link: string }) {
  return link ? (
    <Link href={link} className="text-[12px] text-zinc-300 underline-offset-2 hover:underline">
      {title}
    </Link>
  ) : (
    <span className="text-[12px] text-zinc-300">{title}</span>
  );
}

function Header({ data, busy, press }: { data: ClientPageData; busy: boolean; press: Press }) {
  const c = data.client;
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState("");
  const also = alsoWritten(c.aliases);

  return (
    <div className="space-y-1" data-testid="client-header">
      <p className="text-[13px] text-zinc-400" data-testid="client-since">
        {sinceLine(c)}
      </p>
      {also && (
        <p className="text-[13px] text-zinc-500" data-testid="client-aliases">
          {also}
        </p>
      )}
      {c.why.length > 0 && (
        <div data-testid="client-why">
          <p className="text-[13px] text-zinc-500">{WHY_ASKED}</p>
          <ul className="space-y-0.5 pl-3 text-[12px] text-zinc-400">
            {c.why.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      {c.status === "proposed" && (
        <div className="flex flex-wrap items-center gap-2 pt-1" data-testid="client-ask">
          <span className="text-[13px] text-zinc-200">{isAClient(c.name)}</span>
          <button
            type="button"
            data-testid="client-ask-confirm"
            disabled={busy}
            onClick={() =>
              void press(async () => {
                const res = decodeAnswer(await post<unknown>(clientVerbPath(c.id, "confirm"), {}));
                return [afterYes(res.client?.name ?? c.name), res.note].filter(Boolean).join(" ");
              }, "save your answer")
            }
            className="btn-accent btn-sm"
          >
            {YES_A_CLIENT}
          </button>
          <button
            type="button"
            data-testid="client-ask-decline"
            disabled={busy}
            onClick={() =>
              void press(async () => {
                await post<unknown>(clientVerbPath(c.id, "decline"), { expect: "proposed" });
                return afterNo(c.name);
              }, "save your answer")
            }
            className="btn-ghost btn-sm"
          >
            {NOT_A_CLIENT}
          </button>
        </div>
      )}
      {c.status === "declined" && (
        <div className="flex flex-wrap items-center gap-2 pt-1" data-testid="client-declined-again">
          <button
            type="button"
            data-testid="client-bring-back"
            disabled={busy}
            onClick={() =>
              void press(async () => {
                const was = !!c.confirmedAt;
                const res = decodeAnswer(await post<unknown>(clientVerbPath(c.id, was ? "restore" : "ask-again"), {}));
                return was
                  ? [afterYes(res.client?.name ?? c.name), res.note].filter(Boolean).join(" ")
                  : `${c.name} is waiting for your answer again.`;
              }, "change that")
            }
            className="btn-ghost btn-sm"
          >
            {c.confirmedAt ? MAKE_IT_A_CLIENT_AGAIN : ASK_AGAIN}
          </button>
        </div>
      )}
      {c.status !== "declined" &&
        (renaming ? (
          <form
            className="flex max-w-xl flex-wrap items-center gap-2 pt-1"
            onSubmit={(e) => {
              e.preventDefault();
              const next = draft.trim();
              if (!next) return;
              void press(async () => {
                const res = decodeAnswer(await patch<unknown>(clientPath(c.id), { name: next }));
                setRenaming(false);
                return `Renamed to ${res.client?.name ?? next}.`;
              }, "rename it");
            }}
          >
            <input
              aria-label="The client's name"
              data-testid="client-rename-input"
              value={draft}
              maxLength={80}
              disabled={busy}
              onChange={(e) => setDraft(e.target.value)}
              className={INPUT}
            />
            <button type="submit" disabled={busy || !draft.trim()} className="btn-ghost btn-sm">
              {SAVE}
            </button>
            <button type="button" disabled={busy} onClick={() => setRenaming(false)} className={QUIET_PRESS}>
              {CANCEL}
            </button>
          </form>
        ) : (
          <button
            type="button"
            data-testid="client-rename"
            disabled={busy}
            onClick={() => {
              setDraft(c.name);
              setRenaming(true);
            }}
            className={QUIET_PRESS}
          >
            {RENAME}
          </button>
        ))}
    </div>
  );
}

function Folders({ data, busy, press }: { data: ClientPageData; busy: boolean; press: Press }) {
  const c = data.client;
  const [folder, setFolder] = useState("");
  return (
    <Section id="client-folders" title={FOLDERS_HEADING}>
      {data.folders.length === 0 ? (
        <p className="py-1 text-[13px] text-zinc-500">No folder yet, so no chat is told about {c.name}.</p>
      ) : (
        <ul className="mt-2 divide-y divide-white/5">
          {data.folders.map((f) => (
            <li key={f.folder} data-testid="client-folder" className="flex flex-wrap items-baseline gap-x-3 py-2">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm text-zinc-100" title={f.folder}>
                  {f.folder}
                </p>
                <p className="text-[12px] text-zinc-500" data-testid="client-folder-how">
                  {[
                    folderHowWords(f.how, c.name),
                    f.forgotten ? "" : `${countWords(f.count)}${f.lastAt ? `, last ${timeAgo(f.lastAt)}` : ""}`,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
                {f.forgotten && (
                  <p className="text-[12px] text-zinc-500" data-testid="client-folder-forgotten">
                    {forgottenFolderWords(c.name)}
                  </p>
                )}
              </div>
              <ConfirmButton
                variant="calm"
                label={removeFrom(c.name)}
                confirmLabel="Remove it?"
                testId="client-folder-remove"
                onConfirm={() =>
                  void press(async () => {
                    await post<unknown>(clientRemoveFolderPath(c.id), { folder: f.folder });
                    return `${f.name} is no longer one of ${possessive(c.name)} folders.`;
                  }, "remove the folder")
                }
              />
            </li>
          ))}
        </ul>
      )}
      <form
        className="mt-2 flex max-w-2xl flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const next = folder.trim();
          if (!next) return;
          void press(async () => {
            await post<unknown>(clientFoldersPath(c.id), { folder: next });
            setFolder("");
            return `Added ${next}.`;
          }, "add the folder");
        }}
      >
        <input
          aria-label="A folder to add"
          placeholder="A folder that is only this client's"
          data-testid="client-folder-add-input"
          value={folder}
          maxLength={600}
          disabled={busy}
          onChange={(e) => setFolder(e.target.value)}
          className={INPUT}
        />
        <button type="submit" data-testid="client-folder-add" disabled={busy || !folder.trim()} className="btn-ghost btn-sm">
          {ADD_FOLDER}
        </button>
      </form>
    </Section>
  );
}

function Preferences({ data, busy, press }: { data: ClientPageData; busy: boolean; press: Press }) {
  const c = data.client;
  const [text, setText] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  return (
    <Section id="client-preferences" title={whatWants(c.name)}>
      <p className="mt-1 max-w-2xl text-[13px] text-zinc-500">{prefsIntro(c.name)}</p>
      {data.preferences.length === 0 ? (
        <p className="py-1 text-[13px] text-zinc-500">Nothing kept for {c.name} yet.</p>
      ) : (
        <ul className="mt-2 space-y-2">
          {data.preferences.map((p: ClientPreference) => {
            const why = prefEvidenceLine(p);
            return (
              <li key={p.id} data-testid="client-pref" className="text-[13px]">
                {editing === p.id ? (
                  <form
                    className="flex flex-wrap items-center gap-2"
                    onSubmit={(e) => {
                      e.preventDefault();
                      const next = draft.trim();
                      if (!next) return;
                      void press(async () => {
                        await patch<unknown>(clientPrefPath(c.id, p.id), { text: next });
                        setEditing(null);
                        return "Changed.";
                      }, "change it");
                    }}
                  >
                    <input
                      aria-label="The preference, in your words"
                      data-testid="client-pref-edit"
                      value={draft}
                      maxLength={280}
                      disabled={busy}
                      onChange={(e) => setDraft(e.target.value)}
                      className={INPUT}
                    />
                    <button type="submit" disabled={busy || !draft.trim()} className="btn-ghost btn-sm">
                      {SAVE}
                    </button>
                    <button type="button" disabled={busy} onClick={() => setEditing(null)} className={QUIET_PRESS}>
                      {CANCEL}
                    </button>
                  </form>
                ) : (
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="text-zinc-100">{p.text}</span>
                    {p.localOnly && (
                      <span className="text-[12px] text-zinc-500" data-testid="client-pref-local">
                        {LOCAL_ONLY_TAG}
                      </span>
                    )}
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        setDraft(p.text);
                        setEditing(p.id);
                      }}
                      className={QUIET_PRESS}
                    >
                      {CHANGE}
                    </button>
                    <ConfirmButton
                      variant="calm"
                      label={FORGET}
                      confirmLabel={FORGET_CONFIRM}
                      testId="client-pref-forget"
                      onConfirm={() =>
                        void press(async () => {
                          await del<unknown>(clientPrefPath(c.id, p.id));
                          return "Forgotten.";
                        }, "forget it")
                      }
                    />
                  </div>
                )}
                {why && (
                  <p className="text-[12px] text-zinc-500" data-testid="client-pref-why">
                    {why}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {c.status === "confirmed" && (
        <form
          className="mt-2 flex max-w-2xl flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const next = text.trim();
            if (!next) return;
            void press(async () => {
              await post<unknown>(clientPrefsPath(c.id), { text: next });
              setText("");
              return `Kept for ${c.name}.`;
            }, "keep it");
          }}
        >
          <input
            aria-label={`A preference for ${c.name}`}
            placeholder={PREF_PLACEHOLDER}
            data-testid="client-pref-add-input"
            value={text}
            maxLength={280}
            disabled={busy}
            onChange={(e) => setText(e.target.value)}
            className={INPUT}
          />
          <button type="submit" data-testid="client-pref-add" disabled={busy || !text.trim()} className="btn-ghost btn-sm">
            {keepFor(c.name)}
          </button>
        </form>
      )}
    </Section>
  );
}

function DecisionLine({ d }: { d: ClientDecision }) {
  return (
    <li data-testid="client-decision" data-label={d.label} className="text-[13px]">
      <span className="text-zinc-100">{d.text}</span>
      <p className="text-[12px] text-zinc-500">
        {d.link ? (
          <Link href={d.link} className="underline-offset-2 hover:underline">
            {decisionSource(d)}
          </Link>
        ) : (
          decisionSource(d)
        )}
      </p>
      {!d.current && (
        <p className="text-[12px] text-zinc-500" data-testid="client-decision-stale">
          {RECAP_STALE}
        </p>
      )}
    </li>
  );
}

function Decisions({ decisions }: { decisions: ClientDecision[] }) {
  const decided = decisions.filter((d) => d.label === "decided");
  const open = decisions.filter((d) => d.label === "open");
  return (
    <Section id="client-decisions" title={`${DECIDED} and ${STILL_OPEN.toLowerCase()}`}>
      <p className="mt-1 max-w-2xl text-[12px] text-zinc-500">{DECISIONS_NOTE}</p>
      {decisions.length === 0 ? (
        <p className="py-1 text-[13px] text-zinc-500">No checked recap has a Decided or Open line yet.</p>
      ) : (
        <div className="mt-2 grid gap-4 md:grid-cols-2">
          <div>
            <h3 className="text-[13px] font-medium text-zinc-300">{DECIDED}</h3>
            <ul className="mt-1 space-y-2" data-testid="client-decided">
              {decided.map((d, i) => (
                <DecisionLine key={`${d.entryId}:${i}`} d={d} />
              ))}
            </ul>
          </div>
          <div>
            <h3 className="text-[13px] font-medium text-zinc-300">{STILL_OPEN}</h3>
            <ul className="mt-1 space-y-2" data-testid="client-open">
              {open.map((d, i) => (
                <DecisionLine key={`${d.entryId}:${i}`} d={d} />
              ))}
            </ul>
          </div>
        </div>
      )}
    </Section>
  );
}

/**
 * "Client: Hartleys" for a folder (the Project page), from `GET
 * /clients/of?folder=`: the daemon says whose folder it is, the dashboard
 * never works it out. Nothing at all when the folder has no client or the
 * question cannot be answered.
 */
export function ClientOfFolder({ folder }: { folder: string }) {
  const [client, setClient] = useState<ClientRef | null>(null);
  useEffect(() => {
    let live = true;
    setClient(null);
    if (!folder.trim()) return;
    Promise.resolve()
      .then(() => get<unknown>(clientOfPath(folder)))
      .then((raw) => {
        if (live) setClient(decodeClientOf(raw));
      })
      .catch(() => {
        // Quiet: an older Iron Jarvis, or nothing to say.
      });
    return () => {
      live = false;
    };
  }, [folder]);
  if (!client) return null;
  return (
    <Link
      href={clientHref(client.id)}
      data-testid="client-of-folder"
      className="inline-flex shrink-0 items-center rounded-full border border-white/10 px-1.5 py-0.5 text-[10px] font-medium text-accent-soft hover:border-accent/30"
    >
      {chipWords(client.name)}
    </Link>
  );
}
