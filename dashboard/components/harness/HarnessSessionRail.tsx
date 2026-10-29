"use client";

/**
 * THE SESSION TAB (v1.404.0): one look at a coding session — where it works,
 * what answers it, how full the conversation is, the last thing the user
 * attached, what it changed, and what it left running in the background.
 *
 * The user's words, beside a screenshot of Claude Code's own session panel:
 * "add a sessions rail to the coding sessions, next to the other icons
 * shown, and have it display when selected information like the last
 * attachment". It is the seventh pane tab (HarnessPanes `PANE_TABS`).
 *
 * Every line is read from what the screen already holds (the session view,
 * its messages, the running reply's helpers) or the daemon's own answers
 * (the session's changes, its background programs). A fact nothing says is
 * left out, never guessed. The programs are read only while this tab is
 * shown, again every few seconds while one runs or the session works, and
 * only while the window is on screen.
 */

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { AtSign, FolderOpen, GitBranch, Loader2, Monitor, Paperclip, Sparkles, Square } from "lucide-react";
import { post } from "@/lib/api";
import { fetchTurnChangeSet } from "@/components/chat/ChangedFiles";
import { latestContext, decodeCompaction, meterTitle, offersCompact, readingIsBeforeSummary } from "@/lib/buildContext";
import { accountStatusLine, type BuildAccount } from "@/lib/buildAccounts";
import { decodeFolderRules } from "@/lib/turnReads";
import { formatTokens } from "@/lib/format";
import { isThumbImage, localMediaSrc } from "@/lib/mediaLightbox";
import { PERMISSION_LEVELS, asPermissionMode } from "@/lib/permissionLevels";
import { levelWords } from "@/lib/reasoningChip";
import { REVIEWER_WORDS, TEAM_LEAD_WORDS, WORKERS_WORDS } from "@/lib/sessionTeam";
import { helperName, type HelperView } from "@/lib/buildHelpers";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import { useNow } from "@/components/chat/TurnClock";
import type { BuildMessage, BuildSessionView } from "@/lib/buildSessions";
import {
  CHANGES_POLL_MS,
  LOCAL_ONLY_LINE,
  PROGRAMS_POLL_MS,
  baseName,
  branchWords,
  earlierAttachments,
  fetchPrograms,
  helpersWorking,
  instructionWords,
  lastAttachment,
  openablePath,
  othersWords,
  programWords,
  sentWhen,
  stopProgram,
  type SessionProgram,
} from "@/lib/sessionRail";

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section data-testid={`session-rail-${id}`} className="space-y-1.5">
      <h3 className="text-[12px] font-medium text-zinc-400">{title}</h3>
      {children}
    </section>
  );
}

function Row({ icon, children, title }: { icon?: ReactNode; children: ReactNode; title?: string }) {
  return (
    <div className="flex min-w-0 items-start gap-2 text-[13px] text-zinc-300" title={title}>
      {icon ? <span className="mt-0.5 shrink-0 text-zinc-500">{icon}</span> : null}
      <div className="min-w-0 flex-1 break-words">{children}</div>
    </div>
  );
}

const QUIET = "text-[12px] text-zinc-500";
const PRESS =
  "shrink-0 rounded-md px-1.5 py-0.5 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:opacity-50";

/** The newest reply's instruction files (its `folderRules`), if any. */
function newestRules(messages: readonly BuildMessage[]): string[] {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i];
    if (m.role !== "assistant") continue;
    const names = decodeFolderRules(m.folderRules);
    if (names.length) return names;
    if (m.content || m.error) return [];
  }
  return [];
}

const HELPER_STATE_WORDS: Record<string, string> = {
  starting: "starting",
  queued: "waiting to start",
  running: "working",
  waiting: "waiting for you",
};

export function SessionRail({
  view,
  messages,
  modelWords,
  account = null,
  helpers = [],
  busy = false,
  refreshKey,
  onOpenChanges,
}: {
  view: BuildSessionView;
  /** The conversation as shown, the question being sent included. */
  messages: readonly BuildMessage[];
  /** The model as the box's chip names it ("Opus 5.5"). */
  modelWords: string;
  /** The session's account (the cached snapshot's row), or null. */
  account?: BuildAccount | null;
  /** The running reply's helper agents. */
  helpers?: readonly HelperView[];
  /** A reply is running. */
  busy?: boolean;
  /** Bumped when a turn ends: the changes and programs are read again. */
  refreshKey: number;
  /** Open the Changes tab. */
  onOpenChanges: () => void;
}) {
  const [opening, setOpening] = useState<string | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);

  async function openPath(path: string) {
    setOpening(path);
    setOpenError(null);
    try {
      await post("/documents/open", { path });
    } catch (e) {
      setOpenError(e instanceof Error ? e.message : String(e));
    } finally {
      setOpening(null);
    }
  }

  return (
    <div data-testid="pane-session" className="space-y-5 pb-4">
      <WhereItWorks view={view} messages={messages} opening={opening} onOpen={(p) => void openPath(p)} />
      <WhatAnswers view={view} modelWords={modelWords} account={account} />
      <HowFull view={view} />
      <LastAttachmentSection messages={messages} opening={opening} onOpen={(p) => void openPath(p)} />
      {openError ? (
        <p role="alert" className="text-[12px] text-tone-danger">
          {openError}
        </p>
      ) : null}
      <ChangesSummary
        view={view}
        messages={messages}
        busy={busy}
        refreshKey={refreshKey}
        onOpenChanges={onOpenChanges}
      />
      <Background view={view} helpers={helpers} busy={busy} refreshKey={refreshKey} />
    </div>
  );
}

function WhereItWorks({
  view,
  messages,
  opening,
  onOpen,
}: {
  view: BuildSessionView;
  messages: readonly BuildMessage[];
  opening: string | null;
  onOpen: (path: string) => void;
}) {
  const rules = instructionWords(newestRules(messages));
  return (
    <Section id="where" title="Where it works">
      <Row icon={<Monitor size={14} aria-hidden />}>
        This computer
        {view.setup.localOnly ? (
          <span data-testid="session-rail-local-only" className="block text-[12px] text-zinc-500">
            {LOCAL_ONLY_LINE}
          </span>
        ) : null}
      </Row>
      <Row icon={<FolderOpen size={14} aria-hidden />} title={view.folder || undefined}>
        {view.folder ? (
          <span className="flex min-w-0 items-start gap-2">
            <span data-testid="session-rail-folder" className="min-w-0 flex-1 break-words">
              {view.noFolder ? "Its own folder" : view.folderName}
            </span>
            <button
              type="button"
              data-testid="session-rail-open-folder"
              disabled={opening === view.folder}
              onClick={() => onOpen(view.folder)}
              className={PRESS}
            >
              Open
            </button>
          </span>
        ) : (
          <span data-testid="session-rail-folder">Its own folder is made with your first message.</span>
        )}
      </Row>
      {view.noFolder && !view.folder ? null : (
        <Row icon={<GitBranch size={14} aria-hidden />}>
          <span data-testid="session-rail-branch">{branchWords(view.git)}</span>
        </Row>
      )}
      {rules ? (
        <p data-testid="session-rail-rules" className={QUIET}>
          {rules}
        </p>
      ) : null}
    </Section>
  );
}

function WhatAnswers({
  view,
  modelWords,
  account,
}: {
  view: BuildSessionView;
  modelWords: string;
  account: BuildAccount | null;
}) {
  const level = PERMISSION_LEVELS[asPermissionMode(view.setup.approvalMode)];
  const effort = view.setup.reasoning ? levelWords(view.setup.reasoning) : "";
  const usage = account ? accountStatusLine(account) : null;
  const team = view.team;
  return (
    <Section id="answers" title="What answers">
      <Row>
        <span data-testid="session-rail-model">
          {modelWords || "The default model"}
          {effort ? <span className="text-zinc-500"> · reasoning {effort.toLowerCase()}</span> : null}
        </span>
      </Row>
      {account ? (
        <p data-testid="session-rail-account" className={QUIET}>
          Account {account.name}
          {account.planLabel ? ` · ${account.planLabel}` : ""}
          {usage?.text ? ` · ${usage.text}` : ""}
        </p>
      ) : null}
      <p data-testid="session-rail-approvals" className={QUIET} title={level.description}>
        {level.label}
        {view.mode === "plan" ? " · plans first, for you to approve" : ""}
      </p>
      {team ? (
        <div data-testid="session-rail-team" className={`${QUIET} space-y-0.5`}>
          {team.lead ? (
            <p>
              {TEAM_LEAD_WORDS}: {team.lead.label || team.lead.model}
            </p>
          ) : null}
          {team.workers.length ? (
            <p>
              {WORKERS_WORDS}: {team.workers.map((w) => w.label || w.name).join(", ")}
            </p>
          ) : null}
          {team.reviewer ? (
            <p>
              {REVIEWER_WORDS}: {team.reviewer.label || team.reviewer.name}
            </p>
          ) : null}
        </div>
      ) : null}
    </Section>
  );
}

function HowFull({ view }: { view: BuildSessionView }) {
  const reading = latestContext(view.messages);
  const compaction = decodeCompaction(view.compaction);
  const summarized = !!compaction && readingIsBeforeSummary(reading?.at ?? null, compaction);
  // A summary made AFTER the newest reading stands in for the turns it read:
  // that reading is out of date, so it is not shown (ContextMeter's rule).
  if (summarized) {
    return (
      <Section id="context" title="Context">
        <p data-testid="session-rail-summarized" className={QUIET}>
          Earlier turns are summarized; the model reads the summary in their place. The next reply measures again.
        </p>
      </Section>
    );
  }
  if (!reading) {
    return (
      <Section id="context" title="Context">
        <p data-testid="session-rail-context-none" className={QUIET}>
          Measured after the first reply.
        </p>
      </Section>
    );
  }
  const c = reading.context;
  // The box's meter's own rule: the warning tone exactly when it offers
  // Compact (past the daemon's mark, and compaction not turned off).
  const warn = offersCompact(c, false);
  return (
    <Section id="context" title="Context">
      <div className="flex items-baseline justify-between gap-2 text-[13px]">
        <span data-testid="session-rail-context-percent" className={warn ? "text-tone-warn" : "text-zinc-200"}>
          {c.percent}% used
        </span>
        <span data-testid="session-rail-context-window" className={QUIET}>
          of {formatTokens(c.window)} tokens
        </span>
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-white/[0.08]" aria-hidden>
        <div
          data-testid="session-rail-context-bar"
          className={`h-full rounded-full ${warn ? "bg-tone-warn" : "bg-zinc-400"}`}
          style={{ width: `${Math.max(0, Math.min(100, c.percent))}%` }}
        />
      </div>
      <p className={QUIET}>{meterTitle(c)}</p>
    </Section>
  );
}

function LastAttachmentSection({
  messages,
  opening,
  onOpen,
}: {
  messages: readonly BuildMessage[];
  opening: string | null;
  onOpen: (path: string) => void;
}) {
  const last = lastAttachment(messages);
  const earlier = earlierAttachments(messages);
  if (!last) {
    return (
      <Section id="attachment" title="Last attachment">
        <p data-testid="session-rail-attachment-none" className={QUIET}>
          Nothing attached yet. Pictures and files you send, and files you pick with @, show here.
        </p>
      </Section>
    );
  }
  return (
    <Section id="attachment" title="Last attachment">
      {last.files.length ? (
        <div data-testid="session-rail-attachment-files" className="flex flex-wrap gap-2">
          {last.files.map((p, i) => {
            const name = baseName(p);
            const openable = openablePath(p);
            return isThumbImage(name) ? (
              <button
                key={`${p}-${i}`}
                type="button"
                title={openable ? `Open ${name}` : name}
                disabled={!openable || opening === p}
                onClick={() => openable && onOpen(openable)}
                className="rounded-lg focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-default"
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={localMediaSrc(p)}
                  alt={name}
                  data-testid="session-rail-attachment-thumb"
                  className="h-24 max-w-full rounded-lg object-cover"
                />
              </button>
            ) : (
              <span
                key={`${p}-${i}`}
                data-testid="session-rail-attachment-file"
                title={p}
                className="flex min-w-0 max-w-full items-center gap-1.5 rounded-lg bg-white/[0.06] py-1 pl-2 pr-1 text-[12px] text-zinc-300"
              >
                <Paperclip size={12} aria-hidden className="shrink-0" />
                <span className="truncate">{name}</span>
                {openable ? (
                  <button type="button" disabled={opening === p} onClick={() => onOpen(openable)} className={PRESS}>
                    Open
                  </button>
                ) : null}
              </span>
            );
          })}
        </div>
      ) : null}
      {last.picks.length || last.skill ? (
        <div className="flex flex-wrap gap-1.5">
          {last.skill ? (
            <span
              data-testid="session-rail-attachment-skill"
              className="flex items-center gap-1 rounded-lg bg-white/[0.06] px-2 py-1 text-[12px] text-zinc-300"
            >
              <Sparkles size={12} aria-hidden /> {last.skill}
            </span>
          ) : null}
          {last.picks.map((p, i) => (
            <span
              key={`${p}-${i}`}
              data-testid="session-rail-attachment-pick"
              title={p}
              className="flex min-w-0 max-w-full items-center gap-1 rounded-lg bg-white/[0.06] px-2 py-1 text-[12px] text-zinc-300"
            >
              <AtSign size={12} aria-hidden className="shrink-0" /> <span className="truncate">{baseName(p)}</span>
            </span>
          ))}
        </div>
      ) : null}
      <SentLine at={last.at} words={last.words} />
      {earlier.total ? (
        <details data-testid="session-rail-earlier" className="group">
          <summary className="cursor-pointer text-[12px] text-zinc-500 hover:text-zinc-300">
            Earlier uploads ({earlier.total})
          </summary>
          <ul className="mt-1.5 space-y-1">
            {earlier.paths.map((p) => (
              <li key={p} className="flex min-w-0 items-center gap-1.5 text-[12px] text-zinc-400" title={p}>
                <Paperclip size={12} aria-hidden className="shrink-0" />
                <span className="min-w-0 flex-1 truncate">{baseName(p)}</span>
                {openablePath(p) ? (
                  <button type="button" disabled={opening === p} onClick={() => onOpen(p)} className={PRESS}>
                    Open
                  </button>
                ) : null}
              </li>
            ))}
            {earlier.total > earlier.paths.length ? (
              <li className={QUIET}>and {earlier.total - earlier.paths.length} more</li>
            ) : null}
          </ul>
        </details>
      ) : null}
    </Section>
  );
}

/** When the attachment was sent and its first words. It re-reads the time
 *  itself, alone (useNow), so "Sent just now" turns into "Sent 2m ago" while
 *  the session sits idle — the rest of the tab does not re-render. */
function SentLine({ at, words }: { at: string | null; words: string }) {
  const now = useNow(!!at);
  const when = sentWhen(at, now);
  return (
    <p data-testid="session-rail-attachment-when" className={QUIET}>
      {[when, words ? `with “${words}”` : "with no words"].filter(Boolean).join(" ")}
    </p>
  );
}

function ChangesSummary({
  view,
  messages,
  busy,
  refreshKey,
  onOpenChanges,
}: {
  view: BuildSessionView;
  /** The shown conversation: a NEW session's first question is only here
   *  while its reply runs (the view is read again when the reply ends). */
  messages: readonly BuildMessage[];
  busy: boolean;
  refreshKey: number;
  onOpenChanges: () => void;
}) {
  const since = messages.find((m) => typeof m.at === "string" && m.at)?.at ?? null;
  const [count, setCount] = useState<{ files: number; more: boolean } | null>(null);
  const [error, setError] = useState(false);
  const [round, setRound] = useState(0);
  useEffect(() => {
    let live = true;
    if (!since) {
      setCount({ files: 0, more: false });
      return;
    }
    fetchTurnChangeSet({ since, sessionId: view.sessionId })
      .then((set) => {
        if (!live) return;
        setCount({ files: set.changes.length, more: set.more });
        setError(false);
      })
      .catch(() => {
        if (live) setError(true);
      });
    return () => {
      live = false;
    };
  }, [since, view.sessionId, refreshKey, round]);
  // While a reply works it may be changing files: the count follows it.
  useVisibleInterval(() => setRound((n) => n + 1), CHANGES_POLL_MS, busy);
  return (
    <Section id="changes" title="Changes">
      <div className="flex items-center gap-2">
        <p data-testid="session-rail-changes-count" className="min-w-0 flex-1 text-[13px] text-zinc-300">
          {error
            ? "The changes could not be read."
            : count === null
              ? "Reading…"
              : count.files === 0
                ? "No files changed yet."
                : `${count.files}${count.more ? "+" : ""} file${count.files === 1 && !count.more ? "" : "s"} changed in this session`}
        </p>
        <button type="button" data-testid="session-rail-open-changes" onClick={onOpenChanges} className={PRESS}>
          See changes
        </button>
      </div>
    </Section>
  );
}

function Background({
  view,
  helpers,
  busy,
  refreshKey,
}: {
  view: BuildSessionView;
  helpers: readonly HelperView[];
  busy: boolean;
  refreshKey: number;
}) {
  const [programs, setPrograms] = useState<SessionProgram[] | null>(null);
  const [others, setOthers] = useState(0);
  const [readError, setReadError] = useState<string | null>(null);
  const [stopError, setStopError] = useState<string | null>(null);
  const [stopping, setStopping] = useState<string | null>(null);
  const read = useCallback(() => {
    fetchPrograms(view.id)
      .then((got) => {
        setPrograms(got.programs);
        setOthers(got.others);
        setReadError(null);
      })
      .catch((e: unknown) => {
        // An older Iron Jarvis has no such read (404): nothing to list. Any
        // other failure is SAID — never read as "nothing is running".
        if ((e as { status?: unknown } | null)?.status === 404) {
          setPrograms((prev) => prev ?? []);
          setReadError(null);
        } else {
          setReadError(e instanceof Error ? e.message : String(e));
        }
      });
  }, [view.id]);
  useEffect(() => {
    read();
  }, [read, refreshKey]);
  const anyRunning = !!programs?.some((p) => p.running) || others > 0;
  useVisibleInterval(read, PROGRAMS_POLL_MS, anyRunning || busy);

  async function stop(id: string) {
    setStopping(id);
    setStopError(null);
    try {
      const row = await stopProgram(view.id, id);
      if (row) setPrograms((prev) => (prev ?? []).map((p) => (p.id === row.id ? row : p)));
      else read();
    } catch (e) {
      // Kept apart from the read's own error: the read that follows must not
      // wipe the reason a Stop failed.
      setStopError(e instanceof Error ? e.message : String(e));
      read();
    } finally {
      setStopping(null);
    }
  }

  const working = helpers.filter((h) => HELPER_STATE_WORDS[h.state]);
  const listed = programs ?? [];
  const nothing = programs !== null && listed.length === 0 && others === 0 && helpersWorking(helpers) === 0;
  return (
    <Section id="background" title="Background">
      {working.length ? (
        <ul data-testid="session-rail-helpers" className="space-y-1">
          {working.map((h) => (
            <li key={h.id} className="text-[13px] text-zinc-300">
              {helperName(h.agent)} <span className="text-zinc-500">· {HELPER_STATE_WORDS[h.state]}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {programs === null ? (
        readError ? null : (
          <p className={`${QUIET} flex items-center gap-1.5`}>
            <Loader2 size={12} className="animate-spin" aria-hidden /> Reading…
          </p>
        )
      ) : nothing ? (
        <p data-testid="session-rail-background-none" className={QUIET}>
          Nothing it started is running in the background.
        </p>
      ) : listed.length === 0 ? null : (
        <ul data-testid="session-rail-programs" className="space-y-1.5">
          {listed.map((p) => (
            <li key={p.id} data-testid="session-rail-program" className="flex min-w-0 items-start gap-2">
              <div className="min-w-0 flex-1">
                <p className="truncate font-mono text-[12px] text-zinc-300" title={p.command}>
                  {p.command || p.id}
                </p>
                <p className={`text-[12px] ${p.running ? "text-zinc-400" : "text-zinc-500"}`}>{programWords(p)}</p>
              </div>
              {p.running ? (
                <button
                  type="button"
                  data-testid="session-rail-program-stop"
                  aria-label={`Stop ${p.command || p.id}`}
                  title="Stop it and everything it started"
                  disabled={stopping === p.id}
                  onClick={() => void stop(p.id)}
                  className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:opacity-50"
                >
                  {stopping === p.id ? <Loader2 size={12} className="animate-spin" /> : <Square size={11} aria-hidden />}
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {others > 0 ? (
        <p data-testid="session-rail-others" className={QUIET}>
          {othersWords(others)}
        </p>
      ) : null}
      {readError ? (
        <p data-testid="session-rail-background-error" role="alert" className="text-[12px] text-tone-danger">
          The background programs could not be read: {readError}
        </p>
      ) : null}
      {stopError ? (
        <p data-testid="session-rail-stop-error" role="alert" className="text-[12px] text-tone-danger">
          Stop did not go through: {stopError}
        </p>
      ) : null}
    </Section>
  );
}
