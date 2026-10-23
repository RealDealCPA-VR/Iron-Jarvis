"use client";

/**
 * Pictures (and other files) in the IJ harness composer (v1.371.0).
 *
 * The user's question: "sending images … within the IJ harness — fully
 * capable now?" It was not: the Build session's composer sent only its text,
 * though `POST /build/sessions/{id}/turn` took `attachments` and the Claude
 * and Codex connections pass a picture to the model. This module is the
 * missing half, on the chat page's own contract:
 *
 * - each file goes up through `POST /documents/upload` (base64, ≤ 20 MB),
 *   UPLOAD_CONCURRENCY at a time, the user's order kept; one that fails
 *   never costs the others (the chat page's v1.322.0 rule);
 * - a PASTED picture gets its own name (`pasted-<time>-<n>.png`): browsers
 *   name every clipboard image "image.png", and the upload folder keeps one
 *   file per name — a second paste would replace the first one's bytes;
 * - the composer shows each file as a chip, a picture as a thumbnail read
 *   back through the daemon (`/creative/file-by-path`), with a × to drop it.
 *
 * The send rules live in HarnessSession: a send waits for files still going
 * up (never goes without them — the v1.275.0 rule), and a picture alone is a
 * message.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Paperclip, X } from "lucide-react";
import { ApiError, post } from "@/lib/api";
import { isThumbImage, localMediaSrc } from "@/lib/mediaLightbox";

/** The chat page's limits (app/chat/page.tsx MAX_ATTACHMENTS / MAX_FILE_BYTES). */
export const MAX_FILES = 20;
export const MAX_FILE_BYTES = 20 * 1024 * 1024;
export const UPLOAD_CONCURRENCY = 3;

export interface HarnessFile {
  name: string;
  path: string;
  bytes: number;
  /** What the chip says: the file's own name, or "Pasted picture" for a
   *  paste (its upload name is a unique stamp nobody needs to read). */
  label: string;
}

interface UploadResult {
  path: string;
  name: string;
  bytes?: number;
}

/** Read a File as raw base64 (a data: URL with its prefix removed). */
export function readAsBase64(file: File): Promise<string> {
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

/** The extension a pasted picture is saved with, by its type. */
const PICTURE_EXT: Readonly<Record<string, string>> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/gif": "gif",
  "image/webp": "webp",
  "image/bmp": "bmp",
};

/** Whether a pasted file is a PICTURE (a screenshot, a copied image) — the
 *  only kind that arrives under the clipboard's generic "image.png". */
export function isPastedPicture(file: File): boolean {
  return (file.type || "").toLowerCase().startsWith("image/");
}

/** The name a file is uploaded under. A pasted PICTURE gets its own, unique
 *  name (every clipboard image arrives as "image.png"); any other pasted file
 *  — one copied in Explorer — keeps its own name, because its type is no
 *  guide to its extension (v1.371.0 review: a copied `notes.md` has no type
 *  and went up as a `.png` the turn then failed to send as a picture; a
 *  `.py` became `.xpyth`, a `.ts` `.mp2t`). */
export function uploadName(file: File, pasted: boolean, n: number, now: Date = new Date()): string {
  if (!pasted || !isPastedPicture(file)) return file.name || `file-${n + 1}`;
  const own = /\.([a-z0-9]{1,5})$/i.exec(file.name || "")?.[1]?.toLowerCase();
  const ext = PICTURE_EXT[(file.type || "").toLowerCase()] || own || "png";
  const stamp = now.toISOString().replace(/[-:]/g, "").replace(/\..*$/, "").replace("T", "-");
  const tail = Math.random().toString(36).slice(2, 6);
  return `pasted-${stamp}-${n + 1}-${tail}.${ext}`;
}

/** The files a clipboard paste carries (none for plain text). */
export function pastedFiles(data: DataTransfer | null): File[] {
  if (!data) return [];
  const out: File[] = [];
  for (const f of Array.from(data.files ?? [])) out.push(f);
  return out;
}

/** v1.373.0 review: how many files one message's turn READS (uploads and
 *  the "@" files together — lib/buildComposer READ_PER_MESSAGE, the daemon's
 *  `chat_turn._MAX_ATTACHMENTS`). The box used to take up to MAX_FILES
 *  pictures while the turn read four and dropped the rest without a word. */
export interface ReadLimit {
  /** Files one message may carry in all. */
  max: number;
  /** How many of them the message already carries besides its uploads. */
  others: () => number;
  /** What the box says when one more would not be read. */
  words: string;
}

export function useHarnessAttachments(limit?: ReadLimit) {
  const limitRef = useRef(limit);
  limitRef.current = limit;
  const [files, setFiles] = useState<HarnessFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const filesRef = useRef<HarnessFile[]>([]);
  filesRef.current = files;
  /** True from the first file picked until every upload settled (the state
   *  lags a render; a send pressed in that window must still wait). */
  const busyRef = useRef(false);
  /** Uploads still going up, across every pick and paste: a picture pasted
   *  while another was still going up used to clear `busyRef` when the FIRST
   *  settled, so a held send went without the second (v1.371.0 review). */
  const inFlight = useRef(0);
  /** Files picked and still going up (v1.373.0 review): they hold a place
   *  against the read limit, so two quick picks cannot overfill a message. */
  const pendingRef = useRef(0);
  /** The ONE send waiting for the uploads (v1.371.0 review): Enter pressed
   *  twice while a picture went up queued two sends, and both fired — the
   *  second read the box before React cleared it and posted the same
   *  message again. The chat page keeps one slot too (queuedSendRef). */
  const settled = useRef<Array<() => void>>([]);
  /** False once the composer is gone (the user opened another session): an
   *  upload still in flight must not fire the send it was holding — it would
   *  post to the session the user LEFT, without the picture (the chat page's
   *  leaveConversation drops its queued send the same way). */
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
      settled.current = [];
    };
  }, []);

  const add = useCallback(async (picked: File[], opts: { pasted?: boolean } = {}) => {
    setProblem(null);
    const lim = limitRef.current;
    // Uploads still going up hold a place too (two picks in a row).
    const held = filesRef.current.length + pendingRef.current;
    const readRoom = lim ? lim.max - lim.others() - held : Infinity;
    const room = Math.min(MAX_FILES - held, readRoom);
    const accepted: File[] = [];
    for (const f of picked) {
      if (f.size > MAX_FILE_BYTES) {
        setProblem(`${f.name || "That file"} is too large (max 20 MB).`);
        continue;
      }
      if (accepted.length >= room) {
        setProblem(lim && accepted.length >= readRoom ? lim.words : `Up to ${MAX_FILES} files per message.`);
        break;
      }
      accepted.push(f);
    }
    if (accepted.length === 0) return;
    inFlight.current += 1;
    pendingRef.current += accepted.length;
    busyRef.current = true;
    setUploading(true);
    const got: (HarnessFile | undefined)[] = new Array(accepted.length);
    const failed: string[] = [];
    let next = 0;
    const worker = async () => {
      while (next < accepted.length) {
        const i = next++;
        const f = accepted[i];
        const filename = uploadName(f, !!opts.pasted, i);
        try {
          const content_b64 = await readAsBase64(f);
          const res = await post<UploadResult>("/documents/upload", { filename, content_b64 });
          got[i] = {
            name: res.name,
            path: res.path,
            bytes: f.size,
            label: opts.pasted && isPastedPicture(f) ? "Pasted picture" : f.name || res.name,
          };
        } catch (e) {
          failed.push(`${f.name || filename}: ${e instanceof ApiError ? e.message : String(e)}`);
        }
      }
    };
    try {
      await Promise.all(Array.from({ length: Math.min(UPLOAD_CONCURRENCY, accepted.length) }, worker));
      const ok = got.filter((g): g is HarnessFile => !!g);
      if (ok.length) {
        // The ref at once too: the read limit counts it before the next
        // render (the place it held as `pending` is given back below).
        filesRef.current = [...filesRef.current, ...ok].slice(0, MAX_FILES);
        setFiles((prev) => [...prev, ...ok].slice(0, MAX_FILES));
      }
      if (failed.length) {
        setProblem(
          failed.length === 1
            ? `Couldn't attach ${failed[0]}`
            : `Couldn't attach ${failed.length} files. The others are attached.`,
        );
      }
    } finally {
      inFlight.current -= 1;
      pendingRef.current -= accepted.length;
      if (inFlight.current === 0) {
        busyRef.current = false;
        setUploading(false);
        const waiting = settled.current.splice(0);
        // Run AFTER the render that commits the new files (a send reads
        // them), and only while this composer is still on screen.
        if (live.current && waiting.length) {
          setTimeout(() => {
            if (live.current) waiting.forEach((fn) => fn());
          }, 0);
        }
      }
    }
  }, []);

  const remove = useCallback((i: number) => {
    setFiles((prev) => prev.filter((_, j) => j !== i));
  }, []);

  const clear = useCallback(() => {
    setFiles([]);
    setProblem(null);
  }, []);

  /** v1.373.0: put a message's files back (it never started, or it came off
   *  the waiting list) BESIDE any the box holds now — v1.373.0 review: a box
   *  that already held files used to keep them and drop these. */
  const restore = useCallback((back: HarnessFile[]) => {
    if (!back.length) return;
    setFiles((prev) => [...prev, ...back.filter((b) => !prev.some((f) => f.path === b.path))].slice(0, MAX_FILES));
  }, []);

  /** Call ``fn`` once the uploads in flight have settled (at once when none).
   *  ONE waiting call: a later one replaces it (one send per press of the
   *  box, however many presses landed while a picture went up). */
  const whenSettled = useCallback((fn: () => void) => {
    if (busyRef.current) settled.current = [fn];
    else fn();
  }, []);

  /** Files this message carries from the box's uploads, those still going
   *  up included (the "@" picks are counted against the same limit). */
  const held = useCallback(() => filesRef.current.length + pendingRef.current, []);

  return { files, filesRef, uploading, busyRef, problem, add, remove, clear, restore, whenSettled, held };
}

/** The paperclip: opens the file picker. */
export function AttachButton({ onPick, disabled }: { onPick: (files: File[]) => void; disabled?: boolean }) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <>
      <button
        type="button"
        data-testid="harness-attach"
        aria-label="Attach pictures or files"
        title="Attach pictures or files (or paste a picture into the box)"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
        className="grid h-[30px] w-[30px] shrink-0 place-items-center rounded-full text-zinc-400 transition-colors hover:bg-white/[0.08] hover:text-zinc-200 disabled:opacity-40"
      >
        <Paperclip size={15} aria-hidden />
      </button>
      <input
        ref={inputRef}
        type="file"
        multiple
        hidden
        data-testid="harness-attach-input"
        onChange={(e) => {
          const picked = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (picked.length) onPick(picked);
        }}
      />
    </>
  );
}

/** The files waiting to be sent: a picture as a thumbnail, any other file
 *  by name; each with a × to drop it. */
export function AttachmentChips({
  files,
  uploading,
  onRemove,
}: {
  files: HarnessFile[];
  uploading: boolean;
  onRemove: (i: number) => void;
}) {
  if (files.length === 0 && !uploading) return null;
  return (
    <div data-testid="harness-attachments" className="flex flex-wrap items-center gap-2 px-3 pt-2">
      {files.map((f, i) => (
        <span
          key={`${f.path}-${i}`}
          data-testid="harness-attachment"
          title={f.path}
          className="flex max-w-[220px] items-center gap-1.5 rounded-lg bg-white/[0.06] py-1 pl-1 pr-1.5 text-[12px] text-zinc-300"
        >
          {isThumbImage(f.name) ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={localMediaSrc(f.path)}
              alt=""
              data-testid="harness-attachment-thumb"
              className="h-8 w-8 shrink-0 rounded object-cover"
            />
          ) : (
            <Paperclip size={13} aria-hidden className="ml-1 shrink-0 text-zinc-500" />
          )}
          <span className="truncate">{f.label}</span>
          <button
            type="button"
            aria-label={`Remove ${f.label}`}
            onClick={() => onRemove(i)}
            className="shrink-0 rounded text-zinc-500 hover:text-zinc-200"
          >
            <X size={13} aria-hidden />
          </button>
        </span>
      ))}
      {uploading ? (
        <span data-testid="harness-attachments-uploading" role="status" className="text-[12px] text-zinc-500">
          Attaching…
        </span>
      ) : null}
    </div>
  );
}

/** The files a sent message carried, in its bubble: pictures as thumbnails,
 *  other files by name. */
export function SentAttachments({ paths }: { paths: readonly string[] }) {
  if (!paths.length) return null;
  return (
    <div data-testid="harness-sent-attachments" className="flex max-w-[88%] flex-wrap justify-end gap-1.5">
      {paths.map((p, i) => {
        const name = p.split(/[\\/]/).pop() || p;
        return isThumbImage(name) ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            key={`${p}-${i}`}
            src={localMediaSrc(p)}
            alt={name}
            title={name}
            data-testid="harness-sent-thumb"
            className="h-20 max-w-[160px] rounded-lg object-cover"
          />
        ) : (
          <span
            key={`${p}-${i}`}
            title={p}
            className="flex items-center gap-1 rounded-lg bg-white/[0.06] px-2 py-1 text-[12px] text-zinc-300"
          >
            <Paperclip size={12} aria-hidden /> {name}
          </span>
        );
      })}
    </div>
  );
}
