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
 * was added, so `light-amber-v1233.test.tsx` stays as strict as it was — and
 * v1.239.0 added none either: the Test readout is the existing notes, the folder
 * block is zinc, and its "could not find the add-on" sentence uses
 * `text-amber-100/80`, which is already in that generated block (the same utility
 * the Last-problem row below has always used).
 *
 * v1.239.0 (Ship 5), and both halves answer the same question — is what this
 * card SAYS true:
 *
 *  - THE FOLDER IS NAMED, ABSOLUTELY, AND IT IS COPYABLE. Ship 1 could only name a
 *    path inside a source checkout, so a packaged user was told to build the
 *    add-on with a pnpm they do not have, in a checkout they do not have. The
 *    installer now carries the built add-on, and `GET /browser/status` carries
 *    `addon_dir` — the folder the DAEMON resolved on this machine — so the card
 *    prints and copies a real path rather than a folder NAME Chrome's file picker
 *    cannot resolve. Where the app was installed is a fact only the installed app
 *    knows, which is why the app is asked instead of guessed. When it answers
 *    nothing, the card says so and gives the doctor's own remedy; it does not
 *    print a name and send the user to a page that never prints the path.
 *  - THE TEST READOUT REPORTS THE FAILURE AS FULLY AS THE SUCCESS — the daemon's
 *    sentence, its BROWSER_* code, and the elapsed figure, which is displayed and
 *    never asserted anywhere: it measures the machine. An instant refusal and a
 *    fifteen-second timeout are different faults wearing the same sentence, and so
 *    are two different codes.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  AppWindow,
  ClipboardCopy,
  Link2,
  Link2Off,
  ShieldCheck,
  Unplug,
  ChevronDown,
  ChevronRight,
  Activity,
  Wand2,
  PanelRight,
} from "lucide-react";
import { post, put, ApiError } from "@/lib/api";
import { useApi, usePolledApi } from "@/lib/useApi";
import type {
  BrowserPendingPairing,
  BrowserStatus,
  BrowserTab,
  BrowserTestResult,
  Health,
} from "@/lib/types";
import {
  Card,
  ConfirmButton,
  ErrorNote,
  LoaderInline,
  SectionLabel,
  SuccessNote,
} from "@/components/ui";
// ONE clipboard path for the whole app (v1.229.0): it prefers the Electron
// bridge, falls back to navigator.clipboard, and — the part that matters here —
// REPORTS "unavailable" instead of pretending. A second copy helper in this file
// would be a second thing to fix the day the bridge changes.
import { copyPlain } from "@/components/settings/MaintenanceTools";
// The guided setup window (v1.240.0). It is a separate file rather than a
// block in this one because it is a dialog with its own lifecycle — it arms a
// time-boxed window on open and gives it back on close — and this card is
// already six states long.
import { BrowserSetupModal } from "@/components/browser/BrowserSetupModal";

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
    hint: "Jarvis can see your open tabs, read the text of the page you are looking at, and take a screenshot of it. Looking only — changing a page needs Interactive.",
  },
  {
    value: "interactive",
    label: "Interactive",
    hint: "Jarvis can also click, type and navigate. Anything that changes a page is gated, and a target that looks destructive or transactional — or that cannot be identified at all — stops and asks you first.",
  },
];

/** The folder the user points Chrome's Load unpacked at in a SOURCE CHECKOUT. */
const ADDON_FOLDER = "extensions/chrome";

/** The same folder in a PACKAGED install, named only in PROSE (v1.239.0, D27).
 *
 *  The path the user copies is NOT this string. It is `status.addon_dir`, the
 *  absolute folder the daemon resolved with `onboarding.doctor.browser_addon_dir()`
 *  — the one resolver, reading the `IRONJARVIS_BROWSER_ADDON_DIR` the desktop
 *  supervisor exports. A folder NAME is not something Chrome's Load unpacked
 *  picker can resolve, and the earlier card handed the user one and then pointed at
 *  the Overview's setup checks for the real path — a block that renders nothing at
 *  all while the doctor is healthy, and `browser_addon` is RECOMMENDED, so a
 *  missing add-on does not make the doctor unhealthy either. That sentence was
 *  false in exactly the case it existed for.
 *
 *  These two constants survive only to SAY WHERE the folder normally lives when the
 *  daemon could not find one, and that sentence names them as the usual location,
 *  never as something to copy. What pins the shipped name is
 *  `tests/test_browser_packaging_v1239.py`, which compares `desktop/package.json`'s
 *  `extraResources` target against `desktop/afterPack.js` and `desktop/main.js`;
 *  neither this constant nor `doctor.BROWSER_ADDON_RESOURCE_DIR` is pinned against
 *  those, so both are prose a rename can leave stale — survivable here precisely
 *  because nothing is built out of them. */
const PACKAGED_ADDON_FOLDER = "browser-addon";

/** Its parent inside the installation, so the fallback sentence reads as one path. */
const PACKAGED_ADDON_PARENT = "resources";

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
/*  Where the add-on is                                                        */
/* -------------------------------------------------------------------------- */

/** One copyable folder name, with the sentence that says where it lives.
 *
 *  Copyable because the next thing the user does with it is paste it into
 *  Chrome's file picker, and a folder name they have to retype from a screenshot
 *  of a card is a folder name they mistype.
 *
 *  The copy REPORTS what happened, including failure. `copyPlain` prefers the
 *  Electron bridge and falls back to `navigator.clipboard`, which is
 *  permission-gated in Electron and simply absent over plain HTTP on a phone —
 *  and a Copy button that silently did nothing is the DraftCard lie (v1.161.0):
 *  claiming a copy that did not happen. When nothing takes it, the name is still
 *  on screen to read. */
function FolderLine({
  testid,
  folder,
  where,
  note,
}: {
  testid: string;
  folder: string;
  where: string;
  note?: string;
}) {
  const [said, setSaid] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  async function copy() {
    const outcome = await copyPlain(folder);
    setSaid(outcome === "copied" ? "Copied" : "Nothing here can copy — read it above");
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setSaid(null), 2500);
  }

  return (
    <div data-testid={testid} className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <code
          data-testid={`${testid}-value`}
          className="rounded-md bg-white/[0.04] px-2 py-1 font-mono text-[12px] text-zinc-200"
        >
          {folder}
        </code>
        <button
          type="button"
          data-testid={`${testid}-copy`}
          onClick={copy}
          title={`Copy ${folder}`}
          aria-label={`Copy ${folder}`}
          className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:border-accent/30 hover:text-zinc-200"
        >
          <ClipboardCopy size={12} /> Copy
        </button>
        {said && (
          <span data-testid={`${testid}-copied`} className="text-[11px] text-zinc-500">
            {said}
          </span>
        )}
      </div>
      <p className="text-[11px] leading-relaxed text-zinc-500">
        {where}
        {note ? ` ${note}` : ""}
      </p>
    </div>
  );
}

/** The folder Chrome's Load unpacked is pointed at (D27), as an ABSOLUTE path.
 *
 *  Ship 1 could not write this block: the add-on existed only in a source
 *  checkout, so the card said "build it first" to a packaged user who had no
 *  checkout to build in. v1.239.0 bundles the built add-on with the installer, so
 *  the installed folder is named FIRST and the checkout is the footnote — that is
 *  the order the readership is in.
 *
 *  AND IT IS THE REAL PATH. `GET /browser/status` carries `addon_dir`, the folder
 *  the DAEMON resolved on this machine (`doctor.browser_addon_dir()`), so nothing
 *  here is guessed: the installed app is the one thing that knows where it was
 *  installed, and it is now the thing that says. What the user copies is what they
 *  paste into Chrome's file picker.
 *
 *  WHEN IT IS EMPTY, SAY THAT. A daemon that could not find the add-on gets the
 *  honest block: no path, and no folder name dressed up as one. The remedy is the
 *  doctor's own — reinstall, or use the checkout below — and not a page that does
 *  not print the path, which is the sentence this block was rewritten to remove. */
function AddonFolder({ addonDir }: { addonDir: string }) {
  const resolved = addonDir.trim();
  return (
    <div
      data-testid="browser-addon-folder"
      className="rounded-xl border border-white/[0.08] bg-white/[0.02] px-3 py-2.5"
    >
      <SectionLabel>The add-on folder</SectionLabel>
      <div className="mt-2 space-y-3">
        {resolved ? (
          <FolderLine
            testid="browser-addon-packaged"
            folder={resolved}
            where="This is the folder on this machine, already built, from your Iron Jarvis install. Copy it and paste it into Chrome's Load unpacked box."
          />
        ) : (
          <p
            data-testid="browser-addon-unresolved"
            className="text-[11px] leading-relaxed text-amber-100/80"
          >
            Iron Jarvis could not find the add-on folder on this machine, so there is no path to
            copy. It normally ships inside the app, in the {PACKAGED_ADDON_FOLDER} folder under{" "}
            {PACKAGED_ADDON_PARENT} — reinstalling the current release puts it back. From a source
            checkout, use the folder below instead.
          </p>
        )}
        <FolderLine
          testid="browser-addon-checkout"
          folder={ADDON_FOLDER}
          where="From a source checkout instead, it is this folder — build it once first (below)."
        />
      </div>
    </div>
  );
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
 *  TWO READERS, AND ONLY ONE OF THEM HAS A CHECKOUT (v1.239.0). The add-on now
 *  ships inside the installer, so nearly every reader of these steps has the folder
 *  already and no pnpm at all. The build step therefore stops being step 3 for
 *  everybody: the packaged reader is served first, by the folder block above these
 *  steps, and the build step is scoped explicitly to "from a source checkout". A
 *  packaged user told to run pnpm is at a dead end, and that is the failure this
 *  ordering exists to prevent. */
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
            Choose Load unpacked and select the folder named above — the one holding{" "}
            <code className="font-mono text-zinc-300">manifest.json</code>, not{" "}
            <code className="font-mono text-zinc-300">dist</code>.
          </li>
          <li>Come back here. This card will say Waiting to pair — press Pair.</li>
          <li className="list-none pl-0 pt-1 text-zinc-500">
            Working from a source checkout instead? Build the add-on once first — run{" "}
            <code className="font-mono text-zinc-300">pnpm install &amp;&amp; pnpm run check</code>{" "}
            inside <code className="font-mono text-zinc-300">{ADDON_FOLDER}</code>, or Chrome
            refuses the folder. The copy that comes with the app is already built.
          </li>
        </ol>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  The sidebar, said out loud                                                 */
/* -------------------------------------------------------------------------- */

/** THE ONE PLACE THIS APP TELLS A USER THE SIDEBAR EXISTS (v1.242.0).
 *
 *  v1.242.0 put a chat sidebar inside the user's browser and this dashboard
 *  said nothing about it anywhere: no "sidebar", no "side panel", no "toolbar"
 *  in any page or component. The guided window now ends on it, but a window is
 *  read once — this block is for the user who set the browser up last month and
 *  never opens that dialog again, so it is on the card in EVERY state rather
 *  than only where the wizard appears.
 *
 *  AND IT LEADS WITH THE PIN, because that is the part that decides whether the
 *  feature is reachable at all: Chrome does not put a newly loaded unpacked
 *  add-on on the toolbar, it files it behind the puzzle-piece menu. A user told
 *  "click the icon" who has no icon concludes the feature is broken.
 *
 *  THE VERSION LINE IS THE STALE-BUILD REMEDY. Chrome keeps serving the copy of
 *  the add-on it loaded until somebody presses Reload, so an Iron Jarvis update
 *  can leave the previous add-on running — and the previous one still declares
 *  a popup, which means the toolbar click opens the retired popup and the
 *  sidebar cannot be reached at all. `GET /browser/status` does not carry the
 *  add-on's version, so nothing here can compare the two numbers for the user;
 *  what it CAN do is print the number this app is and say where the other one
 *  is written (the sidebar's own header), which is the comparison made
 *  reachable rather than a comparison claimed.
 *
 *  LIGHT MARKS: `border-amber-500/25`, `bg-amber-500/10`, `text-amber-100/80`
 *  and `text-amber-200` all already carry mark1 + mark8 rules in the generated
 *  light-amber-overrides block in app/globals.css. Nothing new is introduced. */
function SidebarNote({ appVersion }: { appVersion: string }) {
  return (
    <div
      data-testid="browser-sidebar-note"
      className="rounded-xl border border-white/[0.08] bg-white/[0.02] px-3 py-2.5"
    >
      <SectionLabel>The Jarvis sidebar</SectionLabel>
      <p className="mt-2 flex items-start gap-2 text-[12px] leading-relaxed text-zinc-400">
        <PanelRight size={14} className="mt-0.5 shrink-0 text-zinc-500" aria-hidden="true" />
        <span>
          Click the Iron Jarvis icon in your browser&apos;s toolbar and a chat sidebar opens down
          the right-hand side of the window — ask Jarvis about the page you are looking at without
          leaving it.
        </span>
      </p>
      <p
        data-testid="browser-sidebar-pin"
        className="mt-2 rounded-lg border border-amber-500/25 bg-amber-500/10 px-2.5 py-2 text-[11.5px] leading-relaxed text-amber-100/80"
      >
        <span className="font-semibold text-amber-200">No icon on the toolbar?</span> Chrome does
        not put a newly loaded add-on there. Click the puzzle-piece button at the top right of your
        browser, find Iron Jarvis in that list, and press the pin beside it.
      </p>
      <p
        data-testid="browser-sidebar-stale"
        className="mt-2 text-[11px] leading-relaxed text-zinc-500"
      >
        If the icon opens a small popup instead of a sidebar, your browser is still running an
        older copy of the add-on — open{" "}
        <code className="font-mono text-zinc-400">chrome://extensions</code> and press Reload on
        Iron Jarvis.
        {appVersion ? (
          <>
            {" "}
            This copy of Iron Jarvis is{" "}
            <span data-testid="browser-sidebar-app-version" className="text-zinc-400">
              {appVersion}
            </span>
            , and the sidebar prints the add-on version your browser is running in its own header —
            an older number there is the copy to reload.
          </>
        ) : null}
      </p>
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
  // The version THIS app is, for the stale-add-on line below. Fetched once, not
  // polled: it cannot change without the process restarting, and a second 5 s
  // interval on this card would be traffic bought for a string.
  const { data: health } = useApi<Health>("/health");
  const appVersion = (health?.version ?? "").trim();

  const [busy, setBusy] = useState<
    "pair" | "grant" | "test" | "disconnect" | "forget" | "access" | null
  >(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [test, setTest] = useState<BrowserTestResult | null>(null);
  const [highlightAccess, setHighlightAccess] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
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

            {/* THE GUIDED WINDOW (v1.240.0). One button, on the three states
                that still have work to do — nothing is loaded, the add-on is
                paired but down, or access is off entirely. It is the entry
                point for the whole capability, and it is deliberately ABOVE
                the per-state block: a user who presses it never has to read
                the rest of the card. The connected and waiting states are
                excluded because both are one click from finished on the card
                itself, and a wizard offered to somebody already there is
                noise. A dialog ALREADY OPEN stays open across that transition
                — it is rendered outside this condition — because pairing is
                step three of the wizard (v1.240.0, F2) and the user walking
                through it lands in `waiting` half way down. */}
            {(state === "off" || state === "not_connected" || state === "paired_down") && (
              <div className="flex flex-wrap items-center gap-2.5">
                <button
                  type="button"
                  data-testid="browser-setup-open"
                  onClick={() => setSetupOpen(true)}
                  className="btn-accent py-1.5 text-xs"
                >
                  <Wand2 size={14} /> Set up my browser
                </button>
                <span className="text-[11px] text-zinc-500">
                  Five steps, guided — Iron Jarvis does everything else itself.
                </span>
              </div>
            )}

            {setupOpen && (
              <BrowserSetupModal
                status={data ?? null}
                appVersion={appVersion}
                onClose={() => setSetupOpen(false)}
                onChanged={reload}
              />
            )}

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

            {/* Where the add-on is, then how to load it — in that order, and only
                where nothing is talking to us. A connected user has already done
                this and does not need the folder again. */}
            {(state === "not_connected" || state === "paired_down") && (
              <>
                <AddonFolder addonDir={data?.addon_dir ?? ""} />
                <InstallSteps />
              </>
            )}

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

            {/* A read-only round trip, reported in full (D25).
                THE ELAPSED FIGURE IS DISPLAYED AND NEVER ASSERTED. It measures the
                user's machine and their browser, so a threshold on it would be a
                test of the hardware; the number is here because "42 ms" and
                "9,800 ms" are two different diagnoses of the same green result.
                IT IS SHOWN ON FAILURE TOO, which is where it says the most: a
                refusal that came back instantly is a different fault from one that
                spent the whole timeout waiting for a browser that never answered,
                and the failure sentence alone cannot tell those apart. */}
            {test && (
              <div data-testid="browser-test-result" data-ok={test.ok ? "true" : "false"}>
                {test.ok ? (
                  <SuccessNote>
                    <span data-testid="browser-test-detail">{test.detail}</span>
                    {test.active_tab?.title ? ` · ${test.active_tab.title}` : ""}
                    {test.active_tab?.url ? ` · ${test.active_tab.url}` : ""}
                    {" — "}
                    <span data-testid="browser-test-elapsed">{test.round_trip_ms} ms</span>
                  </SuccessNote>
                ) : (
                  <ErrorNote>
                    <span data-testid="browser-test-detail">{test.detail}</span>
                    {/* The daemon's machine-readable code, SHOWN rather than
                        matched on: two refusals can wear the same sentence, the
                        code is what tells them apart, and it is what a user pastes
                        into a bug report. Absent on success and on a daemon that
                        does not send one. */}
                    {test.code ? (
                      <>
                        {" "}
                        <code
                          data-testid="browser-test-code"
                          className="rounded-md bg-white/[0.06] px-1.5 py-0.5 font-mono text-[11px]"
                        >
                          {test.code}
                        </code>
                      </>
                    ) : null}
                    {" — after "}
                    <span data-testid="browser-test-elapsed">{test.round_trip_ms} ms</span>
                  </ErrorNote>
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

            {/* THE SIDEBAR, NAMED. Above the access selector and in every state,
                because a user who set this up once and never opens the guided
                window again would otherwise never learn the sidebar exists —
                and because the pin instruction is what makes it reachable. */}
            <SidebarNote appVersion={appVersion} />

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
