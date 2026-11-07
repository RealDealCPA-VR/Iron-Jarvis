"use client";

/**
 * v1.443.0 — ANTIGRAVITY ON THE CONNECTIONS PAGE. The user: "The login is not
 * being recognized in the connections". Google's Antigravity (`agy`) signs in
 * by ITSELF, with its own Google sign-in kept in Windows' credential store;
 * Iron Jarvis reads it only through `agy` itself (hosted/antigravity_catalog:
 * one short `agy models` run in the background, once Antigravity's own
 * sign-in finished). It is a Build › Code tool, not a chat model, so the row
 * says where it is used — and it is NOT the "Google (Gemini)" card above,
 * which is Google's pay-per-use API.
 *
 * Reads GET /build/app/harnesses (the Code view's own list). "Check again"
 * asks for a fresh read (`?recheck=antigravity`) and looks again while it
 * runs; the card's "Look again" does the same (`lookAgain` changes). An older
 * daemon (the route missing or failing) renders nothing.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Orbit, RefreshCw } from "lucide-react";
import { ApiError, get } from "@/lib/api";
import { decodeCodingTools, type CodingTool } from "@/lib/buildApp";
import { launchAppSignIn, paneHref } from "@/lib/cliSignIn";
import { Badge } from "@/components/ui";
import { CALM_ACTION } from "@/components/connections/endpointChip";

/** Google's own page for the Antigravity CLI (terminals/ai_clis' Launch row). */
export const ANTIGRAVITY_INSTALL_URL = "https://github.com/google-antigravity/antigravity-cli";
/** While Antigravity's list is being read, the row looks again this often… */
export const AGY_REREAD_MS = 2_000;
/** …at most this many times. */
export const AGY_REREAD_TRIES = 8;

export const AGY_DESCRIPTION = "Your Google sign-in · used in Build › Code";
export const AGY_SIGNED_IN = "Signed in · Build › Code";
export const AGY_NOT_SIGNED_IN = "Installed · not signed in";
export const AGY_NOT_CHECKED = "Installed · not checked yet";
export const AGY_CHECKING = "Checking…";
/** What the row says about where Antigravity is used (its title). */
export const AGY_WHERE =
  "Antigravity runs in Build › Code with its own tools. Chat's model menu does not use it, and it is separate " +
  "from the Google (Gemini) API key card.";

type AgyState = "missing" | "signed_in" | "signed_out" | "checking" | "unknown";

/** The row's state from the Code view's tool list (null = say nothing). */
export function antigravityState(tools: CodingTool[] | null): AgyState | null {
  if (tools === null) return null;
  const t = tools.find((x) => x.id === "antigravity");
  if (!t || !t.installed) return "missing";
  if (t.signedIn === true) return "signed_in";
  if (t.signedIn === false || t.signinFinished === false) return "signed_out";
  if (t.modelsReading) return "checking";
  return "unknown";
}

const ROW_LINK =
  "font-medium text-accent-soft underline decoration-accent/40 underline-offset-2 transition-colors hover:text-accent";

export function AntigravityRow({ lookAgain = 0 }: { lookAgain?: number }) {
  const [tools, setTools] = useState<CodingTool[] | null>(null);
  const [note, setNote] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [pane, setPane] = useState<string | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(true);

  const read = useCallback(async (recheck: boolean, tries: number) => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    let got: CodingTool[];
    try {
      const raw = await get<unknown>(`/build/app/harnesses${recheck ? "?recheck=antigravity" : ""}`);
      const list = (raw as { harnesses?: unknown } | null)?.harnesses;
      if (!Array.isArray(list)) return; // not the Code view's list: say nothing
      got = decodeCodingTools(list);
    } catch {
      return; // an older daemon, or the daemon is not answering: say nothing
    }
    if (!alive.current) return;
    setTools(got);
    const t = got.find((x) => x.id === "antigravity");
    setNote(t?.modelsNote ?? "");
    if (t?.modelsReading && tries > 0) {
      timer.current = setTimeout(() => void read(false, tries - 1), AGY_REREAD_MS);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void read(false, AGY_REREAD_TRIES);
    return () => {
      alive.current = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [read]);

  useEffect(() => {
    if (lookAgain > 0) void read(true, AGY_REREAD_TRIES);
  }, [lookAgain, read]);

  const state = antigravityState(tools);
  if (state === null) return null;

  async function signIn() {
    setBusy(true);
    setError(null);
    try {
      setPane(await launchAppSignIn("agy"));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const check = (
    <button
      type="button"
      data-testid="agy-check-again"
      onClick={() => void read(true, AGY_REREAD_TRIES)}
      className={`-ml-1 ${CALM_ACTION}`}
    >
      <RefreshCw size={12} aria-hidden /> Check again
    </button>
  );

  return (
    <div data-testid="agy-row" className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
      <div className="flex min-w-0 items-center gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-white/[0.08] bg-white/[0.03]">
          <Orbit size={16} className="text-tone-info" aria-hidden />
        </span>
        <div className="min-w-0">
          <div className="text-sm font-medium text-zinc-100">Antigravity</div>
          <div className="truncate text-[11px] text-zinc-500" title={AGY_WHERE}>
            {AGY_DESCRIPTION}
          </div>
          {state === "missing" ? (
            <div data-testid="agy-get" className="mt-1 text-[11px] leading-relaxed text-zinc-500">
              Not on this PC yet.{" "}
              <a href={ANTIGRAVITY_INSTALL_URL} target="_blank" rel="noreferrer" className={ROW_LINK}>
                Get Antigravity
              </a>
            </div>
          ) : state === "signed_out" ? (
            <div data-testid="agy-sign-in" className="mt-1 text-[11px] leading-relaxed text-zinc-500">
              Not signed in yet. Sign in opens a Build terminal running agy; sign in with Google there.{" "}
              <button
                type="button"
                onClick={() => void signIn()}
                disabled={busy}
                className={`-ml-1 ${CALM_ACTION}`}
              >
                {busy && <Loader2 size={13} className="animate-spin motion-reduce:animate-none" aria-hidden />}
                Sign in
              </button>
              {check}
              {pane !== undefined && (
                <span className="block">
                  <a href={paneHref(pane)} className={ROW_LINK}>
                    Open the Build terminal
                  </a>{" "}
                  · then press Check again.
                </span>
              )}
            </div>
          ) : state !== "signed_in" ? (
            <div data-testid="agy-unknown" className="mt-1 text-[11px] leading-relaxed text-zinc-500">
              {note || "Iron Jarvis asks Antigravity for its models to see that its sign-in works."} {check}
            </div>
          ) : null}
          {error && (
            <span role="alert" className="mt-0.5 block text-[11px] text-tone-danger">
              {error}
            </span>
          )}
        </div>
      </div>
      {state === "signed_in" ? (
        <Badge variant="calm" data-testid="agy-badge" value={AGY_SIGNED_IN} tone="green" keepCase />
      ) : state === "signed_out" ? (
        <Badge variant="calm" data-testid="agy-badge" value={AGY_NOT_SIGNED_IN} tone="amber" keepCase />
      ) : state === "missing" ? (
        <Badge variant="calm" data-testid="agy-badge" value="Not detected" tone="slate" keepCase />
      ) : (
        <Badge
          variant="calm"
          data-testid="agy-badge"
          value={state === "checking" ? AGY_CHECKING : AGY_NOT_CHECKED}
          tone="slate"
          keepCase
        />
      )}
    </div>
  );
}
