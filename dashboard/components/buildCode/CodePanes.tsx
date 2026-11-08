"use client";

/**
 * BUILD › CODE'S RIGHT COLUMN (v1.414.0): Changes, Usage, Files and Session —
 * the same icon tabs Chat › Code's coding sessions have (HarnessPanes,
 * v1.401.2: a 32 px icon, its word a tip shown only on a resting pointer or
 * keyboard focus, and that word is the tab's accessible name). The user:
 * "Within the build module under the </> code section, there should be the
 * icons for changes, remaining usage, files and sessions to right just as in
 * the native harness off to the right."
 *
 * The REAL coding tool writes files itself, so the changes come from git (the
 * folder since the session started) or, with no repository, from the tool's
 * own record of its file edits — the pane says which, with its limits, and
 * offers no Undo. Usage is cached numbers only (Iron-Proxy's snapshot and
 * what the tool itself last reported). Each pane is mounted only while it is
 * shown, so its reads run only then; a read repeats only while the session
 * works and the window is on screen (useVisibleInterval).
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  AtSign,
  FileDiff,
  FolderOpen,
  Gauge,
  GitBranch,
  Loader2,
  Monitor,
  Network,
  Paperclip,
  RefreshCw,
  SquareMenu,
  type LucideIcon,
} from "lucide-react";
import { get, post } from "@/lib/api";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import { ChangesReview } from "@/components/harness/ChangesReview";
import { ConfirmButton } from "@/components/ui";
import { FilesPanel } from "@/components/terminal/FilesPanel";
import { Row, Section } from "@/components/harness/HarnessSessionRail";
import { UsageRefresh } from "@/components/accounts/UsageRefresh";
import { ContextBreakdown } from "@/components/harness/ContextBreakdown";
import { decodeCodeContext, totalReading, type CodeContext } from "@/lib/contextBreakdown";
import { BackgroundWorkList, McpReloadButton, McpRowMenu } from "@/components/buildCode/SessionControls";
import { isThumbImage, localMediaSrc } from "@/lib/mediaLightbox";
import { baseName, openablePath } from "@/lib/sessionRail";
import {
  accountWords,
  jarvisToolsWords,
  modeLabel,
  toolLabel,
  usageWords,
  type CodeEvent,
  type CodeSession,
  type CodingTool,
} from "@/lib/buildApp";
import {
  agoWords,
  branchLine,
  changesCountWords,
  contextNotKnownWords,
  decodeCodeChanges,
  decodeCodeOverview,
  decodeCodeUsage,
  lastCodeAttachment,
  noWindowsWords,
  REVERT_LABEL,
  REVERT_WAITS,
  revertBody,
  revertConfirmWords,
  SCOPE_WORDS,
  changesQuery,
  whereLine,
  windowLine,
  type ChangesScope,
  type CodeChange,
  type CodeChanges,
  type CodeMcp,
  type CodeOverview,
  type CodeUsage,
  type UsageWindow,
} from "@/lib/codePanes";

export type CodePaneTab = "changes" | "usage" | "files" | "session";

export const CODE_PANE_TABS: readonly { value: CodePaneTab; label: string; icon: LucideIcon }[] = [
  { value: "changes", label: "Changes", icon: FileDiff },
  { value: "usage", label: "Usage", icon: Gauge },
  { value: "files", label: "Files", icon: FolderOpen },
  { value: "session", label: "Session", icon: SquareMenu },
];

const TAB_KEY = "ij.code.pane";
/** How often a pane reads again while the session works. */
export const CODE_PANES_POLL_MS = 5_000;

const QUIET = "text-[12px] text-zinc-500";
const PRESS =
  "shrink-0 rounded-md px-1.5 py-0.5 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:opacity-50";

function readTab(): CodePaneTab {
  try {
    const v = window.localStorage.getItem(TAB_KEY);
    return CODE_PANE_TABS.some((t) => t.value === v) ? (v as CodePaneTab) : "changes";
  } catch {
    return "changes";
  }
}

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** One read of `/build/app/sessions/{id}/{part}`, again on `refresh` and every
 *  few seconds while `running` (only while the window is on screen). */
function usePaneRead<T>(
  sessionId: string,
  part: "changes" | "usage" | "overview" | "context",
  decode: (raw: unknown) => T | null,
  refresh: number,
  running: boolean,
  /** v1.420.0: the read's query (the Changes scope), "" for none. */
  query = "",
): { data: T | null; error: string | null; again: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  // v1.414.0 review: the read still out. A TIMED read waits for it (a slow git
  // read of a big repository must not pile reads up on the daemon); a press
  // or a reply's end reads again at once (the newest answer wins).
  const inFlight = useRef(0);
  const decodeRef = useRef(decode);
  decodeRef.current = decode;
  const again = useCallback(() => {
    const n = ++seq.current;
    inFlight.current = n;
    get(`/build/app/sessions/${encodeURIComponent(sessionId)}/${part}${query}`)
      .then((raw) => {
        if (n !== seq.current) return;
        setData(decodeRef.current(raw));
        setError(null);
      })
      .catch((e) => {
        if (n === seq.current) setError(errText(e));
      })
      .finally(() => {
        if (inFlight.current === n) inFlight.current = 0;
      });
  }, [sessionId, part, query]);
  useEffect(() => {
    again();
  }, [again, refresh]);
  const tick = useCallback(() => {
    if (inFlight.current === 0) again();
  }, [again]);
  useVisibleInterval(tick, CODE_PANES_POLL_MS, running);
  return { data, error, again };
}

export function CodePanes({
  session,
  tools,
  events,
  running,
  onSend,
}: {
  session: CodeSession;
  tools: readonly CodingTool[];
  /** The conversation's events (the last attachment is read from them). */
  events: readonly CodeEvent[];
  /** A reply is running. */
  running: boolean;
  /** v1.415.0: send a message as the user would (Claude Code's own
   *  `/compact`, from the Context drop-down). */
  onSend?: (text: string) => void;
}) {
  const [tab, setTab] = useState<CodePaneTab>("changes");
  const baseId = useId();
  useEffect(() => setTab(readTab()), []);
  // A reply that ended may have changed files: read again on its end.
  const [refresh, setRefresh] = useState(0);
  const wasRunning = useRef(running);
  useEffect(() => {
    if (wasRunning.current && !running) setRefresh((n) => n + 1);
    wasRunning.current = running;
  }, [running]);
  const choose = useCallback((t: CodePaneTab) => {
    setTab(t);
    try {
      window.localStorage.setItem(TAB_KEY, t);
    } catch {
      /* the choice lasts this visit */
    }
  }, []);
  const toolName = toolLabel(session.tool, tools);

  return (
    <div data-testid="code-panes" className="flex h-full min-h-0 flex-col gap-2">
      <div
        role="tablist"
        aria-label="Session views"
        data-testid="code-pane-tabs"
        className="flex shrink-0 flex-wrap items-center gap-x-1 gap-y-1 px-1"
      >
        {CODE_PANE_TABS.map((t) => {
          const selected = tab === t.value;
          const Icon = t.icon;
          return (
            <button
              key={t.value}
              type="button"
              role="tab"
              id={`${baseId}-tab-${t.value}`}
              data-testid={`code-pane-tab-${t.value}`}
              aria-selected={selected}
              aria-controls={`${baseId}-panel`}
              tabIndex={selected ? 0 : -1}
              onClick={() => choose(t.value)}
              onKeyDown={(e) => {
                if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
                e.preventDefault();
                const i = CODE_PANE_TABS.findIndex((x) => x.value === t.value);
                const n = CODE_PANE_TABS.length;
                const next = CODE_PANE_TABS[(i + (e.key === "ArrowRight" ? 1 : -1) + n) % n];
                choose(next.value);
                document.getElementById(`${baseId}-tab-${next.value}`)?.focus();
              }}
              className={`group relative inline-grid h-8 w-8 place-items-center rounded-md transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 ${
                selected ? "bg-white/[0.08] text-zinc-100" : "text-zinc-500 hover:bg-white/[0.04] hover:text-zinc-200"
              }`}
            >
              <Icon size={16} aria-hidden strokeWidth={selected ? 2.25 : 1.75} />
              {/* The word: shown only while the pointer rests here (after
                  300 ms) or the tab has keyboard focus; never removed — it is
                  the tab's name for a screen reader. */}
              <span
                data-testid={`code-pane-tip-${t.value}`}
                className="pointer-events-none absolute left-1/2 top-full z-30 mt-1 -translate-x-1/2 whitespace-nowrap rounded-md border border-white/10 bg-ink-900 px-2 py-0.5 text-[12px] text-zinc-200 opacity-0 shadow-lg shadow-black/30 transition-opacity duration-150 group-hover:opacity-100 group-hover:delay-300 group-focus-visible:opacity-100"
              >
                {t.label}
              </span>
            </button>
          );
        })}
      </div>
      <div
        id={`${baseId}-panel`}
        role="tabpanel"
        aria-labelledby={`${baseId}-tab-${tab}`}
        className="relative min-h-0 flex-1 overflow-y-auto px-1"
      >
        {tab === "changes" ? (
          <ChangesPane sessionId={session.id} toolName={toolName} refresh={refresh} running={running} />
        ) : null}
        {tab === "usage" ? (
          <UsagePane session={session} toolName={toolName} refresh={refresh} running={running} />
        ) : null}
        {tab === "files" ? (
          <FilesPanel
            variant="calm"
            folder={session.folder || null}
            noFolderText={
              session.noFolder
                ? "This session's folder is made with your first message, so it has no files yet."
                : "This session has no folder."
            }
          />
        ) : null}
        {tab === "session" ? (
          <SessionPaneTab
            session={session}
            tools={tools}
            toolName={toolName}
            events={events}
            refresh={refresh}
            running={running}
            onOpenChanges={() => choose("changes")}
            onSend={onSend}
          />
        ) : null}
      </div>
    </div>
  );
}

// ── Changes ─────────────────────────────────────────────────────────────────

function ChangesPane({
  sessionId,
  toolName,
  refresh,
  running,
}: {
  sessionId: string;
  toolName: string;
  refresh: number;
  running: boolean;
}) {
  // v1.420.0 (C4.12): "This message" beside "Whole session" (the whole
  // session stays the default — what the pane always listed).
  const [scope, setScope] = useState<ChangesScope>("session");
  const { data, error, again } = usePaneRead<CodeChanges>(
    sessionId, "changes", decodeCodeChanges, refresh, running, changesQuery(scope),
  );
  const [revertNote, setRevertNote] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => setRevertNote(null), [scope, sessionId]);
  async function revert(c: CodeChange) {
    if (!data) return;
    try {
      const res = (await post(
        `/build/app/sessions/${encodeURIComponent(sessionId)}/changes/revert`,
        revertBody(c, data),
      )) as { words?: unknown } | null;
      const words = res && typeof res.words === "string" ? res.words : "";
      setRevertNote({ ok: true, text: words || `${c.name || c.rel || c.path} was put back.` });
    } catch (e) {
      setRevertNote({ ok: false, text: errText(e) });
    }
    again();
  }
  return (
    <div data-testid="code-pane-changes" className="space-y-2 pb-4">
      <div role="group" aria-label="Which changes" className="flex items-center gap-1" data-testid="code-changes-scope">
        {(["session", "turn"] as const).map((s) => (
          <button
            key={s}
            type="button"
            data-testid={`code-changes-scope-${s}`}
            aria-pressed={scope === s}
            onClick={() => setScope(s)}
            className={`h-7 rounded-md px-2 text-[12px] transition-colors ${
              scope === s ? "bg-white/[0.08] text-zinc-100" : "text-zinc-500 hover:bg-white/[0.04] hover:text-zinc-200"
            }`}
          >
            {SCOPE_WORDS[s]}
          </button>
        ))}
      </div>
      <div className="flex items-start gap-2">
        <p data-testid="code-changes-source" className="min-w-0 flex-1 text-[12px] text-zinc-500">
          {data ? data.words : "What this session changed on disk."}
        </p>
        <button
          type="button"
          aria-label="Read the changes again"
          title="Read the changes again"
          onClick={again}
          className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
        >
          <RefreshCw size={13} />
        </button>
      </div>
      {error ? (
        <p role="alert" data-testid="code-changes-error" className="text-[12px] text-tone-danger">
          The changes could not be read: {error}
        </p>
      ) : !data ? (
        <p className="flex items-center gap-1.5 text-[12px] text-zinc-500">
          <Loader2 size={12} className="animate-spin" aria-hidden /> Reading the changes…
        </p>
      ) : data.source === "none" ? null : data.changes.length === 0 ? (
        <p data-testid="code-changes-none" className="text-[13px] text-zinc-400">
          No files changed yet.
        </p>
      ) : (
        <>
          {data.source === "git" && data.byTool > 0 ? (
            <p data-testid="code-changes-by-tool" className={QUIET} title={byToolTitle(data)}>
              {data.byTool} of these {data.byTool === 1 ? "was" : "were"} edited by {toolName}&apos;s own file tools.
            </p>
          ) : null}
          {/* The v1.405.0 review: every diff inline, Reviewed marks, Open in
              VS Code / Cursor — and no Undo (Iron Jarvis did not write these). */}
          <ChangesReview changes={data.changes} sessionId={`code:${sessionId}`} more={data.more} />
          {data.noUndo ? (
            <p data-testid="code-changes-no-undo" className={QUIET}>
              {data.noUndo}
            </p>
          ) : null}
          {data.canRevert ? (
            running ? (
              <p data-testid="code-changes-revert-waits" className={QUIET}>
                {REVERT_WAITS}
              </p>
            ) : (
              <ul data-testid="code-changes-revert" aria-label="Put a file back" className="space-y-1">
                {data.changes.map((c, i) => (
                  // v1.420.0 review: keyed by the SCOPE too — a press armed
                  // under one scope must never confirm under the other (the
                  // same file goes back to a different base there).
                  <li
                    key={`${data.scope}:${data.turn}:${c.path}`}
                    className="flex min-w-0 items-center gap-2 text-[12px] text-zinc-400"
                  >
                    <span className="min-w-0 flex-1 truncate" title={c.rel || c.path}>
                      {c.rel || c.name || c.path}
                    </span>
                    <span data-testid={`code-revert-${i}`}>
                      <ConfirmButton
                        label={REVERT_LABEL}
                        confirmLabel={revertConfirmWords(c.name || c.rel || c.path)}
                        title={`Put ${c.name || c.rel || c.path} back; every other file stays`}
                        onConfirm={() => revert(c)}
                      />
                    </span>
                  </li>
                ))}
              </ul>
            )
          ) : null}
        </>
      )}
      {revertNote ? (
        <p
          role={revertNote.ok ? "status" : "alert"}
          data-testid="code-changes-revert-note"
          className={revertNote.ok ? QUIET : "text-[12px] text-tone-danger"}
        >
          {revertNote.text}
        </p>
      ) : null}
    </div>
  );
}

function byToolTitle(c: CodeChanges): string {
  return c.changes
    .filter((x) => x.byTool)
    .map((x) => x.rel || x.path)
    .join("\n");
}

// ── Usage ───────────────────────────────────────────────────────────────────

function WindowRows({ windows, testid }: { windows: readonly UsageWindow[]; testid: string }) {
  return (
    <ul data-testid={testid} className="space-y-2">
      {windows.map((w, i) => {
        const warn = w.percent >= 80;
        return (
          <li key={`${w.label}-${i}`} data-testid="code-usage-window" className="space-y-1">
            <div className="flex items-baseline justify-between gap-2 text-[13px]">
              <span className="text-zinc-200">{w.label}</span>
              <span className={warn ? "text-tone-warn" : "text-zinc-400"}>{100 - w.percent}% left</span>
            </div>
            <div className="h-1 overflow-hidden rounded-full bg-white/[0.08]" aria-hidden>
              <div
                className={`h-full rounded-full ${warn ? "bg-tone-warn" : "bg-zinc-400"}`}
                style={{ width: `${w.percent}%` }}
              />
            </div>
            <p className={QUIET}>{windowLine(w)}</p>
          </li>
        );
      })}
    </ul>
  );
}

function UsagePane({
  session,
  toolName,
  refresh,
  running,
}: {
  session: CodeSession;
  toolName: string;
  refresh: number;
  running: boolean;
}) {
  const { data, error, again } = usePaneRead<CodeUsage>(session.id, "usage", decodeCodeUsage, refresh, running);
  if (error) {
    return (
      <p role="alert" data-testid="code-usage-error" className="text-[12px] text-tone-danger">
        The usage could not be read: {error}
      </p>
    );
  }
  if (!data) {
    return (
      <p className="flex items-center gap-1.5 text-[12px] text-zinc-500">
        <Loader2 size={12} className="animate-spin" aria-hidden /> Reading…
      </p>
    );
  }
  const acc = data.account;
  const t = data.totals;
  return (
    <div data-testid="code-pane-usage" className="space-y-5 pb-4">
      <Section id="usage-account" title="Remaining usage">
        <p data-testid="code-usage-who" className="text-[13px] text-zinc-300">
          {toolName} · {acc ? acc.name : accountWords(session)}
          {acc?.planLabel ? <span className="text-zinc-500"> · {acc.planLabel}</span> : null}
          {!acc && data.seen?.plan ? <span className="text-zinc-500"> · {data.seen.plan}</span> : null}
        </p>
        {/* v1.439.0: read this account's usage from its own CLI now (no
            model call); the tab reads the fresh number after. */}
        {acc && acc.canRefresh ? (
          <UsageRefresh
            accountId={acc.id}
            accountName={acc.name}
            readAt={acc.usageReadAt || null}
            onRead={() => again()}
          />
        ) : null}
        {acc && acc.windows.length ? (
          <>
            {/* v1.414.0 review: with its age — Iron-Proxy sees a number only
                when the account makes a call, which can be hours ago. */}
            <p data-testid="code-usage-proxy-when" className={QUIET}>
              From the account manager&apos;s last look at this account{acc.at && agoWords(acc.at) ? `, ${agoWords(acc.at)}` : ""}.
            </p>
            <WindowRows windows={acc.windows} testid="code-usage-proxy" />
          </>
        ) : null}
        {data.seen ? (
          <>
            <p data-testid="code-usage-seen-when" className={QUIET}>
              As {toolName} reported it{data.seen.at ? `, ${agoWords(data.seen.at)}` : ""}.
            </p>
            <WindowRows windows={data.seen.windows} testid="code-usage-seen" />
          </>
        ) : null}
        {!data.seen && !(acc && acc.windows.length) ? (
          <p data-testid="code-usage-not-known" className="text-[13px] text-zinc-400">
            {noWindowsWords(session.tool, toolName)}
          </p>
        ) : null}
      </Section>
      <Section id="usage-session" title="This session">
        {t.replies === 0 ? (
          <p data-testid="code-usage-totals" className={QUIET}>
            Nothing used yet.
          </p>
        ) : (
          <p data-testid="code-usage-totals" className="text-[13px] text-zinc-300">
            {t.replies} repl{t.replies === 1 ? "y" : "ies"} · {usageWords(t)}
          </p>
        )}
        {t.listPrice && t.costUsd ? (
          <p className={QUIET}>A plan&apos;s use is shown at list price: it is what it would cost, not what you paid.</p>
        ) : null}
      </Section>
    </div>
  );
}

// ── Session ─────────────────────────────────────────────────────────────────

function SessionPaneTab({
  session,
  tools,
  toolName,
  events,
  refresh,
  running,
  onOpenChanges,
  onSend,
}: {
  session: CodeSession;
  tools: readonly CodingTool[];
  toolName: string;
  events: readonly CodeEvent[];
  refresh: number;
  running: boolean;
  onOpenChanges: () => void;
  onSend?: (text: string) => void;
}) {
  const over = usePaneRead<CodeOverview>(session.id, "overview", decodeCodeOverview, refresh, running);
  const changes = usePaneRead<CodeChanges>(session.id, "changes", decodeCodeChanges, refresh, running);
  const usage = usePaneRead<CodeUsage>(session.id, "usage", decodeCodeUsage, refresh, false);
  // v1.415.0: the tool's own breakdown (read again when a reply ends).
  const cctx = usePaneRead<CodeContext>(session.id, "context", decodeCodeContext, refresh, false);
  const [opening, setOpening] = useState<string | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);
  const tool = tools.find((x) => x.id === session.tool) ?? null;
  const o = over.data;

  async function openPath(path: string) {
    setOpening(path);
    setOpenError(null);
    try {
      await post("/documents/open", { path });
    } catch (e) {
      setOpenError(errText(e));
    } finally {
      setOpening(null);
    }
  }

  const mode = tool?.modes.find((m) => m.id === session.mode);
  const acc = usage.data?.account ?? null;
  const peak = [...(acc?.windows ?? []), ...(usage.data?.seen?.windows ?? [])].sort((a, b) => b.percent - a.percent)[0];
  const ctx = o?.context ?? null;
  const last = lastCodeAttachment(events);

  return (
    <div data-testid="code-pane-session" className="space-y-5 pb-4">
      <Section id="where" title="Where it works">
        <Row icon={<Monitor size={14} aria-hidden />}>This computer</Row>
        <Row icon={<FolderOpen size={14} aria-hidden />} title={session.folder || undefined}>
          {session.folder ? (
            <span className="flex min-w-0 items-start gap-2">
              <span data-testid="code-session-folder" className="min-w-0 flex-1 break-words">
                {session.noFolder ? "Its own folder" : baseName(session.folder)}
              </span>
              <button
                type="button"
                data-testid="code-session-open-folder"
                disabled={opening === session.folder || !o?.folderExists}
                onClick={() => void openPath(session.folder)}
                className={PRESS}
              >
                Open
              </button>
            </span>
          ) : (
            <span data-testid="code-session-folder">Its own folder is made with your first message.</span>
          )}
        </Row>
        {o && session.folder ? (
          <Row icon={<GitBranch size={14} aria-hidden />}>
            <span data-testid="code-session-branch">{branchLine(o)}</span>
          </Row>
        ) : null}
        {o?.rules.length ? (
          <p data-testid="code-session-rules" className={QUIET}>
            Instruction files here: {o.rules.join(", ")}
          </p>
        ) : null}
        {/* v1.448.0 (Wave A, A3): where the model answers, like a chat
            receipt's "· cloud" / "· local". Drawn only when the daemon knows. */}
        {whereLine(o) ? (
          <Row icon={<Network size={14} aria-hidden />}>
            <span data-testid="code-session-where" data-on-this-network={String(o?.where.onThisNetwork)}>
              {whereLine(o)}
            </span>
          </Row>
        ) : null}
      </Section>

      <Section id="answers" title="What answers">
        <Row>
          <span data-testid="code-session-model">
            {toolName} · {session.model || "its default model"}
            {session.effort ? <span className="text-zinc-500"> · effort {session.effort}</span> : null}
          </span>
        </Row>
        <p data-testid="code-session-account" className={QUIET}>
          Account {acc ? acc.name : accountWords(session, tools)}
          {acc?.planLabel ? ` · ${acc.planLabel}` : ""}
          {peak ? ` · ${peak.percent}% of its ${peak.label} used` : ""}
        </p>
        <p data-testid="code-session-mode" className={QUIET} title={mode?.about || undefined}>
          {modeLabel(session.mode, tool)}
          {mode?.about ? ` — ${mode.about}` : ""}
        </p>
        {session.jarvisTools ? (
          <p data-testid="code-session-jarvis-tools" className={QUIET}>
            {jarvisToolsWords(session.jarvisTools)}
          </p>
        ) : null}
      </Section>

      {o?.mcp ? (
        <ToolMcpSection mcp={o.mcp} toolName={toolName} sessionId={session.id} tool={session.tool} onChanged={over.again} />
      ) : null}

      <CodeContextSection
        cctx={cctx.data}
        seen={ctx}
        toolName={toolName}
        tool={session.tool}
        running={running}
        onSend={onSend}
      />

      <Section id="attachment" title="Last attachment">
        {last ? (
          <>
            <div data-testid="code-session-attachment" className="flex flex-wrap gap-2">
              {last.files.map((p, i) => {
                const name = baseName(p);
                const openable = openablePath(p);
                return isThumbImage(name) ? (
                  <button
                    key={`${p}-${i}`}
                    type="button"
                    title={openable ? `Open ${name}` : name}
                    disabled={!openable || opening === p}
                    onClick={() => openable && void openPath(openable)}
                    className="rounded-lg focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-default"
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={localMediaSrc(p)} alt={name} className="h-24 max-w-full rounded-lg object-cover" />
                  </button>
                ) : (
                  <span
                    key={`${p}-${i}`}
                    title={p}
                    className="flex min-w-0 max-w-full items-center gap-1.5 rounded-lg bg-white/[0.06] py-1 pl-2 pr-1 text-[12px] text-zinc-300"
                  >
                    {p.startsWith("@") ? <AtSign size={12} aria-hidden /> : <Paperclip size={12} aria-hidden className="shrink-0" />}
                    <span className="truncate">{name}</span>
                    {openable ? (
                      <button type="button" disabled={opening === p} onClick={() => void openPath(openable)} className={PRESS}>
                        Open
                      </button>
                    ) : null}
                  </span>
                );
              })}
            </div>
            <p className={QUIET}>
              {[agoWords(last.at) && `Sent ${agoWords(last.at)}`, last.words ? `with “${last.words}”` : "with no words"]
                .filter(Boolean)
                .join(" ")}
            </p>
          </>
        ) : (
          <p data-testid="code-session-attachment-none" className={QUIET}>
            Nothing attached yet. Pictures and files you send show here.
          </p>
        )}
      </Section>
      {openError ? (
        <p role="alert" className="text-[12px] text-tone-danger">
          {openError}
        </p>
      ) : null}

      <Section id="changes" title="Changes">
        <div className="flex items-center gap-2">
          <p data-testid="code-session-changes-count" className="min-w-0 flex-1 text-[13px] text-zinc-300">
            {changes.error ? "The changes could not be read." : changesCountWords(changes.data, toolName)}
          </p>
          <button type="button" data-testid="code-session-open-changes" onClick={onOpenChanges} className={PRESS}>
            See changes
          </button>
        </div>
      </Section>

      <Section id="background" title="Background">
        {over.error ? (
          <p role="alert" className="text-[12px] text-tone-danger">
            What runs under {toolName} could not be read: {over.error}
          </p>
        ) : !o ? (
          <p className={`${QUIET} flex items-center gap-1.5`}>
            <Loader2 size={12} className="animate-spin" aria-hidden /> Reading…
          </p>
        ) : !o.toolRunning ? (
          <p data-testid="code-session-tool-stopped" className={QUIET}>
            {toolName} is not running right now; it starts again with your next message.
          </p>
        ) : !o.programsKnown ? (
          <p data-testid="code-session-programs-unknown" className={QUIET}>
            What runs under {toolName} could not be read right now.
          </p>
        ) : o.programs.length === 0 ? (
          <p data-testid="code-session-programs-none" className={QUIET}>
            Nothing is running under {toolName} right now.
          </p>
        ) : (
          <>
            <p className={QUIET}>Running under {toolName} right now (its own helpers included):</p>
            <ul data-testid="code-session-programs" className="space-y-1.5">
              {o.programs.map((p) => (
                <li key={p.pid} className="min-w-0">
                  <p className="truncate text-[13px] text-zinc-300">{p.name}</p>
                  {p.command ? (
                    <p className="truncate font-mono text-[11px] text-zinc-500" title={p.command}>
                      {p.command}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
            {o.programsMore ? (
              <p data-testid="code-session-programs-more" className={QUIET}>
                More run than are listed here (the first {o.programs.length} are shown).
              </p>
            ) : null}
          </>
        )}
        {/* v1.441.0 (g1): the tool's OWN background work, each with Stop. */}
        {o?.toolRunning && o.backgroundWork?.length ? (
          <BackgroundWorkList sessionId={session.id} work={o.backgroundWork} toolName={toolName} onChanged={over.again} />
        ) : null}
      </Section>
    </div>
  );
}

/** v1.426.0 (E4): the tool's OWN MCP servers, as it reported them when it
 *  last started (Claude Code's init frame, Codex's startup statuses): each
 *  one's state in words, and for one waiting for a sign-in, the tool's own
 *  way to sign in. A tool that never said (or never reports) says so. */
function ToolMcpSection({
  mcp,
  toolName,
  sessionId,
  tool,
  onChanged,
}: {
  mcp: CodeMcp;
  toolName: string;
  sessionId: string;
  tool: string;
  onChanged: () => void;
}) {
  const at = mcp.at ? agoWords(mcp.at) : "";
  return (
    <Section id="mcp" title="Its own tool servers">
      {!mcp.known || mcp.servers.length === 0 ? (
        <p data-testid="code-session-mcp-note" className={QUIET}>
          {mcp.note}
        </p>
      ) : (
        <>
          <ul data-testid="code-session-mcp" className="space-y-1.5">
            {mcp.servers.map((s) => (
              <li key={s.name} data-testid={`code-session-mcp-${s.name}`} data-state={s.state} className="min-w-0">
                <div className="flex min-w-0 flex-wrap items-center gap-x-2 text-[13px] text-zinc-300">
                  <span className="min-w-0 truncate">{s.name}</span>
                  <span
                    className={`shrink-0 text-[12px] ${
                      s.state === "connected"
                        ? "text-tone-success"
                        : s.state === "failed" || s.state === "needs_sign_in"
                          ? "text-tone-warn"
                          : "text-zinc-500"
                    }`}
                  >
                    {s.words}
                  </span>
                  {s.actions?.length ? (
                    <McpRowMenu sessionId={sessionId} server={s} tool={tool} toolName={toolName} onChanged={onChanged} />
                  ) : null}
                </div>
                {s.error ? (
                  <p className="truncate font-mono text-[11px] text-zinc-500" title={s.error}>
                    {s.error}
                  </p>
                ) : null}
                {s.signIn ? (
                  <p data-testid={`code-session-mcp-sign-in-${s.name}`} className={QUIET}>
                    {s.signIn}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
          <p className={QUIET}>
            As {toolName} reported them{at ? ` ${at}` : ""}, when it started.
          </p>
        </>
      )}
      {mcp.reload ? <McpReloadButton sessionId={sessionId} toolName={toolName} onChanged={onChanged} /> : null}
    </Section>
  );
}

/** v1.415.0: the Session tab's Context drop-down (the shared
 *  ContextBreakdown): the tool's OWN numbers from `GET /context` — Claude
 *  Code's `/context`, Pi's stats — else the total it said after its last
 *  reply (the overview's). Compact is the tool's own `/compact`, sent as a
 *  message, and only when no reply runs; a tool with none says why. */
function CodeContextSection({
  cctx,
  seen,
  toolName,
  tool,
  running,
  onSend,
}: {
  cctx: CodeContext | null;
  seen: CodeOverview["context"] | null;
  toolName: string;
  tool: string;
  running: boolean;
  onSend?: (text: string) => void;
}) {
  const reading = cctx?.reading ?? (seen ? totalReading(seen.window, seen.used, seen.percent, seen.at) : null);
  const fromTool = !!cctx?.reading && cctx.reading.parts.length > 0;
  const at = reading?.at ? agoWords(reading.at) : "";
  const sourceLine = !reading
    ? undefined
    : fromTool
      ? `As ${toolName} counted it${at ? ` ${at}` : ""}.`
      : `As ${toolName} said${at ? ` ${at}` : ""}, after its last reply.`;
  const compactText = cctx?.compactHow === "message" ? cctx.compactText || "/compact" : "";
  return (
    <ContextBreakdown
      prefix="code-session"
      reading={reading}
      emptyWords={contextNotKnownWords(tool, toolName)}
      sourceLine={sourceLine}
      note={[cctx?.note, cctx?.waiting ? cctx.waitingNote : ""].filter(Boolean).join(" ") || undefined}
      compact={
        compactText && onSend
          ? {
              onPress: () => onSend(compactText),
              blocked: running ? "Wait for the reply to finish, then compact." : "",
            }
          : null
      }
      compactAbsent={cctx?.compactWhy || undefined}
    />
  );
}
