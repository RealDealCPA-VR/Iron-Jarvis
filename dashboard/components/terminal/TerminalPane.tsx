"use client";

// A single live terminal pane: an xterm.js terminal attached over a WebSocket
// to one daemon shell session. xterm itself is imported dynamically inside the
// effect so it never runs during SSR / `next build`.

import { useEffect, useRef, useState } from "react";
import "@xterm/xterm/css/xterm.css";
import {
  CornerDownLeft,
  Loader2,
  Play,
  Plug,
  PlugZap,
  Sparkles,
  Terminal as TerminalIcon,
  X,
} from "lucide-react";
import { ApiError, post, wsUrl } from "@/lib/api";
import type { ModelOption, TerminalInfo } from "@/lib/types";

type AIResult = { reply: string; command: string; provider: string; model: string };

type ConnState = "connecting" | "open" | "reconnecting" | "closed";

/** xterm theme tuned to the arc-reactor cyan / near-black aesthetic. */
const XTERM_THEME = {
  background: "#0a0c11",
  foreground: "#cdd3df",
  cursor: "#22d3ee",
  cursorAccent: "#0a0c11",
  selectionBackground: "rgba(34,211,238,0.28)",
  black: "#0b0d11",
  red: "#fb7185",
  green: "#34d399",
  yellow: "#fbbf24",
  blue: "#38bdf8",
  magenta: "#a78bfa",
  cyan: "#22d3ee",
  white: "#cdd3df",
  brightBlack: "#475569",
  brightRed: "#fda4af",
  brightGreen: "#6ee7b7",
  brightYellow: "#fcd34d",
  brightBlue: "#7dd3fc",
  brightMagenta: "#c4b5fd",
  brightCyan: "#67e8f9",
  brightWhite: "#f4f4f5",
} as const;

export function TerminalPane({
  info,
  focused,
  onFocus,
  onClose,
  models = [],
}: {
  info: TerminalInfo;
  focused: boolean;
  onFocus: () => void;
  onClose: () => void;
  /** Model catalog for the PER-PANE AI assist picker (from /models). */
  models?: ModelOption[];
}) {
  const holderRef = useRef<HTMLDivElement | null>(null);
  const [state, setState] = useState<ConnState>("connecting");
  // The live WS, exposed to the AI bar so "Run" can type into THIS shell.
  const wsRef = useRef<WebSocket | null>(null);

  // --- Per-pane AI assist (suggest-only; Run is an explicit click) ---------
  const [aiOpen, setAiOpen] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiResult, setAiResult] = useState<AIResult | null>(null);
  const [choice, setChoice] = useState(""); // "" = the app's default model

  async function askAI(e: React.FormEvent) {
    e.preventDefault();
    if (!aiPrompt.trim() || aiBusy) return;
    setAiBusy(true);
    setAiError(null);
    setAiResult(null);
    try {
      const [provider, model] = choice ? choice.split("::") : ["", ""];
      const res = await post<AIResult>(`/terminals/${info.id}/ai`, {
        prompt: aiPrompt.trim(),
        provider,
        model,
      });
      setAiResult(res);
    } catch (err) {
      setAiError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setAiBusy(false);
    }
  }

  function runSuggested() {
    const ws = wsRef.current;
    if (!aiResult?.command || !ws || ws.readyState !== WebSocket.OPEN) return;
    // Type the command into the shell WITHOUT submitting it — the user presses
    // Enter themselves (a last look before anything executes).
    ws.send(aiResult.command);
    setAiResult(null);
    setAiPrompt("");
  }

  useEffect(() => {
    const holder = holderRef.current;
    if (!holder || typeof window === "undefined") return;

    let disposed = false;
    let term: import("@xterm/xterm").Terminal | null = null;
    let fit: import("@xterm/addon-fit").FitAddon | null = null;
    let ws: WebSocket | null = null;
    let ro: ResizeObserver | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;
    let focusedOnce = false; // steal focus on FIRST connect only — a reconnect
    // mid-interaction would close an open dropdown/popup out from under the user

    const doFit = () => {
      try {
        fit?.fit();
      } catch {
        /* container not measurable yet */
      }
    };

    const sendResize = () => {
      if (ws && ws.readyState === WebSocket.OPEN && term) {
        ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      }
    };

    const onWinResize = () => {
      doFit();
      sendResize();
    };

    const connect = () => {
      ws = new WebSocket(wsUrl(`/terminals/${info.id}/ws`));
      wsRef.current = ws; // the AI bar's "Run" types through this socket
      ws.binaryType = "arraybuffer";
      ws.onopen = () => {
        attempts = 0;
        setState("open");
        doFit();
        sendResize();
        if (!focusedOnce) {
          focusedOnce = true;
          term?.focus();
        }
      };
      ws.onmessage = (ev: MessageEvent) => {
        if (!term) return;
        // Server -> client: PTY output as binary (ArrayBuffer); text just in case.
        if (typeof ev.data === "string") term.write(ev.data);
        else term.write(new Uint8Array(ev.data as ArrayBuffer));
      };
      ws.onclose = (ev: CloseEvent) => {
        if (disposed) return;
        // 4000 = the SHELL ITSELF exited (daemon's explicit signal). There is
        // nothing to reconnect to — retrying just re-attached to a dead PTY in
        // a crash loop that also stole focus every cycle.
        if (ev.code === 4000) {
          setState("closed");
          return;
        }
        if (attempts < 4) {
          attempts += 1;
          setState("reconnecting");
          reconnectTimer = setTimeout(connect, 500 * attempts);
        } else {
          setState("closed");
        }
      };
      ws.onerror = () => {
        try {
          ws?.close();
        } catch {
          /* noop */
        }
      };
    };

    (async () => {
      const [{ Terminal }, { FitAddon }] = await Promise.all([
        import("@xterm/xterm"),
        import("@xterm/addon-fit"),
      ]);
      if (disposed) return;

      term = new Terminal({
        cursorBlink: true,
        cursorStyle: "bar",
        fontSize: 12.5,
        lineHeight: 1.15,
        fontFamily:
          'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
        theme: { ...XTERM_THEME },
        scrollback: 5000,
        allowProposedApi: true,
      });
      fit = new FitAddon();
      term.loadAddon(fit);
      term.open(holder);
      doFit();

      // Client -> server: raw keystrokes as text.
      term.onData((d: string) => {
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(d);
      });

      ro = new ResizeObserver(() => {
        doFit();
        sendResize();
      });
      ro.observe(holder);
      window.addEventListener("resize", onWinResize);

      setState("connecting");
      connect();
    })();

    return () => {
      disposed = true;
      wsRef.current = null;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      window.removeEventListener("resize", onWinResize);
      ro?.disconnect();
      try {
        ws?.close();
      } catch {
        /* noop */
      }
      try {
        term?.dispose();
      } catch {
        /* noop */
      }
    };
    // Re-wire only when the session id changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info.id]);

  return (
    <div
      onMouseDown={onFocus}
      className={`group flex h-full flex-col overflow-hidden rounded-2xl border bg-[#0a0c11] shadow-card transition-colors ${
        focused
          ? "border-accent/50 shadow-glow-sm ring-1 ring-accent/30"
          : "border-white/[0.07] hover:border-white/[0.14]"
      }`}
    >
      {/* Pane header: shell · cwd · connection state · close */}
      <header className="flex shrink-0 items-center gap-2 border-b border-white/[0.06] bg-ink-900/60 px-3 py-2">
        <TerminalIcon
          size={13}
          className={focused ? "text-accent" : "text-zinc-500"}
        />
        <span className="shrink-0 font-mono text-[11px] font-semibold text-zinc-200">
          {info.shell}
        </span>
        <span
          className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-500"
          title={info.cwd}
        >
          {info.cwd}
        </span>
        {/* Per-pane AI model — THIS terminal's assist uses THIS model. */}
        <select
          aria-label="AI model for this terminal"
          value={choice}
          onChange={(e) => setChoice(e.target.value)}
          onMouseDown={(e) => e.stopPropagation()}
          className="field w-auto max-w-[10rem] shrink-0 py-0.5 text-[10px]"
        >
          <option value="">default model</option>
          {models.map((m) => (
            <option key={`${m.provider}::${m.model}`} value={`${m.provider}::${m.model}`}>
              {m.provider} · {m.model}
            </option>
          ))}
        </select>
        <button
          onClick={(e) => {
            e.stopPropagation();
            setAiOpen((v) => !v);
          }}
          title="Ask AI about this terminal"
          className={`grid h-5 w-5 shrink-0 place-items-center rounded-md transition-colors ${
            aiOpen
              ? "bg-accent/15 text-accent"
              : "text-zinc-500 hover:bg-accent/15 hover:text-accent-soft"
          }`}
        >
          <Sparkles size={13} />
        </button>
        <ConnPill state={state} />
        <button
          onClick={(e) => {
            e.stopPropagation();
            onClose();
          }}
          title="Close terminal"
          className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-rose-500/15 hover:text-rose-300"
        >
          <X size={13} />
        </button>
      </header>

      {/* AI assist bar — asks about THIS terminal's recent output; the answer's
          command is only ever TYPED into the shell (never auto-submitted). */}
      {aiOpen && (
        <div className="shrink-0 border-b border-white/[0.06] bg-ink-900/40 px-3 py-2">
          <form onSubmit={askAI} className="flex items-center gap-2">
            <Sparkles size={12} className="shrink-0 text-accent-soft" />
            <input
              type="text"
              value={aiPrompt}
              onChange={(e) => setAiPrompt(e.target.value)}
              placeholder="Ask about this terminal — e.g. “why did that fail?” or “command to list the 5 biggest files”"
              aria-label="Ask AI about this terminal"
              className="field flex-1 py-1 text-[12px]"
            />
            <button
              type="submit"
              disabled={aiBusy || !aiPrompt.trim()}
              className="btn-accent shrink-0 px-2 py-1 text-[11px]"
            >
              {aiBusy ? <Loader2 size={12} className="animate-spin" /> : <CornerDownLeft size={12} />}
              Ask
            </button>
          </form>
          {aiError && (
            <p role="alert" className="mt-1.5 text-[11px] leading-relaxed text-rose-300">
              {aiError}
            </p>
          )}
          {aiResult && (
            <div className="mt-1.5 space-y-1.5">
              <p className="max-h-24 overflow-y-auto whitespace-pre-wrap text-[11px] leading-relaxed text-zinc-300">
                {aiResult.reply}
              </p>
              <div className="flex items-center gap-2">
                {aiResult.command && (
                  <button
                    onClick={runSuggested}
                    disabled={state !== "open"}
                    className="btn-accent px-2 py-1 text-[11px]"
                    title="Types the command into the shell — press Enter yourself to run it"
                  >
                    <Play size={11} /> Type it in
                  </button>
                )}
                <span className="text-[10px] text-zinc-600">
                  {aiResult.provider} · {aiResult.model}
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Terminal surface */}
      <div className="relative flex-1 overflow-hidden px-2 py-1.5">
        <div ref={holderRef} className="h-full w-full" />
        {(state === "reconnecting" || state === "closed") && (
          <div className="pointer-events-none absolute inset-0 grid place-items-center bg-[#0a0c11]/70 backdrop-blur-[1px]">
            <div
              className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium ${
                state === "reconnecting"
                  ? "border-amber-500/30 bg-amber-500/10 text-amber-200"
                  : "border-rose-500/30 bg-rose-500/10 text-rose-200"
              }`}
            >
              {state === "reconnecting" ? (
                <>
                  <Loader2 size={13} className="animate-spin" /> Reconnecting…
                </>
              ) : (
                <>
                  <Plug size={13} /> Session closed
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function ConnPill({ state }: { state: ConnState }) {
  if (state === "open") {
    return (
      <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-emerald-500/25 bg-emerald-500/10 px-1.5 py-0.5 text-[9px] font-medium text-emerald-300">
        <PlugZap size={9} /> live
      </span>
    );
  }
  if (state === "closed") {
    return (
      <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-rose-500/25 bg-rose-500/10 px-1.5 py-0.5 text-[9px] font-medium text-rose-300">
        <Plug size={9} /> closed
      </span>
    );
  }
  return (
    <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-amber-500/25 bg-amber-500/10 px-1.5 py-0.5 text-[9px] font-medium text-amber-300">
      <Loader2 size={9} className="animate-spin" />
      {state === "reconnecting" ? "reconnecting" : "connecting"}
    </span>
  );
}
