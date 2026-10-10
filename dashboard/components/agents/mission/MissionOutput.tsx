"use client";

// THE CENTRE (v1.307.0): the user's request at the top, the deliverable under
// it — the one thing the screen is for. While the team works the panel shows
// what is being written RIGHT NOW (the coordinator's final answer as it
// streams, else the teammate writing most recently, labelled as a draft);
// once the mission ends it shows the coordinator's finished answer.
//
// Report | Markdown | Preview: the same text rendered, the same text raw (copy
// it anywhere), and the FILES the team made — read off the ledger, previewed
// with the app's one document viewer.
//
// v1.309.0 (UX/speed wave 1) — the screen stops being a dead end and says
// what it knows:
// * WHAT'S NEXT. A finished / stopped / failed mission offers Ask for changes
//   (a continuation carrying the user's words), Run it again (a stopped or
//   failed one), Retry the N failed items (ONE server step — the reset and the
//   continuation can never half-apply), and an interrupted one offers
//   Continue; once continued it links forward instead. Each opens the NEW
//   mission. None of them picks a model; a FAILED mission's Run it again
//   starts fresh on the CURRENT default (`runAgainRequest`), so changing the
//   default on Connections is a way out instead of the same dead provider.
//   "Open the full run" reaches the coordinator's own session page.
// * WHO DID THE WORK. A quiet receipt names the provider and model, worded by
//   what happened (only a completed mission was "answered"), amber with a way
//   to Connections when it was the mock, plus the daemon's own route note
//   when a failover touched the mission (the TurnReceipt rule).
// * EVERY ASK IS ANSWERABLE HERE. A teammate's ask gets the same card as the
//   coordinator's, named for the teammate.
// * THE LIVE TEXT COSTS WHAT CHANGED. Settled markdown is memoised and only
//   the growing tail is re-parsed (the chat stream's split, lib/streamSplit),
//   and the live text is read from the mission's store HERE, so a token flush
//   re-renders this panel and nothing else on the screen.

import Link from "next/link";
import { useEffect, useState } from "react";
import { FileText, RotateCcw, Square } from "lucide-react";
import { ApiError, post } from "@/lib/api";
import { Markdown, MemoMarkdown, CopyIconButton } from "@/components/Markdown";
import { DocPreview } from "@/components/chat/DocPreview";
import {
  MISSION_TERMINAL,
  baseName,
  missionHeadline,
  missionPath,
  type MissionView,
  type WaitingOn,
} from "@/lib/mission";
import { settledSplit } from "@/lib/streamSplit";
import { useMissionLive, type MissionLive, type MissionLiveStore } from "@/lib/useMission";
import { receiptVerbFor } from "./receipt";
import { mockLine } from "./AgentCards";

export type OutputTab = "report" | "markdown" | "preview";

export interface Deliverable {
  text: string;
  /** final = the finished answer; live = the coordinator writing it now;
   *  draft = a teammate's text in progress; none = nothing yet. */
  source: "final" | "live" | "draft" | "none";
  who: string;
}

/** What the centre shows — pure, so the rule is pinned without a DOM. */
export function deliverableFor(view: MissionView | null, live: MissionLive): Deliverable {
  const finished = view !== null && MISSION_TERMINAL.has(view.session.status);
  if (finished && view.deliverable.text.trim()) {
    return { text: view.deliverable.text, source: "final", who: view.coordinator.name };
  }
  if (live.coordinator.trim()) {
    return { text: live.coordinator, source: "live", who: view?.coordinator.name ?? "Jarvis" };
  }
  const latest = live.latest ? live.members[live.latest] : undefined;
  if (!finished && latest && latest.text.trim()) {
    return { text: latest.text, source: "draft", who: latest.name };
  }
  return { text: "", source: "none", who: "" };
}

function ApprovalCard({ waiting, who, onAnswered }: { waiting: WaitingOn; who: string; onAnswered: () => void }) {
  // v1.309.0: also drawn for each TEAMMATE parked on an ask — the daemon files
  // a teammate's ask under the teammate's own session, and the answer route
  // takes the approval id alone, so the same three answers work for it.
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const answer = async (decision: "once" | "conversation" | "deny") => {
    setBusy(true);
    setNote(null);
    try {
      await post(`/chat/approvals/${encodeURIComponent(waiting.approval_id)}`, { decision });
      onAnswered();
    } catch (e) {
      // 404 = already answered elsewhere (the bell, another window).
      setNote(e instanceof ApiError && e.status === 404 ? "Already answered." : "Could not send the answer.");
      onAnswered();
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      data-testid="mission-approval"
      className="mt-3 rounded-xl border border-tone-warn/30 bg-tone-warn/10 px-3 py-2.5 text-[13px] text-zinc-100"
    >
      <div>
        <strong>{who}</strong> wants to use <strong>{waiting.tool.replace(/_/g, " ")}</strong>. Nothing runs until
        you answer.
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        <button type="button" disabled={busy} className="btn-accent px-3 py-1 text-xs" onClick={() => void answer("once")}>
          Allow once
        </button>
        <button
          type="button"
          disabled={busy}
          className="btn-ghost px-3 py-1 text-xs"
          onClick={() => void answer("conversation")}
        >
          Allow for this task
        </button>
        <button type="button" disabled={busy} className="btn-ghost px-3 py-1 text-xs" onClick={() => void answer("deny")}>
          Decline
        </button>
      </div>
      {note && <div className="mt-1 text-[12px] text-tone-warn">{note}</div>}
    </div>
  );
}

/** The streaming caret: an `::after` on the wrapper's last block, so it sits
 *  inline with the running text. Kept as one string so the lock-step pin can
 *  compare it with chat's. */
const STREAMING_CARET_CLASS =
  "[&>*:last-child]:after:ml-0.5 [&>*:last-child]:after:inline-block [&>*:last-child]:after:h-[0.95em] [&>*:last-child]:after:w-[2px] [&>*:last-child]:after:translate-y-[1px] [&>*:last-child]:after:animate-caret [&>*:last-child]:after:rounded-full [&>*:last-child]:after:bg-accent-soft [&>*:last-child]:after:align-baseline [&>*:last-child]:after:content-['']";

/** Markdown that is still being written. Everything before the last blank
 *  line outside a code fence is settled and goes through MemoMarkdown (parsed
 *  once); only the tail is re-parsed per flush.
 *
 *  A LOCK-STEP PAIR with chat's `StreamingText` (app/chat/page.tsx), which
 *  lives in the chat page module and so cannot be imported from here: the
 *  same split, the same two renderers, the same caret classes.
 *  `__tests__/mission-live-markdown-lockstep-v1309.test.ts` fails when either
 *  side changes alone. Lifting StreamingText into components/chat/ (the
 *  finding's plan) is a later wave's job — chat/page.tsx is not this track's
 *  file. */
function LiveMarkdown({ content, caret }: { content: string; caret: boolean }) {
  const cut = settledSplit(content);
  return (
    <div className={caret ? STREAMING_CARET_CLASS : undefined}>
      {cut > 0 && <MemoMarkdown content={content.slice(0, cut)} />}
      <Markdown content={cut > 0 ? content.slice(cut) : content} />
    </div>
  );
}

/** What a mission's Continue sends when the mission has no words of its own
 *  to send (an older daemon's view with neither objective nor task). */
export const CONTINUE_MESSAGE =
  "Iron Jarvis stopped before this objective was finished. Pick up where the team left off and finish it.";

/** The message an interrupted mission's Continue posts: the user's OWN
 *  objective (v1.309.0 review). The daemon stores a mission continuation's
 *  message as the new mission's display objective (orchestrator
 *  `options["objective"]`, sessions `_stamp_continuation`), so a fixed
 *  instruction here became "Your objective" and the Recent-list title of
 *  the resumed mission — words the user never typed. The model is not left
 *  without the instruction: the daemon wraps the message in its own recap
 *  ("[Continuing an earlier session. Original task: … Prior result: … The
 *  earlier workspace files are available …]"). */
export function continueMessage(view: MissionView): string {
  return (view.session.objective || view.session.task || "").trim() || CONTINUE_MESSAGE;
}

/** What "Run it again" posts (v1.309.0 review, mission-dead-end-when-model-down).
 *
 *  `/sessions/{id}/rerun` CLONES the run's stamped provider/model
 *  (orchestrator `rerun_session`: `provider=prev.provider`), and the stamp is
 *  the default AT CREATE. So after a mission failed because its model was
 *  down, the screen's own advice ("choose another default on Connections")
 *  followed by Run it again hit the same dead provider — the only way out was
 *  New task and typing the objective again. A FAILED mission therefore
 *  starts fresh through the door the composer uses, `POST /missions
 *  {objective, project_id}`, which takes the CURRENT default. That is not an
 *  auto-switch: the request names no model, exactly like Start (the
 *  never-auto-switch rule — the default is the user's own choice).
 *
 *  Two cases keep the rerun:
 *  * a STOPPED mission — the user stopped it, nothing was wrong with its
 *    model, and the rerun keeps every input the run had;
 *  * a failed CONTINUATION (its display objective differs from the task the
 *    model read): the user's follow-up words alone ("make it shorter") are not
 *    the job — the task carries the recap of the earlier work, and only the
 *    rerun keeps that recap and the workspace it names. */
export function runAgainRequest(view: MissionView): { path: string; body: Record<string, unknown> } {
  const s = view.session;
  const objective = (s.objective || "").trim();
  const continuation = Boolean(objective) && objective !== (s.task || "").trim();
  if (s.status === "failed" && objective && !continuation) {
    return { path: "/missions", body: { objective, ...(s.project_id ? { project_id: s.project_id } : {}) } };
  }
  return { path: `/sessions/${encodeURIComponent(s.id)}/rerun?wait=false`, body: {} };
}

/** A session row from continue / rerun / retry-failed → its id + project. */
function openedRow(row: unknown): { id: string; project: string } | null {
  if (!row || typeof row !== "object") return null;
  const o = row as Record<string, unknown>;
  const id = typeof o.id === "string" ? o.id : "";
  if (!id) return null;
  return { id, project: typeof o.project_id === "string" ? o.project_id : "" };
}

/** A refused retry, in the user's words (v1.310.0). The route has three 409
 *  sentences and they mean different things — "still running" and "a
 *  follow-up is already running" are WAIT, "nothing failed" is DONE — so the
 *  old one canned line ("Nothing is marked failed any more") told a user
 *  whose follow-up was still working that their failures had vanished. The
 *  daemon's own sentence is shown, capitalised and ended like a sentence;
 *  only the nothing-failed / no-list cases keep words of our own, and a
 *  reply that is not a plain sentence (a bare status line, JSON) never
 *  reaches the screen. */
function retryRefusal(message: string): string {
  const said = message.trim();
  if (/nothing failed/i.test(said)) return "Nothing is marked failed any more. The list may have changed.";
  if (/no worklist/i.test(said)) return "This mission kept no list of items, so there is nothing to retry.";
  if (!said || /^\d{3}\b|[{}\[\]]|\bdetail\b/i.test(said)) {
    return "Iron Jarvis could not retry this right now. Try again in a moment.";
  }
  return plainSentences(said);
}

/** The daemon's own refusal as plain sentences (v1.329.0): its 409 words join
 *  two clauses with a dash ("… is still running — wait for it to finish …");
 *  the calm copy rule has no dash asides, so each dash becomes a full stop and
 *  every sentence starts with a capital and ends with a stop. The words
 *  themselves are the daemon's, unchanged. */
export function plainSentences(said: string): string {
  return said
    .split(/\s+[\u2014\u2013]\s+/)
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const sentence = part[0].toUpperCase() + part.slice(1);
      return /[.!?]$/.test(sentence) ? sentence : `${sentence}.`;
    })
    .join(" ");
}

function actionError(e: unknown, what: string): string {
  if (e instanceof ApiError && e.status === 409) {
    return what === "retry" ? retryRefusal(e.message) : "This objective is already being worked on.";
  }
  if (e instanceof ApiError && (e.status === 404 || e.status === 405)) {
    return what === "retry"
      ? "This daemon can't retry failed items yet. Restart Iron Jarvis to update it."
      : "This objective no longer exists.";
  }
  return e instanceof Error && e.message ? e.message : "Could not start it.";
}

/** The receipt's verb, by what the mission's status says actually happened
 *  (v1.309.0 review). `session.provider/model` is the provider the
 *  orchestrator STAMPED at create (`provider or default_provider`), not proof
 *  that anything answered — so a mission refused because its default model
 *  was down read "Could not finish" with "Answered by fleet-custom" under it.
 *  Only a COMPLETED mission was answered; a running one is working on it; a
 *  failed one tried it; a stopped (or restart-interrupted) one ran on it. */
function receiptVerb(view: MissionView): string {
  const status = view.session.status;
  // v1.310.0: the rule lives in ./receipt (shared with every teammate card);
  // any non-terminal session status — active, queued — is still at it.
  if (!MISSION_TERMINAL.has(status)) return "Working on";
  return receiptVerbFor(status, view.session.interrupted);
}

/** Who did the work: provider · model, quiet; amber when the mock answered
 *  (nothing real ran), with the daemon's route note when a failover touched
 *  the mission. Renders nothing when the daemon said nothing. */
function MissionReceipt({ view }: { view: MissionView }) {
  const { provider, model, route_note: note } = view.session;
  if (!provider && !model && !note) return null;
  const mock = provider === "mock";
  const pair = [provider, model].filter((x, i, all) => x && all.indexOf(x) === i).join(" · ");
  const answered = view.session.status === "completed";
  return (
    <div
      data-testid="mission-receipt"
      data-mock={mock ? "true" : undefined}
      className={`border-t hairline px-5 py-2 text-[12px] ${mock ? "text-tone-warn" : "text-zinc-500"}`}
    >
      {mock ? (
        <span>
          {/* v1.329.0: the chat receipt's words, plain sentences. */}
          {mockLine(answered)}{" "}
          <Link href="/connections" className="underline hover:opacity-80">
            Connect a model on Connections
          </Link>
        </span>
      ) : pair ? (
        <span>
          {receiptVerb(view)} {pair}
        </span>
      ) : null}
      {note && <div className={`${pair || mock ? "mt-0.5 " : ""}text-tone-warn`}>{note}</div>}
    </div>
  );
}

export function MissionOutput({
  view,
  liveStore,
  objective,
  onChanged,
  onOpen,
}: {
  view: MissionView | null;
  /** The mission's live text — read HERE, so a flush re-renders this panel only. */
  liveStore: MissionLiveStore;
  /** The request, shown before the first view arrives. */
  objective: string;
  onChanged: () => void;
  /** Open another mission (a continuation, a re-run) — inside its project. */
  onOpen: (id: string, projectId: string) => void;
}) {
  const live = useMissionLive(liveStore);
  const [tab, setTab] = useState<OutputTab>("report");
  const [picked, setPicked] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const [stopNote, setStopNote] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);
  const [actNote, setActNote] = useState<string | null>(null);
  const [followup, setFollowup] = useState("");
  const out = deliverableFor(view, live);
  const docs = view?.deliverable.documents ?? [];
  const running = view !== null && !MISSION_TERMINAL.has(view.session.status);
  const terminal = view !== null && MISSION_TERMINAL.has(view.session.status);
  const sid = view?.session.id ?? "";
  const project = view?.session.project_id ?? "";
  const continuedAs = view?.session.continued_as ?? null;
  const failedItems = view?.deliverable.worklist?.failed ?? 0;

  // Keep a preview selection only while that file is still in the list.
  useEffect(() => {
    if (picked && !docs.includes(picked)) setPicked(null);
  }, [docs, picked]);

  const stop = async () => {
    if (!sid) return;
    setStopping(true);
    setStopNote(null);
    try {
      await post(`/sessions/${encodeURIComponent(sid)}/cancel`, {});
      onChanged();
    } catch (e) {
      setStopNote(e instanceof Error ? e.message : "Could not stop it.");
    } finally {
      setStopping(false);
    }
  };

  // One press → one request → the NEW mission opens. `busy` is the button's
  // own name so the right one says "Starting…".
  const act = async (name: string, path: string, body: Record<string, unknown>) => {
    if (!sid || acting) return;
    setActing(name);
    setActNote(null);
    try {
      const row = openedRow(await post<unknown>(path, body));
      if (row) {
        if (name === "followup") setFollowup("");
        onOpen(row.id, row.project || project);
      } else {
        setActNote("The daemon did not say which mission it started.");
        onChanged();
      }
    } catch (e) {
      setActNote(actionError(e, name));
    } finally {
      setActing(null);
    }
  };
  const enc = encodeURIComponent(sid);
  const askForChanges = () => {
    const text = followup.trim();
    if (text) void act("followup", `/sessions/${enc}/continue`, { message: text, wait: false });
  };

  const waiting = view?.coordinator.waiting_on ?? null;
  const memberAsks = (view?.members ?? []).filter((m) => m.waiting_on);
  const tabs: { key: OutputTab; label: string }[] = [
    { key: "report", label: "Report" },
    { key: "markdown", label: "Markdown" },
    { key: "preview", label: docs.length ? `Preview (${docs.length})` : "Preview" },
  ];

  return (
    <section data-testid="mission-output" className="card-surface flex min-h-[28rem] flex-col p-0">
      <header className="border-b hairline px-5 py-4">
        <div className="text-[11px] uppercase tracking-wide text-zinc-500">Your objective</div>
        <div className="mt-1 flex items-start justify-between gap-4">
          <p data-testid="mission-objective" className="whitespace-pre-wrap text-[15px] font-medium text-zinc-100">
            {view?.session.objective || view?.session.task || objective}
          </p>
          {terminal && (
            <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
              {continuedAs ? (
                <a
                  data-testid="mission-continued-as"
                  href={missionPath(continuedAs, project)}
                  onClick={(e) => {
                    // An in-page move: the page's route is its own state, so
                    // a full navigation would only reload the same screen.
                    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
                    e.preventDefault();
                    onOpen(continuedAs, project);
                  }}
                  className="text-[12px] text-accent hover:underline"
                >
                  Continued in → open
                </a>
              ) : view?.session.interrupted ? (
                <button
                  type="button"
                  data-testid="mission-continue"
                  disabled={acting !== null}
                  onClick={() =>
                    view &&
                    void act("continue", `/sessions/${enc}/continue`, {
                      message: continueMessage(view),
                      wait: false,
                    })
                  }
                  className="btn-accent px-3 py-1 text-xs"
                >
                  <RotateCcw size={12} /> {acting === "continue" ? "Starting…" : "Continue"}
                </button>
              ) : view?.session.status === "failed" || view?.session.status === "cancelled" ? (
                <button
                  type="button"
                  data-testid="mission-rerun"
                  disabled={acting !== null}
                  title={
                    view && runAgainRequest(view).path === "/missions"
                      ? "Starts this objective fresh on your current default model"
                      : "Runs it again with the same model and settings"
                  }
                  onClick={() => {
                    if (!view) return;
                    const again = runAgainRequest(view);
                    void act("rerun", again.path, again.body);
                  }}
                  className="btn-ghost px-3 py-1 text-xs"
                >
                  <RotateCcw size={12} /> {acting === "rerun" ? "Starting…" : "Run it again"}
                </button>
              ) : null}
              {!continuedAs && failedItems > 0 && (
                <button
                  type="button"
                  data-testid="mission-retry-failed"
                  disabled={acting !== null}
                  onClick={() => void act("retry", `/missions/${enc}/retry-failed`, {})}
                  className="btn-ghost px-3 py-1 text-xs"
                >
                  {acting === "retry"
                    ? "Starting…"
                    : `Retry the ${failedItems} failed item${failedItems === 1 ? "" : "s"}`}
                </button>
              )}
            </div>
          )}
          {running && (
            <button
              type="button"
              data-testid="mission-stop"
              disabled={stopping}
              onClick={() => void stop()}
              className="btn-ghost shrink-0 px-3 py-1 text-xs"
            >
              <Square size={12} /> Stop
            </button>
          )}
        </div>
        <div className="mt-1.5 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <div data-testid="mission-headline" className="text-[12px] text-zinc-400">
            {view ? missionHeadline(view) : "Starting…"}
            {running && (live.coordinatorPhase || live.coordinatorNarration)
              ? ` · ${live.coordinatorPhase || live.coordinatorNarration}`
              : ""}
          </div>
          {/* v1.309.0 review (mission-dead-end-no-followup): the coordinator's
              own session page holds the full transcript and its export — the
              cards link each teammate's run, and this is Jarvis's. */}
          {sid && (
            <Link
              data-testid="mission-open-run"
              href={`/sessions/${enc}`}
              className="shrink-0 text-[12px] text-zinc-500 hover:text-zinc-300 hover:underline"
            >
              Open the full run
            </Link>
          )}
        </div>
        {stopNote && <div className="mt-1 text-[12px] text-tone-danger">{stopNote}</div>}
        {actNote && (
          <div data-testid="mission-action-note" className="mt-1 text-[12px] text-tone-danger">
            {actNote}
          </div>
        )}
        {waiting && <ApprovalCard waiting={waiting} who={view?.coordinator.name ?? "Jarvis"} onAnswered={onChanged} />}
        {memberAsks.map((m) => (
          <ApprovalCard key={m.waiting_on!.approval_id} waiting={m.waiting_on!} who={m.name} onAnswered={onChanged} />
        ))}
      </header>

      <div role="tablist" aria-label="Output views" className="flex items-center gap-1 border-b hairline px-4 pt-2">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            data-testid={`mission-tab-${t.key}`}
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={`-mb-px rounded-t-lg border-b-2 px-3 py-1.5 text-[13px] transition-colors ${
              tab === t.key
                ? "border-accent font-semibold text-zinc-100"
                : "border-transparent text-zinc-400 hover:text-zinc-200"
            }`}
          >
            {t.label}
          </button>
        ))}
        <div className="ml-auto pb-1">
          {out.text && tab !== "preview" && <CopyIconButton text={out.text} title="Copy the result" />}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {out.source === "draft" && (
          <div data-testid="mission-draft-note" className="mb-2 text-[12px] text-zinc-500">
            Draft in progress. {out.who} is writing.
          </div>
        )}
        {tab === "report" &&
          (out.text ? (
            <div data-testid="mission-report" data-source={out.source} className="text-[14px] leading-relaxed text-zinc-200">
              {out.source === "final" ? (
                <MemoMarkdown content={out.text} />
              ) : (
                <LiveMarkdown content={out.text} caret={running} />
              )}
            </div>
          ) : (
            <EmptyOutput running={running} failed={view?.session.status === "failed"} />
          ))}
        {tab === "markdown" &&
          (out.text ? (
            <pre
              data-testid="mission-markdown"
              className="whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed text-zinc-300"
            >
              {out.text}
            </pre>
          ) : (
            <EmptyOutput running={running} failed={view?.session.status === "failed"} />
          ))}
        {tab === "preview" &&
          (docs.length === 0 ? (
            <p data-testid="mission-preview-empty" className="text-[13px] text-zinc-500">
              {running
                ? "Files the team creates appear here as they are written."
                : "The team did not create any files. The result is in Report."}
            </p>
          ) : (
            <div data-testid="mission-preview" className="space-y-3">
              <div className="flex flex-wrap gap-1.5">
                {docs.map((f) => (
                  <button
                    key={f}
                    type="button"
                    title={f}
                    onClick={() => setPicked(f)}
                    className={`inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[12px] ${
                      picked === f ? "bg-accent/20 text-accent" : "bg-white/5 text-zinc-300 hover:bg-white/10"
                    }`}
                  >
                    <FileText size={12} /> {baseName(f)}
                  </button>
                ))}
              </div>
              {picked ? (
                <div className="h-[32rem]">
                  <DocPreview path={picked} onClose={() => setPicked(null)} compareCandidates={docs} />
                </div>
              ) : (
                <p className="text-[12px] text-zinc-500">Pick a file to preview it.</p>
              )}
            </div>
          ))}
      </div>

      {view && <MissionReceipt view={view} />}

      {terminal && !continuedAs && (
        <div data-testid="mission-followup" className="flex items-end gap-2 border-t hairline px-4 py-3">
          <textarea
            data-testid="mission-followup-input"
            value={followup}
            onChange={(e) => setFollowup(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                askForChanges();
              }
            }}
            rows={1}
            placeholder="Ask for changes, for example make it shorter or add a summary table"
            aria-label="Ask for changes"
            className="field min-h-[2.25rem] flex-1 resize-y text-[13px]"
          />
          <button
            type="button"
            data-testid="mission-followup-send"
            disabled={acting !== null || !followup.trim()}
            onClick={askForChanges}
            className="btn-accent shrink-0 px-3 py-1.5 text-xs"
          >
            {acting === "followup" ? "Starting…" : "Ask for changes"}
          </button>
        </div>
      )}
    </section>
  );
}

function EmptyOutput({ running, failed }: { running: boolean; failed: boolean }) {
  return (
    <div data-testid="mission-output-empty" className="flex h-full min-h-[14rem] items-center justify-center">
      <p className="max-w-sm text-center text-[13px] text-zinc-500">
        {running
          ? "Your result appears here as the team writes it. Watch the work underneath."
          : failed
            ? "The team could not finish this objective. The activity below says where it stopped."
            : "No result was written."}
      </p>
    </div>
  );
}
