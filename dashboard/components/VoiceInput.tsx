"use client";

import { useEffect, useRef } from "react";
import { Mic, MicOff } from "lucide-react";
import { useSpeechRecognition } from "@/lib/useSpeechRecognition";

/**
 * Appends a newly dictated chunk to existing text with sane spacing.
 * Use inside an `onTranscript` handler: `setX((p) => appendDictation(p, chunk))`.
 */
export function appendDictation(prev: string, chunk: string): string {
  const add = chunk.trim();
  if (!add) return prev;
  if (!prev) return add;
  return /\s$/.test(prev) ? prev + add : `${prev} ${add}`;
}

/**
 * A mic toggle button (Web Speech API) with a clear recording animation.
 * Emits each newly *finalized* chunk via `onTranscript`. Live (interim) words
 * are shown next to the button as a ghost hint — they are not emitted until
 * finalized. Unsupported browsers render a disabled mic with a tooltip.
 */
export function VoiceInput({
  onTranscript,
  size = "md",
  className = "",
}: {
  onTranscript: (chunk: string) => void;
  size?: "sm" | "md";
  className?: string;
}) {
  const { supported, listening, transcript, interim, error, start, stop, reset } =
    useSpeechRecognition();
  const lastLen = useRef(0);

  // Emit only the suffix that became final since we last emitted.
  useEffect(() => {
    if (transcript.length > lastLen.current) {
      const delta = transcript.slice(lastLen.current);
      lastLen.current = transcript.length;
      onTranscript(delta);
    }
  }, [transcript, onTranscript]);

  function toggle() {
    if (!supported) return;
    if (listening) {
      stop();
    } else {
      reset();
      lastLen.current = 0;
      start();
    }
  }

  const dim = size === "sm" ? "h-8 w-8" : "h-9 w-9";
  const icon = size === "sm" ? 15 : 17;

  if (!supported) {
    return (
      <button
        type="button"
        disabled
        title="Voice input needs Chrome/Edge"
        aria-label="Voice input unsupported"
        className={`group relative grid ${dim} shrink-0 cursor-not-allowed place-items-center rounded-xl border border-white/[0.06] bg-white/[0.02] text-zinc-600 ${className}`}
      >
        <MicOff size={icon} />
      </button>
    );
  }

  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <button
        type="button"
        onClick={toggle}
        title={listening ? "Stop dictation" : "Dictate with your voice"}
        aria-pressed={listening}
        aria-label={listening ? "Stop dictation" : "Start dictation"}
        className={`relative grid ${dim} shrink-0 place-items-center rounded-xl border transition-all ${
          listening
            ? "border-rose-500/50 bg-rose-500/15 text-rose-300 shadow-[0_0_18px_-4px_rgba(244,63,94,0.7)]"
            : "border-white/[0.08] bg-white/[0.02] text-zinc-400 hover:border-accent/50 hover:text-accent-soft"
        }`}
      >
        {listening && (
          <>
            <span className="pointer-events-none absolute inset-0 animate-ping rounded-xl bg-rose-500/30" />
            <span className="pointer-events-none absolute -right-0.5 -top-0.5 h-2 w-2 animate-pulse-glow rounded-full bg-rose-400 shadow-[0_0_8px_2px_rgba(244,63,94,0.6)]" />
          </>
        )}
        <span className="relative z-10">
          {listening ? <Mic size={icon} /> : <Mic size={icon} />}
        </span>
      </button>

      {(listening || interim || error) && (
        <span className="min-w-0 max-w-[16rem] truncate text-xs">
          {error ? (
            <span className="text-rose-300">{error}</span>
          ) : interim ? (
            <span className="text-zinc-500 italic">{interim}</span>
          ) : (
            <span className="text-rose-300/80">listening…</span>
          )}
        </span>
      )}
    </div>
  );
}
