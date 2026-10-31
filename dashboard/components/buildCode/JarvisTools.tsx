"use client";

/**
 * v1.414.0: Build › Code knows the user — with consent.
 *
 * `JarvisToolsChip` — the session's "Iron Jarvis tools" boxes: Memory and
 * Browser, the two Capabilities Iron Jarvis serves to a coding tool (the
 * same two a Build terminal's Capabilities give it). ALL OFF until the user
 * ticks one; a ticked box hands the REAL Claude Code / Codex / Pi / OpenCode
 * Iron Jarvis's own MCP connection for this session (the daemon's
 * `hosted/jarvis_tools`). Each box says what it gives and who sees it BEFORE
 * the press. A box turned on (or the last one off) reaches the tool from the
 * next message; a box turned off is refused on the tool's very next call.
 *
 * `ProfileShareOffer` — one quiet line in a new session whose tool's
 * "Share my profile with <tool>" switch is off: Share turns that existing
 * setting on through the existing route (`PUT /profile/share`); "No thanks"
 * dismisses it for good (this browser). Never automatic.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { get, patch, put } from "@/lib/api";
import {
  JARVIS_TOOL_KEYS,
  appliedNextWords,
  decodeCodeSession,
  jarvisToolHint,
  jarvisToolsWords,
  shareCliOf,
  shareOfferDismissKey,
  type CodeSession,
  type JarvisTools,
} from "@/lib/buildApp";
import { decodeShareView, fileLine, shareSentence, type ShareCli } from "@/lib/profileShare";

const SESSIONS = "/build/app/sessions";

export function JarvisToolsChip({
  session,
  toolName,
  onChanged,
  onInfo,
  disabled = false,
}: {
  session: CodeSession;
  toolName: string;
  onChanged: (s: CodeSession) => void;
  onInfo: (words: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const id = useId();
  const tools: JarvisTools = session.jarvisTools ?? { memory: false, browser: false };

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const flip = useCallback(
    async (key: keyof JarvisTools) => {
      setBusy(key);
      setError(null);
      try {
        const raw = await patch<unknown>(`${SESSIONS}/${encodeURIComponent(session.id)}`, {
          capabilities: { [key]: !tools[key] },
        });
        const next = decodeCodeSession(raw);
        if (next) onChanged(next);
        const later = appliedNextWords(raw);
        if (later) onInfo(later);
      } catch {
        setError("That did not save — the session keeps the tools it had.");
      } finally {
        setBusy(null);
      }
    },
    [session.id, tools, onChanged, onInfo],
  );

  // A conversation found on disk (not started here) has no boxes yet.
  if (!session.jarvisTools) return null;
  const on = JARVIS_TOOL_KEYS.some((k) => tools[k]);
  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        data-testid="code-jarvis-tools"
        data-on={on ? "true" : "false"}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={`${id}-panel`}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={`rounded-md px-2 py-1 text-[12px] transition-colors hover:bg-white/[0.06] disabled:opacity-50 ${
          on ? "text-accent-soft" : "text-zinc-500 hover:text-zinc-200"
        }`}
      >
        {jarvisToolsWords(tools)}
      </button>
      {open ? (
        <div
          id={`${id}-panel`}
          role="dialog"
          aria-label="Iron Jarvis tools for this session"
          data-testid="code-jarvis-tools-panel"
          className="absolute right-0 top-full z-20 mt-1 w-80 max-w-[calc(100vw-2rem)] rounded-xl bg-ink-800 p-3 shadow-card"
        >
          <p className="mb-2 text-[12px] text-zinc-400">
            Let {toolName} use Iron Jarvis&apos;s own tools in this session. Off until you tick one.
          </p>
          <ul className="space-y-2">
            {JARVIS_TOOL_KEYS.map((key) => (
              <li key={key}>
                <label className="flex cursor-pointer items-start gap-2 text-[13px] text-zinc-200">
                  <input
                    type="checkbox"
                    data-testid={`code-jarvis-tool-${key}`}
                    checked={tools[key]}
                    disabled={busy !== null}
                    onChange={() => void flip(key)}
                    className="mt-0.5"
                  />
                  <span>
                    <span className="block">{key === "memory" ? "Memory" : "Browser"}</span>
                    <span className="block text-[12px] text-zinc-500">
                      {jarvisToolHint(key, toolName, session.tool)}
                    </span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
          {error ? (
            <p role="alert" className="mt-2 text-[12px] text-tone-danger">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function dismissed(cli: string): boolean {
  try {
    return window.localStorage.getItem(shareOfferDismissKey(cli)) === "1";
  } catch {
    return false;
  }
}

export function ProfileShareOffer({ tool, toolName }: { tool: string; toolName: string }) {
  const cli = shareCliOf(tool);
  const [row, setRow] = useState<ShareCli | null>(null);
  const [gone, setGone] = useState(() => !cli || (typeof window !== "undefined" && dismissed(cli)));
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!cli || gone) return;
    let live = true;
    get<unknown>("/profile/share")
      .then((raw) => {
        if (!live) return;
        const view = decodeShareView(raw);
        setRow(view?.find((c) => c.cli === cli) ?? null);
      })
      .catch(() => {
        // An older Iron Jarvis (404) or none answering: no offer.
        if (live) setRow(null);
      });
    return () => {
      live = false;
    };
  }, [cli, gone]);

  const share = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const raw = await put<unknown>("/profile/share", { cli, on: true });
      // v1.414.0 review: the answer is the view AFTER the write. A file that
      // could not be written (OpenCode's AGENTS.md while it reads your Claude
      // Code CLAUDE.md, a linked file, …) is said, never "shared".
      const after = decodeShareView(raw)?.find((c) => c.cli === cli);
      const refused = after?.files.map(fileLine).find((line, i) => line && after.files[i].error) ?? null;
      if (refused) {
        setError(refused);
        return;
      }
      setDone(true);
    } catch {
      setError("Sharing did not start. You can turn it on in Settings › Memory & you.");
    } finally {
      setBusy(false);
    }
  }, [cli]);

  const noThanks = useCallback(() => {
    try {
      window.localStorage.setItem(shareOfferDismissKey(cli), "1");
    } catch {
      /* no storage: hidden for this visit */
    }
    setGone(true);
  }, [cli]);

  if (gone || !row || !row.available || (row.on && !done)) return null;
  if (done) {
    return (
      <p role="status" data-testid="code-share-offer-done" className="mb-3 text-center text-[12px] text-zinc-500">
        Your profile is shared with {toolName}. It applies from its next start.
      </p>
    );
  }
  return (
    <div data-testid="code-share-offer" className="mb-3 rounded-lg px-3 py-2 text-[12px] text-zinc-400">
      <p>
        Share your profile with {toolName}? {shareSentence(row)}
      </p>
      <div className="mt-1.5 flex gap-2">
        <button
          type="button"
          data-testid="code-share-offer-yes"
          disabled={busy}
          onClick={() => void share()}
          className="rounded-md px-2 py-1 text-accent-soft hover:bg-white/[0.06] disabled:opacity-50"
        >
          Share your profile with {toolName}
        </button>
        <button
          type="button"
          data-testid="code-share-offer-no"
          onClick={noThanks}
          className="rounded-md px-2 py-1 text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-200"
        >
          No thanks
        </button>
      </div>
      {error ? (
        <p role="alert" className="mt-1 text-tone-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}
