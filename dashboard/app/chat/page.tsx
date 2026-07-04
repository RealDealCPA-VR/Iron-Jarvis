"use client";

// The friendly front door under "Work": the user types what they want and a real
// Iron Jarvis agent replies. It either answers conversationally or uses whatever
// tools it needs — all server-side. The first message opens a session; every later
// message in the same chat continues it.
//
// Sending is NON-BLOCKING: we POST with wait:false (the agent runs in the
// background) and then show a live "working" bubble that narrates the agent's
// steps from the /events stream. We finalize when the session's `agent.completed`
// event arrives (or, as a fallback when the socket is down, by polling the
// session until its status flips to completed/failed).

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Bot, Loader2, MessageSquare, Plus, Send, Sparkles, Square, User } from "lucide-react";
import { get, post, ApiError } from "@/lib/api";
import type { IJEvent, ModelOption, SessionView } from "@/lib/types";
import { useEvents } from "@/lib/useEvents";
import { Card, Empty, ErrorNote, LoaderInline, OfflineHint } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

// Prompts the user can click to prefill the composer on an empty chat.
const EXAMPLES = [
  "What can you do?",
  "Summarize the files in a folder",
  "Draft a follow-up email to a client",
];

// A few agent states worth naming; anything else falls back to "Working…".
const STATE_LABEL: Record<string, string> = {
  initializing: "Getting ready…",
  running: "Working…",
  waiting: "Waiting…",
  paused: "Paused…",
  delegating: "Bringing in a helper…",
  reviewing: "Reviewing the work…",
  completed: "Wrapping up…",
};

// Turn one raw session event into a short, human-friendly progress line (or null
// to skip events that don't read well as a step).
function stepLabel(e: IJEvent): string | null {
  const p = e.payload || {};
  switch (e.type) {
    case "agent.started":
      return "Thinking…";
    case "agent.state_changed": {
      // Backend payload is {from, to}; tolerate a `state` alias just in case.
      const to = (p.to ?? p.state) as string | undefined;
      if (!to) return "Working…";
      return STATE_LABEL[to.toLowerCase()] ?? "Working…";
    }
    case "tool.executed": {
      const tool = p.tool as string | undefined;
      return tool ? `Using ${tool}…` : "Using a tool…";
    }
    case "tool.denied": {
      const tool = p.tool as string | undefined;
      return tool ? `Skipped ${tool} (not permitted)` : "Skipped a tool";
    }
    case "provider.failed": {
      const provider = p.provider as string | undefined;
      return `Provider ${provider} failed — ${String(p.error || "").slice(0, 120)}`;
    }
    case "provider.downgraded":
      return "Model not connected — using offline mock (connect a model)";
    case "agent.completed":
      return "Finishing up…";
    default:
      return null;
  }
}

// The model <select> encodes the choice as `${provider}::${model}` (empty => let the
// server pick its default). Split it back out only when it carries both halves.
function splitChoice(choice: string): { provider?: string; model?: string } {
  const i = choice.indexOf("::");
  if (i === -1) return {};
  const provider = choice.slice(0, i);
  const model = choice.slice(i + 2);
  return provider && model ? { provider, model } : {};
}

function Bubble({ role, children }: { role: ChatMessage["role"]; children: ReactNode }) {
  const isUser = role === "user";
  return (
    <div className={`flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
      <span
        className={`grid h-8 w-8 shrink-0 place-items-center rounded-xl border ${
          isUser
            ? "border-accent/30 bg-accent/10 text-accent-soft"
            : "border-white/[0.08] bg-white/[0.03] text-zinc-300"
        }`}
      >
        {isUser ? <User size={15} /> : <Bot size={15} />}
      </span>
      <div
        className={`max-w-[80%] whitespace-pre-wrap rounded-2xl border px-4 py-2.5 text-sm leading-relaxed ${
          isUser
            ? "border-accent/25 bg-accent/[0.1] text-zinc-100"
            : "border-white/[0.06] bg-white/[0.03] text-zinc-200"
        }`}
      >
        {children}
      </div>
    </div>
  );
}

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  // The session id of the turn currently in flight (null when idle). Drives the
  // live "working" bubble, the completion watcher, and the polling fallback.
  const [awaitingId, setAwaitingId] = useState<string | null>(null);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [choice, setChoice] = useState(""); // "" => server default model
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);

  const { events } = useEvents(150);

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // Latest events, readable synchronously inside send() without re-subscribing.
  const eventsRef = useRef<IJEvent[]>(events);
  eventsRef.current = events;
  // Event-id boundary captured at the start of each turn: we only treat events
  // NEWER than this as belonging to the current turn. This stops a stale
  // `agent.completed` from the previous turn (same session id, still in the
  // buffer) from instantly "completing" the next turn.
  const sinceRef = useRef<string | null>(null);
  // Guards against overlapping finalize attempts (events + polling can both fire).
  const finalizingRef = useRef(false);

  const awaiting = awaitingId !== null;

  // Load the model catalog for the header picker (best-effort — stays on "default").
  useEffect(() => {
    let cancelled = false;
    get<{ models: ModelOption[] }>("/models")
      .then((d) => {
        if (!cancelled) setModels(d.models);
      })
      .catch(() => {
        /* picker just stays on the server default */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Human-readable steps for the current turn, newest-first. Only events after the
  // turn boundary and tagged with this session's id count; consecutive duplicates
  // are collapsed so "Working…, Working…" reads as one line.
  const progress = useMemo(() => {
    if (!awaitingId) return [] as string[];
    const boundary = sinceRef.current;
    const out: string[] = [];
    for (const e of events) {
      if (e.id === boundary) break; // reached events from before this turn
      if (e.session_id !== awaitingId) continue;
      const label = stepLabel(e);
      if (!label) continue;
      if (out.length && out[out.length - 1] === label) continue;
      out.push(label);
    }
    return out;
  }, [events, awaitingId]);

  // Keep the newest message (or the live working bubble) in view.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, awaitingId, progress.length]);

  // Fetch the finished session and turn it into the assistant's reply. Only acts
  // once the session has actually reached a terminal status (the `agent.completed`
  // event can land a beat before the session row flips), so a not-yet-done fetch
  // simply returns and lets the next event/poll retry.
  async function finalize(id: string) {
    if (finalizingRef.current) return;
    finalizingRef.current = true;
    try {
      // GET /sessions/{id} returns { session, transcript } — the session is
      // NESTED (unlike POST /sessions, which returns it flat). Read from the
      // wrapper, tolerating both shapes, so completion is actually detected
      // (reading a top-level `status` here always returned undefined => the
      // chat spun forever even though the session had finished).
      const res = await get<{ session?: SessionView } & Partial<SessionView>>(
        `/sessions/${id}`,
      );
      const session = (res.session ?? (res as SessionView)) || ({} as SessionView);
      setOffline(false); // the daemon answered — clear any transient-blip banner
      const status = (session.status || "").toLowerCase();
      if (status !== "completed" && status !== "failed" && status !== "cancelled") {
        return; // still running — leave the working bubble up; retry later
      }
      const summary = (session.summary || "").trim();
      const content =
        status === "completed"
          ? summary || "(no response)"
          : summary ||
            `The agent stopped before finishing (${status}). Please try again.`;
      setMessages((prev) => [...prev, { role: "assistant", content }]);
      setAwaitingId(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) {
        // Transient network blip — keep the turn alive and let the 1.5s poll
        // retry, so a reply that already completed server-side isn't dropped.
        setOffline(true);
        return;
      }
      // Hard failure: surface it and stop waiting so the turn doesn't hang forever.
      setError(e instanceof ApiError ? e.message : String(e));
      setAwaitingId(null);
    } finally {
      finalizingRef.current = false;
    }
  }

  // PRIMARY completion signal: watch the live event stream for this session's
  // `agent.completed`. Scan only events newer than the turn boundary.
  useEffect(() => {
    if (!awaitingId) return;
    const boundary = sinceRef.current;
    for (const e of events) {
      if (e.id === boundary) break;
      if (e.session_id === awaitingId && e.type === "agent.completed") {
        void finalize(awaitingId);
        break;
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events, awaitingId]);

  // FALLBACK: if the /events socket is down, poll the session until it finishes.
  // The interval is torn down whenever the turn ends or the component unmounts.
  useEffect(() => {
    if (!awaitingId) return;
    const timer = setInterval(() => void finalize(awaitingId), 1500);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [awaitingId]);

  async function send(text: string) {
    const message = text.trim();
    if (!message || awaiting) return;
    setError(null);
    setOffline(false);
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: message }]);
    // Mark where "this turn" begins in the event stream BEFORE kicking off work.
    sinceRef.current = eventsRef.current[0]?.id ?? null;
    try {
      let session: SessionView;
      if (sessionId) {
        // Continue the same chat — runs in the background (wait:false).
        session = await post<SessionView>(`/sessions/${sessionId}/continue`, {
          message,
          wait: false,
        });
      } else {
        // First message opens a session.
        const { provider, model } = splitChoice(choice);
        session = await post<SessionView>("/sessions", {
          task: message,
          agent_type: "builder",
          wait: false,
          ...(provider ? { provider } : {}),
          ...(model ? { model } : {}),
        });
      }
      // ALWAYS chain forward to the returned session id: `continue` spawns a NEW
      // session (recapping the old one), so the next turn must continue from it —
      // sticking with the first id would silently drop the intermediate turns.
      setSessionId(session.id);
      // Hand off to the event watcher + polling fallback to surface the reply.
      setAwaitingId(session.id);
    } catch (e) {
      // Keep the typed thread intact — only surface the failure.
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  // Ask the daemon to cancel the in-flight turn, then release the composer.
  // Cancel is best-effort — even if it fails server-side we stop waiting locally.
  function stop() {
    if (!awaitingId) return;
    post(`/sessions/${awaitingId}/cancel`).catch(() => {});
    setMessages((prev) => [...prev, { role: "assistant", content: "Stopped." }]);
    setAwaitingId(null); // also tears down the event watcher + polling interval
  }

  function newChat() {
    setMessages([]);
    setSessionId(null);
    setAwaitingId(null); // also tears down any polling interval
    setInput("");
    setError(null);
    setOffline(false);
    sinceRef.current = null;
    finalizingRef.current = false;
  }

  function prefill(text: string) {
    setInput(text);
    inputRef.current?.focus();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends; Shift+Enter inserts a newline.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send(input);
    }
  }

  const started = messages.length > 0 || sessionId !== null;

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Chat"
          subtitle="Talk to your Iron Jarvis agent. Ask anything — it answers conversationally, and reads files, searches, and uses tools on its own whenever that helps."
          actions={
            <div className="flex items-center gap-2">
              <select
                aria-label="Model"
                value={choice}
                onChange={(e) => setChoice(e.target.value)}
                disabled={awaiting || started}
                title={started ? "Start a new chat to switch models" : "Model for this chat"}
                className="field w-auto py-1.5 text-[13px]"
              >
                <option value="">default model</option>
                {models.map((m) => {
                  const v = `${m.provider}::${m.model}`;
                  return (
                    <option key={v} value={v}>
                      {m.provider} · {m.model}
                    </option>
                  );
                })}
              </select>
              <button
                onClick={newChat}
                disabled={!started}
                className="btn-ghost py-1.5 text-[13px]"
              >
                <Plus size={14} /> New chat
              </button>
            </div>
          }
        />
      </Reveal>

      <Reveal>
        <p className="flex items-center gap-2 text-xs text-zinc-500">
          <Sparkles size={13} className="shrink-0 text-accent-soft/70" />
          Replies come from a real agent that can read files, search, and use tools — you&apos;ll
          see its steps live as it works.
        </p>
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint detail="Chat needs it running to reach your agent." />
        </Reveal>
      )}

      <Reveal>
        <Card pad={false} className="overflow-hidden">
          {/* Message thread */}
          <div className="flex max-h-[60vh] min-h-[24rem] flex-col gap-4 overflow-y-auto p-4 sm:p-5">
            {messages.length === 0 && !awaiting ? (
              <div className="flex flex-1 flex-col items-center justify-center gap-4">
                <Empty icon={<MessageSquare size={28} />}>
                  Start a conversation. Ask a question or describe what you need — your agent
                  replies and reaches for tools on its own when they help.
                </Empty>
                <div className="flex flex-wrap justify-center gap-2">
                  {EXAMPLES.map((ex) => (
                    <button
                      key={ex}
                      onClick={() => prefill(ex)}
                      className="rounded-full border border-white/[0.08] bg-white/[0.02] px-3 py-1.5 text-xs text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft"
                    >
                      {ex}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <>
                {messages.map((m, i) => (
                  <Bubble key={i} role={m.role}>
                    {m.content}
                  </Bubble>
                ))}
                {awaiting && (
                  <Bubble role="assistant">
                    <div className="flex flex-col gap-1.5">
                      <span className="inline-flex items-center gap-2 text-zinc-300">
                        <Loader2 size={14} className="animate-spin text-accent-soft" />
                        {progress[0] ?? "Thinking…"}
                      </span>
                      {progress.length > 1 && (
                        <ul className="ml-[22px] space-y-0.5 text-xs text-zinc-500">
                          {progress.slice(1, 4).map((s, i) => (
                            <li key={i} className="flex items-center gap-1.5">
                              <span className="h-1 w-1 shrink-0 rounded-full bg-zinc-600" />
                              {s}
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  </Bubble>
                )}
              </>
            )}
            <div ref={bottomRef} />
          </div>

          {error && (
            <div className="border-t hairline p-3">
              <ErrorNote>{error}</ErrorNote>
            </div>
          )}

          {/* Composer */}
          <div className="flex items-end gap-2 border-t hairline p-3">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={awaiting}
              rows={1}
              aria-label="Message"
              placeholder="Message Iron Jarvis…  (Enter to send · Shift+Enter for a new line)"
              className="field max-h-40 min-h-[2.75rem] flex-1 resize-none disabled:opacity-60"
            />
            {awaiting && (
              <button
                onClick={stop}
                className="btn-ghost h-[2.75rem] px-3 py-0 text-[13px]"
                title="Stop this turn"
              >
                <Square size={14} /> Stop
              </button>
            )}
            <button
              onClick={() => send(input)}
              disabled={awaiting || !input.trim()}
              className="btn-accent h-[2.75rem] px-4 py-0 text-[13px]"
            >
              {awaiting ? (
                <LoaderInline />
              ) : (
                <>
                  <Send size={16} /> Send
                </>
              )}
            </button>
          </div>
        </Card>
      </Reveal>
    </PageShell>
  );
}
