"use client";

// AN OLD ROOM, READ-ONLY (v1.308.0). The round table is gone from the Agents
// page, but every link that pointed at a room still exists — the command
// palette's history hits, chat's "open in Agents" under an @-mention reply, old
// bookmarks — and a conversation is part of the record. So `?thread=<id>`
// shows that conversation as it was: who spoke, what they said, honest errors
// included. Nothing here starts a round; new team work is a New task.

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { ApiError, get } from "@/lib/api";
import AgentFace from "@/components/agents/AgentFace";
import { Markdown } from "@/components/Markdown";
import { clock, missionPath } from "@/lib/mission";
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
}

export function RoomTranscript({ thread, onRail }: { thread: string; onRail: (target: RailTarget) => void }) {
  const [room, setRoom] = useState<Room | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    get<Room>(`/agents/threads/${encodeURIComponent(thread)}`)
      .then((r) => alive && setRoom(r))
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
  }, [thread]);

  const names = new Map<string, string>();
  for (const p of room?.participants ?? []) {
    const key = p.key || `${p.source}:${p.name}`;
    names.set(key, p.name || key);
  }
  const project = (room?.project_id || "").trim();

  return (
    <div data-testid="room-transcript" className="grid grid-cols-1 gap-3 lg:grid-cols-[12.5rem_minmax(0,1fr)] lg:items-start">
      <div className="lg:sticky lg:top-3">
        <MissionRail active={null} onAction={onRail} />
      </div>
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
        <div className="space-y-4 px-5 py-4">
          {error ? (
            <p className="text-[13px] text-zinc-400">{error}</p>
          ) : !room ? (
            <p className="text-[13px] text-zinc-500">Loading…</p>
          ) : (room.messages ?? []).length === 0 ? (
            <p className="text-[13px] text-zinc-500">Nothing was said here.</p>
          ) : (
            (room.messages ?? []).map((m, i) => {
              const who = m.who || "user";
              const isUser = who === "user";
              const label = isUser ? "You" : names.get(who) || who.split(":").pop() || who;
              return (
                <article key={`${m.at ?? ""}-${i}`} data-testid="room-entry" className="flex gap-3">
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
                      <span
                        className={`text-[13px] font-semibold text-zinc-100 ${
                          who.includes(":") ? "" : "capitalize"
                        }`}
                      >
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
            })
          )}
        </div>
      </section>
    </div>
  );
}
