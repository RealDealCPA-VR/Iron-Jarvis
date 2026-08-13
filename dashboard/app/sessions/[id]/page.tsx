"use client";

import { use, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  FileText,
  Gauge,
  ListTree,
  Wrench,
  Square,
  RotateCw,
  Send,
  Download,
  Radio,
  History,
  Volume2,
  VolumeX,
} from "lucide-react";
import { useApi } from "@/lib/useApi";
import { useEvents } from "@/lib/useEvents";
import { useRunStream } from "@/lib/useRunStream";
import { useTTS } from "@/lib/useTTS";
import { post, del, API_BASE, ijToken, ApiError } from "@/lib/api";
import type {
  SessionDetail,
  SessionView,
  Evaluation,
  Review,
  IJEvent,
} from "@/lib/types";
import {
  Card,
  Badge,
  Stat,
  StatusDot,
  OfflineHint,
  Empty,
  MockChip,
  SkeletonRows,
  ConfirmButton,
  ErrorNote,
  LoaderInline,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { TeamTree } from "@/components/sessions/TeamTree";
import { SessionFiles } from "@/components/sessions/SessionFiles";
import OriginChip from "@/components/sessions/OriginChip";
import { DocPreview } from "@/components/chat/DocPreview";
import { BlackboardPanel } from "@/components/sessions/BlackboardPanel";
import { ReviewPanel } from "@/components/ReviewPanel";
import { TracesPanel } from "@/components/TracesPanel";
import { SessionFeedback } from "@/components/SessionFeedback";
import { TimeTravelFeed } from "@/components/TimeTravelFeed";
import { PageShell, Reveal } from "@/components/motion";
import { pct, num, clockTime, shortId } from "@/lib/format";

/** Session statuses that represent in-flight work (cancellable). "queued"
 *  (v1.166.0) is parked behind the concurrency cap — not yet running, but
 *  Stop must work on it (cancel_session dequeues + finalizes honestly). */
const ACTIVE = new Set(["active", "running", "pending", "queued"]);
/** Event types that should trigger a live detail/transcript refetch. */
const REFETCH_EVENTS = new Set([
  "tool.executed",
  "agent.state_changed",
  "session.completed",
]);

export default function SessionDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const detail = useApi<SessionDetail>(`/sessions/${id}`);
  const evaluation = useApi<Evaluation>(`/sessions/${id}/evaluation`);
  const review = useApi<Review>(`/sessions/${id}/review`);

  const offline = detail.error && detail.error.status === 0;
  const notFound = detail.error && detail.error.status === 404;

  const session = detail.data?.session;
  const runs = detail.data?.transcript.runs ?? [];
  const tools = detail.data?.transcript.tools ?? [];

  // Token usage is present on the live session view but not on the static type.
  const tokens = session as
    | (SessionView & { input_tokens?: number; output_tokens?: number })
    | undefined;
  const inTok = tokens?.input_tokens ?? 0;
  const outTok = tokens?.output_tokens ?? 0;

  const status = (session?.status ?? "").toLowerCase();
  const isActive = ACTIVE.has(status);
  // Queued = parked, not running: no token stream exists yet, and the badge
  // must not read like "Running now".
  const isQueued = status === "queued";

  /* ---- Live run stream (v1.166.0, B2): tokens + tool activity over SSE ----
   * `GET /sessions/{id}/stream` via the same hook chat uses for its agent
   * bubble. Opened ONCE per visit when the session is genuinely running (a
   * queued session has nothing to stream); the daemon closes the stream on the
   * terminal frame, at which point we fall back to the existing reload flow so
   * the transcript/summary cards take over. The once-guard is a ref: when the
   * stream closes, `runStream.active` flips false while `isActive` is still
   * true for a beat — restarting on that beat would loop forever. */
  const runStream = useRunStream();
  const streamStartedRef = useRef(false);
  /* The live-token <pre> is height-capped, so without follow-scroll the newest
   * tokens land below the fold after ~14 lines and the user watches a frozen
   * top slice for the rest of the run. Mirror chat's pinnedRef pattern: follow
   * new text only while the reader is at (near) the bottom, so scrolling up to
   * re-read is never yanked back down. */
  const liveTextRef = useRef<HTMLPreElement | null>(null);
  const livePinnedRef = useRef(true);
  useEffect(() => {
    const el = liveTextRef.current;
    if (el && livePinnedRef.current) el.scrollTop = el.scrollHeight;
  }, [runStream.text]);
  useEffect(() => {
    if (isActive && !isQueued && !streamStartedRef.current) {
      streamStartedRef.current = true;
      livePinnedRef.current = true; // a fresh stream follows its own output
      runStream.start(id);
    }
    // The cleanup MUST release the guard and stop the stream, or StrictMode's
    // dev-only mount→cleanup→mount cycle strands the page: the first mount sets
    // the ref and opens the stream, the simulated unmount runs useRunStream's
    // cleanup (which closes the socket WITHOUT flipping `active`), and the
    // remounted effect is then blocked by the still-true ref — `active` stays
    // true forever over a stream that no longer exists, showing a permanent
    // "Live run" card. stop() (not just close) keeps `active` truthful. This is
    // still loop-safe against the restart loop the guard exists for: a
    // terminal-frame close changes `runStream.active` but none of these deps,
    // so the effect never re-runs to reopen a finished stream; the
    // queued→active flip and a remount both re-run it with the ref reset.
    return () => {
      streamStartedRef.current = false;
      runStream.stop();
    };
    // start()/stop() are stable useCallbacks; the trigger is the status flip.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isActive, isQueued, id]);
  const wasStreamingRef = useRef(false);
  useEffect(() => {
    if (wasStreamingRef.current && !runStream.active) {
      // Stream just ended (terminal frame or transport drop) — refetch so the
      // finished transcript replaces the live strip.
      detail.reload();
      evaluation.reload();
    }
    wasStreamingRef.current = runStream.active;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runStream.active]);

  /* ---- Voice: speak the assistant's summary aloud (behind a toggle) ------- */
  const tts = useTTS();
  const summary = session?.summary ?? "";
  useEffect(() => {
    if (tts.enabled && summary) tts.speak(summary);
    // speak() dedupes identical text, so re-runs are harmless.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summary, tts.enabled]);

  /* ---- Live feed: filter the global event stream to this session ---------- */
  const { events } = useEvents(100);
  const sessionEvents = useMemo(
    () => events.filter((e) => e.session_id === id),
    [events, id],
  );
  // The newest session-scoped event that warrants a refetch.
  const latestRefetch = sessionEvents.find((e) => REFETCH_EVENTS.has(e.type));
  /* Reload channel for SessionFiles (v1.168.0, P2): a finished session's
   * panel does not poll, but the SAME page's Time-travel feed can undo a
   * write — and an undo is a ledger row that arrives here as tool.executed.
   * Bumping the nonce makes the panel refetch so its counts/rows never
   * present a just-reverted handover as current. */
  const [filesNonce, setFilesNonce] = useState(0);
  useEffect(() => {
    if (!latestRefetch) return;
    detail.reload();
    evaluation.reload();
    setFilesNonce((n) => n + 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latestRefetch?.id]);

  /* ---- Lifecycle actions -------------------------------------------------- */
  const [acting, setActing] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [followup, setFollowup] = useState("");

  /* ---- Files handover (v1.168.0, P2): preview target for SessionFiles rows
   * and TeamTree file chips. One state at the page level so both surfaces
   * drive the SAME embedded DocPreview (the chat right-rail component, reused
   * — never chat/page.tsx). */
  const [previewPath, setPreviewPath] = useState<string | null>(null);

  async function stop() {
    setActing("stop");
    setActionError(null);
    try {
      await post(`/sessions/${id}/cancel`);
      detail.reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActing(null);
    }
  }

  async function rerun() {
    setActing("rerun");
    setActionError(null);
    try {
      const s = await post<SessionView>(`/sessions/${id}/rerun?wait=false`);
      if (s?.id) router.push(`/sessions/${s.id}`);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
      setActing(null);
    }
  }

  async function continueSession() {
    if (!followup.trim()) return;
    setActing("continue");
    setActionError(null);
    try {
      const s = await post<SessionView>(`/sessions/${id}/continue`, {
        message: followup.trim(),
        wait: false,
      });
      if (s?.id) router.push(`/sessions/${s.id}`);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
      setActing(null);
    }
  }

  async function remove() {
    setActing("delete");
    setActionError(null);
    try {
      await del(`/sessions/${id}`);
      router.push("/sessions");
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err));
      setActing(null);
    }
  }

  function exportUrl(format: "md" | "json") {
    const base = `${API_BASE}/sessions/${id}/export?format=${format}`;
    const t = ijToken();
    return t ? `${base}&token=${encodeURIComponent(t)}` : base;
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Session"
          subtitle={id}
          actions={
            <Link
              href="/sessions"
              className="flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-sm text-zinc-300 transition-colors hover:border-white/20 hover:text-zinc-100"
            >
              <ArrowLeft size={14} /> All sessions
            </Link>
          }
        />
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}
      {notFound && (
        <Reveal>
          <Card>
            <Empty>Session not found.</Empty>
          </Card>
        </Reveal>
      )}

      {detail.loading && !detail.data ? (
        <Reveal>
          <Card title="Loading session" icon={<FileText size={15} />}>
            <SkeletonRows rows={5} />
          </Card>
        </Reveal>
      ) : session ? (
        <>
          <Reveal>
            <Card
              title="Summary"
              icon={<FileText size={15} />}
              right={
                <span className="flex items-center gap-2">
                  {tts.supported && (
                    <button
                      type="button"
                      onClick={tts.toggle}
                      title={
                        tts.enabled
                          ? "Voice on — Iron Jarvis reads replies aloud"
                          : "Voice off — click to hear replies"
                      }
                      aria-pressed={tts.enabled}
                      className={`inline-flex h-7 w-7 items-center justify-center rounded-lg border transition-colors ${
                        tts.enabled
                          ? "border-accent/40 text-accent-soft"
                          : "border-white/10 text-zinc-400 hover:border-white/20 hover:text-zinc-200"
                      }`}
                    >
                      {tts.enabled ? <Volume2 size={14} /> : <VolumeX size={14} />}
                    </button>
                  )}
                  {/* Origin chip (v1.168.0): who started this session — a
                      scheduled/automated session says so where the user reads
                      its outcome. Renders nothing when origin is absent. */}
                  <OriginChip origin={session.origin} />
                  {session.provider === "mock" && <MockChip />}
                  {/* "queued" predates statusTone's table — style it here so it
                      reads as "parked", not the slate unknown-status grey. */}
                  <Badge
                    value={session.status}
                    tone={isQueued ? "violet" : undefined}
                  />
                </span>
              }
            >
              <div className="mb-4 flex items-start gap-2.5 text-[15px] text-zinc-100">
                <StatusDot status={session.status} className="mt-1.5" />
                <span>{session.task}</span>
              </div>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Meta label="Agent" value={session.agent_type} />
                <Meta
                  label="Provider / model"
                  value={
                    <span className="font-mono text-xs">
                      {session.provider} / {session.model}
                    </span>
                  }
                />
                <Meta label="Created" value={clockTime(session.created_at)} />
                <Meta label="Finished" value={clockTime(session.finished_at)} />
                <Meta
                  label="Workspace"
                  value={
                    <span className="break-all font-mono text-xs text-zinc-400">
                      {session.workspace_path}
                    </span>
                  }
                />
              </div>
              {(inTok > 0 || outTok > 0) && (
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <span className="text-[11px] font-medium uppercase tracking-[0.1em] text-zinc-400">
                    Tokens
                  </span>
                  {inTok > 0 && (
                    <Badge value={`${inTok.toLocaleString()} in`} tone="violet" />
                  )}
                  {outTok > 0 && (
                    <Badge value={`${outTok.toLocaleString()} out`} tone="cyan" />
                  )}
                </div>
              )}
              {session.summary && (
                <div className="mt-4 rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5 text-sm text-zinc-300">
                  {session.summary}
                </div>
              )}
            </Card>
          </Reveal>

          {/* Lifecycle controls */}
          <Reveal>
            <Card title="Run controls" icon={<Wrench size={15} />}>
              <div className="flex flex-wrap items-center gap-2">
                {isActive && (
                  <button
                    type="button"
                    onClick={stop}
                    disabled={acting === "stop"}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-sm font-medium text-zinc-300 transition-colors hover:border-amber-500/40 hover:text-amber-300 disabled:opacity-50"
                  >
                    {acting === "stop" ? (
                      <LoaderInline label="Stopping…" />
                    ) : (
                      <>
                        <Square size={14} /> Stop
                      </>
                    )}
                  </button>
                )}
                <button
                  type="button"
                  onClick={rerun}
                  disabled={acting === "rerun"}
                  title="Clone this session and run it again"
                  className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-sm font-medium text-zinc-300 transition-colors hover:border-accent/40 hover:text-accent-soft disabled:opacity-50"
                >
                  {acting === "rerun" ? (
                    <LoaderInline label="Rerunning…" />
                  ) : (
                    <>
                      <RotateCw size={14} /> Rerun
                    </>
                  )}
                </button>
                <a
                  href={exportUrl("md")}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-sm font-medium text-zinc-300 transition-colors hover:border-white/20 hover:text-zinc-100"
                >
                  <Download size={14} /> Export .md
                </a>
                <a
                  href={exportUrl("json")}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-sm font-medium text-zinc-300 transition-colors hover:border-white/20 hover:text-zinc-100"
                >
                  <Download size={14} /> Export .json
                </a>
                <ConfirmButton
                  label="Delete session"
                  confirmLabel="Confirm delete?"
                  onConfirm={remove}
                  className="!px-3 !py-1.5 !text-sm"
                />
              </div>

              {/* Continue: a follow-up that reuses the same workspace */}
              <div className="mt-3 flex flex-wrap items-stretch gap-2">
                <input
                  value={followup}
                  onChange={(e) => setFollowup(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      continueSession();
                    }
                  }}
                  placeholder="Send a follow-up — reuses this workspace…"
                  className="min-w-[200px] flex-1 rounded-lg border border-white/[0.08] bg-ink-900/80 px-3 py-1.5 text-sm text-zinc-100 outline-none transition-colors placeholder:text-zinc-600 focus:border-accent/60"
                />
                <button
                  type="button"
                  onClick={continueSession}
                  disabled={acting === "continue" || !followup.trim()}
                  className="btn-accent !py-1.5"
                >
                  {acting === "continue" ? (
                    <LoaderInline label="Sending…" />
                  ) : (
                    <>
                      <Send size={14} /> Send
                    </>
                  )}
                </button>
              </div>

              {actionError && (
                <div className="mt-3">
                  <ErrorNote>{actionError}</ErrorNote>
                </div>
              )}
            </Card>
          </Reveal>

          {/* Live run stream (v1.166.0, B2): tokens + tool cards as they happen.
              Only while the SSE stream is open — once the daemon sends the
              terminal frame, the reload flow above swaps in the real transcript. */}
          {runStream.active && (
            <Reveal>
              <Card
                title="Live run"
                icon={<Radio size={15} />}
                right={
                  runStream.phase ? (
                    <span className="text-[11px] capitalize text-accent-soft/80">
                      {runStream.phase.phase}
                      {runStream.phase.detail
                        ? ` — ${runStream.phase.detail}`
                        : ""}
                    </span>
                  ) : (
                    <span className="text-[11px] text-zinc-500">streaming</span>
                  )
                }
              >
                {runStream.tools.length > 0 && (
                  <div className="mb-2.5 flex flex-wrap gap-1.5">
                    {runStream.tools.map((t) => (
                      <span
                        key={t.id}
                        className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-[11px] ${
                          t.status === "running"
                            ? "border-accent/30 text-accent-soft"
                            : t.ok === false
                              ? "border-rose-500/30 text-rose-300"
                              : "border-white/10 text-zinc-400"
                        }`}
                      >
                        {t.status === "running" ? (
                          <LoaderInline label={t.name} />
                        ) : (
                          <>{t.ok === false ? `${t.name} — failed` : t.name}</>
                        )}
                      </span>
                    ))}
                  </div>
                )}
                {runStream.text ? (
                  <pre
                    ref={liveTextRef}
                    data-testid="live-run-text"
                    onScroll={(e) => {
                      const el = e.currentTarget;
                      // Re-pin only when the reader returns to (near) the
                      // bottom; anywhere above it, new tokens must not yank.
                      livePinnedRef.current =
                        el.scrollHeight - el.scrollTop - el.clientHeight < 24;
                    }}
                    className="max-h-56 overflow-y-auto whitespace-pre-wrap rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5 font-mono text-xs leading-relaxed text-zinc-300"
                  >
                    {runStream.text}
                  </pre>
                ) : (
                  <div className="text-xs text-zinc-500">
                    Connected — waiting for the first token…
                  </div>
                )}
              </Card>
            </Reveal>
          )}

          {/* Files handover (v1.168.0, P2) — what this session's tool ledger
              says it wrote, previewable/downloadable right here. Renders
              nothing when the session produced no files. */}
          <SessionFiles
            sessionId={id}
            workspacePath={session.workspace_path}
            active={isActive}
            onPreview={setPreviewPath}
            reloadNonce={filesNonce}
          />

          {/* Embedded preview for a handed-over file — the chat's DocPreview,
              driven by SessionFiles rows AND TeamTree file chips. */}
          {previewPath && (
            <Reveal>
              <div className="h-[34rem]" data-testid="session-doc-preview">
                <DocPreview
                  path={previewPath}
                  onClose={() => setPreviewPath(null)}
                />
              </div>
            </Reveal>
          )}

          {/* Team + blackboard (v1.166.0, B3) — both render nothing for a
              solo session, so most pages are unchanged. */}
          <TeamTree
            sessionId={id}
            active={isActive}
            onPreviewFile={setPreviewPath}
          />
          <BlackboardPanel sessionId={id} active={isActive} />

          {/* Live activity feed (filtered to this session) */}
          <Reveal>
            <Card
              title="Live activity"
              icon={<Radio size={15} />}
              right={
                <span className="text-[11px] text-zinc-500">
                  {sessionEvents.length} event{sessionEvents.length === 1 ? "" : "s"}
                </span>
              }
            >
              {sessionEvents.length === 0 ? (
                <Empty icon={<Radio size={22} />}>
                  No live events yet. New activity streams in here as the agent works.
                </Empty>
              ) : (
                <div className="max-h-72 space-y-1 overflow-y-auto font-mono text-xs">
                  {sessionEvents.slice(0, 30).map((e) => (
                    <div
                      key={e.id}
                      className="flex items-start gap-2 rounded-lg border border-white/[0.04] bg-white/[0.015] px-2.5 py-1.5"
                    >
                      <span className="shrink-0 text-zinc-600">{clockTime(e.ts)}</span>
                      <span className="shrink-0 text-accent-soft/80">{e.type}</span>
                      <span className="min-w-0 flex-1 truncate text-zinc-400">
                        {summarizeEvent(e)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </Reveal>

          {/* Time-travel — replay every action/token/decision, undo what allows */}
          <Reveal>
            <Card
              title="Time-travel"
              icon={<History size={15} />}
              right={
                <span className="text-[11px] text-zinc-500">
                  replay &amp; undo
                </span>
              }
            >
              <TimeTravelFeed sessionId={id} />
            </Card>
          </Reveal>

          {/* Feedback — how did I do? */}
          <Reveal>
            <SessionFeedback sessionId={id} />
          </Reveal>

          {/* Evaluation */}
          <Reveal>
            <Card title="Evaluation" icon={<Gauge size={15} />}>
              {evaluation.error && evaluation.error.status === 404 ? (
                <Empty icon={<Gauge size={22} />}>No evaluation recorded for this session.</Empty>
              ) : evaluation.loading && !evaluation.data ? (
                <SkeletonRows rows={2} />
              ) : evaluation.data ? (
                <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
                  <Stat label="Completion" value={pct(evaluation.data.completion)} accent />
                  <Stat label="Tool success" value={pct(evaluation.data.tool_success_rate)} />
                  <Stat label="Tool calls" value={evaluation.data.tool_calls} />
                  <Stat label="Steps" value={evaluation.data.step_count} />
                  <Stat label="Latency" value={`${num(evaluation.data.latency_s)}s`} />
                </div>
              ) : (
                <Empty>No evaluation.</Empty>
              )}
            </Card>
          </Reveal>

          {/* Review (only when present) */}
          {review.data && !(review.error && review.error.status === 404) && (
            <Reveal>
              <ReviewPanel
                sessionId={id}
                review={review.data}
                onAction={() => {
                  review.reload();
                  detail.reload();
                }}
              />
            </Reveal>
          )}

          {/* Transcript: agent runs */}
          <Reveal>
            <Card title={`Agent runs · ${runs.length}`} icon={<ListTree size={15} />}>
              {runs.length === 0 ? (
                <Empty>No agent runs.</Empty>
              ) : (
                <div className="-mx-1 overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b hairline text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                        <th className="px-2 py-2.5 font-medium">Run</th>
                        <th className="px-2 py-2.5 font-medium">Agent</th>
                        <th className="px-2 py-2.5 font-medium">State</th>
                        <th className="px-2 py-2.5 font-medium">Steps</th>
                        <th className="px-2 py-2.5 font-medium">Parent</th>
                        <th className="px-2 py-2.5 font-medium">Result</th>
                      </tr>
                    </thead>
                    <tbody>
                      {runs.map((r) => (
                        <tr
                          key={r.id}
                          className="border-b border-white/[0.04] align-top last:border-0 hover:bg-white/[0.02]"
                        >
                          <td className="px-2 py-2.5 font-mono text-[11px] text-zinc-500">
                            {shortId(r.id)}
                          </td>
                          <td className="px-2 py-2.5 text-zinc-300">{r.agent_type}</td>
                          <td className="px-2 py-2.5">
                            <Badge value={r.state} />
                          </td>
                          <td className="px-2 py-2.5 text-zinc-400">{r.steps}</td>
                          <td className="px-2 py-2.5 font-mono text-[11px] text-zinc-600">
                            {r.parent_id ? shortId(r.parent_id) : "—"}
                          </td>
                          <td className="max-w-sm px-2 py-2.5 text-zinc-400">
                            <span className="line-clamp-2">{r.result || "—"}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </Reveal>

          {/* Transcript: tool invocations */}
          <Reveal>
            <Card title={`Tool invocations · ${tools.length}`} icon={<Wrench size={15} />}>
              {tools.length === 0 ? (
                <Empty>No tool invocations.</Empty>
              ) : (
                <div className="-mx-1 overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b hairline text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                        <th className="px-2 py-2.5 font-medium">Tool</th>
                        <th className="px-2 py-2.5 font-medium">Verdict</th>
                        <th className="px-2 py-2.5 font-medium">OK</th>
                        <th className="px-2 py-2.5 font-medium">Output</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tools.map((t) => (
                        <tr
                          key={t.id}
                          className="border-b border-white/[0.04] align-top last:border-0 hover:bg-white/[0.02]"
                        >
                          <td className="px-2 py-2.5 font-mono text-zinc-200">{t.tool}</td>
                          <td className="px-2 py-2.5">
                            <Badge value={t.verdict} />
                          </td>
                          <td className="px-2 py-2.5">
                            <Badge value={t.ok ? "ok" : "failed"} />
                          </td>
                          <td className="max-w-md px-2 py-2.5">
                            <span className="line-clamp-2 font-mono text-xs text-zinc-400">
                              {t.output || "—"}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </Reveal>

          <Reveal>
            <TracesPanel sessionId={id} />
          </Reveal>
        </>
      ) : null}
    </PageShell>
  );
}

/** Compact, human-readable one-liner for a live event row. */
function summarizeEvent(e: IJEvent): string {
  const p = (e.payload || {}) as Record<string, unknown>;
  const s = (k: string) => (p[k] == null ? "?" : String(p[k]));
  if (e.type === "tool.executed") return `${s("tool")} → ${p.ok ? "ok" : "failed"}`;
  if (e.type === "agent.state_changed") return `${s("from")} → ${s("to")}`;
  if (e.type === "session.completed") return `status: ${s("status")}`;
  try {
    return JSON.stringify(p);
  } catch {
    return "";
  }
}

function Meta({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-[11px] font-medium uppercase tracking-[0.1em] text-zinc-400">
        {label}
      </div>
      <div className="mt-1 text-sm text-zinc-200">{value}</div>
    </div>
  );
}
