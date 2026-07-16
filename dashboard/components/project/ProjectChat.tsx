"use client";

// A lightweight, self-contained project-grounded chat. Unlike the full /chat
// page this has NO agent mode, voice, skill picker, or tool loop — just a direct
// POST /chat completion grounded in THIS project (project_id is always sent, so
// the reply carries the project's instructions + knowledge). Conversations live
// in a narrow left sub-rail; every completed turn autosaves to
// PUT /chat/threads/{id} with project_id so the thread stays in this project.

import {
  createContext,
  isValidElement,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import {
  Bot,
  Check,
  Copy,
  Loader2,
  MessageSquare,
  Paperclip,
  Plus,
  Send,
  Share2,
  Square,
  Trash2,
  User,
  X,
} from "lucide-react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { get, post, put, del, ApiError, API_BASE, ijToken } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { Empty, ErrorNote, OfflineHint } from "@/components/ui";
import { VoiceInput, appendDictation } from "@/components/VoiceInput";
import { ShareChatDialog } from "@/components/chat/ShareChatDialog";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  attachmentNames?: string[];
}

/** One row from GET /chat/threads (scoped to this project). */
interface ThreadRow {
  id: string;
  title: string;
  updated_at: string;
  project_id?: string | null;
}

/** GET /chat/threads/{id}. */
interface ThreadDetail {
  id: string;
  title: string;
  project_id?: string | null;
  messages: ChatMessage[];
}

interface ChatResponse {
  reply: string;
  provider?: string;
  model?: string;
  tools_used?: string[];
}

/** One uploaded attachment ready to ride along on the next message. */
interface UploadedFile {
  name: string;
  path: string;
  bytes: number;
}

const MAX_ATTACHMENTS = 4;
const MAX_FILE_BYTES = 20 * 1024 * 1024; // 20 MB

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

// ------------------------------------------------------------------ markdown

function nodeText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number" || typeof node === "bigint")
    return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement(node))
    return nodeText((node as ReactElement<{ children?: ReactNode }>).props.children);
  return "";
}

function CopyIconButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );
  function copy() {
    navigator.clipboard
      .writeText(text)
      .then(() => {
        setCopied(true);
        if (timerRef.current !== null) window.clearTimeout(timerRef.current);
        timerRef.current = window.setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {
        /* clipboard unavailable */
      });
  }
  return (
    <button
      type="button"
      onClick={copy}
      title="Copy code"
      aria-label="Copy code"
      className="absolute right-2 top-2 z-10 grid h-6 w-6 place-items-center rounded-md border border-white/10 bg-white/[0.06] text-zinc-400 opacity-0 transition-opacity hover:text-zinc-100 focus-visible:opacity-100 group-hover/code:opacity-100"
    >
      {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
    </button>
  );
}

const PreContext = createContext(false);

function MarkdownPre({ children }: { children?: ReactNode }) {
  const text = nodeText(children).replace(/\n$/, "");
  return (
    <div className="group/code relative my-2">
      <CopyIconButton text={text} />
      <PreContext.Provider value={true}>
        <pre className="overflow-x-auto rounded bg-black/40 p-3 font-mono text-xs leading-relaxed text-zinc-200">
          {children}
        </pre>
      </PreContext.Provider>
    </div>
  );
}

function MarkdownCode({ className, children }: { className?: string; children?: ReactNode }) {
  const inPre = useContext(PreContext);
  if (inPre) return <code className={className}>{children}</code>;
  return (
    <code className="rounded bg-white/[0.08] px-1.5 py-0.5 font-mono text-[0.85em] text-accent-soft">
      {children}
    </code>
  );
}

const MEDIA_EXT_RX =
  /\.(png|jpe?g|webp|gif|bmp|svg|mp4|webm|mov|m4v|mkv|mp3|wav|ogg|m4a|flac|aac|opus)$/i;
const VIDEO_EXT_RX = /\.(mp4|webm|mov|m4v|mkv)$/i;
const AUDIO_EXT_RX = /\.(mp3|wav|ogg|m4a|flac|aac|opus)$/i;

/** Inline media in replies — local absolute paths (pixio/creative output) are
 * rewritten through the daemon's guarded /creative/file-by-path. */
function MarkdownMedia({ src, alt }: { src?: string | Blob; alt?: string }) {
  const raw = typeof src === "string" ? src : "";
  if (!raw) return null;
  const isLocal = /^([A-Za-z]:[\\/]|\/(?!\/))/.test(raw) || raw.startsWith("file://");
  let resolved = raw;
  if (isLocal) {
    const path = raw.replace(/^file:\/\//, "");
    if (!MEDIA_EXT_RX.test(path))
      return <code className="text-[12px] text-zinc-400">{raw}</code>;
    const token = ijToken();
    resolved = `${API_BASE}/creative/file-by-path?path=${encodeURIComponent(path)}${
      token ? `&token=${encodeURIComponent(token)}` : ""
    }`;
  }
  if (VIDEO_EXT_RX.test(raw))
    return (
      <video
        src={resolved}
        controls
        preload="metadata"
        className="my-2 max-h-96 w-full max-w-xl rounded-xl border border-white/10"
      />
    );
  if (AUDIO_EXT_RX.test(raw))
    return <audio src={resolved} controls className="my-2 w-full max-w-xl" />;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={resolved}
      alt={alt || "generated media"}
      loading="lazy"
      className="my-2 max-h-96 w-auto max-w-full rounded-xl border border-white/10"
    />
  );
}

const MD_COMPONENTS: Components = {
  h1: ({ children }) => (
    <h1 className="mb-1.5 mt-3 text-base font-semibold text-zinc-100 first:mt-0">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-1.5 mt-3 text-[15px] font-semibold text-zinc-100 first:mt-0">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="mb-1 mt-2.5 text-sm font-semibold text-zinc-100 first:mt-0">{children}</h3>
  ),
  p: ({ children }) => <p className="my-1.5 leading-relaxed first:mt-0 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="my-1.5 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-1.5 list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed [&>p]:my-0">{children}</li>,
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-white/10 bg-white/[0.05] px-2.5 py-1.5 text-left font-medium text-zinc-100">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-white/10 px-2.5 py-1.5 align-top text-zinc-300">{children}</td>
  ),
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-accent-soft underline decoration-accent/40 underline-offset-2 transition-colors hover:decoration-accent"
    >
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-accent/40 pl-3 text-zinc-400 [&>p]:my-0.5">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-3 border-white/10" />,
  strong: ({ children }) => <strong className="font-semibold text-zinc-100">{children}</strong>,
  pre: MarkdownPre,
  code: MarkdownCode,
  img: MarkdownMedia,
};

const REMARK_PLUGINS = [remarkGfm];

function Markdown({ content }: { content: string }) {
  return (
    <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={MD_COMPONENTS}>
      {content}
    </ReactMarkdown>
  );
}

// ------------------------------------------------------------------- bubbles

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
        className={`min-w-0 max-w-[85%] rounded-2xl border px-4 py-2.5 text-sm leading-relaxed ${
          isUser
            ? "whitespace-pre-wrap border-accent/25 bg-accent/[0.1] text-zinc-100"
            : "border-white/[0.06] bg-white/[0.03] text-zinc-200"
        }`}
      >
        {children}
      </div>
    </div>
  );
}

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

// --------------------------------------------------------------------- chat

export function ProjectChat({
  projectId,
  defaultProvider,
  defaultModel,
  hasRoot = false,
}: {
  projectId: string;
  defaultProvider?: string;
  defaultModel?: string;
  /** True when the project has a folder — the chat then arms file tools so the
   *  model can read, edit, and create files directly in that folder. */
  hasRoot?: boolean;
}) {
  const [threads, setThreads] = useState<ThreadRow[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  // Share dialog target (full transcript / compacted digest of one thread).
  const [shareFor, setShareFor] = useState<ThreadRow | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const [attachments, setAttachments] = useState<UploadedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // The in-flight request's aborter — a Stop button calls abort() to cancel.
  const abortRef = useRef<AbortController | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  // Bumped on New chat / thread switch so an in-flight reply from the OLD thread
  // can't land in the fresh one.
  const chatGenRef = useRef(0);
  const messagesRef = useRef<ChatMessage[]>(messages);
  messagesRef.current = messages;
  const attachmentsRef = useRef<UploadedFile[]>(attachments);
  attachmentsRef.current = attachments;
  // Serialize saves so the first turn's "new" resolves to a real id before the
  // next save reads it (rapid turns can never mint two threads).
  const saveChainRef = useRef<Promise<void>>(Promise.resolve());
  const saveTargetRef = useRef<{ id: string | null }>({ id: null });

  const url = `/chat/threads?project_id=${encodeURIComponent(projectId)}`;

  async function refreshThreads() {
    try {
      const d = await get<{ threads: ThreadRow[] }>(url);
      setThreads(d.threads ?? []);
    } catch {
      /* quiet — the list just goes stale */
    }
  }

  useEffect(() => {
    let cancelled = false;
    get<{ threads: ThreadRow[] }>(url)
      .then((d) => {
        if (!cancelled) setThreads(d.threads ?? []);
      })
      .catch(() => {
        /* sidebar stays empty */
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);

  // Auto-disarm a pending thread delete after a moment.
  useEffect(() => {
    if (!pendingDelete) return;
    const t = setTimeout(() => setPendingDelete(null), 3000);
    return () => clearTimeout(t);
  }, [pendingDelete]);

  /** Queue ONE autosave for a completed turn — ALWAYS tagged with project_id so
   * the thread stays in this project. */
  function queueSave(msgs: ChatMessage[]) {
    if (msgs.length === 0) return;
    const target = saveTargetRef.current;
    saveChainRef.current = saveChainRef.current.then(async () => {
      try {
        const res = await put<{ id: string; title: string }>(
          `/chat/threads/${target.id ?? "new"}`,
          { messages: msgs, project_id: projectId },
        );
        target.id = res.id; // "new" → real id; later saves reuse it
        if (saveTargetRef.current === target) setThreadId(res.id);
        await refreshThreads();
      } catch {
        /* autosave is best-effort */
      }
    });
  }

  function newChat() {
    chatGenRef.current += 1;
    abortRef.current?.abort(); // cancel any in-flight reply
    setBusy(false);
    setMessages([]);
    setThreadId(null);
    setInput("");
    setError(null);
    setOffline(false);
    setAttachments([]);
    saveTargetRef.current = { id: null };
    inputRef.current?.focus();
  }

  async function openThread(id: string) {
    if (id === threadId) return;
    chatGenRef.current += 1;
    abortRef.current?.abort(); // cancel any in-flight reply
    setBusy(false);
    setError(null);
    setOffline(false);
    setAttachments([]);
    try {
      const t = await get<ThreadDetail>(`/chat/threads/${encodeURIComponent(id)}`);
      setMessages(t.messages ?? []);
      setThreadId(t.id);
      saveTargetRef.current = { id: t.id };
      inputRef.current?.focus();
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function removeThread(id: string) {
    try {
      await del(`/chat/threads/${encodeURIComponent(id)}`);
      setThreads((prev) => prev.filter((t) => t.id !== id));
      if (id === threadId) newChat();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setPendingDelete(null);
    }
  }

  async function complete(history: ChatMessage[], atts: UploadedFile[]) {
    const gen = chatGenRef.current;
    setMessages(history);
    setBusy(true);
    setError(null);
    setOffline(false);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const body: Record<string, unknown> = {
        messages: history.map(({ role, content }) => ({ role, content })),
        project_id: projectId,
        ...(defaultProvider ? { provider: defaultProvider } : {}),
        ...(defaultModel ? { model: defaultModel } : {}),
        ...(atts.length ? { attachments: atts.map((a) => a.path) } : {}),
        // A project with a folder gets file tools, so the model can find, read,
        // EXTRACT (read_document handles PDF/Word/Excel/PowerPoint), and GENERATE
        // files directly in the folder (run there server-side). Capped at 6 —
        // the tool loop enforces it — covering search + text/doc read + write.
        ...(hasRoot
          ? {
              tools: [
                "file_search",
                "read_document",
                "read_file",
                "list_files",
                "write_document",
                "write_file",
              ],
            }
          : {}),
      };
      const res = await post<ChatResponse>("/chat", body, { signal: controller.signal });
      if (chatGenRef.current !== gen) return; // switched away mid-flight
      const reply = (res.reply ?? "").trim() || "(no response)";
      const full: ChatMessage[] = [...history, { role: "assistant", content: reply }];
      setMessages(full);
      queueSave(full);
    } catch (e) {
      if (chatGenRef.current !== gen) return;
      // A user-initiated Stop surfaces as an aborted signal — that's a clean
      // cancel, not an error/offline. Just keep the conversation as-is.
      if (controller.signal.aborted) return;
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      if (chatGenRef.current === gen) setBusy(false);
    }
  }

  /** Stop the in-flight reply — aborts the request and hands control back. */
  function stop() {
    abortRef.current?.abort();
  }

  function send() {
    const message = input.trim();
    if (!message || busy) return;
    const atts = attachments;
    setInput("");
    setAttachments([]);
    const userMsg: ChatMessage = {
      role: "user",
      content: message,
      ...(atts.length ? { attachmentNames: atts.map((a) => a.name) } : {}),
    };
    void complete([...messagesRef.current, userMsg], atts);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

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
      if (accepted.length >= room) break;
      accepted.push(f);
    }
    if (accepted.length === 0) return;
    setUploading(true);
    try {
      for (const f of accepted) {
        const content_b64 = await readAsBase64(f);
        const res = await post<{ path: string; name: string }>("/documents/upload", {
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

  function onPickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files ? Array.from(e.target.files) : [];
    e.target.value = "";
    if (files.length) void addFiles(files);
  }

  function removeAttachment(index: number) {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  }

  return (
    <div className="grid gap-4 md:grid-cols-[13rem_minmax(0,1fr)]">
      {/* Conversations sub-rail */}
      <div className="flex flex-col gap-2">
        <button
          type="button"
          onClick={newChat}
          className="btn-accent w-full justify-center !py-1.5"
        >
          <Plus size={14} /> New chat
        </button>
        <div className="max-h-[60vh] space-y-1 overflow-y-auto">
          {threads.length === 0 ? (
            <p className="px-1 py-2 text-[11px] text-zinc-600">No conversations yet.</p>
          ) : (
            threads.map((t) => (
              <div
                key={t.id}
                className={`group flex items-center gap-1 rounded-lg border px-2 py-1.5 text-xs transition-colors ${
                  t.id === threadId
                    ? "border-accent/30 bg-accent/[0.08]"
                    : "border-white/[0.05] bg-white/[0.02] hover:border-white/15"
                }`}
              >
                <button
                  type="button"
                  onClick={() => void openThread(t.id)}
                  className="min-w-0 flex-1 text-left"
                >
                  <div className="truncate text-zinc-200">{t.title || "Untitled chat"}</div>
                  <div className="text-[10px] text-zinc-600">{timeAgo(t.updated_at)}</div>
                </button>
                <button
                  type="button"
                  onClick={() => setShareFor(t)}
                  className="shrink-0 rounded p-1 text-zinc-500 transition-colors hover:text-accent-soft"
                  title="Share this conversation"
                >
                  <Share2 size={12} />
                </button>
                {pendingDelete === t.id ? (
                  <button
                    type="button"
                    onClick={() => void removeThread(t.id)}
                    className="shrink-0 rounded p-1 text-rose-300"
                    title="Confirm delete"
                  >
                    <Check size={12} />
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => setPendingDelete(t.id)}
                    className="shrink-0 rounded p-1 text-zinc-500 transition-colors hover:text-rose-300"
                    title="Delete conversation"
                  >
                    <Trash2 size={12} />
                  </button>
                )}
              </div>
            ))
          )}
        </div>
      </div>

      {/* Chat pane */}
      <div className="flex min-w-0 flex-col rounded-2xl border hairline bg-white/[0.02]">
        <div className="max-h-[65vh] min-h-[45vh] flex-1 space-y-4 overflow-y-auto p-4">
          {offline && <OfflineHint />}
          {messages.length === 0 && !busy ? (
            <Empty icon={<MessageSquare size={22} />}>
              No conversations yet — start one; it&apos;s grounded in this project&apos;s
              instructions and knowledge.
            </Empty>
          ) : (
            messages.map((m, i) => (
              <Bubble key={i} role={m.role}>
                {m.role === "assistant" ? <Markdown content={m.content} /> : m.content}
                {m.attachmentNames?.length ? <AttachmentFooter names={m.attachmentNames} /> : null}
              </Bubble>
            ))
          )}
          {busy && (
            <Bubble role="assistant">
              <span className="inline-flex items-center gap-2 text-zinc-400">
                <Loader2 size={14} className="animate-spin" /> Thinking…
              </span>
            </Bubble>
          )}
          {error && <ErrorNote>{error}</ErrorNote>}
          <div ref={bottomRef} />
        </div>

        {/* Composer */}
        <div className="border-t hairline p-3">
          {attachments.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {attachments.map((a, i) => (
                <span
                  key={`${a.name}-${i}`}
                  className="inline-flex items-center gap-1 rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1 text-[11px] text-zinc-300"
                >
                  <Paperclip size={10} className="text-accent-soft/70" />
                  {a.name}
                  <button
                    type="button"
                    onClick={() => removeAttachment(i)}
                    className="text-zinc-500 hover:text-zinc-200"
                    aria-label={`Remove ${a.name}`}
                  >
                    <X size={11} />
                  </button>
                </span>
              ))}
            </div>
          )}
          <div className="flex items-end gap-2">
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              disabled={uploading}
              title="Attach a file"
              className="btn-ghost shrink-0 !px-2.5"
            >
              {uploading ? <Loader2 size={14} className="animate-spin" /> : <Paperclip size={14} />}
            </button>
            <input ref={fileRef} type="file" multiple className="hidden" onChange={onPickFiles} />
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              rows={2}
              placeholder="Message this project…"
              className="field min-w-0 flex-1 resize-none text-sm"
            />
            {/* Dictate the message (offline in the desktop app), same as the main
                chat + Build surfaces. */}
            <VoiceInput onTranscript={(chunk) => setInput((p) => appendDictation(p, chunk))} />
            {busy ? (
              <button
                type="button"
                onClick={stop}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-xl border border-rose-500/30 bg-rose-500/[0.08] px-4 py-2.5 text-xs font-medium text-rose-300 transition-colors hover:bg-rose-500/[0.16]"
                aria-label="Stop generating"
                title="Stop"
              >
                <Square size={13} className="fill-current" /> Stop
              </button>
            ) : (
              <button
                type="button"
                onClick={send}
                disabled={!input.trim()}
                className="btn-accent shrink-0"
                aria-label="Send"
              >
                <Send size={14} />
              </button>
            )}
          </div>
        </div>
      </div>

      {shareFor && (
        <ShareChatDialog
          threadId={shareFor.id}
          title={shareFor.title?.trim() || "Chat"}
          onClose={() => setShareFor(null)}
        />
      )}
    </div>
  );
}
