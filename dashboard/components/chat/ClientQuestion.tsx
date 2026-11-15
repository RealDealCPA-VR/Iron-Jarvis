"use client";

/**
 * v1.499.0 (vision plan leap 3, Phase 3): ONE quiet question under a reply,
 * when Iron Jarvis keeps seeing a name in the folders this chat works in.
 *
 *   "Iron Jarvis has seen Hartleys in 2 folders and 9 pieces of work. Is
 *    Hartleys a client?"  [Yes, a client] · [No]
 *
 * The preference line's family (components/chat/PreferenceSuggestion): the
 * same 11px type, the same accent, no modal, no focus taken. Each answer is
 * one press on the client's own route:
 *
 *   Yes → POST /clients/{id}/confirm → "Hartleys is a client now." + a link
 *         to "Hartleys' page"
 *   No  → POST /clients/{id}/decline → "Iron Jarvis won't ask about
 *         Hartleys again."
 *
 * A 404 or 409 (answered somewhere else, or gone) shows the route's own
 * sentence instead of the question. The answer is handed to `onSettle` so the
 * page stores it ON THE MESSAGE and a reopened chat shows the answer, never
 * the question again. The id in a path is only ever a client id (the decoder
 * drops anything else), never a name.
 */

import { useState } from "react";
import Link from "next/link";
import { Loader2, Users } from "lucide-react";
import { post } from "@/lib/api";
import { failureOf } from "@/lib/loadFailed";
import {
  NO,
  NOT_ON_RECORD,
  YES_A_CLIENT,
  afterNo,
  afterYes,
  alreadyAnswered,
  clientHref,
  clientVerbPath,
  decodeAnswer,
  pageWords,
  pressFailed,
  questionLine,
  type ChatClientQuestion,
} from "@/lib/clients";

export interface ClientQuestionProps {
  question: ChatClientQuestion;
  /** The owner answered: the page stores this on the message and saves. */
  onSettle?: (next: ChatClientQuestion) => void;
}

export function ClientQuestion({ question, onSettle }: ClientQuestionProps) {
  const [local, setLocal] = useState<ChatClientQuestion | null>(null);
  const [pending, setPending] = useState<"yes" | "no" | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The route's own sentence when the question was answered elsewhere.
  const [refusal, setRefusal] = useState<string | null>(null);
  // What the confirm said besides yes (a folder another client already has).
  const [note, setNote] = useState("");

  const q = local ?? question;
  const domId = `client-question-${q.id}`;

  async function answer(kind: "yes" | "no") {
    if (pending) return;
    setPending(kind);
    setError(null);
    try {
      const res = await post<unknown>(clientVerbPath(q.id, kind === "yes" ? "confirm" : "decline"), {});
      const got = decodeAnswer(res);
      const next: ChatClientQuestion = {
        ...q,
        name: kind === "yes" && got.client?.id === q.id ? got.client.name : q.name,
        state: kind,
      };
      setLocal(next);
      setNote(kind === "yes" ? got.note : "");
      onSettle?.(next);
    } catch (e) {
      const f = failureOf(e);
      if (f.status === 404 || f.status === 409) {
        const said = (f.message ?? "").trim();
        setRefusal(said || (f.status === 404 ? NOT_ON_RECORD : alreadyAnswered(q.name)));
        const next: ChatClientQuestion = { ...q, state: "gone" };
        setLocal(next);
        onSettle?.(next);
        return;
      }
      setError(pressFailed(f, "save your answer"));
    } finally {
      setPending(null);
    }
  }

  if (q.state === "gone") {
    return refusal ? (
      <div id={domId} data-testid="client-question" data-state="gone" className="mt-1 text-[11px] text-zinc-500">
        <span data-testid="client-question-refused">{refusal}</span>
      </div>
    ) : null;
  }
  if (q.state === "yes") {
    return (
      <div id={domId} data-testid="client-question" data-state="yes" className="mt-1 text-[11px] text-zinc-500">
        <span data-testid="client-question-yes" className="text-accent-soft">
          {afterYes(q.name)}
        </span>{" "}
        <Link
          href={clientHref(q.id)}
          data-testid="client-question-page"
          className="text-accent-soft underline decoration-dotted underline-offset-2 hover:text-zinc-200"
        >
          {pageWords(q.name)}
        </Link>
        {note && <span className="text-zinc-500"> {note}</span>}
      </div>
    );
  }
  if (q.state === "no") {
    return (
      <div id={domId} data-testid="client-question" data-state="no" className="mt-1 text-[11px] text-zinc-600">
        <span data-testid="client-question-no">{afterNo(q.name)}</span>
      </div>
    );
  }

  const busy = pending !== null;
  const action =
    "rounded px-1 py-px text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-accent-soft disabled:opacity-40";
  return (
    <div id={domId} data-testid="client-question" data-state="open" className="mt-1 text-[11px] text-zinc-500">
      <div className="inline-flex max-w-full flex-wrap items-center gap-x-1.5 gap-y-1">
        <Users size={10} className="shrink-0 text-accent-soft/70" aria-hidden="true" />
        <span className="min-w-0" data-testid="client-question-line">
          {questionLine(q)}
        </span>
        <span className="inline-flex items-center gap-0.5">
          <button
            type="button"
            data-testid="client-question-confirm"
            disabled={busy}
            onClick={() => void answer("yes")}
            className={action}
          >
            {pending === "yes" && <Loader2 size={10} className="mr-0.5 inline animate-spin" />}
            {YES_A_CLIENT}
          </button>
          <span aria-hidden="true">·</span>
          <button
            type="button"
            data-testid="client-question-decline"
            disabled={busy}
            onClick={() => void answer("no")}
            className={action}
          >
            {pending === "no" && <Loader2 size={10} className="mr-0.5 inline animate-spin" />}
            {NO}
          </button>
        </span>
      </div>
      {error && (
        <p data-testid="client-question-error" className="mt-1 text-[11px] text-tone-danger">
          {error}
        </p>
      )}
    </div>
  );
}
