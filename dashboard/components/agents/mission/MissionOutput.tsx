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

import { useEffect, useState } from "react";
import { FileText, Square } from "lucide-react";
import { ApiError, post } from "@/lib/api";
import { MemoMarkdown, CopyIconButton } from "@/components/Markdown";
import { DocPreview } from "@/components/chat/DocPreview";
import {
  MISSION_TERMINAL,
  baseName,
  missionHeadline,
  type MissionView,
  type WaitingOn,
} from "@/lib/mission";
import type { MissionLive } from "@/lib/useMission";

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
      className="mt-3 rounded-xl border border-amber-400/30 bg-amber-300/10 px-3 py-2.5 text-[13px] text-amber-100"
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
      {note && <div className="mt-1 text-[12px] text-amber-200/80">{note}</div>}
    </div>
  );
}

export function MissionOutput({
  view,
  live,
  objective,
  onChanged,
}: {
  view: MissionView | null;
  live: MissionLive;
  /** The request, shown before the first view arrives. */
  objective: string;
  onChanged: () => void;
}) {
  const [tab, setTab] = useState<OutputTab>("report");
  const [picked, setPicked] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const [stopNote, setStopNote] = useState<string | null>(null);
  const out = deliverableFor(view, live);
  const docs = view?.deliverable.documents ?? [];
  const running = view !== null && !MISSION_TERMINAL.has(view.session.status);
  const sid = view?.session.id ?? "";

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

  const waiting = view?.coordinator.waiting_on ?? null;
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
            {view?.session.task || objective}
          </p>
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
        <div data-testid="mission-headline" className="mt-1.5 text-[12px] text-zinc-400">
          {view ? missionHeadline(view) : "Starting…"}
          {running && live.coordinatorPhase ? ` · ${live.coordinatorPhase}` : ""}
        </div>
        {stopNote && <div className="mt-1 text-[12px] text-rose-300">{stopNote}</div>}
        {waiting && <ApprovalCard waiting={waiting} who={view?.coordinator.name ?? "Jarvis"} onAnswered={onChanged} />}
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
            Draft in progress — {out.who} is writing
          </div>
        )}
        {tab === "report" &&
          (out.text ? (
            <div data-testid="mission-report" data-source={out.source} className="text-[14px] leading-relaxed text-zinc-200">
              <MemoMarkdown content={out.text} />
              {out.source !== "final" && running && (
                <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse rounded-sm bg-accent align-middle" />
              )}
            </div>
          ) : (
            <EmptyOutput running={running} failed={view?.session.status === "failed"} />
          ))}
        {tab === "markdown" &&
          (out.text ? (
            <pre
              data-testid="mission-markdown"
              className="whitespace-pre-wrap break-words font-mono text-[12.5px] leading-relaxed text-zinc-300"
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
                : "The team did not create any files — the result is in Report."}
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
            ? "The team could not finish this objective — the activity below says where it stopped."
            : "No result was written."}
      </p>
    </div>
  );
}
