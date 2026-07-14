"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { get, post, wsUrl, ApiError } from "@/lib/api";
import { useSpeechRecognition } from "@/lib/useSpeechRecognition";

/** Resample Float32 mic audio → 16 kHz mono PCM16 (little-endian) for Vosk. A
 *  naive nearest-sample decimation is plenty for speech (Vosk is robust to it);
 *  when the AudioContext already runs at 16 kHz it's a straight conversion. */
function floatTo16kPCM(input: Float32Array, inRate: number): ArrayBuffer {
  let data = input;
  if (inRate !== 16000 && inRate > 0) {
    const ratio = inRate / 16000;
    const outLen = Math.floor(input.length / ratio);
    const out = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) out[i] = input[Math.floor(i * ratio)] || 0;
    data = out;
  }
  const pcm = new Int16Array(data.length);
  for (let i = 0; i < data.length; i++) {
    const s = Math.max(-1, Math.min(1, data[i]));
    pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return pcm.buffer;
}

/* -------------------------------------------------------------------------- */
/*  Unified dictation — Web Speech where it works, daemon transcription where  */
/*  it doesn't (the packaged Electron app has no built-in speech service).     */
/* -------------------------------------------------------------------------- */

/**
 * Engine choice, decided once per mount:
 *  - `webspeech`: a real Chrome/Edge — free, streaming, interim results.
 *  - `server`:    Electron (window.ironjarvis present) or any browser without
 *                 the Web Speech API — records mic audio, voice-activity-detects
 *                 utterance ends, and POSTs each clip to /voice/transcribe.
 */
export type DictationEngine = "webspeech" | "server";

interface VoiceStatus {
  available: boolean;
  backend: string | null;
  /** "stream" = bundled offline Vosk over the /voice/stream WebSocket (live
   *  partials); "clip" = record + POST /voice/transcribe. */
  mode?: string;
  hint: string;
}

function isElectron(): boolean {
  return (
    typeof window !== "undefined" &&
    !!(window as unknown as { ironjarvis?: unknown }).ironjarvis
  );
}

function hasWebSpeech(): boolean {
  if (typeof window === "undefined") return false;
  const w = window as unknown as {
    SpeechRecognition?: unknown;
    webkitSpeechRecognition?: unknown;
  };
  return !!(w.SpeechRecognition ?? w.webkitSpeechRecognition);
}

/** Pick the recorder mime the runtime actually supports (Chromium: webm/opus). */
function pickMime(): string {
  if (typeof window === "undefined" || typeof MediaRecorder === "undefined") return "";
  for (const m of ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"]) {
    try {
      if (MediaRecorder.isTypeSupported(m)) return m;
    } catch {
      /* ignore */
    }
  }
  return "";
}

async function blobToB64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onerror = () => reject(r.error);
    r.onload = () => {
      const url = String(r.result || "");
      resolve(url.slice(url.indexOf(",") + 1)); // strip the data: prefix
    };
    r.readAsDataURL(blob);
  });
}

export interface UseDictation {
  /** Whether dictation can work here at all (engine present + backend ready). */
  supported: boolean;
  /** When !supported: the honest reason / what to connect. */
  reason: string | null;
  engine: DictationEngine | null;
  listening: boolean;
  /** Server engine only: a clip is being transcribed right now. */
  processing: boolean;
  /** Accumulated FINAL transcript across the current listening session. */
  transcript: string;
  /** In-flight words (webspeech only — the server engine has no interim). */
  interim: string;
  error: string | null;
  start: () => void;
  stop: () => void;
  reset: () => void;
}

/** Voice-activity detection tuning for the server engine. */
const VAD = {
  /** RMS (0..1) above which a frame counts as speech. */
  speechRms: 0.02,
  /** ms of continuous silence that finalizes an utterance. */
  silenceMs: 1400,
  /** never let a single clip run longer than this (ms). */
  maxUtteranceMs: 30_000,
  /** ignore clips smaller than this (breath, click) — bytes. */
  minClipBytes: 2_000,
  /** how often we sample the analyser (ms). */
  tickMs: 100,
} as const;

/**
 * One dictation hook for every surface. Same contract as
 * `useSpeechRecognition` (accumulating `transcript`, live `interim`), but it
 * ALSO works inside the packaged desktop app by recording utterances
 * (MediaRecorder + a small RMS voice-activity detector) and transcribing them
 * through the daemon's /voice/transcribe. Availability is checked up front via
 * /voice/status so the mic can be offered — or greyed with a reason — honestly.
 */
export function useDictation(lang = "en-US"): UseDictation {
  const web = useSpeechRecognition(lang);

  const [engine, setEngine] = useState<DictationEngine | null>(null);
  const [serverReady, setServerReady] = useState<boolean | null>(null);
  const [reason, setReason] = useState<string | null>(null);

  // --- server-engine state ------------------------------------------------
  const [listening, setListening] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [interim, setInterim] = useState(""); // live partial words (stream mode)
  const [error, setError] = useState<string | null>(null);

  const wantRef = useRef(false);
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const vadTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Streaming (Vosk) mode: the WS + the audio graph nodes to tear down.
  const modeRef = useRef<"clip" | "stream">("clip");
  const wsRef = useRef<WebSocket | null>(null);
  const procRef = useRef<ScriptProcessorNode | null>(null);
  const srcRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const langRef = useRef(lang);
  langRef.current = lang;

  // Decide the engine once on mount; probe the daemon only when we need it.
  useEffect(() => {
    if (!isElectron() && hasWebSpeech()) {
      setEngine("webspeech");
      return;
    }
    setEngine("server");
    let cancelled = false;
    get<VoiceStatus>("/voice/status")
      .then((s) => {
        if (cancelled) return;
        setServerReady(s.available);
        modeRef.current = s.mode === "stream" ? "stream" : "clip";
        if (!s.available) setReason(s.hint || "No speech-to-text backend connected.");
      })
      .catch(() => {
        if (cancelled) return;
        setServerReady(false);
        setReason("Voice needs the daemon running (couldn't reach /voice/status).");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const teardown = useCallback(() => {
    wantRef.current = false;
    if (vadTimerRef.current) {
      clearInterval(vadTimerRef.current);
      vadTimerRef.current = null;
    }
    const rec = recorderRef.current;
    recorderRef.current = null;
    try {
      if (rec && rec.state !== "inactive") rec.stop();
    } catch {
      /* ignore */
    }
    // Streaming (Vosk) mode teardown: signal EOF, close the socket + audio graph.
    const ws = wsRef.current;
    wsRef.current = null;
    try {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send('{"eof":true}');
    } catch {
      /* ignore */
    }
    try {
      ws?.close();
    } catch {
      /* ignore */
    }
    try {
      procRef.current?.disconnect();
    } catch {
      /* ignore */
    }
    procRef.current = null;
    try {
      srcRef.current?.disconnect();
    } catch {
      /* ignore */
    }
    srcRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    audioCtxRef.current?.close().catch(() => undefined);
    audioCtxRef.current = null;
    setInterim("");
    setListening(false);
  }, []);

  useEffect(() => teardown, [teardown]); // unmount cleanup

  /** Transcribe one finished clip through the daemon, appending the text. */
  const transcribeClip = useCallback(async (blob: Blob, mime: string) => {
    if (blob.size < VAD.minClipBytes) return;
    setProcessing(true);
    try {
      const audio_b64 = await blobToB64(blob);
      const res = await post<{ text: string }>("/voice/transcribe", {
        audio_b64,
        mime,
        language: (langRef.current || "").split("-")[0] || "",
      });
      const text = (res.text || "").trim();
      if (text) {
        setTranscript((prev) =>
          prev ? `${prev.replace(/\s+$/, "")} ${text}` : text,
        );
      }
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "transcription failed";
      setError(msg);
      wantRef.current = false; // a broken backend won't heal mid-session
      teardown();
    } finally {
      setProcessing(false);
    }
  }, [teardown]);

  /**
   * Record one utterance: MediaRecorder on the shared stream, finalized when
   * the RMS voice-activity detector sees `silenceMs` of quiet after speech
   * (or at `maxUtteranceMs`). Re-arms itself while the user still wants to listen.
   */
  const recordUtterance = useCallback(() => {
    const stream = streamRef.current;
    const ctx = audioCtxRef.current;
    if (!stream || !ctx || !wantRef.current) return;

    const mime = pickMime();
    let recorder: MediaRecorder;
    try {
      recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    } catch {
      setError("Recording isn't supported here.");
      teardown();
      return;
    }
    recorderRef.current = recorder;
    const chunks: Blob[] = [];
    recorder.ondataavailable = (ev) => {
      if (ev.data && ev.data.size) chunks.push(ev.data);
    };
    recorder.onstop = () => {
      const blob = new Blob(chunks, { type: recorder.mimeType || mime || "audio/webm" });
      void transcribeClip(blob, recorder.mimeType || mime || "audio/webm");
      if (wantRef.current) recordUtterance(); // next utterance
    };

    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    const buf = new Uint8Array(analyser.fftSize);

    const startedAt = Date.now();
    let lastSpeechAt = 0;
    let heardSpeech = false;

    if (vadTimerRef.current) clearInterval(vadTimerRef.current);
    vadTimerRef.current = setInterval(() => {
      if (!wantRef.current || recorder.state === "inactive") return;
      analyser.getByteTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) {
        const d = (buf[i] - 128) / 128;
        sum += d * d;
      }
      const rms = Math.sqrt(sum / buf.length);
      const now = Date.now();
      if (rms >= VAD.speechRms) {
        heardSpeech = true;
        lastSpeechAt = now;
      }
      const quietLongEnough =
        heardSpeech && lastSpeechAt > 0 && now - lastSpeechAt >= VAD.silenceMs;
      const tooLong = now - startedAt >= VAD.maxUtteranceMs;
      if (quietLongEnough || (tooLong && heardSpeech)) {
        if (vadTimerRef.current) {
          clearInterval(vadTimerRef.current);
          vadTimerRef.current = null;
        }
        try {
          source.disconnect();
        } catch {
          /* ignore */
        }
        try {
          recorder.stop(); // onstop → transcribe + re-arm
        } catch {
          /* ignore */
        }
      } else if (tooLong) {
        // 30s of pure silence — drop the clip, restart the utterance window.
        if (vadTimerRef.current) {
          clearInterval(vadTimerRef.current);
          vadTimerRef.current = null;
        }
        try {
          source.disconnect();
        } catch {
          /* ignore */
        }
        recorder.onstop = () => {
          if (wantRef.current) recordUtterance();
        };
        try {
          recorder.stop();
        } catch {
          /* ignore */
        }
      }
    }, VAD.tickMs);

    try {
      recorder.start();
    } catch {
      setError("Couldn't start recording.");
      teardown();
    }
  }, [teardown, transcribeClip]);

  /**
   * STREAM mode (bundled offline Vosk): open /voice/stream, capture the mic as
   * 16 kHz mono PCM16 via a ScriptProcessor, and push frames over the socket.
   * Live `{partial}` fills `interim`; `{text, final}` at each pause commits into
   * `transcript` — the same real-time feel as the browser engine, fully offline.
   */
  const startStream = useCallback(async () => {
    if (wantRef.current) return;
    setError(null);
    wantRef.current = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      if (!wantRef.current) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      streamRef.current = stream;
      type AC = typeof AudioContext;
      const Ctor: AC | undefined =
        (window as unknown as { AudioContext?: AC; webkitAudioContext?: AC }).AudioContext ??
        (window as unknown as { webkitAudioContext?: AC }).webkitAudioContext;
      if (!Ctor) throw new Error("no AudioContext");
      // Ask for 16 kHz so the mic is resampled for us; fall back to the default
      // rate (floatTo16kPCM then downsamples).
      let ctx: AudioContext;
      try {
        ctx = new Ctor({ sampleRate: 16000 });
      } catch {
        ctx = new Ctor();
      }
      audioCtxRef.current = ctx;

      const ws = new WebSocket(wsUrl("/voice/stream"));
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;
      ws.onmessage = (ev) => {
        let msg: { partial?: string; text?: string; error?: string };
        try {
          msg = JSON.parse(String(ev.data));
        } catch {
          return;
        }
        if (msg.error) {
          setError(msg.error);
          wantRef.current = false;
          teardown();
          return;
        }
        if (typeof msg.partial === "string") {
          setInterim(msg.partial);
          return;
        }
        if (msg.text) {
          const t = String(msg.text).trim();
          if (t) setTranscript((prev) => (prev ? `${prev.replace(/\s+$/, "")} ${t}` : t));
          setInterim("");
        }
      };
      ws.onerror = () => setError("Voice stream error — is the daemon running?");

      // Wait for the socket to open (with a timeout) before wiring audio.
      await new Promise<void>((resolve, reject) => {
        const to = setTimeout(() => reject(new Error("voice stream timeout")), 5000);
        ws.addEventListener("open", () => {
          clearTimeout(to);
          resolve();
        }, { once: true });
        ws.addEventListener("error", () => {
          clearTimeout(to);
          reject(new Error("voice stream failed"));
        }, { once: true });
      });
      if (!wantRef.current) {
        ws.close();
        return;
      }

      const src = ctx.createMediaStreamSource(stream);
      srcRef.current = src;
      const proc = ctx.createScriptProcessor(4096, 1, 1);
      procRef.current = proc;
      proc.onaudioprocess = (e) => {
        if (!wantRef.current || ws.readyState !== WebSocket.OPEN) return;
        const pcm = floatTo16kPCM(e.inputBuffer.getChannelData(0), ctx.sampleRate);
        if (pcm.byteLength) {
          try {
            ws.send(pcm);
          } catch {
            /* socket closing */
          }
        }
      };
      src.connect(proc);
      // ScriptProcessor needs a destination to fire; its output buffer is left
      // silent (we never copy input→output), so nothing is heard — no echo.
      proc.connect(ctx.destination);
      setListening(true);
    } catch (e) {
      wantRef.current = false;
      const name = (e as { name?: string })?.name;
      setError(
        name === "NotAllowedError"
          ? "Microphone permission denied. Allow mic access and try again."
          : name === "NotFoundError"
            ? "No microphone found. Check Windows Settings → Privacy → Microphone."
            : "Couldn't start offline voice.",
      );
      teardown();
    }
  }, [teardown]);

  const startServer = useCallback(async () => {
    if (wantRef.current) return;
    setError(null);
    wantRef.current = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!wantRef.current) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      streamRef.current = stream;
      type AC = typeof AudioContext;
      const Ctor: AC | undefined =
        (window as unknown as { AudioContext?: AC; webkitAudioContext?: AC }).AudioContext ??
        (window as unknown as { webkitAudioContext?: AC }).webkitAudioContext;
      if (!Ctor) throw new Error("no AudioContext");
      audioCtxRef.current = new Ctor();
      setListening(true);
      recordUtterance();
    } catch (e) {
      wantRef.current = false;
      const name = (e as { name?: string })?.name;
      setError(
        name === "NotAllowedError"
          ? "Microphone permission denied. Allow mic access and try again."
          : name === "NotFoundError"
            ? "No microphone found. Check Windows Settings → Privacy → Microphone."
            : "Couldn't open the microphone.",
      );
      teardown();
    }
  }, [recordUtterance, teardown]);

  // --- unified surface ------------------------------------------------------

  const start = useCallback(() => {
    if (engine === "webspeech") web.start();
    else if (engine === "server" && serverReady)
      void (modeRef.current === "stream" ? startStream() : startServer());
    else if (engine === "server") setError(reason || "Voice backend not connected.");
  }, [engine, serverReady, reason, startServer, startStream, web]);

  const stop = useCallback(() => {
    if (engine === "webspeech") web.stop();
    else teardown();
  }, [engine, teardown, web]);

  const reset = useCallback(() => {
    if (engine === "webspeech") web.reset();
    setTranscript("");
    setInterim("");
    setError(null);
  }, [engine, web]);

  if (engine === "webspeech") {
    return {
      supported: web.supported,
      reason: web.supported ? null : "Voice input needs Chrome/Edge.",
      engine,
      listening: web.listening,
      processing: false,
      transcript: web.transcript,
      interim: web.interim,
      error: web.error,
      start,
      stop,
      reset,
    };
  }
  return {
    supported: engine === "server" && serverReady === true,
    reason:
      engine === "server" && serverReady === false
        ? reason
        : engine === null || serverReady === null
          ? null // still deciding — callers treat as "not yet supported"
          : reason,
    engine,
    listening,
    processing,
    transcript,
    interim, // populated in stream (Vosk) mode; "" in clip mode
    error,
    start,
    stop,
    reset,
  };
}
