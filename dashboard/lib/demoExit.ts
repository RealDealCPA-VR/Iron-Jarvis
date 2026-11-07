/**
 * THE WAY OUT OF THE DEMO MODEL (v1.445.0, ease-of-use review item 2).
 *
 * Before: the strip under the title bar said "a model is connected but not
 * chosen yet" when nothing a person could pick was connected, its "Choose a
 * model" opened a menu where every real row was greyed with the reason only in
 * a hover tooltip, and the connect choices on a new chat vanished with the
 * first message. Every surface that offers the way out now reads the same
 * three facts from here:
 *
 *  - `realModelRows`: what is REALLY connected (never the demo model itself,
 *    never a saved web sign-in, which is not a model anyone can pick);
 *  - `pressableCandidates`: what ONE press ("Use X for answers", POST
 *    /onboarding/use-model, which sets the default everywhere) can choose;
 *  - `openConnect`: show the connect choices where the user is (the chat
 *    page's doors), answering false when no screen took it, so the caller can
 *    go to Settings › Connections instead.
 *
 * Small on purpose (the strip, the model menu and the chat all import it):
 * the one hook, `useConnectTarget`, only listens for the open event.
 */

import { useEffect, useRef } from "react";
import { answerCandidates, type AnswerCandidate, type HealthRow } from "@/lib/onboarding";
import type { Health } from "@/lib/types";

/** The window event that asks the screen on view to show its connect choices. */
export const OPEN_CONNECT_EVENT = "ij:open-connect";

/** Where the connect choices live when no screen on view shows them. */
export const CONNECTIONS_HREF = "/connections";

/** The words of the press that shows the connect choices. */
export const CONNECT_A_MODEL = "Connect a model";

/** The model menu's last row. */
export const CONNECT_A_MODEL_ROW = "Connect a model…";

/** The visible reason on a greyed model menu row. */
export const NOT_CONNECTED_YET = "Not connected yet";

/** What POST /onboarding/use-model can choose (readiness.USE_MODEL_CLIS +
 *  USE_MODEL_LOCAL + providers.manager.API_PROVIDERS + the two local servers).
 *  The same set as ConnectDoors' ONE_PRESS (pinned equal in
 *  __tests__/demo-exit-v1443.test.tsx): a name outside it is answered 409. */
export const PRESSABLE: ReadonlySet<string> = new Set([
  "claude-cli",
  "codex-cli",
  "ollama",
  "custom",
  "anthropic",
  "openai",
  "google",
  "xai",
  "openrouter",
  "lmstudio",
  "llamacpp",
]);

/** Rows of /health that are a real model answering now. The daemon already
 *  leaves the demo model out of /health, but a row named "mock" (or of class
 *  "mock") never counts here either, and a saved web sign-in (class
 *  "browser", the Browser vault) is not a model anyone can choose. */
export function realModelRows(health: Health | null | undefined): HealthRow[] {
  return ((health?.providers ?? []) as HealthRow[]).filter(
    (p) => !!p && p.available === true && p.provider !== "mock" && p.class !== "mock" && p.class !== "browser",
  );
}

/** What one "Use X for answers" press can choose, best first. */
export function pressableCandidates(health: Health | null | undefined): AnswerCandidate[] {
  return answerCandidates(health).filter((c) => PRESSABLE.has(c.provider));
}

interface OpenConnectDetail {
  handled: boolean;
}

/** Ask the screen on view to show its connect choices. True when one did;
 *  false means nothing on screen shows them (go to Connections). */
export function openConnect(): boolean {
  if (typeof window === "undefined") return false;
  const detail: OpenConnectDetail = { handled: false };
  try {
    window.dispatchEvent(new CustomEvent<OpenConnectDetail>(OPEN_CONNECT_EVENT, { detail }));
  } catch {
    return false;
  }
  return detail.handled;
}

/** Is this element somewhere a person can see and use it (in the page, not
 *  under an `inert` cover such as the chat kept behind Chat › Code)? */
export function onView(el: Element | null | undefined): boolean {
  if (!el || !el.isConnected) return false;
  if (el.closest("[inert]")) return false;
  if (el.closest("[hidden]")) return false;
  return true;
}

/** Answer OPEN_CONNECT_EVENT for the element in `ref` while `enabled`: the
 *  first screen on view that can show the choices takes it. */
export function useConnectTarget(
  ref: { current: Element | null },
  enabled: boolean,
  onOpen: () => void,
): void {
  const openRef = useRef(onOpen);
  openRef.current = onOpen;
  useEffect(() => {
    if (!enabled || typeof window === "undefined") return;
    const listen = (e: Event) => {
      const detail = (e as CustomEvent<OpenConnectDetail>).detail;
      if (!detail || detail.handled) return;
      if (!onView(ref.current)) return;
      detail.handled = true;
      openRef.current();
    };
    window.addEventListener(OPEN_CONNECT_EVENT, listen);
    return () => window.removeEventListener(OPEN_CONNECT_EVENT, listen);
  }, [enabled, ref]);
}
