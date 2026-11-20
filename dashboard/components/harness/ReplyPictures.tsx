"use client";

/**
 * BEFORE AND AFTER PICTURES under a coding reply (R2.15, phase 27-a).
 *
 * Under a reply that changed a screen file (`evidence` on its line,
 * lib/replyPictures): the page before the change and after it, side by side
 * on a wide screen and STACKED on a phone (one column below 640 px), each with
 * its address and time; a press opens it full screen (the chat's own
 * lightbox), Left/Right walk the two. "The page looks the same before and
 * after" and why a picture is missing are the daemon's own sentences.
 *
 * Decision D-27.1: pictures only when you ask. The NEWEST reply, when it has
 * no after picture, shows ONE quiet press, Show me (a picture of the page
 * open in the session's Browser, taken now); "show me it works" typed alone
 * presses the same one (lib/replyPictures `onShowMe`). It is the page AS IT
 * IS NOW, so an earlier reply never offers it (the picture would show the
 * later replies' changes as its own), and a reply that has its after keeps
 * it (the daemon refuses both too). The first Show me ever offers the
 * automatic setting once, naming its cost; the answer is the one settings
 * writer's (`PUT /settings/values`), so Settings and chat's Undo see it.
 * Nothing here draws per streamed word: it is drawn under a SETTLED reply.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Camera, Loader2 } from "lucide-react";
import { ApiError, post, put } from "@/lib/api";
import { MediaLightbox, type LightboxPicture } from "@/components/MediaLightbox";
import { previewAddressFor } from "@/components/harness/BrowserPreview";
import {
  GONE_WORDS,
  OFFER_NO,
  OFFER_OFF_WORDS,
  OFFER_ON_WORDS,
  OFFER_WORDS,
  OFFER_YES,
  SHOW_ME,
  SHOW_ME_TITLE,
  SHOW_ME_WORKING,
  STRIP_LABEL,
  decodePictures,
  onShowMe,
  pictureSrc,
  pictureTime,
  showMeOffered,
  showMePath,
  type ReplyPictures as Pictures,
} from "@/lib/replyPictures";

const QUIET_BTN =
  "inline-flex min-h-7 items-center gap-1.5 rounded-md px-1.5 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-50";
const LINK_BTN =
  "text-zinc-300 underline decoration-zinc-600 underline-offset-2 transition-colors hover:text-zinc-100 disabled:opacity-50";

type Offer = "none" | "open" | "on" | "off";

export function ReplyPictures({
  raw,
  threadId,
  turnId,
  busy,
  archived,
  newest,
}: {
  raw: unknown;
  threadId: string;
  turnId: string;
  /** A reply is running in the session (Show me waits for it). */
  busy: boolean;
  archived: boolean;
  /** This reply ends the conversation (Show me is offered only here). */
  newest: boolean;
}) {
  const fromLine = useMemo(() => decodePictures(raw), [raw]);
  // A press's answer, until the session's own line is read again (then the
  // line holds it: a new `raw` wins).
  const [pressed, setPressed] = useState<{ raw: unknown; rec: Pictures } | null>(null);
  const rec = pressed && pressed.raw === raw ? pressed.rec : fromLine;
  const [pressing, setPressing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offer, setOffer] = useState<Offer>("none");
  const [answering, setAnswering] = useState(false);
  const [zoom, setZoom] = useState<number | null>(null);
  const [gone, setGone] = useState<ReadonlySet<string>>(() => new Set());
  // Show me: only under the newest reply, only while it has no after.
  const offered = !!rec && showMeOffered(rec, { newest, archived });

  async function press() {
    if (!offered || pressing || busy || archived) return;
    setPressing(true);
    setError(null);
    try {
      const out = await post<{ evidence?: unknown; offer?: boolean }>(showMePath(threadId), {
        turn_id: turnId,
        preview_url: previewAddressFor(threadId),
      });
      const next = decodePictures(out?.evidence);
      if (next) {
        setPressed({ raw, rec: next });
        setGone(new Set());
      }
      if (out?.offer === true) setOffer("open");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setPressing(false);
    }
  }

  // "show me it works" typed alone presses THIS Show me (one press path) —
  // only while it is offered.
  const pressRef = useRef(press);
  pressRef.current = press;
  useEffect(
    () => (offered ? onShowMe(threadId, turnId, () => void pressRef.current()) : undefined),
    [threadId, turnId, offered],
  );

  async function answer(on: boolean) {
    if (answering) return;
    setAnswering(true);
    setError(null);
    try {
      await put("/settings/values", { values: { reply_pictures: on } });
      setOffer(on ? "on" : "off");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setAnswering(false);
    }
  }

  if (!rec) return null;
  const items = rec.items;
  const pictures: LightboxPicture[] = items.map((it) => ({
    src: pictureSrc(threadId, turnId, it),
    raw: "",
    name: `${it.label} · ${it.where || "the page"}`,
    alt: `${it.label}: ${it.where || "the page"}`,
  }));

  return (
    <div data-testid="reply-pictures" className="mt-2 min-w-0 space-y-1.5">
      {items.length ? (
        <ul
          aria-label={STRIP_LABEL}
          data-testid="reply-pictures-strip"
          className="grid max-w-[560px] grid-cols-1 gap-2 sm:grid-cols-2"
        >
          {items.map((it, i) => (
            <li key={it.id} data-testid={`reply-picture-${it.id}`} className="min-w-0">
              {gone.has(it.id) ? (
                <p
                  data-testid={`reply-picture-gone-${it.id}`}
                  className="rounded-md border border-white/[0.08] px-2 py-3 text-[12px] text-zinc-500"
                >
                  {GONE_WORDS}
                </p>
              ) : (
                <button
                  type="button"
                  data-testid={`reply-picture-open-${it.id}`}
                  onClick={() => setZoom(i)}
                  title={`Open the ${it.label.toLowerCase()} picture`}
                  className="block w-full cursor-zoom-in overflow-hidden rounded-md border border-white/[0.08] bg-black/20 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={pictures[i].src}
                    alt={pictures[i].alt}
                    loading="lazy"
                    className="block h-auto w-full"
                    onError={() => setGone((g) => new Set(g).add(it.id))}
                  />
                </button>
              )}
              <p className="mt-0.5 min-w-0 truncate text-[11px] text-zinc-500" data-testid={`reply-picture-caption-${it.id}`}>
                <span className="text-zinc-300">{it.label}</span>
                {it.where ? ` · ${it.where}` : ""}
                {pictureTime(it.at) ? ` · ${pictureTime(it.at)}` : ""}
              </p>
            </li>
          ))}
        </ul>
      ) : null}
      {rec.note ? (
        <p data-testid="reply-pictures-note" className="min-w-0 break-words text-[12px] text-zinc-400">
          {rec.note}
        </p>
      ) : null}
      {offered ? (
        <button
          type="button"
          data-testid="reply-pictures-show-me"
          onClick={() => void press()}
          disabled={pressing || busy}
          title={SHOW_ME_TITLE}
          className={QUIET_BTN}
        >
          {pressing ? (
            <Loader2 size={12} aria-hidden className="animate-spin motion-reduce:animate-none" />
          ) : (
            <Camera size={12} aria-hidden />
          )}
          {pressing ? SHOW_ME_WORKING : SHOW_ME}
        </button>
      ) : null}
      {error ? (
        <p data-testid="reply-pictures-error" role="alert" className="min-w-0 break-words text-[12px] text-tone-warn">
          {error}
        </p>
      ) : null}
      {offer === "open" ? (
        <p data-testid="reply-pictures-offer" className="min-w-0 text-[12px] leading-relaxed text-zinc-400">
          {OFFER_WORDS}{" "}
          <button
            type="button"
            data-testid="reply-pictures-offer-yes"
            disabled={answering}
            onClick={() => void answer(true)}
            className={LINK_BTN}
          >
            {OFFER_YES}
          </button>
          <span aria-hidden="true"> · </span>
          <button
            type="button"
            data-testid="reply-pictures-offer-no"
            disabled={answering}
            onClick={() => void answer(false)}
            className={LINK_BTN}
          >
            {OFFER_NO}
          </button>
        </p>
      ) : offer === "on" || offer === "off" ? (
        <p data-testid="reply-pictures-offer-done" role="status" className="text-[12px] text-zinc-500">
          {offer === "on" ? OFFER_ON_WORDS : OFFER_OFF_WORDS}
        </p>
      ) : null}
      {zoom !== null && pictures.length ? (
        <MediaLightbox pictures={pictures} index={zoom} onIndex={setZoom} onClose={() => setZoom(null)} />
      ) : null}
    </div>
  );
}
