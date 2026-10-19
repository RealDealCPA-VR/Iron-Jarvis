"use client";

/**
 * "Another computer running Jarvis" (v1.357.0, G61) — THIS computer's task
 * door, in Agents › Your team beside the remote agents it pairs with.
 *
 * Turn it on here and the daemon mints a token (shown ONCE, rotated by
 * turning it on again) and says the address to give the other computer —
 * with the same reachability sentence the remote inbound door gives (the
 * packaged app answers 127.0.0.1 only; nothing is opened on the user's
 * behalf). On the other computer the user adds this one through its EXISTING
 * "Connect a remote agent" form: http-task, this address, this token. Its
 * "@desk do X" then runs here and the answer goes back to its chat.
 *
 * The calm pieces only: a sub-label over a hairline, quiet grey lines, ghost
 * controls, ONE primary while off, and the token box + reachability note in
 * exactly the shapes the remote row's message-back already draws. An older
 * daemon (404) renders nothing; a failed read is the one LoadFailedLine.
 */

import { useCallback, useEffect, useId, useState } from "react";
import { ApiError, get, post } from "@/lib/api";
import { ConfirmButton, ErrorNote, LoaderInline } from "@/components/ui";
import { LoadFailedLine } from "@/components/LoadFailedLine";
import { timeAgo } from "@/lib/format";
import {
  CALLER_LABEL,
  CALLER_PLACEHOLDER,
  CARD_TITLE,
  OFF_EXPLAINER,
  TOKEN_ONCE,
  TURN_ON,
  addThisComputerSteps,
  decodeDoor,
  lastLine,
  onLine,
  replyFailedLine,
  type TaskDoorStatus,
} from "@/lib/anotherJarvis";
import { GHOST_BTN, QUIET_FIELD, SUB_LABEL, primaryBtn } from "./teamLook";

export function AnotherJarvisCard() {
  const fieldId = useId();
  const titleId = useId();
  const [status, setStatus] = useState<TaskDoorStatus | null>(null);
  const [supported, setSupported] = useState(true);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [caller, setCaller] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // THE TOKEN IS SHOWN ONCE, right here, right after it is minted — the
  // daemon never returns it again (a status read carries no token).
  const [minted, setMinted] = useState<string | null>(null);
  const [copied, setCopied] = useState<"token" | "address" | null>(null);

  const load = useCallback(async () => {
    try {
      const raw = await get<unknown>("/agents/task-door");
      setStatus(decodeDoor(raw));
      setSupported(true);
      setLoadError(null);
    } catch (err) {
      // An older Iron Jarvis has no door: show nothing, never a broken control.
      if (err instanceof ApiError && err.status === 404) {
        setSupported(false);
        return;
      }
      setLoadError(err instanceof ApiError ? err : new ApiError(String(err), 0));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function turnOn(name: string) {
    setBusy(true);
    setError(null);
    setCopied(null);
    try {
      const raw = await post<unknown>("/agents/task-door/enable", { name: name.trim() });
      const next = decodeDoor(raw);
      setMinted(next.token ?? null);
      setStatus({ ...next, token: undefined });
      setCaller("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function turnOff() {
    setError(null);
    try {
      const raw = await post<unknown>("/agents/task-door/disable");
      setStatus(decodeDoor(raw));
      setMinted(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function copy(text: string, what: "token" | "address") {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(what); // only when writeText resolved — never a fake check
    } catch {
      setCopied(null);
    }
  }

  if (!supported) return null;

  const failed = status ? replyFailedLine(status) : "";
  const reachNote =
    status?.enabled && status.reachable && status.reachable.host_allowed === false
      ? status.reachable.note ?? ""
      : "";

  return (
    <section
      data-testid="another-jarvis"
      aria-labelledby={titleId}
      className="space-y-2.5 border-t hairline pt-3"
    >
      <h4 id={titleId} className={`px-1 ${SUB_LABEL}`}>
        {CARD_TITLE}
      </h4>

      {!status && loadError && (
        <LoadFailedLine
          thing="This computer's task door"
          error={loadError}
          onRetry={() => void load()}
          className="px-1 text-[12px] text-zinc-500"
          testId="another-jarvis-load-failed"
        />
      )}

      {status && !status.enabled && (
        <>
          <p className="px-1 text-[12px] leading-relaxed text-zinc-500">{OFF_EXPLAINER}</p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void turnOn(caller);
            }}
            className="flex flex-col gap-2 sm:flex-row sm:items-center"
          >
            <label htmlFor={fieldId} className="sr-only">
              {CALLER_LABEL}
            </label>
            <input
              id={fieldId}
              value={caller}
              onChange={(e) => setCaller(e.target.value)}
              placeholder={`${CALLER_LABEL} For example ${CALLER_PLACEHOLDER}`}
              autoComplete="off"
              maxLength={40}
              className={QUIET_FIELD}
            />
            <button
              type="submit"
              disabled={busy}
              data-testid="another-jarvis-on-press"
              className={primaryBtn(!busy)}
            >
              {busy ? <LoaderInline label="Turning on…" /> : TURN_ON}
            </button>
          </form>
        </>
      )}

      {status?.enabled && (
        <>
          <p data-testid="another-jarvis-on" className="px-1 text-[12px] leading-relaxed text-zinc-400">
            {onLine(status)}{" "}
            <span className="text-zinc-500">{lastLine(status, timeAgo)}</span>
          </p>
          {failed && (
            <p
              data-testid="another-jarvis-reply-failed"
              className="px-1 text-[12px] leading-relaxed text-tone-warn"
            >
              {failed}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2 px-1">
            <span className="text-[12px] text-zinc-500">Address</span>
            <code
              data-testid="another-jarvis-url"
              className="min-w-0 truncate font-mono text-[12px] text-zinc-200"
            >
              {status.url}
            </code>
            <button
              type="button"
              onClick={() => void copy(status.url, "address")}
              disabled={!status.url}
              className={GHOST_BTN}
            >
              {copied === "address" ? "Copied" : "Copy address"}
            </button>
          </div>
          {reachNote && (
            <p
              data-testid="another-jarvis-reach-note"
              className="rounded-md bg-tone-warn/[0.08] px-2.5 py-1.5 text-[12px] leading-relaxed text-tone-warn"
            >
              {reachNote}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-1.5 px-1">
            <button
              type="button"
              onClick={() => void turnOn("")}
              disabled={busy}
              data-testid="another-jarvis-rotate"
              title="Make a new token. The old one stops working at once"
              className={GHOST_BTN}
            >
              {busy ? <LoaderInline label="…" /> : "Rotate token"}
            </button>
            <ConfirmButton
              variant="calm"
              onConfirm={turnOff}
              label="Turn off"
              title="Stop taking tasks from another computer. Its token is forgotten"
            />
          </div>
        </>
      )}

      {minted && status?.enabled && (
        <div
          data-testid="another-jarvis-minted"
          aria-live="polite"
          className="rounded-lg bg-tone-success/[0.06] px-3 py-2 text-[12px] text-zinc-200"
        >
          <div className="font-medium">{TOKEN_ONCE}</div>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <code className="min-w-0 truncate rounded-md bg-ink-900/80 px-2 py-1 font-mono text-[12px] text-zinc-100">
              {minted}
            </code>
            <button
              type="button"
              onClick={() => void copy(minted, "token")}
              className="rounded-md px-2 py-1 text-[12px] font-medium text-tone-success transition-colors hover:bg-tone-success/10"
            >
              {copied === "token" ? "Copied" : "Copy token"}
            </button>
          </div>
          <p data-testid="another-jarvis-steps" className="mt-1.5 leading-relaxed text-zinc-400">
            {addThisComputerSteps(status.caller)}
          </p>
          <button
            type="button"
            onClick={() => setMinted(null)}
            className="btn-ghost mt-1 py-0.5 text-[11px]"
          >
            Done
          </button>
        </div>
      )}

      {error && <ErrorNote>{error}</ErrorNote>}
    </section>
  );
}

export default AnotherJarvisCard;
