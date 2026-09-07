"use client";

/**
 * The "Your browser" card (v1.235.0, Browser Ship 1).
 *
 * This is the only surface from which the user's own Chrome/Edge is paired,
 * inspected, tested and dropped. It is its own component — not a block inside
 * app/computeruse/page.tsx — for the same reason DaemonTokenCard was lifted out
 * of the Settings page: a connection card with six mutually exclusive states is
 * exactly the thing that rots silently, and it can only be unit-mounted per
 * state if it is a component.
 *
 * WHY EVERY STATE CARRIES A WORD. Colour alone is not a state. The four amber
 * states here mean four different things a user must do next ("press Pair",
 * "start the add-on", "grant site access", "nothing — but the last command
 * failed"), and an amber dot says none of them. So the badge always prints the
 * word, and the primary action underneath it is the single next step.
 *
 * WHY THE CARD POLLS. Pairing begins in the browser, not here: the add-on
 * connects to /browser/ws with no token, the daemon holds that socket in a
 * restricted state and reports `pending_pairing`, and this card is where the
 * user approves it. Nothing pushes that transition, so the card polls
 * GET /browser/status (a route documented never to fail — it degrades to
 * `connected: false`).
 *
 * VOCABULARY. "Extension" is this app's user-facing word for an MCP server
 * (VOCABULARY.md, v1.216.0), so no string a user reads here may call the
 * Chrome add-on one. It is "the Iron Jarvis browser add-on". The one exception
 * is the literal `chrome://extensions` URL the user must type into Chrome,
 * which is Chrome's name for its own page and not ours.
 *
 * LIGHT MARKS. Every amber utility used here already has a mark1 + mark8 rule
 * in the generated light-amber-overrides block in app/globals.css
 * (`text-amber-300`, `text-amber-200`, `text-amber-100/80`,
 * `border-amber-500/25`, `bg-amber-500/10`, `ring-amber-400/40`). Nothing new
 * was added, so `light-amber-v1233.test.tsx` stays as strict as it was.
 */

import { useRef, useState } from "react";
import Link from "next/link";
import {
  AppWindow,
  Link2,
  Link2Off,
  ShieldCheck,
  Unplug,
  ChevronDown,
  ChevronRight,
  Activity,
} from "lucide-react";
import { post, put, ApiError } from "@/lib/api";
import { usePolledApi } from "@/lib/useApi";
import type {
  BrowserPendingPairing,
  BrowserStatus,
  BrowserTab,
  BrowserTestResult,
} from "@/lib/types";
import {
  Card,
  ConfirmButton,
  ErrorNote,
  LoaderInline,
  SectionLabel,
  SuccessNote,
} from "@/components/ui";

/* -------------------------------------------------------------------------- */
/*  Contracts                                                                 */
/* -------------------------------------------------------------------------- */
/*  These four shapes live in lib/types.ts, NOT here. They started as local
   declarations under the "daemon additions not yet in lib/types" convention
   and were promoted the moment the coordinator landed them, because two
   declarations of one wire contract drift: the card would keep compiling
   against its own idea of /browser/status long after the daemon changed.  */

/* -------------------------------------------------------------------------- */
/*  Access levels                                                              */
/* -------------------------------------------------------------------------- */

interface AccessLevel {
  value: string;
  label: string;
  hint: string;
}

const ACCESS_LEVELS: AccessLevel[] = [
  { value: "off", label: "Off", hint: "Jarvis cannot see or touch your browser." },
  {
    value: "read_only",
    label: "Read only",
    hint: "Jarvis can see your open tabs and which one you are looking at. Reading a page's text arrives in v1.236.0.",
  },
  {
    value: "interactive",
    label: "Interactive",
    hint: "Nothing more than Read only in this version. Clicking, typing and navigating arrive in v1.237.0, and sensitive steps will ask first.",
  },
];

/** The folder the user points Chrome's Load unpacked at, relative to an Iron
 *  Jarvis source checkout. A packaged install ships the add-on inside the app
 *  instead; that bundling is v1.239.0, and the card names the real path then
 *  rather than inventing one now. */
const ADDON_FOLDER = "extensions/chrome";

/* -------------------------------------------------------------------------- */
/*  State machine                                                              */
/* -------------------------------------------------------------------------- */

export type BrowserCardState =
  | "off"
  | "not_connected"
  | "waiting"
  | "paired_down"
  | "no_site_access"
  | "connected";

/**
 * The six states of the card, resolved in one place so the badge, the primary
 * action and the tests cannot disagree about which one is showing.
 *
 * Order matters. `off` wins over everything because a connected socket under
 * `browser_access: off` still refuses every tool, and saying "Connected" there
 * would be the v1.218.0 lesson again — a surface implying an ability the server
 * does not grant. `waiting` outranks `paired`/`not_connected` because a pending
 * socket is the one thing here that needs the user right now; it cannot
 * coexist with a healthy connection from the same browser, since a browser that
 * already holds a pairing token never asks to pair.
 */
export function browserCardState(s: BrowserStatus | null): BrowserCardState {
  if (!s || (s.access ?? "off") === "off") return "off";
  if (s.pending_pairing) return "waiting";
  if (s.connected && !s.host_permission) return "no_site_access";
  if (s.connected) return "connected";
  if (s.paired) return "paired_down";
  return "not_connected";
}

interface BadgeMeta {
  word: string;
  tone: string;
  dot: string;
}

const BADGES: Record<BrowserCardState, BadgeMeta> = {
  off: {
    word: "Off",
    tone: "border-zinc-500/25 bg-zinc-500/10 text-zinc-300",
    dot: "bg-zinc-500",
  },
  not_connected: {
    word: "Not connected",
    tone: "border-zinc-500/25 bg-zinc-500/10 text-zinc-300",
    dot: "bg-zinc-500",
  },
  waiting: {
    word: "Waiting to pair",
    tone: "border-amber-500/25 bg-amber-500/10 text-amber-300",
    dot: "bg-amber-400",
  },
  paired_down: {
    word: "Paired — not running",
    tone: "border-amber-500/25 bg-amber-500/10 text-amber-300",
    dot: "bg-amber-400",
  },
  no_site_access: {
    word: "Connected — no site access",
    tone: "border-amber-500/25 bg-amber-500/10 text-amber-300",
    dot: "bg-amber-400",
  },
  connected: {
    word: "Connected",
    tone: "border-emerald-500/25 bg-emerald-500/10 text-emerald-300",
    dot: "bg-emerald-400 shadow-[0_0_8px_2px_rgba(52,211,153,0.5)]",
  },
};

function StatePill({ state }: { state: BrowserCardState }) {
  const meta = BADGES[state];
  return (
    <span
      data-testid="browser-badge"
      data-state={state}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${meta.tone}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
      {meta.word}
    </span>
  );
}

function errText(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

/* -------------------------------------------------------------------------- */
/*  Install instructions                                                       */
/* -------------------------------------------------------------------------- */

/** Expandable, because a paired user never needs to read it again — and the
 *  same words live in extensions/chrome/README.md, deliberately mirrored so a
 *  user who found the folder first is told the same thing.
 *
 *  The BUILD step is step 3 and is not optional: manifest.json points at
 *  dist/background.js and extensions/chrome/.gitignore ignores dist/, so a fresh
 *  checkout has nothing for Load unpacked to load and Chrome refuses it with
 *  "Could not load background script" — a message that names no remedy. The
 *  README has always carried this step; the card dropped it, which made the other
 *  four steps fail together.
 *
 *  The folder is named as what it is — a path inside a source checkout. A packaged
 *  install carries the add-on inside the app; bundling it is v1.239.0's work, and
 *  the exact path is named here when that lands rather than guessed now. */
function InstallSteps() {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.02]">
      <button
        type="button"
        data-testid="browser-install-toggle"
        aria-expanded={open}
        onClick={() => setOpen((x) => !x)}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-[12px] font-medium text-zinc-300"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        How to load the Iron Jarvis browser add-on
      </button>
      {open && (
        <ol
          data-testid="browser-install-steps"
          className="list-decimal space-y-1.5 border-t hairline px-8 py-3 text-[12px] leading-relaxed text-zinc-400"
        >
          <li>
            In Chrome or Edge, open{" "}
            <code className="font-mono text-zinc-300">chrome://extensions</code>.
          </li>
          <li>Turn on Developer mode.</li>
          <li>
            Build the add-on once. In an Iron Jarvis source checkout, run{" "}
            <code className="font-mono text-zinc-300">pnpm install &amp;&amp; pnpm run check</code> inside{" "}
            <code className="font-mono text-zinc-300">{ADDON_FOLDER}</code>. Without this
            step Chrome refuses the folder.
          </li>
          <li>
            Choose Load unpacked and select the{" "}
            <code className="font-mono text-zinc-300">{ADDON_FOLDER}</code> folder in that
            checkout — the one holding{" "}
            <code className="font-mono text-zinc-300">manifest.json</code>, not{" "}
            <code className="font-mono text-zinc-300">dist</code>.
          </li>
          <li>Come back here. This card will say Waiting to pair — press Pair.</li>
          <li className="list-none pl-0 pt-1 text-zinc-500">
            Running the packaged app instead of a checkout? The installer ships the add-on
            inside the app from v1.239.0, and this card will name that folder for you then.
          </li>
        </ol>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  The card                                                                   */
/* -------------------------------------------------------------------------- */

export function YourBrowserCard() {
  // 5s, the cadence the connections page uses for a surface the user is
  // actively watching. `usePolledApi` tears the interval down while the window
  // is hidden (v1.230.0), so a minimised dashboard costs nothing here.
  const { data, error, reload } = usePolledApi<BrowserStatus>("/browser/status", 5000);

  const [busy, setBusy] = useState<
    "pair" | "grant" | "test" | "disconnect" | "forget" | "access" | null
  >(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [test, setTest] = useState<BrowserTestResult | null>(null);
  const [highlightAccess, setHighlightAccess] = useState(false);
  const readOnlyRef = useRef<HTMLButtonElement | null>(null);

  const state = browserCardState(data);
  const access = data?.access ?? "off";

  // A daemon that predates this ship answers 404. Say so plainly rather than
  // rendering an "Off" card that no switch on it can change. A status-0 error
  // is the offline case, which the page's own OfflineHint already owns.
  const unavailable = !data && error !== null && error.status !== 0;

  async function run(
    which: "pair" | "grant" | "test" | "disconnect" | "forget",
    fn: () => Promise<void>,
  ) {
    if (busy) return; // one in flight at a time: Pair must post exactly once
    setBusy(which);
    setActionError(null);
    setNote(null);
    try {
      await fn();
    } catch (e) {
      setActionError(errText(e));
    } finally {
      setBusy(null);
    }
  }

  async function pair() {
    const requestId = data?.pending_pairing?.request_id ?? "";
    if (!requestId) return;
    await run("pair", async () => {
      await post("/browser/pair", { request_id: requestId });
      setNote("Paired. Your browser is now connected to Iron Jarvis.");
      reload();
    });
  }

  async function grant() {
    await run("grant", async () => {
      await post("/browser/request-host-permission");
      setNote(
        "Asked your browser for site access — accept the prompt it opens, then this card turns green.",
      );
      reload();
    });
  }

  async function runTest() {
    await run("test", async () => {
      const result = await post<BrowserTestResult>("/browser/test");
      setTest(result);
    });
  }

  async function disconnect() {
    await run("disconnect", async () => {
      await post("/browser/disconnect");
      setTest(null);
      setNote("Disconnected. Your browser stays paired and reconnects on its own.");
      reload();
    });
  }

  async function forget() {
    await run("forget", async () => {
      await post("/browser/forget");
      setTest(null);
      setNote("Forgotten. The next time your browser connects it asks to pair again.");
      reload();
    });
  }

  async function setAccess(value: string) {
    if (busy) return;
    setBusy("access");
    setActionError(null);
    setNote(null);
    setHighlightAccess(false);
    try {
      await put("/settings", { values: { browser_access: value } });
      reload();
    } catch (e) {
      setActionError(errText(e));
    } finally {
      setBusy(null);
    }
  }

  const activeTab = data?.active_tab ?? null;

  return (
    <Card
      title="Your browser"
      icon={<AppWindow size={15} />}
      right={<StatePill state={state} />}
      className="border-accent/20"
    >
      <div className="space-y-3.5" data-testid="your-browser-card">
        {unavailable ? (
          <ErrorNote>
            This daemon does not have the Browser surface yet — restart Iron Jarvis and reopen this
            page.
          </ErrorNote>
        ) : (
          <>
            {/* What this is, in one line. */}
            <p className="text-[12px] leading-relaxed text-zinc-400">
              Jarvis works inside the browser you already use — your tabs, your logins, your
              session. Load the Iron Jarvis browser add-on, pair it here, and Jarvis can read the
              page you are looking at.
            </p>

            {/* Primary action per state. Exactly one next step. */}
            {state === "off" && (
              <div className="flex flex-wrap items-center gap-2.5">
                <button
                  type="button"
                  data-testid="browser-turn-on"
                  onClick={() => {
                    setHighlightAccess(true);
                    readOnlyRef.current?.focus();
                  }}
                  className="btn-accent py-1.5 text-xs"
                >
                  <ShieldCheck size={14} /> Turn on
                </button>
                <span className="text-[11px] text-zinc-500">
                  Pick Read only or Interactive below — Jarvis never chooses this for you.
                </span>
              </div>
            )}

            {state === "waiting" && (
              <div className="space-y-2">
                <div
                  data-testid="browser-waiting-note"
                  className="rounded-xl border border-amber-500/25 bg-amber-500/10 px-3 py-2.5 text-[12px] leading-relaxed text-amber-100/80"
                >
                  <span className="font-semibold text-amber-200">A browser is asking to pair.</span>{" "}
                  Pair it only if you just loaded the Iron Jarvis browser add-on yourself.
                  {/* WHO is asking. Pairing needs no credential by design, so this row
                      is reachable by anything on this machine — and the one fact that
                      distinguishes the real add-on from an impostor is the identity
                      Chrome assigns it. Saying nothing here would ask the user to
                      approve a credential for an anonymous caller. */}
                  <div
                    data-testid="browser-waiting-identity"
                    className="mt-1.5 font-mono text-[11px] text-amber-100/70"
                  >
                    {data?.pending_pairing?.extension_id
                      ? data.pending_pairing.extension_id === data.expected_extension_id
                        ? `Identified as the Iron Jarvis browser add-on (${data.pending_pairing.extension_id})`
                        : `Unrecognised caller: ${data.pending_pairing.extension_id} — this is not the Iron Jarvis browser add-on`
                      : "This caller did not identify itself. Do not pair it unless you just loaded the add-on."}
                  </div>
                </div>
                <button
                  type="button"
                  data-testid="browser-pair"
                  onClick={pair}
                  disabled={busy !== null}
                  className="btn-accent py-1.5 text-xs disabled:opacity-50"
                >
                  {busy === "pair" ? (
                    <LoaderInline label="Pairing…" />
                  ) : (
                    <>
                      <Link2 size={14} /> Pair
                    </>
                  )}
                </button>
              </div>
            )}

            {state === "no_site_access" && (
              <div className="space-y-2">
                <div
                  data-testid="browser-no-site-access-note"
                  className="rounded-xl border border-amber-500/25 bg-amber-500/10 px-3 py-2.5 text-[12px] leading-relaxed text-amber-100/80"
                >
                  <span className="font-semibold text-amber-200">Paired, but blind.</span> Jarvis can
                  see your tab list and nothing on the pages. Granting site access opens one prompt
                  in your browser.
                </div>
                <button
                  type="button"
                  data-testid="browser-grant"
                  onClick={grant}
                  disabled={busy !== null}
                  className="btn-accent py-1.5 text-xs disabled:opacity-50"
                >
                  {busy === "grant" ? (
                    <LoaderInline label="Asking…" />
                  ) : (
                    <>
                      <ShieldCheck size={14} /> Grant site access
                    </>
                  )}
                </button>
              </div>
            )}

            {state === "paired_down" && (
              <div
                data-testid="browser-paired-down-note"
                className="rounded-xl border border-amber-500/25 bg-amber-500/10 px-3 py-2.5 text-[12px] leading-relaxed text-amber-100/80"
              >
                <span className="font-semibold text-amber-200">This browser is paired</span> but
                nothing is connected right now. Open the browser you paired, or load the Iron Jarvis
                browser add-on again.
              </div>
            )}

            {/* Install instructions, wherever the add-on is not talking to us. */}
            {(state === "not_connected" || state === "paired_down") && <InstallSteps />}

            {/* The live tab — the proof that this actually works. */}
            {state === "connected" && (
              <div
                data-testid="browser-active-tab"
                className="rounded-xl border border-white/[0.08] bg-white/[0.02] px-3 py-2.5"
              >
                <SectionLabel>Active tab</SectionLabel>
                <div className="mt-1 truncate text-sm text-zinc-200">
                  {activeTab?.title || "Untitled tab"}
                </div>
                <div className="truncate font-mono text-[11px] text-zinc-500">
                  {activeTab?.url || "no address"}
                </div>
              </div>
            )}

            {/* Test / Disconnect / Forget: only meaningful on a live socket. */}
            {state === "connected" && (
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  data-testid="browser-test"
                  onClick={runTest}
                  disabled={busy !== null}
                  className="btn-accent py-1.5 text-xs disabled:opacity-50"
                >
                  {busy === "test" ? (
                    <LoaderInline label="Testing…" />
                  ) : (
                    <>
                      <Activity size={14} /> Test
                    </>
                  )}
                </button>
                <button
                  type="button"
                  data-testid="browser-disconnect"
                  onClick={disconnect}
                  disabled={busy !== null}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-xs font-medium text-zinc-400 transition-colors hover:border-accent/30 hover:text-zinc-200 disabled:opacity-50"
                >
                  <Unplug size={14} /> Disconnect
                </button>
                <ConfirmButton
                  onConfirm={forget}
                  label="Forget"
                  title="Delete the pairing, so this browser must be paired again"
                />
                <span className="text-[11px] text-zinc-500">
                  Disconnect drops the link. Forget also deletes the pairing.
                </span>
              </div>
            )}

            {/* A read-only round trip, reported in full (D25). The elapsed
                figure is displayed and never asserted — it measures hardware. */}
            {test && (
              <div data-testid="browser-test-result">
                {test.ok ? (
                  <SuccessNote>
                    {test.detail} — {test.round_trip_ms} ms
                    {test.active_tab?.title ? ` · ${test.active_tab.title}` : ""}
                  </SuccessNote>
                ) : (
                  <ErrorNote>{test.detail}</ErrorNote>
                )}
              </div>
            )}

            {/* The daemon's own last failure, surfaced instead of logged (the
                v1.229.0 lesson: a failure the user cannot see is a failure they
                blame on the app). */}
            {data?.last_error && (
              <div
                data-testid="browser-last-error"
                className="rounded-xl border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-100/80"
              >
                <span className="font-semibold text-amber-200">Last problem:</span> {data.last_error}
              </div>
            )}

            {note && <SuccessNote>{note}</SuccessNote>}
            {actionError && <ErrorNote>{actionError}</ErrorNote>}

            {/* The access selector. Always present, because it is the switch the
                whole feature hangs on and hiding it behind a state would make
                "Turn on" a dead end. */}
            <div
              data-testid="browser-access"
              className={`rounded-xl border border-white/[0.08] bg-white/[0.02] p-3 ${
                highlightAccess ? "ring-1 ring-amber-400/40" : ""
              }`}
            >
              <SectionLabel>Browser access</SectionLabel>
              <div className="mt-2 flex flex-wrap gap-2">
                {ACCESS_LEVELS.map((lvl) => {
                  const on = access === lvl.value;
                  return (
                    <button
                      key={lvl.value}
                      ref={lvl.value === "read_only" ? readOnlyRef : undefined}
                      type="button"
                      data-testid={`browser-access-${lvl.value}`}
                      aria-pressed={on}
                      disabled={busy !== null}
                      onClick={() => setAccess(lvl.value)}
                      className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-50 ${
                        on
                          ? "border-accent/40 bg-accent/10 text-zinc-100"
                          : "border-white/10 text-zinc-400 hover:border-accent/30 hover:text-zinc-200"
                      }`}
                    >
                      {lvl.label}
                    </button>
                  );
                })}
              </div>
              <p className="mt-2 text-[11px] leading-relaxed text-zinc-500">
                {ACCESS_LEVELS.find((l) => l.value === access)?.hint ?? ACCESS_LEVELS[0].hint} Every
                level still refuses anything you have not allowed —{" "}
                <Link href="/settings" className="text-accent-soft hover:text-accent">
                  permissions
                </Link>{" "}
                decide the rest.
              </p>
            </div>

            {state !== "connected" && state !== "off" && (
              <p className="flex items-center gap-1.5 text-[11px] text-zinc-600">
                <Link2Off size={12} /> Nothing reads a page until this card says Connected.
              </p>
            )}
          </>
        )}
      </div>
    </Card>
  );
}
