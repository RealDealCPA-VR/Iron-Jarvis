"use client";

/**
 * "Your clients" on the Memory page (v1.499.0, vision plan leap 3, Phase 3),
 * between what Iron Jarvis knows about you and your work by folder.
 *
 * One read of `GET /clients` when the section mounts: how many names wait for
 * an answer (with a link to the review), then one row per client. Read-only;
 * the answers are given on the Clients page. An older Iron Jarvis (404)
 * draws nothing; a read that failed is said, never shown as "no clients".
 *
 * v1.499.1: an answer that is not a client list (a daemon mid-restart) is a
 * failed read too, with Try again. It used to hide the section until the
 * page was reloaded, Try again included.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { get } from "@/lib/api";
import { failureOf, type FailureLike } from "@/lib/loadFailed";
import {
  CLIENTS_HREF,
  CLIENTS_PATH,
  LOOK_AT_THEM,
  MEMORY_EMPTY,
  WAITING_HREF,
  YOUR_CLIENTS,
  clientHref,
  clientRowLine,
  decodeClientsList,
  waitingLine,
  type ClientsList,
} from "@/lib/clients";
import { LoadFailedLine } from "@/components/LoadFailedLine";

/** Client rows the section shows before "All clients". */
export const CLIENTS_SHOWN = 8;

export function YourClients() {
  const [data, setData] = useState<ClientsList | null>(null);
  const [failed, setFailed] = useState<FailureLike | null>(null);
  const [hidden, setHidden] = useState(false);
  const alive = useRef(true);

  const read = useCallback(() => {
    Promise.resolve()
      .then(() => get<unknown>(CLIENTS_PATH))
      .then((raw) => {
        if (!alive.current) return;
        const got = decodeClientsList(raw);
        if (!got) {
          // Not a client list: said, with Try again (never nothing).
          setFailed({ status: 200, message: "The answer was not a client list." });
          return;
        }
        setData(got);
        setFailed(null);
      })
      .catch((e: unknown) => {
        if (!alive.current) return;
        // Read off the failure's own status, never `instanceof` (many tests
        // mock lib/api whole).
        const f = failureOf(e);
        if (f.status === 404) setHidden(true);
        else setFailed(f);
      });
  }, []);

  useEffect(() => {
    alive.current = true;
    read();
    return () => {
      alive.current = false;
    };
  }, [read]);

  if (hidden) return null;

  return (
    <section className="mt-6" aria-labelledby="your-clients-heading" data-testid="your-clients-memory">
      <h2 id="your-clients-heading" className="text-[15px] font-medium text-zinc-100">
        <Link href={CLIENTS_HREF} className="underline-offset-2 hover:underline">
          {YOUR_CLIENTS}
        </Link>
      </h2>
      {failed ? (
        <LoadFailedLine thing="Your clients" error={failed} onRetry={read} testId="your-clients-failed" />
      ) : data === null ? (
        <p className="py-2 text-sm text-zinc-500">Reading your clients…</p>
      ) : data.clients.length === 0 && data.waiting.length === 0 ? (
        <p className="py-2 max-w-2xl text-[13px] text-zinc-500" data-testid="your-clients-empty">
          {MEMORY_EMPTY}
        </p>
      ) : (
        <>
          {data.waiting.length > 0 && (
            <p className="mt-1 text-[13px] text-zinc-400" data-testid="your-clients-waiting">
              {waitingLine(data.waiting.length)}{" "}
              <Link href={WAITING_HREF} className="text-accent underline-offset-2 hover:underline">
                {LOOK_AT_THEM}
              </Link>
            </p>
          )}
          {data.clients.length > 0 && (
            <ul className="mt-2 divide-y divide-white/5">
              {data.clients.slice(0, CLIENTS_SHOWN).map((c) => (
                <li key={c.id} data-testid="your-client" className="py-1.5">
                  <Link href={clientHref(c.id)} className="text-sm text-zinc-100 underline-offset-2 hover:underline">
                    {c.name}
                  </Link>
                  <span className="ml-2 text-[12px] text-zinc-500">{clientRowLine(c)}</span>
                </li>
              ))}
            </ul>
          )}
          {data.clients.length > CLIENTS_SHOWN && (
            <p className="pt-1 text-[12px] text-zinc-500">
              <Link href={CLIENTS_HREF} className="underline-offset-2 hover:underline">
                All {data.clients.length} clients
              </Link>
            </p>
          )}
        </>
      )}
    </section>
  );
}
