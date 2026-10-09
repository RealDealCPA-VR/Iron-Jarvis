"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { plainText } from "@/components/Markdown";

/* -------------------------------------------------------------------------- */
/*  Voice output (Web Speech Synthesis) — hear Iron Jarvis reply               */
/* -------------------------------------------------------------------------- */

/** localStorage key that remembers the user's voice-output preference. */
const PREF_KEY = "ironjarvis.tts.enabled";

/**
 * Iron Jarvis's spoken persona — a warm, plain-spoken teammate (mirrors the
 * `_VOICE` system persona). A slightly-under-1 rate reads naturally; we prefer a
 * natural en-US/en-GB voice when the platform exposes one.
 */
const VOICE_PERSONA = {
  rate: 0.98,
  pitch: 1.0,
  volume: 1.0,
  langPrefix: "en",
  /** Substrings we prefer in a voice name, best first. */
  preferred: ["natural", "google", "samantha", "aria", "jenny", "daniel"],
} as const;

/* -------------------------------------------------------------------------- */
/*  Pure helpers (no DOM) — safe to unit-test in isolation                     */
/* -------------------------------------------------------------------------- */

/**
 * Split prose into speakable sentences. Breaks on sentence-ending punctuation
 * (`. ! ? …` and newlines), keeps the punctuation, trims whitespace, and drops
 * empties. A buffer with no terminator yields a single trimmed chunk. Pure and
 * deterministic — this is the unit-testable core of the streaming speech queue.
 */
export function splitSentences(text: string): string[] {
  if (!text) return [];
  const matches = text.match(/[^.!?…\n]+(?:[.!?…]+|\n+|$)/g);
  if (!matches) return [];
  return matches.map((s) => s.trim()).filter((s) => s.length > 0);
}

/**
 * Given a streaming `buffer` and how many chars were already spoken, return the
 * newly *complete* sentences plus the count of chars consumed. A trailing,
 * not-yet-terminated fragment is left unconsumed until `flush` is true (e.g. on
 * stream end), so partial words are never spoken mid-stream. Pure.
 */
export function takeCompleteSentences(
  buffer: string,
  alreadyConsumed: number,
  flush = false,
): { sentences: string[]; consumed: number } {
  const remainder = buffer.slice(alreadyConsumed);
  if (!remainder.trim()) return { sentences: [], consumed: alreadyConsumed };

  // The terminated prefix is everything up to the last sentence terminator.
  const lastTerm = Math.max(
    remainder.lastIndexOf("."),
    remainder.lastIndexOf("!"),
    remainder.lastIndexOf("?"),
    remainder.lastIndexOf("…"),
    remainder.lastIndexOf("\n"),
  );
  const ready = flush || lastTerm < 0 ? remainder : remainder.slice(0, lastTerm + 1);
  if (!ready.trim()) return { sentences: [], consumed: alreadyConsumed };

  return {
    sentences: splitSentences(ready),
    consumed: alreadyConsumed + ready.length,
  };
}

/**
 * A reply as words to READ ALOUD (v1.323.0): markdown stripped LINE BY LINE
 * (`plainText` collapses all whitespace, which would run a list's items and
 * a heading into one breathless sentence), blank lines dropped, one line per
 * spoken line — `splitSentences` breaks on the newlines. Pure.
 */
export function speakableText(md: string): string {
  return (md || "")
    .split(/\r?\n/)
    .map((line) => plainText(line))
    .filter((line) => line.length > 0)
    .join("\n");
}

/* -------------------------------------------------------------------------- */
/*  Hook                                                                        */
/* -------------------------------------------------------------------------- */

function synth(): SpeechSynthesis | null {
  if (typeof window === "undefined") return null;
  return window.speechSynthesis ?? null;
}

function pickVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | null {
  const en = voices.filter((v) => v.lang?.toLowerCase().startsWith(VOICE_PERSONA.langPrefix));
  const pool = en.length ? en : voices;
  for (const want of VOICE_PERSONA.preferred) {
    const hit = pool.find((v) => v.name.toLowerCase().includes(want));
    if (hit) return hit;
  }
  return pool.find((v) => v.default) ?? pool[0] ?? null;
}

export interface UseTTS {
  /** Whether the browser exposes the Speech Synthesis API at all. */
  supported: boolean;
  /** Whether voice output is currently turned on (persisted preference). */
  enabled: boolean;
  /** Whether something is being spoken right now. */
  speaking: boolean;
  /** Turn voice on (call from a user gesture so browsers allow audio). */
  enable: () => void;
  /** Turn voice off and stop any in-flight speech. */
  disable: () => void;
  /** Toggle voice on/off (gesture-safe). */
  toggle: () => void;
  /**
   * Speak `text` (split into sentences). When `enabled` is false this is a
   * no-op. Passing the same text twice in a row is ignored, so it is safe to
   * call on every render with the latest assistant output.
   */
  speak: (text: string) => void;
  /**
   * Zero the per-turn "already consumed" counter used by {@link speakMore}.
   * Call once at the start of a streaming turn before feeding deltas.
   */
  resetStream: () => void;
  /**
   * Incremental sibling of {@link speak} for token streaming: given the FULL
   * text so far, enqueue only the newly-complete sentences (via
   * `takeCompleteSentences`) WITHOUT cancelling in-flight speech and WITHOUT the
   * whole-reply dedupe. Pass `flush = true` on stream end to speak a trailing
   * fragment. No-op while voice is off.
   */
  speakMore: (fullText: string, flush?: boolean) => void;
  /** Stop and clear the queue immediately (and end any read-aloud). */
  cancel: () => void;
  /**
   * v1.323.0: read ONE reply aloud on an explicit press — REGARDLESS of
   * `enabled` (the press is the consent). Markdown is stripped to plain
   * words; anything already speaking is cancelled first. Pressing it again
   * with the same `key` while that reply is being read stops it (a toggle).
   */
  readAloud: (text: string, key: string) => void;
  /** v1.323.0: the key of the reply being read aloud, or null. */
  readingKey: string | null;
}

/**
 * SSR-safe wrapper around `window.speechSynthesis` that speaks assistant output
 * sentence-by-sentence in the Iron Jarvis voice persona, behind a user toggle
 * whose preference is remembered. Everything degrades to a no-op when the API is
 * missing or voice is off, so callers just call `speak()` with the latest text.
 */
export function useTTS(): UseTTS {
  const [supported, setSupported] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  // v1.323.0: the reply being read aloud. The ref is the truth for the
  // toggle (a press must not wait for a render); the token retires the
  // callbacks of a read that was cancelled or replaced, so its late
  // onend/onerror can never clear a NEWER read's key.
  const [readingKey, setReadingKey] = useState<string | null>(null);
  const readingKeyRef = useRef<string | null>(null);
  const readTokenRef = useRef(0);

  const voiceRef = useRef<SpeechSynthesisVoice | null>(null);
  const lastSpokenRef = useRef<string>(""); // dedupe identical re-speak calls
  const enabledRef = useRef(false);
  // Chars of the current streaming turn already enqueued by speakMore(). Zeroed
  // by resetStream() at the start of each turn so incremental speech never
  // repeats a sentence.
  const streamConsumedRef = useRef(0);

  // Detect support + restore the saved preference (guarded for SSR).
  useEffect(() => {
    const s = synth();
    if (!s) {
      setSupported(false);
      return;
    }
    setSupported(true);

    const loadVoices = () => {
      voiceRef.current = pickVoice(s.getVoices());
    };
    loadVoices();
    s.addEventListener?.("voiceschanged", loadVoices);

    try {
      if (window.localStorage.getItem(PREF_KEY) === "1") {
        setEnabled(true);
        enabledRef.current = true;
      }
    } catch {
      /* private mode / blocked storage — default off */
    }

    return () => {
      s.removeEventListener?.("voiceschanged", loadVoices);
      try {
        s.cancel();
      } catch {
        /* ignore */
      }
    };
  }, []);

  const cancel = useCallback(() => {
    const s = synth();
    if (!s) return;
    try {
      s.cancel();
    } catch {
      /* ignore */
    }
    setSpeaking(false);
    lastSpokenRef.current = "";
    streamConsumedRef.current = 0;
    readTokenRef.current += 1;
    readingKeyRef.current = null;
    setReadingKey(null);
  }, []);

  const persist = useCallback((on: boolean) => {
    try {
      window.localStorage.setItem(PREF_KEY, on ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, []);

  const enable = useCallback(() => {
    if (!synth()) return;
    enabledRef.current = true;
    setEnabled(true);
    persist(true);
  }, [persist]);

  const disable = useCallback(() => {
    enabledRef.current = false;
    setEnabled(false);
    persist(false);
    cancel();
  }, [persist, cancel]);

  const toggle = useCallback(() => {
    if (enabledRef.current) disable();
    else enable();
  }, [enable, disable]);

  // The per-utterance body shared by speak() (whole reply) and speakMore()
  // (streaming). `speaking` tracks the queue: true on any utterance start, false
  // once the queue drains (`pending` is false when this is the last one).
  const enqueueUtterance = useCallback((sentence: string, onDone?: () => void) => {
    const s = synth();
    if (!s) return;
    const u = new SpeechSynthesisUtterance(sentence);
    if (voiceRef.current) u.voice = voiceRef.current;
    u.rate = VOICE_PERSONA.rate;
    u.pitch = VOICE_PERSONA.pitch;
    u.volume = VOICE_PERSONA.volume;
    u.onstart = () => setSpeaking(true);
    u.onend = () => {
      setSpeaking(s.pending);
      onDone?.();
    };
    u.onerror = () => {
      setSpeaking(s.pending);
      onDone?.();
    };
    try {
      s.speak(u);
    } catch {
      /* a single utterance failing must not break the queue */
    }
  }, []);

  const speak = useCallback(
    (text: string) => {
      const s = synth();
      if (!s || !enabledRef.current) return;
      const clean = (text || "").trim();
      if (!clean || clean === lastSpokenRef.current) return;
      lastSpokenRef.current = clean;

      try {
        s.cancel(); // replace any in-flight speech with the newest output
      } catch {
        /* ignore */
      }
      // v1.323.0: that cancel ended any read-aloud too — its key goes with it.
      if (readingKeyRef.current !== null) {
        readTokenRef.current += 1;
        readingKeyRef.current = null;
        setReadingKey(null);
      }

      const sentences = splitSentences(clean);
      if (!sentences.length) return;
      sentences.forEach((sentence) => enqueueUtterance(sentence));
    },
    [enqueueUtterance],
  );

  const resetStream = useCallback(() => {
    streamConsumedRef.current = 0;
  }, []);

  const speakMore = useCallback(
    (fullText: string, flush = false) => {
      const s = synth();
      if (!s || !enabledRef.current) return;
      const { sentences, consumed } = takeCompleteSentences(
        fullText || "",
        streamConsumedRef.current,
        flush,
      );
      streamConsumedRef.current = consumed;
      if (!sentences.length) return;
      // No s.cancel() (never interrupt in-flight speech) and no whole-reply
      // dedupe — the consumed counter alone guards against re-speaking.
      sentences.forEach((sentence) => enqueueUtterance(sentence));
    },
    [enqueueUtterance],
  );

  const readAloud = useCallback(
    (text: string, key: string) => {
      const s = synth();
      if (!s) return;
      // The same reply pressed again while it is being read: stop (toggle).
      if (readingKeyRef.current !== null && readingKeyRef.current === key) {
        cancel();
        return;
      }
      const sentences = splitSentences(speakableText(text));
      if (!sentences.length) return;
      try {
        s.cancel(); // an explicit press replaces whatever is speaking
      } catch {
        /* ignore */
      }
      // A later auto-speak of the same reply is not a duplicate of this.
      lastSpokenRef.current = "";
      const token = ++readTokenRef.current;
      readingKeyRef.current = key;
      setReadingKey(key);
      const last = sentences.length - 1;
      sentences.forEach((sentence, i) =>
        enqueueUtterance(
          sentence,
          i === last
            ? () => {
                if (readTokenRef.current !== token) return; // retired read
                readingKeyRef.current = null;
                setReadingKey(null);
              }
            : undefined,
        ),
      );
    },
    [cancel, enqueueUtterance],
  );

  return {
    supported,
    enabled,
    speaking,
    enable,
    disable,
    toggle,
    speak,
    resetStream,
    speakMore,
    cancel,
    readAloud,
    readingKey,
  };
}
