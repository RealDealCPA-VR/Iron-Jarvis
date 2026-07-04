"use client";

// The friendly front door under "Work". Two modes, one thread:
//
// CHAT (default): a DIRECT completion via POST /chat — the full local bubble
// history is sent on every turn and the reply comes back in seconds. Personas
// and file attachments ride along (text is extracted server-side; images go to
// vision). No session machinery at all — multi-turn is just the local array.
//
// AGENT: the original session-based flow, preserved verbatim. The message opens
// (or continues) a real Iron Jarvis session that can use tools. Sending is
// NON-BLOCKING: we POST with wait:false (the agent runs in the background) and
// then show a live "working" bubble that narrates the agent's steps from the
// /events stream. We finalize when the session's `agent.completed` event
// arrives (or, as a fallback when the socket is down, by polling the session
// until its status flips to completed/failed).

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Bot,
  Loader2,
  MessageSquare,
  Paperclip,
  Plus,
  Send,
  Sparkles,
  Square,
  User,
  Wrench,
  X,
} from "lucide-react";
import { get, post, ApiError } from "@/lib/api";
import type { IJEvent, ModelOption, SessionView } from "@/lib/types";
import { useEvents } from "@/lib/useEvents";
import { Card, Empty, ErrorNote, LoaderInline, OfflineHint } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

type Mode = "chat" | "agent";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  /** Display names of files attached to this (user) message — footer chips. */
  attachmentNames?: string[];
}

/** What POST /chat expects. */
interface ChatRequestMessage {
  role: "user" | "assistant";
  content: string;
}
interface ChatRequestBody {
  messages: ChatRequestMessage[];
  provider?: string;
  model?: string;
  persona?: string;
  attachments?: string[]; // uploaded document paths
}
interface ChatResponse {
  reply: string;
  provider?: string;
  model?: string;
  images?: string[];
}

interface PersonaOption {
  name: string;
  description: string;
}

/** POST /documents/upload response (same contract NewSessionForm uses). */
interface UploadResult {
  path: string;
  name: string;
  bytes?: number;
}

/** One uploaded, ready-to-send attachment chip. */
interface UploadedFile {
  name: string;
  path: string;
  bytes: number;
}

// Attachment limits: keep uploads snappy and the /chat context sane.
const MAX_ATTACHMENTS = 4;
const MAX_FILE_BYTES = 20 * 1024 * 1024; // 20 MB

// Persona persistence (chat mode only).
const PERSONA_KEY = "ij_chat_persona";
const PERSONA_CUSTOM_KEY = "ij_chat_persona_custom";
const CUSTOM_PERSONA = "__custom__";

// Fallback until GET /chat/personas answers (or if it never does).
const DEFAULT_PERSONAS: PersonaOption[] = [
  { name: "assistant", description: "Helpful general-purpose assistant" },
];

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

/** Read a File as raw base64 (FileReader gives a data: URL — strip the prefix). */
function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("could not read file"));
    reader.onload = () => {
      const res = String(reader.result);
      const comma = res.indexOf(",");
      resolve(comma >= 0 ? res.slice(comma + 1) : res);
    };
    reader.readAsDataURL(file);
  });
}

function fmtSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

function capitalize(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
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

/** The small "attached files" footer under a user bubble. */
function AttachmentFooter({ names }: { names: string[] }) {
  if (names.length === 0) return null;
  return (
    <div className="mt-1.5 space-y-0.5 border-t border-white/10 pt-1.5">
      {names.map((n, i) => (
        <div key={`${n}-${i}`} className="flex items-center gap-1.5 text-[11px] text-zinc-400">
          <Paperclip size={10} className="shrink-0 text-accent-soft/70" />
          {n}
        </div>
      ))}
    </div>
  );
}

export default function ChatPage() {
  const [mode, setMode] = useState<Mode>("chat");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  // AGENT MODE: the session id of the turn currently in flight (null when idle).
  // Drives the live "working" bubble, the completion watcher, and the polling
  // fallback.
  const [awaitingId, setAwaitingId] = useState<string | null>(null);
  // CHAT MODE: a direct /chat call is in flight (drives the shimmer bubble).
  const [chatBusy, setChatBusy] = useState(false);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [choice, setChoice] = useState(""); // "" => server default model
  const [personas, setPersonas] = useState<PersonaOption[]>(DEFAULT_PERSONAS);
  const [persona, setPersona] = useState("assistant");
  const [customPersona, setCustomPersona] = useState("");
  const [attachments, setAttachments] = useState<UploadedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);

  const { events } = useEvents(150);

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  // Latest events, readable synchronously inside send() without re-subscribing.
  const eventsRef = useRef<IJEvent[]>(events);
  eventsRef.current = events;
  // Latest attachments, readable from the window-level drop handler (which is
  // registered once and would otherwise close over a stale array).
  const attachmentsRef = useRef<UploadedFile[]>(attachments);
  attachmentsRef.current = attachments;
  // Event-id boundary captured at the start of each agent turn: we only treat
  // events NEWER than this as belonging to the current turn. This stops a stale
  // `agent.completed` from the previous turn (same session id, still in the
  // buffer) from instantly "completing" the next turn.
  const sinceRef = useRef<string | null>(null);
  // Guards against overlapping finalize attempts (events + polling can both fire).
  const finalizingRef = useRef(false);
  // Bumped by "New chat" so an in-flight /chat reply from the OLD thread can't
  // land in the fresh one.
  const chatGenRef = useRef(0);

  const awaiting = awaitingId !== null;
  const busy = awaiting || chatBusy;

  // Load the model catalog for the header picker (best-effort — stays on "default").
  useEffect(() => {
    let cancelled = false;
    get<{ models: ModelOption[] }>("/models")
      .then((d) => {
        // Only offer models the user can ACTUALLY run (provider connected);
        // tolerate older daemons that don't send the flag.
        if (!cancelled) setModels(d.models.filter((m) => m.available !== false));
      })
      .catch(() => {
        /* picker just stays on the server default */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Load the persona catalog (best-effort — falls back to "assistant" + Custom).
  useEffect(() => {
    let cancelled = false;
    get<{ personas: PersonaOption[] }>("/chat/personas")
      .then((d) => {
        if (!cancelled && d.personas?.length) setPersonas(d.personas);
      })
      .catch(() => {
        /* keep the fallback list */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Restore the saved persona choice + custom text (after mount, so SSR markup
  // matches the first client render).
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(PERSONA_KEY);
      if (saved) setPersona(saved);
      const savedCustom = window.localStorage.getItem(PERSONA_CUSTOM_KEY);
      if (savedCustom) setCustomPersona(savedCustom);
    } catch {
      /* ignore */
    }
  }, []);

  function choosePersona(value: string) {
    setPersona(value);
    try {
      window.localStorage.setItem(PERSONA_KEY, value);
    } catch {
      /* ignore */
    }
  }

  function editCustomPersona(value: string) {
    setCustomPersona(value);
    try {
      window.localStorage.setItem(PERSONA_CUSTOM_KEY, value);
    } catch {
      /* ignore */
    }
  }

  // ---------------------------------------------------------------- attachments

  async function addFiles(files: File[]) {
    setError(null);
    const room = MAX_ATTACHMENTS - attachmentsRef.current.length;
    if (room <= 0) {
      setError(`Up to ${MAX_ATTACHMENTS} files per message.`);
      return;
    }
    const accepted: File[] = [];
    for (const f of files) {
      if (f.size > MAX_FILE_BYTES) {
        setError(`${f.name} is too large (max 20 MB).`);
        continue;
      }
      if (accepted.length >= room) {
        setError(`Up to ${MAX_ATTACHMENTS} files per message.`);
        break;
      }
      accepted.push(f);
    }
    if (accepted.length === 0) return;
    setUploading(true);
    try {
      for (const f of accepted) {
        const content_b64 = await readAsBase64(f);
        const res = await post<UploadResult>("/documents/upload", {
          filename: f.name,
          content_b64,
        });
        setAttachments((prev) =>
          prev.length >= MAX_ATTACHMENTS
            ? prev
            : [...prev, { name: res.name, path: res.path, bytes: f.size }],
        );
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  }

  // Stable handle for the once-registered window drag listeners below.
  const addFilesRef = useRef(addFiles);
  addFilesRef.current = addFiles;

  function onPickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files ? Array.from(e.target.files) : [];
    e.target.value = ""; // allow re-selecting the same file
    if (files.length) void addFiles(files);
  }

  function removeAttachment(index: number) {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  }

  // Full-page drag-and-drop: dragging files anywhere over the page lights up the
  // chat card with an accent ring; dropping uploads them. Registered on window so
  // the browser never navigates away to the dropped file.
  useEffect(() => {
    let depth = 0; // dragenter/dragleave fire per element — track nesting
    const hasFiles = (e: DragEvent) =>
      Array.from(e.dataTransfer?.types ?? []).includes("Files");
    const onDragEnter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth += 1;
      setDragging(true);
    };
    const onDragOver = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
    };
    const onDragLeave = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) setDragging(false);
    };
    const onDrop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      setDragging(false);
      const files = e.dataTransfer?.files;
      if (files && files.length) void addFilesRef.current(Array.from(files));
    };
    window.addEventListener("dragenter", onDragEnter);
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("dragleave", onDragLeave);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onDragEnter);
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("drop", onDrop);
    };
  }, []);

  // ---------------------------------------------------------- agent-mode machinery

  // Human-readable steps for the current agent turn, newest-first. Only events
  // after the turn boundary and tagged with this session's id count; consecutive
  // duplicates are collapsed so "Working…, Working…" reads as one line.
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
  }, [messages, awaitingId, chatBusy, progress.length]);

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

  // ------------------------------------------------------------------- sending

  /** CHAT MODE: one direct /chat completion with the full local history. */
  async function sendChat(message: string) {
    const gen = chatGenRef.current;
    const atts = attachments;
    setAttachments([]); // chips are consumed by this message
    const userMsg: ChatMessage = {
      role: "user",
      content: message,
      ...(atts.length ? { attachmentNames: atts.map((a) => a.name) } : {}),
    };
    const history = [...messages, userMsg];
    setMessages(history);
    setChatBusy(true);
    try {
      const { provider, model } = splitChoice(choice);
      const personaValue =
        persona === CUSTOM_PERSONA ? customPersona.trim() : persona;
      const body: ChatRequestBody = {
        // Full conversation every turn — the backend is stateless here.
        messages: history.map(({ role, content }) => ({ role, content })),
        ...(provider ? { provider } : {}),
        ...(model ? { model } : {}),
        ...(personaValue ? { persona: personaValue } : {}),
        ...(atts.length ? { attachments: atts.map((a) => a.path) } : {}),
      };
      const res = await post<ChatResponse>("/chat", body);
      if (chatGenRef.current !== gen) return; // "New chat" happened mid-flight
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: (res.reply ?? "").trim() || "(no response)" },
      ]);
    } catch (e) {
      if (chatGenRef.current !== gen) return;
      // Keep the typed thread intact — only surface the failure (a 502 carries
      // the provider's own message, e.g. a rate limit, in `detail`).
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      if (chatGenRef.current === gen) setChatBusy(false);
    }
  }

  /** AGENT MODE: the original session flow (wait:false + live steps + finalize). */
  async function sendAgent(message: string) {
    const atts = attachments;
    setAttachments([]); // chips are consumed by this message
    // Match the kanban precedent: point the agent at the uploaded files in-text.
    const attachLines = atts.map((a) => `\n\nAttached file: ${a.path}`).join("");
    const task = message + attachLines;
    setMessages((prev) => [
      ...prev,
      {
        role: "user",
        content: message,
        ...(atts.length ? { attachmentNames: atts.map((a) => a.name) } : {}),
      },
    ]);
    // Mark where "this turn" begins in the event stream BEFORE kicking off work.
    sinceRef.current = eventsRef.current[0]?.id ?? null;
    try {
      let session: SessionView;
      if (sessionId) {
        // Continue the same chat — runs in the background (wait:false).
        session = await post<SessionView>(`/sessions/${sessionId}/continue`, {
          message: task,
          wait: false,
        });
      } else {
        // First message opens a session.
        const { provider, model } = splitChoice(choice);
        session = await post<SessionView>("/sessions", {
          task,
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

  function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setError(null);
    setOffline(false);
    setInput("");
    if (mode === "chat") void sendChat(message);
    else void sendAgent(message);
  }

  // Ask the daemon to cancel the in-flight agent turn, then release the composer.
  // Cancel is best-effort — even if it fails server-side we stop waiting locally.
  function stop() {
    if (!awaitingId) return;
    post(`/sessions/${awaitingId}/cancel`).catch(() => {});
    setMessages((prev) => [...prev, { role: "assistant", content: "Stopped." }]);
    setAwaitingId(null); // also tears down the event watcher + polling interval
  }

  function newChat() {
    chatGenRef.current += 1; // orphan any in-flight /chat reply
    setMessages([]);
    setSessionId(null);
    setAwaitingId(null); // also tears down any polling interval
    setChatBusy(false);
    setAttachments([]);
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
      send(input);
    }
  }

  const started = messages.length > 0 || sessionId !== null;
  const personaNames = personas.map((p) => p.name);
  const selectedPersonaDesc =
    personas.find((p) => p.name === persona)?.description ?? "";

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Chat"
          subtitle="Talk to Iron Jarvis. Chat mode answers directly in seconds; Agent mode does real work with tools."
          actions={
            <div className="flex flex-wrap items-center gap-2">
              {/* Mode toggle: fast direct chat vs. the tool-using agent session. */}
              <div
                role="group"
                aria-label="Mode"
                className="flex items-center overflow-hidden rounded-xl border border-white/10 bg-white/[0.02]"
              >
                <button
                  type="button"
                  onClick={() => setMode("chat")}
                  aria-pressed={mode === "chat"}
                  title="Direct replies in seconds"
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-[13px] font-medium transition-colors ${
                    mode === "chat"
                      ? "bg-accent/15 text-accent-soft"
                      : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  <MessageSquare size={13} /> Chat
                </button>
                <button
                  type="button"
                  onClick={() => setMode("agent")}
                  aria-pressed={mode === "agent"}
                  title="Does real work with tools — files, web, terminals; slower"
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-[13px] font-medium transition-colors ${
                    mode === "agent"
                      ? "bg-accent/15 text-accent-soft"
                      : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  <Wrench size={13} /> Agent
                </button>
              </div>
              {mode === "chat" && (
                <select
                  aria-label="Persona"
                  value={persona}
                  onChange={(e) => choosePersona(e.target.value)}
                  disabled={busy}
                  title={
                    persona === CUSTOM_PERSONA
                      ? "Your own persona instructions"
                      : selectedPersonaDesc || "Persona for replies"
                  }
                  className="field w-auto py-1.5 text-[13px]"
                >
                  {/* Tolerate a saved persona the daemon no longer lists. */}
                  {!personaNames.includes(persona) && persona !== CUSTOM_PERSONA && (
                    <option value={persona}>{capitalize(persona)}</option>
                  )}
                  {personas.map((p) => (
                    <option key={p.name} value={p.name} title={p.description}>
                      {capitalize(p.name)}
                    </option>
                  ))}
                  <option value={CUSTOM_PERSONA}>Custom…</option>
                </select>
              )}
              <select
                aria-label="Model"
                value={choice}
                onChange={(e) => setChoice(e.target.value)}
                disabled={mode === "agent" ? awaiting || sessionId !== null : busy}
                title={
                  mode === "agent" && sessionId !== null
                    ? "Start a new chat to switch models"
                    : "Model for this chat"
                }
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
                disabled={!started && attachments.length === 0}
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
          {mode === "chat"
            ? "Fast, direct answers — attach files or drop them anywhere on the page. Switch to Agent mode when you need real work done with tools."
            : "Replies come from a real agent that can read files, search, and use tools — you'll see its steps live as it works."}
        </p>
      </Reveal>

      {mode === "chat" && persona === CUSTOM_PERSONA && (
        <Reveal>
          <textarea
            value={customPersona}
            onChange={(e) => editCustomPersona(e.target.value)}
            rows={2}
            aria-label="Custom persona"
            placeholder="Describe the persona — e.g. “You are a sharp tax accountant. Be concise and cite the code section.”"
            className="field resize-y text-[13px]"
          />
        </Reveal>
      )}

      {offline && (
        <Reveal>
          <OfflineHint detail="Chat needs it running to reach your agent." />
        </Reveal>
      )}

      <Reveal>
        <Card
          pad={false}
          className={`overflow-hidden transition-shadow ${
            dragging ? "ring-2 ring-accent/60" : ""
          }`}
        >
          {/* Message thread */}
          <div className="flex max-h-[60vh] min-h-[24rem] flex-col gap-4 overflow-y-auto p-4 sm:p-5">
            {messages.length === 0 && !busy ? (
              <div className="flex flex-1 flex-col items-center justify-center gap-4">
                <Empty icon={<MessageSquare size={28} />}>
                  {mode === "chat"
                    ? "Start a conversation. Ask a question, pick a persona, drop in a file — replies come back in seconds."
                    : "Start a conversation. Ask a question or describe what you need — your agent replies and reaches for tools on its own when they help."}
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
                    {m.attachmentNames && m.attachmentNames.length > 0 && (
                      <AttachmentFooter names={m.attachmentNames} />
                    )}
                  </Bubble>
                ))}
                {/* CHAT MODE: a subtle thinking shimmer — no step feed needed. */}
                {chatBusy && (
                  <Bubble role="assistant">
                    <span className="inline-flex items-center gap-2 text-zinc-400">
                      <Loader2 size={14} className="animate-spin text-accent-soft" />
                      <span className="animate-pulse">Thinking…</span>
                    </span>
                  </Bubble>
                )}
                {/* AGENT MODE: the live working bubble narrating agent steps. */}
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

          {/* Attachment chips — queued for the next message */}
          {attachments.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 border-t hairline px-3 py-2.5">
              {attachments.map((a, i) => (
                <span
                  key={`${a.path}-${i}`}
                  className="inline-flex items-center gap-1.5 rounded-full border border-accent/25 bg-accent/[0.06] px-2.5 py-1 text-[11px] text-zinc-300"
                >
                  <Paperclip size={11} className="shrink-0 text-accent-soft" />
                  <span className="max-w-[14rem] truncate">{a.name}</span>
                  <span className="text-zinc-500">{fmtSize(a.bytes)}</span>
                  <button
                    type="button"
                    onClick={() => removeAttachment(i)}
                    aria-label={`Remove ${a.name}`}
                    className="text-zinc-500 transition-colors hover:text-rose-300"
                  >
                    <X size={11} />
                  </button>
                </span>
              ))}
            </div>
          )}

          {/* Composer */}
          <div className="flex items-end gap-2 border-t hairline p-3">
            <input
              ref={fileRef}
              type="file"
              multiple
              className="hidden"
              onChange={onPickFiles}
            />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              disabled={uploading || attachments.length >= MAX_ATTACHMENTS}
              aria-label="Attach files"
              title={`Attach files (up to ${MAX_ATTACHMENTS}, 20 MB each) — or drop them anywhere`}
              className="btn-ghost h-[2.75rem] px-3 py-0"
            >
              {uploading ? <LoaderInline /> : <Paperclip size={15} />}
            </button>
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={busy}
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
              disabled={busy || !input.trim()}
              className="btn-accent h-[2.75rem] px-4 py-0 text-[13px]"
            >
              {busy ? (
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
