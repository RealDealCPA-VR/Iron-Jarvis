"use client";

/**
 * v1.441.0 (lane g1): the Session tab's live controls in Build › Code — the
 * coding tool's own background work with Stop, and its own MCP servers'
 * actions (reconnect, turn off or on, sign in). Each row keeps one small ⋯
 * menu, never a row of buttons; a row the tool offers nothing for shows no
 * menu at all, and an older Iron Jarvis (no `background_work`, no `actions`)
 * leaves the tab exactly as it was.
 *
 * Stopping one background task goes through the tool's own stop (Claude
 * Code's `stop_task`, Codex's background terminal `terminate`): the reply,
 * if one runs, goes on, and the conversation says what was stopped. A sign-in
 * opens the tool's OWN sign-in page in the user's browser (the desktop sends
 * every window.open through its safe external-link check); Iron Jarvis never
 * signs in.
 */

import { useEffect, useRef, useState } from "react";
import { MoreHorizontal } from "lucide-react";
import { post } from "@/lib/api";
import {
  BACKGROUND_KIND_WORDS,
  MCP_ACTION_WORDS,
  MCP_RELOAD_WORDS,
  mcpActionTitle,
  mcpReloadTitle,
  type BackgroundWork,
  type CodeMcpServer,
} from "@/lib/codePanes";

const QUIET = "text-[12px] text-zinc-500";
const ITEM =
  "flex w-full rounded-lg px-2.5 py-1.5 text-left text-[13px] text-zinc-300 hover:bg-white/[0.06] focus-visible:outline-none focus-visible:bg-white/[0.06] disabled:opacity-50";

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** A sign-in address the page may open: http(s) only. */
export function openableSignIn(raw: unknown): string {
  if (typeof raw !== "string" || !raw) return "";
  try {
    const u = new URL(raw);
    return (u.protocol === "https:" || u.protocol === "http:") && !u.username && !u.password ? u.toString() : "";
  } catch {
    return "";
  }
}

/** One ⋯ button and its menu. Escape or leaving the menu closes it. */
function RowMenu({
  label,
  testId,
  busy,
  items,
}: {
  label: string;
  testId: string;
  busy: boolean;
  items: { key: string; label: string; title?: string; onPick: () => void }[];
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);
  if (items.length === 0) return null;
  return (
    <div
      ref={box}
      className="relative shrink-0"
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          e.stopPropagation();
          setOpen(false);
        }
      }}
    >
      <button
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        data-testid={testId}
        disabled={busy}
        onClick={() => setOpen((v) => !v)}
        className="grid h-6 w-6 place-items-center rounded-md text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:opacity-50"
      >
        <MoreHorizontal size={13} aria-hidden />
      </button>
      {open ? (
        <div role="menu" className="absolute right-0 top-full z-20 w-44 rounded-xl bg-ink-800 p-1 shadow-card">
          {items.map((it) => (
            <button
              key={it.key}
              type="button"
              role="menuitem"
              data-testid={`${testId}-${it.key}`}
              title={it.title || undefined}
              onClick={() => {
                setOpen(false);
                it.onPick();
              }}
              className={ITEM}
            >
              {it.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** The tool's own background work, each with Stop in its ⋯ menu. */
export function BackgroundWorkList({
  sessionId,
  work,
  toolName,
  onChanged,
}: {
  sessionId: string;
  work: readonly BackgroundWork[];
  toolName: string;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  async function stop(w: BackgroundWork) {
    setBusy(w.id);
    setProblem(null);
    try {
      await post(`/build/app/sessions/${encodeURIComponent(sessionId)}/background/${encodeURIComponent(w.id)}/stop`, {});
      onChanged();
    } catch (e) {
      setProblem(errText(e));
    } finally {
      setBusy(null);
    }
  }

  if (work.length === 0) return null;
  return (
    <div data-testid="code-session-background-work" className="space-y-1.5">
      <p className={QUIET}>{toolName} is still running in the background:</p>
      <ul className="space-y-1">
        {work.map((w) => (
          <li key={w.id} data-testid={`code-session-work-${w.id}`} className="flex min-w-0 items-center gap-2">
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] text-zinc-300" title={w.title}>
                {w.title}
              </span>
              <span className="text-[11px] text-zinc-500">
                {busy === w.id ? "Stopping…" : BACKGROUND_KIND_WORDS[w.kind] ?? "Task"}
              </span>
            </span>
            <RowMenu
              label={`More for ${w.title}`}
              testId={`code-session-work-more-${w.id}`}
              busy={busy !== null}
              items={
                w.canStop
                  ? [{ key: "stop", label: "Stop", title: "Stops only this; the reply goes on.", onPick: () => void stop(w) }]
                  : []
              }
            />
          </li>
        ))}
      </ul>
      {problem ? (
        <p role="alert" className="text-[12px] text-tone-danger">
          {problem}
        </p>
      ) : null}
    </div>
  );
}

/** The ⋯ menu of one of the tool's own MCP servers (only what it offers). */
export function McpRowMenu({
  sessionId,
  server,
  tool,
  toolName,
  onChanged,
}: {
  sessionId: string;
  server: CodeMcpServer;
  tool: string;
  toolName: string;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<{ text: string; tone: "quiet" | "danger"; link?: string } | null>(null);

  async function act(action: string) {
    setBusy(true);
    setSaid(null);
    try {
      const res = (await post(`/build/app/sessions/${encodeURIComponent(sessionId)}/mcp/${encodeURIComponent(server.name)}`, {
        action,
      })) as { words?: unknown; url?: unknown } | null;
      const words = typeof res?.words === "string" ? res.words : "";
      const url = openableSignIn(res?.url);
      if (action === "sign_in" && url) {
        // window.open with "noopener" always answers null (the HTML spec),
        // and the desktop app opens it in the browser and answers null too —
        // so whether a page opened is NOT known here (v1.441.0 review). The
        // user's own link stays, worded for the case it is for.
        window.open(url, "_blank", "noopener,noreferrer");
        setSaid({ text: words || "Finish signing in in your browser.", tone: "quiet", link: url });
      } else if (words) {
        setSaid({ text: words, tone: "quiet" });
      }
      onChanged();
    } catch (e) {
      setSaid({ text: errText(e), tone: "danger" });
    } finally {
      setBusy(false);
    }
  }

  const items = (server.actions ?? []).map((a) => ({
    key: a,
    label: MCP_ACTION_WORDS[a] ?? a,
    title: mcpActionTitle(a, tool, toolName),
    onPick: () => void act(a),
  }));
  return (
    <>
      <span className="ml-auto">
        <RowMenu label={`More for ${server.name}`} testId={`code-session-mcp-more-${server.name}`} busy={busy} items={items} />
      </span>
      {said ? (
        <span
          data-testid={`code-session-mcp-said-${server.name}`}
          role={said.tone === "danger" ? "alert" : "status"}
          className={`basis-full ${said.tone === "danger" ? "text-[12px] text-tone-danger" : QUIET}`}
        >
          {said.text}
          {said.link ? (
            <>
              {" If no page opened: "}
              <a href={said.link} target="_blank" rel="noopener noreferrer" className="text-accent underline">
                Open the sign-in page
              </a>
            </>
          ) : null}
        </span>
      ) : null}
    </>
  );
}

/** v1.447.1: the whole MCP list's own reload (Codex's
 *  `config/mcpServer/reload`): one quiet button under the list, refused in
 *  the daemon's words while a reply runs. */
export function McpReloadButton({
  sessionId,
  toolName,
  onChanged,
}: {
  sessionId: string;
  toolName: string;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<{ text: string; tone: "quiet" | "danger" } | null>(null);

  async function reload() {
    setBusy(true);
    setSaid(null);
    try {
      const res = (await post(`/build/app/sessions/${encodeURIComponent(sessionId)}/mcp-reload`, {})) as {
        words?: unknown;
      } | null;
      const words = typeof res?.words === "string" ? res.words : "";
      if (words) setSaid({ text: words, tone: "quiet" });
      onChanged();
    } catch (e) {
      setSaid({ text: errText(e), tone: "danger" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-1">
      <button
        type="button"
        data-testid="code-session-mcp-reload"
        disabled={busy}
        title={mcpReloadTitle(toolName)}
        onClick={() => void reload()}
        className="rounded-lg px-2 py-1 text-[12px] text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-200 disabled:opacity-50"
      >
        {busy ? "Reloading…" : MCP_RELOAD_WORDS}
      </button>
      {said ? (
        <p
          data-testid="code-session-mcp-reload-said"
          role={said.tone === "danger" ? "alert" : "status"}
          className={said.tone === "danger" ? "text-[12px] text-tone-danger" : QUIET}
        >
          {said.text}
        </p>
      ) : null}
    </div>
  );
}
