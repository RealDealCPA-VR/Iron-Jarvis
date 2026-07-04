"use client";

import Link from "next/link";
import { createContext, useContext, useRef, useState } from "react";
import { useDraggable } from "@dnd-kit/core";
import {
  GripVertical,
  Check,
  X,
  Cpu,
  LoaderCircle,
  RotateCcw,
  MessageSquarePlus,
  Paperclip,
  Send,
} from "lucide-react";
import { post, del, ApiError } from "@/lib/api";
import type { SessionView } from "@/lib/types";
import type { LaneId } from "@/lib/kanban";
import { StatusDot, ConfirmButton, LoaderInline } from "@/components/ui";
import { timeAgo } from "@/lib/format";

export interface CardData {
  session: SessionView;
  lane: LaneId;
}

/**
 * Card-level actions (retry / dismiss / add-context) need the board's `reload`
 * and toast, but cards render through KanbanColumn, whose prop surface is
 * frozen — so the board provides them via context instead of threading new
 * props through the column.
 */
export interface KanbanCardActions {
  reload: () => void;
  notify: (kind: "ok" | "err", text: string) => void;
}

export const KanbanActionsContext = createContext<KanbanCardActions | null>(null);

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

/** Purely presentational card body — shared by the live card and the drag overlay. */
export function CardInner({
  session,
  lane,
  dragging = false,
  overlay = false,
  busy,
  onApprove,
  onReject,
  dragHandle,
  footer,
}: {
  session: SessionView;
  lane: LaneId;
  dragging?: boolean;
  overlay?: boolean;
  busy?: boolean;
  onApprove?: () => void;
  onReject?: () => void;
  dragHandle?: React.ReactNode;
  /** Extra per-lane actions rendered inside the card (live card only, not the drag ghost). */
  footer?: React.ReactNode;
}) {
  const reviewable = lane === "review";
  return (
    <div
      className={`group/card relative rounded-xl border bg-ink-850/90 p-3.5 transition-all duration-200 ${
        overlay
          ? "border-accent/40 shadow-glow rotate-[1.5deg] scale-[1.02]"
          : "border-white/[0.07] hover:border-white/[0.14] hover:bg-ink-800/90 hover:shadow-card-hover"
      } ${dragging ? "opacity-40" : ""}`}
    >
      <div className="flex items-start gap-2">
        <StatusDot status={reviewable ? "review" : session.status} className="mt-1.5" />
        <p className="flex-1 line-clamp-2 text-[13px] font-medium leading-snug text-zinc-100">
          {session.task || "(untitled task)"}
        </p>
        {dragHandle}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <span className="inline-flex items-center gap-1 rounded-md bg-white/[0.05] px-1.5 py-0.5 text-[10.5px] font-medium text-zinc-300">
          <Cpu size={11} className="text-accent-soft/70" />
          {session.agent_type}
        </span>
        <span className="rounded-md bg-white/[0.05] px-1.5 py-0.5 font-mono text-[10.5px] text-zinc-400">
          {session.provider}
        </span>
        <span className="ml-auto text-[11px] tabular-nums text-zinc-500">
          {timeAgo(session.created_at)}
        </span>
      </div>

      {reviewable && (
        <div className="mt-3 flex items-center gap-2 border-t border-white/[0.06] pt-3">
          <button
            type="button"
            disabled={busy}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onApprove?.();
            }}
            className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-2 py-1.5 text-[12px] font-semibold text-emerald-300 transition-colors hover:bg-emerald-500/20 disabled:opacity-40"
          >
            {busy ? <LoaderCircle size={13} className="animate-spin-slow" /> : <Check size={13} />}
            Approve
          </button>
          <button
            type="button"
            disabled={busy}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onReject?.();
            }}
            className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-rose-500/30 bg-rose-500/10 px-2 py-1.5 text-[12px] font-semibold text-rose-300 transition-colors hover:bg-rose-500/20 disabled:opacity-40"
          >
            <X size={13} />
            Reject
          </button>
        </div>
      )}

      {footer}
    </div>
  );
}

export function SessionCard({
  session,
  lane,
  busy,
  onApprove,
  onReject,
}: {
  session: SessionView;
  lane: LaneId;
  busy?: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const { setNodeRef, listeners, attributes, isDragging } = useDraggable({
    id: session.id,
    data: { lane },
  });

  // Lane-specific extras: failed cards get Retry/Dismiss, review cards get an
  // inline "Add context" form. Rendered only on the live card (never the ghost).
  const footer =
    lane === "failed" ? (
      <FailedActions session={session} />
    ) : lane === "review" ? (
      <AddContext session={session} />
    ) : undefined;

  // A stretched <Link> is the keyboard-accessible primary action ("open session").
  // The card itself is NOT an interactive element, so the drag handle + approve/
  // reject buttons aren't nested inside a role=button (axe nested-interactive), and
  // the action is reachable by keyboard (was a click-only div). The content layer is
  // pointer-events-none so a body click falls through to the link; buttons re-enable.
  return (
    <div ref={setNodeRef} className="relative rounded-xl">
      <Link
        href={`/sessions/${session.id}`}
        aria-label={`Open session: ${session.task || "untitled task"}`}
        className="absolute inset-0 z-0 rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
      />
      <div className="pointer-events-none relative z-10 [&_button]:pointer-events-auto">
        <CardInner
          session={session}
          lane={lane}
          dragging={isDragging}
          busy={busy}
          onApprove={onApprove}
          onReject={onReject}
          footer={footer}
          dragHandle={
            <button
              type="button"
              aria-label="Drag card"
              {...listeners}
              {...attributes}
              className="-m-1 cursor-grab rounded-md p-1 text-zinc-600 opacity-0 outline-none transition-opacity hover:text-zinc-300 active:cursor-grabbing group-hover/card:opacity-100 focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-accent/40"
            >
              <GripVertical size={15} aria-hidden="true" />
            </button>
          }
        />
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Failed lane: Retry / Dismiss                                               */
/* -------------------------------------------------------------------------- */

// NOTE on both footers: the card's content layer is pointer-events-none (so body
// clicks fall through to the stretched link) with only buttons re-enabled —
// `pointer-events-auto` on the container re-enables the textarea/inputs too, and
// the container-level stopPropagation follows the existing card-button precedent.

function FailedActions({ session }: { session: SessionView }) {
  const actions = useContext(KanbanActionsContext);
  const [retrying, setRetrying] = useState(false);
  if (!actions) return null;
  const { reload, notify } = actions;

  async function retry() {
    setRetrying(true);
    try {
      await post<SessionView>(`/sessions/${session.id}/rerun?wait=false`);
      notify("ok", "Retry started — a fresh run is underway.");
      reload();
    } catch (err) {
      notify("err", `Could not retry: ${err instanceof ApiError ? err.message : String(err)}`);
    } finally {
      setRetrying(false);
    }
  }

  async function dismiss() {
    try {
      await del<unknown>(`/sessions/${session.id}`);
      notify("ok", "Session dismissed.");
      reload();
    } catch (err) {
      notify("err", `Could not dismiss: ${err instanceof ApiError ? err.message : String(err)}`);
    }
  }

  return (
    <div
      className="pointer-events-auto mt-3 flex items-center gap-2 border-t border-white/[0.06] pt-3"
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        disabled={retrying}
        onClick={retry}
        className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-accent/30 bg-accent/10 px-2 py-1.5 text-[12px] font-semibold text-accent-soft transition-colors hover:bg-accent/20 disabled:opacity-40"
      >
        {retrying ? <LoaderCircle size={13} className="animate-spin-slow" /> : <RotateCcw size={13} />}
        Retry
      </button>
      <ConfirmButton
        label="Dismiss"
        confirmLabel="Confirm?"
        title="Remove this session permanently"
        onConfirm={dismiss}
        className="flex-1 justify-center py-1.5"
      />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Review lane: inline "Add context" form                                     */
/* -------------------------------------------------------------------------- */

function AddContext({ session }: { session: SessionView }) {
  const actions = useContext(KanbanActionsContext);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [sending, setSending] = useState(false);
  const [attaching, setAttaching] = useState(false);
  const [attached, setAttached] = useState<{ name: string; path: string } | null>(null);
  if (!actions) return null;
  const { reload, notify } = actions;

  async function onAttach(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file
    if (!file) return;
    setAttaching(true);
    try {
      const content_b64 = await readAsBase64(file);
      const res = await post<{ path: string; name: string; bytes: number }>(
        "/documents/upload",
        { filename: file.name, content_b64 },
      );
      setAttached({ name: res.name, path: res.path });
    } catch (err) {
      notify("err", `Could not upload: ${err instanceof ApiError ? err.message : String(err)}`);
    } finally {
      setAttaching(false);
    }
  }

  async function submit() {
    const text = note.trim();
    if (!text) return;
    setSending(true);
    try {
      const message = attached ? `${text}\n\nAttached file: ${attached.path}` : text;
      await post<SessionView>(`/sessions/${session.id}/continue`, { message, wait: false });
      notify(
        "ok",
        "Context sent — the agent is revising; a new review will appear (the original stays until it lands).",
      );
      setOpen(false);
      setNote("");
      setAttached(null);
      reload();
    } catch (err) {
      notify("err", `Could not send context: ${err instanceof ApiError ? err.message : String(err)}`);
    } finally {
      setSending(false);
    }
  }

  return (
    <div
      className="pointer-events-auto mt-2.5 border-t border-white/[0.06] pt-2.5"
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:border-accent/30 hover:text-accent-soft"
      >
        <MessageSquarePlus size={12} />
        {open ? "Cancel" : "Add context"}
      </button>

      {open && (
        <div className="mt-2 space-y-2">
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            placeholder="What should change / extra context…"
            className="w-full resize-y rounded-lg border border-white/10 bg-ink-900/70 px-2.5 py-2 text-[12px] text-zinc-200 outline-none transition-colors placeholder:text-zinc-600 focus:border-accent/40"
          />
          <div className="flex flex-wrap items-center gap-2">
            <input ref={fileRef} type="file" className="hidden" onChange={onAttach} />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              disabled={attaching}
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2 py-1 text-[11px] text-zinc-400 transition-colors hover:border-accent/30 hover:text-accent-soft disabled:opacity-50"
            >
              {attaching ? (
                <LoaderInline label="Uploading…" />
              ) : (
                <>
                  <Paperclip size={12} /> Attach file
                </>
              )}
            </button>
            {attached && (
              <span className="min-w-0 truncate text-[11px] text-emerald-300">
                Attached {attached.name}
              </span>
            )}
            <button
              type="button"
              disabled={sending || attaching || !note.trim()}
              onClick={submit}
              className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/10 px-2.5 py-1 text-[11px] font-semibold text-accent-soft transition-colors hover:bg-accent/20 disabled:opacity-40"
            >
              {sending ? (
                <LoaderInline label="Sending…" />
              ) : (
                <>
                  <Send size={12} /> Send
                </>
              )}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
