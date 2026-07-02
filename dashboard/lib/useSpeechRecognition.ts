"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/* -------------------------------------------------------------------------- */
/*  Minimal typings for the (non-standard) Web Speech API                     */
/* -------------------------------------------------------------------------- */

interface SpeechRecognitionAlternative {
  transcript: string;
  confidence: number;
}
interface SpeechRecognitionResult {
  readonly length: number;
  readonly isFinal: boolean;
  item(index: number): SpeechRecognitionAlternative;
  [index: number]: SpeechRecognitionAlternative;
}
interface SpeechRecognitionResultList {
  readonly length: number;
  item(index: number): SpeechRecognitionResult;
  [index: number]: SpeechRecognitionResult;
}
interface SpeechRecognitionEvent extends Event {
  readonly resultIndex: number;
  readonly results: SpeechRecognitionResultList;
}
interface SpeechRecognitionErrorEvent extends Event {
  readonly error: string;
  readonly message: string;
}
interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((ev: SpeechRecognitionEvent) => void) | null;
  onerror: ((ev: SpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function getCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export interface UseSpeechRecognition {
  /** Whether the browser exposes the Web Speech API at all. */
  supported: boolean;
  /** Whether we are actively listening to the mic right now. */
  listening: boolean;
  /** Accumulated final transcript across the current listening session. */
  transcript: string;
  /** The in-flight (not yet finalized) words. */
  interim: string;
  /** A human-readable error string, or null. */
  error: string | null;
  start: () => void;
  stop: () => void;
  reset: () => void;
}

/**
 * Wraps `window.SpeechRecognition || window.webkitSpeechRecognition`.
 *
 * Continuous + interim results. Final chunks are accumulated into
 * `transcript`; the live (unfinalized) words live in `interim`. Permission and
 * unsupported-browser cases are surfaced via `supported`/`error` rather than
 * thrown, so callers can render a graceful fallback.
 */
export function useSpeechRecognition(lang = "en-US"): UseSpeechRecognition {
  const [supported, setSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [interim, setInterim] = useState("");
  const [error, setError] = useState<string | null>(null);

  const recRef = useRef<SpeechRecognitionLike | null>(null);
  // We keep the *intent* to listen so we can auto-restart when the engine
  // ends a turn on its own (some browsers stop after a pause even in
  // continuous mode).
  const wantRef = useRef(false);

  useEffect(() => {
    const Ctor = getCtor();
    if (!Ctor) {
      setSupported(false);
      return;
    }
    setSupported(true);

    const rec = new Ctor();
    rec.lang = lang;
    rec.continuous = true;
    rec.interimResults = true;
    rec.maxAlternatives = 1;

    rec.onstart = () => {
      setListening(true);
      setError(null);
    };

    rec.onresult = (ev) => {
      let finalChunk = "";
      let interimChunk = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const res = ev.results[i];
        const text = res[0]?.transcript ?? "";
        if (res.isFinal) finalChunk += text;
        else interimChunk += text;
      }
      if (finalChunk) {
        setTranscript((prev) =>
          prev ? `${prev.replace(/\s+$/, "")} ${finalChunk.trim()}` : finalChunk.trim(),
        );
      }
      setInterim(interimChunk);
    };

    rec.onerror = (ev) => {
      // "no-speech" / "aborted" are benign and self-recover; surface the rest.
      if (ev.error === "not-allowed") {
        wantRef.current = false;
        setError("Microphone permission denied. Allow mic access and try again.");
      } else if (ev.error === "audio-capture") {
        wantRef.current = false;
        setError(
          "No microphone available. Check that one is connected and enabled in " +
            "Windows Settings → Privacy → Microphone.",
        );
      } else if (ev.error === "service-not-allowed" || ev.error === "network") {
        // The DESKTOP-APP gap: Electron's Chromium has no built-in cloud speech
        // service, so recognition can't run even with mic access. Be honest —
        // this is NOT a mic problem.
        wantRef.current = false;
        setError(
          "Voice recognition isn't available in the desktop app yet. It works in " +
            "Chrome/Edge; ask to enable built-in transcription for the app.",
        );
      } else if (ev.error !== "no-speech" && ev.error !== "aborted") {
        setError(ev.message || ev.error || "Speech recognition error.");
      }
    };

    rec.onend = () => {
      setInterim("");
      // Auto-restart if the user still wants to listen (engine timed out).
      if (wantRef.current) {
        try {
          rec.start();
          return;
        } catch {
          /* already started or cannot restart */
        }
      }
      setListening(false);
    };

    recRef.current = rec;
    return () => {
      wantRef.current = false;
      try {
        rec.onresult = null;
        rec.onerror = null;
        rec.onend = null;
        rec.onstart = null;
        rec.abort();
      } catch {
        /* ignore */
      }
      recRef.current = null;
    };
  }, [lang]);

  const start = useCallback(() => {
    const rec = recRef.current;
    if (!rec) return;
    wantRef.current = true;
    setError(null);
    try {
      rec.start();
    } catch {
      // start() throws if already running — that's fine.
    }
  }, []);

  const stop = useCallback(() => {
    const rec = recRef.current;
    wantRef.current = false;
    setListening(false);
    if (!rec) return;
    try {
      rec.stop();
    } catch {
      /* ignore */
    }
  }, []);

  const reset = useCallback(() => {
    setTranscript("");
    setInterim("");
    setError(null);
  }, []);

  return { supported, listening, transcript, interim, error, start, stop, reset };
}
