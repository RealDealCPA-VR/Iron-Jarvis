"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { usePathname } from "next/navigation";
import { ShieldAlert } from "lucide-react";
import { ApiError, get, post } from "@/lib/api";
import { useDaemon } from "@/lib/daemon";
import { useEvents } from "@/lib/useEvents";

/**
 * v1.446.0 (ease-of-use review item 23): Emergency stop, seen everywhere.
 *
 * Once Emergency stop is on, schedules, goals, rules, phone tasks and
 * Autonomy are all skipped — and before this nothing outside the Autonomy
 * page said so. Forget you turned it on and you got a silent week. This file
 * holds:
 *
 *  - ONE small store of "is Emergency stop on" for this window. It is read
 *    from `GET /autonomy` (`kill_switch`, the setting `autonomy_kill_switch`)
 *    when the window opens, when the daemon comes back after an outage, and
 *    whenever a `config.changed` event names `autonomy_kill_switch` — the
 *    settings writer publishes one for every door that flips it (the Autonomy
 *    page, Settings, Ctrl K, chat). No poll: nothing re-reads on a clock.
 *    The Autonomy page also feeds the store from its own 10 s read and from
 *    its button's answer, so the strip follows a press there at once.
 *  - The app-wide strip (`EmergencyStopStrip`, mounted once in the layout
 *    under the demo strip): "Emergency stop is on — nothing starts on its
 *    own · Turn off". Turn off is the Autonomy page's own action
 *    (`POST /autonomy/kill {enabled: false}`). Hidden on the Autonomy page,
 *    which carries its own, longer notice and button.
 *
 * HEIGHT (the v1.314.0 `--ij-strip-h` rule): the full-height modules (chat,
 * the agents team screen, Build's rail…) subtract `--ij-strip-h`, which the
 * demo strip (SimulatedBanner) writes with ITS height on <html>. This strip
 * cannot change those files or that one, so while it shows it keeps the same
 * variable equal to BOTH heights: its own plus the demo strip's (measured
 * from the DOM). When the demo strip rewrites the variable (it resizes,
 * shows, hides), a MutationObserver on <html>'s style sees the value no
 * longer equals the sum and writes the sum again; a write that already
 * equals the sum is left alone, so the two never loop. When this strip hides
 * it puts the variable back to the demo strip's own height.
 */

/* ---- The store ----------------------------------------------------------- */

export interface EmergencyStopState {
  /** True once a read or a press has said what the daemon holds. */
  known: boolean;
  /** Emergency stop is on (the daemon's `kill_switch`). */
  on: boolean;
}

let state: EmergencyStopState = { known: false, on: false };
const listeners = new Set<() => void>();
let inflight: Promise<void> | null = null;
let readAgain = false;
/** Bumped by every press/known answer, so a read that started before it
 *  never overwrites what the press said. */
let generation = 0;

function setState(next: EmergencyStopState) {
  if (next.known === state.known && next.on === state.on) return;
  state = next;
  for (const l of listeners) l();
}

/** The current value (for tests and plain reads). */
export function emergencyStopState(): EmergencyStopState {
  return state;
}

/** What the daemon just said — from a press's answer or another page's own
 *  read of `/autonomy`. Wins over a read already on its way. */
export function noteEmergencyStop(on: boolean) {
  generation += 1;
  setState({ known: true, on: !!on });
}

/** Read `GET /autonomy` once. One read at a time; a read asked for while one
 *  is out runs once more after it (the first may have started before the
 *  change it was asked about). A failed read changes nothing. */
export function refreshEmergencyStop(): Promise<void> {
  if (inflight) {
    readAgain = true;
    return inflight;
  }
  const started = generation;
  inflight = (async () => {
    try {
      const r = await get<{ kill_switch?: unknown }>("/autonomy");
      if (generation === started && r && typeof r === "object" && typeof r.kill_switch === "boolean") {
        setState({ known: true, on: r.kill_switch });
      }
    } catch {
      /* offline or an older daemon — keep what we knew */
    } finally {
      inflight = null;
      if (readAgain) {
        readAgain = false;
        void refreshEmergencyStop();
      }
    }
  })();
  return inflight;
}

/** For tests: forget everything this window knew. */
export function resetEmergencyStopForTests() {
  state = { known: false, on: false };
  listeners.clear();
  inflight = null;
  readAgain = false;
  generation = 0;
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}
const snapshot = () => state;
// v1.483.1: ONE object — React compares the server snapshot by identity and
// warns ("getServerSnapshot should be cached") when it is new every call.
const SERVER_STATE: EmergencyStopState = { known: false, on: false };
const serverSnapshot = (): EmergencyStopState => SERVER_STATE;

/** Is Emergency stop on? Reads once when nothing is known yet (a page shown
 *  outside the layout, or before the strip's first read lands). */
export function useEmergencyStop(): EmergencyStopState {
  const s = useSyncExternalStore(subscribe, snapshot, serverSnapshot);
  useEffect(() => {
    if (!state.known) void refreshEmergencyStop();
  }, []);
  return s;
}

/** The setting the store follows (config.changed names it). */
export const EMERGENCY_STOP_KEY = "autonomy_kill_switch";

/** Does a `config.changed` payload name Emergency stop? */
export function namesEmergencyStop(payload: unknown): boolean {
  if (!payload || typeof payload !== "object") return false;
  const keys = (payload as { keys?: unknown }).keys;
  return Array.isArray(keys) && keys.includes(EMERGENCY_STOP_KEY);
}

/** Turn Emergency stop off — the Autonomy page's own action. Returns the
 *  daemon's error words, or null when it worked. */
export async function turnOffEmergencyStop(): Promise<string | null> {
  try {
    const r = await post<{ kill_switch?: boolean }>("/autonomy/kill", { enabled: false });
    noteEmergencyStop(typeof r?.kill_switch === "boolean" ? r.kill_switch : false);
    return null;
  } catch (err) {
    return err instanceof ApiError ? err.message : String(err);
  }
}

/* ---- The words (one place) ----------------------------------------------- */

export const EMERGENCY_STOP_ON_WORDS = "Emergency stop is on — nothing starts on its own";
export const EMERGENCY_STOP_TURN_OFF = "Turn off";

/* ---- The height keeper --------------------------------------------------- */

/** The demo strip's own variable (components/SimulatedBanner STRIP_HEIGHT_VAR). */
const STRIP_VAR = "--ij-strip-h";
/** The demo strip, found in the DOM (its testid is pinned by its own tests). */
const DEMO_SELECTOR = '[data-testid="simulated-banner"]';

function demoHeight(): number {
  try {
    const el = document.querySelector(DEMO_SELECTOR);
    return el ? Math.max(0, Math.round(el.getBoundingClientRect().height)) : 0;
  } catch {
    return 0;
  }
}

function writeStripVar(px: number) {
  try {
    const root = document.documentElement;
    const want = `${Math.max(0, Math.round(px))}px`;
    if (root.style.getPropertyValue(STRIP_VAR) !== want) root.style.setProperty(STRIP_VAR, want);
  } catch {
    /* no document */
  }
}

/* ---- The strip ----------------------------------------------------------- */

const EVENT_OPTS = { types: ["config.changed"] };

export function EmergencyStopStrip() {
  const stop = useEmergencyStop();
  const { online } = useDaemon();
  const { events } = useEvents(10, EVENT_OPTS);
  const pathname = usePathname();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Read again when the daemon comes back (it may have changed while away).
  const wasOnline = useRef(online);
  useEffect(() => {
    if (online && !wasOnline.current) void refreshEmergencyStop();
    wasOnline.current = online;
  }, [online]);

  // Read again when a settings change names Emergency stop.
  const lastSeen = useRef<string | null>(null);
  useEffect(() => {
    if (events.length === 0) return;
    let named = false;
    for (const ev of events) {
      if (ev.id && ev.id === lastSeen.current) break;
      if (namesEmergencyStop(ev.payload)) named = true;
    }
    lastSeen.current = events[0].id || lastSeen.current || "";
    if (named) void refreshEmergencyStop();
  }, [events]);

  const onAutonomyPage = (pathname ?? "").startsWith("/autonomy");
  const visible = stop.known && stop.on && !onAutonomyPage;

  // Keep --ij-strip-h = demo strip + this strip while this strip shows.
  const boxRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!visible) return;
    const el = boxRef.current;
    if (!el) return;
    const mine = () => Math.max(0, Math.round(el.getBoundingClientRect().height));
    const keep = () => writeStripVar(demoHeight() + mine());
    keep();
    let resize: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      resize = new ResizeObserver(keep);
      resize.observe(el);
    }
    let mutation: MutationObserver | null = null;
    if (typeof MutationObserver !== "undefined") {
      mutation = new MutationObserver(keep);
      mutation.observe(document.documentElement, { attributes: true, attributeFilter: ["style"] });
    }
    return () => {
      resize?.disconnect();
      mutation?.disconnect();
      // Back to the demo strip's own height (0 when it is not shown).
      writeStripVar(demoHeight());
    };
  }, [visible]);

  if (!visible) return null;

  async function turnOff() {
    setBusy(true);
    setError(null);
    const err = await turnOffEmergencyStop();
    setBusy(false);
    if (err) setError(err);
  }

  return (
    <div
      ref={boxRef}
      role="status"
      data-testid="emergency-stop-strip"
      className="border-b border-rose-500/30 bg-rose-500/[0.08] backdrop-blur-sm"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2 sm:px-6 lg:px-10">
        <ShieldAlert size={15} className="shrink-0 text-rose-300" aria-hidden="true" />
        <p className="min-w-0 flex-1 text-[13px] leading-snug text-rose-100/90">
          <span className="font-semibold text-rose-200">{EMERGENCY_STOP_ON_WORDS}</span>
          <span className="text-rose-100/60">
            {" "}
            — schedules, goals, rules and phone tasks are skipped. Chat and work you start still run.
          </span>
        </p>
        <button
          type="button"
          onClick={() => void turnOff()}
          disabled={busy}
          data-testid="emergency-stop-turn-off"
          title="Turn Emergency stop off: schedules, goals and rules start on their own again"
          className="shrink-0 rounded-lg border border-rose-500/30 px-2.5 py-1 text-xs font-medium text-rose-200 transition-colors hover:bg-rose-500/[0.16] disabled:opacity-50"
        >
          {busy ? "Turning off…" : EMERGENCY_STOP_TURN_OFF}
        </button>
        {error ? (
          <span role="alert" data-testid="emergency-stop-error" className="basis-full text-xs text-rose-100/90">
            {error}
          </span>
        ) : null}
      </div>
    </div>
  );
}
