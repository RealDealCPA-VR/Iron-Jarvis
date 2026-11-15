"use client";

/**
 * The Clients page's body (v1.499.0, vision plan leap 3, Phase 3): the names
 * waiting for an answer (the bulk review), "Your clients", "Add a client",
 * "Look again now" and the names that are not clients.
 *
 * One read of `GET /clients` when the page opens and again after every
 * answer. A read that fails says so (never "no clients"). Reads change
 * nothing: Iron Jarvis looks for new names on its own, or when the owner
 * presses "Look again now".
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { get, post } from "@/lib/api";
import { failureOf, type FailureLike } from "@/lib/loadFailed";
import {
  ADD_ABOUT,
  ADD_A_CLIENT,
  ADD_FOLDER_LABEL,
  ADD_NAME_LABEL,
  ASK_AGAIN,
  CLIENTS_EMPTY,
  CLIENTS_PATH,
  LOOK_AGAIN_NOW,
  MAKE_IT_A_CLIENT_AGAIN,
  NOTICE_PATH,
  NOT_CLIENTS_HEADING,
  YOUR_CLIENTS,
  afterYes,
  clientHref,
  clientRowLine,
  clientVerbPath,
  decodeAnswer,
  decodeClientsList,
  decodeNotice,
  lookedAgainWords,
  pressFailed,
  type ClientsList,
  type DeclinedName,
} from "@/lib/clients";
import { LoadFailedLine } from "@/components/LoadFailedLine";
import { ClientReview } from "./ClientReview";

/** How many folder names a client row shows. */
const FOLDERS_SHOWN = 3;

export function ClientList() {
  const [data, setData] = useState<ClientsList | null>(null);
  const [failed, setFailed] = useState<FailureLike | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ words: string; failed: boolean } | null>(null);
  const [name, setName] = useState("");
  const [folder, setFolder] = useState("");
  const alive = useRef(true);
  const seq = useRef(0);

  const read = useCallback(() => {
    const mine = ++seq.current;
    Promise.resolve()
      .then(() => get<unknown>(CLIENTS_PATH))
      .then((raw) => {
        if (!alive.current || mine !== seq.current) return;
        const got = decodeClientsList(raw);
        if (!got) {
          setFailed({ status: 500, message: "" });
          return;
        }
        setData(got);
        setFailed(null);
      })
      .catch((e: unknown) => {
        if (!alive.current || mine !== seq.current) return;
        setFailed(failureOf(e));
      });
  }, []);

  useEffect(() => {
    alive.current = true;
    read();
    return () => {
      alive.current = false;
    };
  }, [read]);

  async function press(run: () => Promise<string>, action: string) {
    if (busy) return;
    setBusy(true);
    setNote(null);
    try {
      const words = await run();
      if (!alive.current) return;
      setNote({ words, failed: false });
      read();
    } catch (e) {
      if (!alive.current) return;
      setNote({ words: pressFailed(failureOf(e), action), failed: true });
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  const add = () =>
    press(async () => {
      const body: Record<string, string> = { folder: folder.trim() };
      if (name.trim()) body.name = name.trim();
      const made = decodeAnswer(await post<unknown>(CLIENTS_PATH, body));
      setName("");
      setFolder("");
      return [afterYes(made.client?.name ?? (name.trim() || folder.trim())), made.note].filter(Boolean).join(" ");
    }, "add the client");

  const lookAgain = () =>
    press(async () => lookedAgainWords(decodeNotice(await post<unknown>(NOTICE_PATH, {}))), "look again");

  const bringBack = (d: DeclinedName) =>
    press(async () => {
      const res = decodeAnswer(await post<unknown>(clientVerbPath(d.id, d.wasClient ? "restore" : "ask-again"), {}));
      return d.wasClient
        ? [afterYes(res.client?.name ?? d.name), res.note].filter(Boolean).join(" ")
        : `${d.name} is waiting for your answer again.`;
    }, d.wasClient ? "make it a client again" : "ask again");

  if (failed && !data) {
    return <LoadFailedLine thing="Your clients" error={failed} onRetry={read} testId="clients-failed" />;
  }
  if (!data) return <p className="py-2 text-sm text-zinc-500">Reading your clients…</p>;

  return (
    <div className="space-y-8" data-testid="client-list">
      <p
        role="status"
        data-testid="clients-note"
        className={`text-[13px] ${note ? "py-1" : ""} ${note?.failed ? "text-tone-danger" : "text-zinc-400"}`}
      >
        {note?.words ?? ""}
      </p>
      {failed && (
        <LoadFailedLine thing="The newest list" error={failed} onRetry={read} testId="clients-stale" />
      )}

      <ClientReview waiting={data.waiting} noticing={data.noticing} onChanged={read} />

      <section aria-labelledby="your-clients-heading" data-testid="your-clients">
        <h2 id="your-clients-heading" className="text-[15px] font-medium text-zinc-100">
          {YOUR_CLIENTS}
        </h2>
        {data.clients.length === 0 ? (
          <p className="py-1 text-[13px] text-zinc-500" data-testid="clients-empty">
            {CLIENTS_EMPTY}
          </p>
        ) : (
          <ul className="mt-2 divide-y divide-white/5">
            {data.clients.map((c) => (
              <li key={c.id} data-testid="client-row" className="py-2">
                <Link href={clientHref(c.id)} className="text-sm text-zinc-100 underline-offset-2 hover:underline">
                  {c.name}
                </Link>
                <p className="text-[12px] text-zinc-500">{clientRowLine(c)}</p>
                {c.folders.length > 0 && (
                  <p className="truncate text-[12px] text-zinc-500" title={c.folders.map((f) => f.folder).join("\n")}>
                    {c.folders
                      .slice(0, FOLDERS_SHOWN)
                      .map((f) => f.name)
                      .join(", ")}
                    {c.folders.length > FOLDERS_SHOWN ? `, and ${c.folders.length - FOLDERS_SHOWN} more` : ""}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="add-client-heading" data-testid="client-add">
        <h2 id="add-client-heading" className="text-[15px] font-medium text-zinc-100">
          {ADD_A_CLIENT}
        </h2>
        <p className="mt-1 max-w-2xl text-[13px] text-zinc-500">{ADD_ABOUT}</p>
        <form
          className="mt-2 flex max-w-2xl flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (folder.trim()) void add();
          }}
        >
          <input
            aria-label={ADD_FOLDER_LABEL}
            placeholder={ADD_FOLDER_LABEL}
            data-testid="client-add-folder"
            value={folder}
            maxLength={600}
            disabled={busy}
            onChange={(e) => setFolder(e.target.value)}
            className="min-w-[16rem] flex-1 rounded border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[13px] text-zinc-200 outline-none focus:border-accent/40"
          />
          <input
            aria-label={ADD_NAME_LABEL}
            placeholder={ADD_NAME_LABEL}
            data-testid="client-add-name"
            value={name}
            maxLength={80}
            disabled={busy}
            onChange={(e) => setName(e.target.value)}
            className="min-w-[10rem] rounded border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[13px] text-zinc-200 outline-none focus:border-accent/40"
          />
          <button type="submit" data-testid="client-add-press" disabled={busy || !folder.trim()} className="btn-ghost btn-sm">
            {ADD_A_CLIENT}
          </button>
        </form>
      </section>

      <div>
        <button
          type="button"
          data-testid="client-look-again"
          disabled={busy || !data.noticing}
          onClick={() => void lookAgain()}
          className="btn-ghost btn-sm"
        >
          {LOOK_AGAIN_NOW}
        </button>
      </div>

      {data.declined.length > 0 && (
        <section aria-labelledby="not-clients-heading" data-testid="clients-declined">
          <h2 id="not-clients-heading" className="text-[13px] font-medium text-zinc-300">
            {NOT_CLIENTS_HEADING} ({data.declined.length})
          </h2>
          <ul className="mt-1 space-y-1">
            {data.declined.map((d) => (
              <li key={d.id} data-testid="client-declined" className="flex flex-wrap items-baseline gap-x-3 text-[13px]">
                <span className="min-w-0 truncate text-zinc-300">{d.name}</span>
                <button
                  type="button"
                  data-testid="client-bring-back"
                  aria-label={`${d.wasClient ? MAKE_IT_A_CLIENT_AGAIN : ASK_AGAIN}: ${d.name}`}
                  disabled={busy}
                  onClick={() => void bringBack(d)}
                  className="shrink-0 text-accent underline-offset-2 hover:underline disabled:opacity-40 focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent"
                >
                  {d.wasClient ? MAKE_IT_A_CLIENT_AGAIN : ASK_AGAIN}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
