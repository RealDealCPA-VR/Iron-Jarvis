"use client";

/**
 * ArchiveChatDialog — "Archive '<title>'?" (v1.328.0, calm chat B7).
 *
 * Archiving hides a chat from the list and keeps all of it. The one thing it
 * must never do is quietly leave work running in a chat the user can no
 * longer see. So the daemon refuses to archive a busy chat
 * (`POST /chat/threads/{id}/archive` answers 409 with `running`, plain words
 * like "a reply in progress"), and this dialog is the user's answer:
 *
 *   * nothing running: a plain confirm, Archive or Cancel;
 *   * something running: it lists what would stop and offers
 *     "Stop and archive" (danger tone) or Cancel. Confirming sends
 *     `{stop: true}`, which stops those turns with the same stop the Stop
 *     button sends, then archives.
 *
 * Pure: the caller owns the request and passes `busy` / `error` back in.
 * Built on <Modal>, so Escape and the backdrop close it (not while busy),
 * focus moves in, Tab stays inside, and focus returns to the opener.
 * Colours are theme tokens only; the danger action reads the danger tone, so
 * it is right on the dark Marks and on Daylight alike.
 */

import { Modal } from "@/components/Modal";

export interface ArchiveChatDialogProps {
  /** The chat's title as the list shows it ("" reads "this chat"). */
  title: string;
  /** What the chat is running, in the daemon's own words ([] = nothing). */
  running: string[];
  /** Confirm. `stop` is true when something was listed as running. */
  onConfirm: (stop: boolean) => void;
  onClose: () => void;
  /** The archive request is in flight: buttons and dismissals wait. */
  busy?: boolean;
  /** One plain sentence when the request failed. */
  error?: string | null;
}

/** The heading's words: "Archive 'Quarterly notes'?" */
export function archiveHeading(title: string): string {
  const name = title.trim();
  return name ? `Archive '${name}'?` : "Archive this chat?";
}

export function ArchiveChatDialog({
  title,
  running,
  onConfirm,
  onClose,
  busy = false,
  error = null,
}: ArchiveChatDialogProps) {
  const items = running.filter((r) => r.trim() !== "");
  const working = items.length > 0;
  const heading = archiveHeading(title);

  return (
    <Modal
      label={heading}
      onClose={onClose}
      busy={busy}
      className="w-full max-w-md"
      testId="archive-chat-dialog"
    >
      <div className="space-y-3 px-5 pb-4 pt-5">
        <h2
          className="break-words text-[15px] font-medium text-zinc-100"
          data-testid="archive-chat-heading"
        >
          {heading}
        </h2>
        {working ? (
          <div className="space-y-2" data-testid="archive-chat-running">
            <p className="text-[13px] leading-relaxed text-zinc-400">
              This chat is still working. Archiving it will stop:
            </p>
            <ul className="space-y-1 border-l hairline pl-3">
              {items.map((item) => (
                <li
                  key={item}
                  className="text-[13px] text-zinc-300"
                  data-testid="archive-chat-running-item"
                >
                  {item}
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="text-[13px] leading-relaxed text-zinc-400">
            It leaves your chat list. Nothing is deleted, and you can bring it
            back from your archived chats.
          </p>
        )}
        {error ? (
          <p role="alert" className="text-[13px] text-tone-danger" data-testid="archive-chat-error">
            {error}
          </p>
        ) : null}
      </div>
      <div className="flex justify-end gap-2 border-t hairline px-5 py-3">
        <button
          type="button"
          onClick={onClose}
          disabled={busy}
          className="btn-ghost btn-sm"
          data-testid="archive-chat-cancel"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => onConfirm(working)}
          disabled={busy}
          className={`${working ? "btn-danger" : "btn-soft"} btn-sm`}
          data-testid="archive-chat-confirm"
        >
          {working ? (busy ? "Stopping…" : "Stop and archive") : busy ? "Archiving…" : "Archive"}
        </button>
      </div>
    </Modal>
  );
}

export default ArchiveChatDialog;
