"use client";

// v1.279.0 — WHAT JARVIS KNOWS ABOUT YOU, at the top of the Memory page.
//
// The page opened on a search box over a store that is empty on most installs,
// so it never answered the one question a person opening it has: what do you
// actually remember about me? This card answers it from `/memory/overview`:
// the profile, the preferences Jarvis keeps (and reads into every
// conversation), how many notes and conversations it can search, and — when
// there is nothing yet — how to give it something, in one sentence typed in
// chat. Task reflections are counted but named for what they are: notes about
// past jobs, not knowledge about the user (they no longer reach the prompt).
//
// Silent on a daemon that cannot answer (an older daemon, a fetch failure):
// an empty card claiming "nothing known" would be a verdict nobody reached.
import Link from "next/link";
import { useEffect, useState } from "react";
import { BrainCircuit, UserRound } from "lucide-react";
import { get } from "@/lib/api";
import { Card } from "@/components/ui";

export interface MemoryOverview {
  profile: {
    filled: boolean;
    enabled: boolean;
    about_line: string;
    tone: string;
    writing_style: string;
  };
  preferences: { id: string; text: string; source: string; weight: number; created_at: string }[];
  lessons: { total: number; reflections: number; by_source: Record<string, number> };
  bases: { name: string; kind: string; notes: number | null }[];
  working: Record<string, number>;
  history: { docs: number; available: boolean };
  empty: boolean;
}

/** The word for a lesson's origin, as the user would say it. */
export function sourceWord(source: string): string {
  switch (source) {
    case "preference":
      return "you said so";
    case "feedback":
      return "from your feedback";
    case "distilled":
      return "learned over time";
    case "user":
      return "you wrote it";
    default:
      return source || "lesson";
  }
}

/** One line of counts, only the parts that are non-zero. */
export function countsLine(o: MemoryOverview): string {
  const parts: string[] = [];
  const notes = o.bases.reduce((n, b) => n + (b.notes ?? 0), 0);
  const uncounted = o.bases.filter((b) => b.notes === null).length;
  if (notes > 0) parts.push(`${notes} note${notes === 1 ? "" : "s"}`);
  if (uncounted > 0) parts.push(`${uncounted} remote base${uncounted === 1 ? "" : "s"}`);
  const working = Object.values(o.working).reduce((n, v) => n + v, 0);
  if (working > 0) parts.push(`${working} working ${working === 1 ? "memory" : "memories"}`);
  if (o.history.available && o.history.docs > 0) {
    parts.push(`${o.history.docs} past conversation${o.history.docs === 1 ? "" : "s"} searchable`);
  }
  if (o.lessons.reflections > 0) {
    parts.push(
      `${o.lessons.reflections} task reflection${o.lessons.reflections === 1 ? "" : "s"} (notes about past jobs — not read into conversations)`,
    );
  }
  return parts.join(" · ");
}

export function KnowsAboutYou() {
  const [overview, setOverview] = useState<MemoryOverview | null>(null);

  useEffect(() => {
    let live = true;
    void (async () => {
      try {
        const data = await get<MemoryOverview>("/memory/overview");
        if (live && data && typeof data === "object" && Array.isArray(data.preferences)) {
          setOverview(data);
        }
      } catch {
        /* an older daemon or a failed fetch: say nothing rather than "nothing" */
      }
    })();
    return () => {
      live = false;
    };
  }, []);

  if (!overview) return null;

  const { profile, preferences } = overview;
  const counts = countsLine(overview);

  return (
    <Card title="What Jarvis knows about you" icon={<BrainCircuit size={14} />}>
      <div data-testid="knows-about-you" className="space-y-3">
        {/* The profile: the one paragraph that governs HOW every answer is written. */}
        <div className="flex flex-wrap items-start gap-2 text-[12.5px]">
          <UserRound size={14} className="mt-0.5 shrink-0 text-zinc-500" />
          {profile.filled ? (
            <p className="min-w-0 text-zinc-300">
              <span className="text-zinc-200">{profile.about_line || "Your profile is set."}</span>
              {(profile.tone || profile.writing_style) && (
                <span className="text-zinc-500">
                  {" "}
                  · {[profile.tone, profile.writing_style].filter(Boolean).join(", ")}
                </span>
              )}
              {!profile.enabled && <span className="text-amber-300"> · profile is switched off</span>}
              <Link href="/you" className="ml-2 text-accent-soft hover:underline">
                Edit
              </Link>
            </p>
          ) : (
            <p className="text-zinc-400">
              No profile yet.{" "}
              <Link href="/you" className="text-accent-soft hover:underline">
                Tell Jarvis who you are and how you like answers
              </Link>
              .
            </p>
          )}
        </div>

        {/* The preferences: read into every conversation, forgettable one by one. */}
        {preferences.length > 0 ? (
          <ul data-testid="knows-preferences" className="space-y-1">
            {preferences.map((p) => (
              <li key={p.id} className="flex items-start gap-2 text-[12.5px] text-zinc-300">
                <span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-accent/70" />
                <span className="min-w-0">
                  {p.text}
                  <span className="ml-1.5 text-[10.5px] uppercase tracking-wide text-zinc-600">
                    {sourceWord(p.source)}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p data-testid="knows-onramp" className="text-[12.5px] leading-relaxed text-zinc-400">
            Jarvis has not learned a preference yet. Say it in chat the way you would to a
            colleague — <span className="text-zinc-300">&ldquo;From now on, keep answers short&rdquo;</span>,{" "}
            <span className="text-zinc-300">&ldquo;Always give me numbered steps&rdquo;</span> — and it is kept
            here and used in every conversation. Forget any of them from the What I&apos;ve learned tab.
          </p>
        )}

        {counts && <p className="text-[11.5px] text-zinc-500">{counts}</p>}
      </div>
    </Card>
  );
}
