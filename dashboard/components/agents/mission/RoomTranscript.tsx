"use client";

// AN OLD ROOM, READ-ONLY (v1.308.0). The round table is gone from the Agents
// page, but every link that pointed at a room still exists — the command
// palette's history hits, chat's "open in Agents" under an @-mention reply, old
// bookmarks — and a conversation is part of the record. So `?thread=<id>`
// shows that conversation as it was: who spoke, what they said, honest errors
// included. Nothing here starts a round; new team work is a New task.
//
// 48-b (idea 48, "Scroll back forever"): a room keeps its newest 400 messages
// live and, from this release, MOVES the rest to its older messages instead
// of cutting them; `GET /agents/threads/{id}` says how many (`older`). A long
// room has the chat's own quiet "Earlier messages" line at its top
// (components/chat/OlderMessages); pressing it loads the 50 before, back to
// the room's first message. This page opens at its top, so the line is in
// view at once: it loads only when pressed, and opening reads nothing older.

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { ApiError, get } from "@/lib/api";
import AgentFace from "@/components/agents/AgentFace";
import { Markdown } from "@/components/Markdown";
import { OlderMessages } from "@/components/chat/OlderMessages";
import { clock, missionPath } from "@/lib/mission";
import {
  OLDER_WORDS,
  decodeOlderPage,
  hasEarlier,
  olderFailed,
  olderLoading,
  olderStart,
  roomOlderPagePath,
  withEarlierPage,
  type OlderState,
} from "@/lib/olderMessages";
import { MissionRail, type RailTarget } from "./MissionRail";

interface Participant {
  key?: string;
  source?: string;
  name?: string;
  role?: string;
}
interface Entry {
  who?: string;
  role?: string;
  content?: string;
  error?: string;
  at?: string;
}
interface Room {
  id: string;
  title?: string;
  participants?: Participant[];
  messages?: Entry[];
  project_id?: string;
  /** 48-b: how many earlier messages the room keeps before `messages[0]`. */
  older?: number;
}

/** A room's message as this page reads one (a speaker, or words): the kept,
 *  earlier ones of a long room are the same entries. */
function isEntry(m: object): boolean {
  const e = m as Entry;
  return typeof e.who === "string" || typeof e.content === "string" || typeof e.error === "string";
}

/** The line comes into view at once (the page opens at its top): reaching it
 *  reads nothing; only a press loads. */
const NO_REACH = () => {};

function EntryRow({ m, names }: { m: Entry; names: Map<string, string> }) {
  const who = m.who || "user";
  const isUser = who === "user";
  const label = isUser ? "You" : names.get(who) || who.split(":").pop() || who;
  return (
    <article data-testid="room-entry" className="flex gap-3">
      {isUser ? (
        <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-accent/20 text-[11px] font-semibold text-accent">
          You
        </span>
      ) : (
        <AgentFace name={label} size={28} title="" />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          {/* v1.316.0: a custom/remote speaker ("custom:x") reads
              as typed; a built-in id is title-cased by CSS. */}
          <span className={`text-[13px] font-semibold text-zinc-100 ${who.includes(":") ? "" : "capitalize"}`}>
            {label}
          </span>
          <span className="text-[11px] text-zinc-500">{clock(m.at ?? null)}</span>
        </div>
        {m.content ? (
          <div className="text-[14px] leading-relaxed text-zinc-300">
            <Markdown content={m.content} />
          </div>
        ) : null}
        {m.error ? <p className="text-[12px] text-tone-danger">{m.error}</p> : null}
      </div>
    </article>
  );
}

export function RoomTranscript({ thread, onRail }: { thread: string; onRail: (target: RailTarget) => void }) {
  const [room, setRoom] = useState<Room | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [older, setOlderState] = useState<OlderState<Entry>>(() => olderStart<Entry>("", 0));
  const olderRef = useRef(older);
  const setOlder = useCallback((next: OlderState<Entry>) => {
    olderRef.current = next;
    setOlderState(next);
  }, []);
  useEffect(() => {
    let alive = true;
    setOlder(olderStart<Entry>("", 0));
    get<Room>(`/agents/threads/${encodeURIComponent(thread)}`)
      .then((r) => {
        if (!alive) return;
        setRoom(r);
        setOlder(olderStart<Entry>(thread, r?.older));
      })
      .catch((e) => {
        if (!alive) return;
        setError(
          e instanceof ApiError && e.status === 404
            ? "This conversation no longer exists."
            : "Could not load this conversation.",
        );
      });
    return () => {
      alive = false;
    };
  }, [thread, setOlder]);

  // 48-b: the 50 just above the messages held. A read for a room this page
  // no longer shows, or for a position it no longer starts at, lands nowhere.
  const loadEarlier = useCallback(() => {
    const s = olderRef.current;
    if (s.threadId !== thread || s.loading || !hasEarlier(s)) return;
    const before = s.loadedFrom;
    setOlder(olderLoading(s));
    get<unknown>(roomOlderPagePath(thread, before))
      .then((raw) => decodeOlderPage<Entry>(raw, isEntry))
      .catch(() => null)
      .then((page) => {
        const cur = olderRef.current;
        if (cur.threadId !== thread) return;
        setOlder(page ? withEarlierPage(cur, before, page) : olderFailed(cur));
      });
  }, [thread, setOlder]);

  const names = useMemo(() => {
    const out = new Map<string, string>();
    for (const p of room?.participants ?? []) {
      const key = p.key || `${p.source}:${p.name}`;
      out.set(key, p.name || key);
    }
    return out;
  }, [room]);
  const renderOlder = useCallback((m: Entry) => <EntryRow m={m} names={names} />, [names]);
  const project = (room?.project_id || "").trim();
  const live = room?.messages ?? [];

  return (
    <div data-testid="room-transcript" className="space-y-3">
      <MissionRail active={null} onAction={onRail} />
      <section className="card-surface p-0">
        <header className="border-b hairline px-5 py-4">
          {project && (
            <Link
              href={missionPath("", project)}
              className="mb-2 inline-flex items-center gap-1 text-[12px] text-accent hover:underline"
            >
              <ArrowLeft size={12} /> Back to the project
            </Link>
          )}
          <h1 className="text-[15px] font-semibold text-zinc-100">{room?.title || "Conversation"}</h1>
          <p className="mt-1 text-[12px] text-zinc-500">
            A conversation from the old agent round table, kept as it was. To put the team to work, start a{" "}
            <button type="button" className="text-accent hover:underline" onClick={() => onRail("new")}>
              New task
            </button>
            .
          </p>
        </header>
        {/* A column of rows with a gap: the earlier rows (drawn inside a
            `display: contents` block) take the same gap as the live ones. */}
        <div className="flex flex-col gap-4 px-5 py-4">
          {error ? (
            <p className="text-[13px] text-zinc-400">{error}</p>
          ) : !room ? (
            <p className="text-[13px] text-zinc-500">Loading…</p>
          ) : live.length === 0 ? (
            <p className="text-[13px] text-zinc-500">Nothing was said here.</p>
          ) : (
            <>
              <OlderMessages
                state={older}
                firstLiveAt={live[0]?.at}
                onReach={NO_REACH}
                onLoad={loadEarlier}
                renderRow={renderOlder}
                startText={OLDER_WORDS.roomStart}
                column="w-full min-w-0"
              />
              {live.map((m, i) => (
                <EntryRow key={`${m.at ?? ""}-${i}`} m={m} names={names} />
              ))}
            </>
          )}
        </div>
      </section>
    </div>
  );
}
