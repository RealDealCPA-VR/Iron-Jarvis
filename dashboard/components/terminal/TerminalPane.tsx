"use client";

// A single live terminal pane: an xterm.js terminal attached over a WebSocket
// to one daemon shell session. xterm itself is imported dynamically inside the
// effect so it never runs during SSR / `next build`.

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import "@xterm/xterm/css/xterm.css";
import {
  Check,
  ClipboardCopy,
  CornerDownLeft,
  ExternalLink,
  Image as ImageIcon,
  Layers,
  Loader2,
  Paperclip,
  Play,
  Eraser,
  Plug,
  PlugZap,
  Rocket,
  Sparkles,
  Terminal as TerminalIcon,
  Workflow,
  X,
} from "lucide-react";
import { ApiError, get, patch, post } from "@/lib/api";
import { waitForStableSize } from "@/lib/layout";
import {
  CLI_IMAGE_BUDGET_BYTES,
  fileToBase64,
  imageFilesFromDrop,
  imageItemsFromPaste,
  shrinkToFit,
} from "@/lib/snippet";
import { VoiceInput, appendDictation } from "@/components/VoiceInput";
import { outputNotifyAt } from "@/components/terminal/paneStatusCore";
import { acquirePaneHost, type ConnState, type PaneHost } from "@/components/terminal/paneHost";
import { resizeAllowed } from "@/components/terminal/resizeGate";
import { useDaemon } from "@/lib/daemon";
import type { AiCli, ModelOption, Skill, TerminalInfo } from "@/lib/types";
import { PaneStateChip, type PaneState } from "@/components/terminal/PaneState";

type AIResult = {
  reply: string;
  command: string;
  provider: string;
  model: string;
  /** Skill playbooks injected into this answer (names). */
  skills?: string[];
};

// --- Launch recipes (v1.238.0, D18) ---------------------------------------
// Before this ship a launch was one string typed into a live shell, and the
// menu had nothing to say about what the CLI would be able to DO once it
// started. D18 makes that a recipe: detect the version, verify a method,
// configure it, hand it a pane token — and, when any of that cannot be done,
// "surface incompatibility clearly". A harness that cannot be isolated has to
// say so WHERE THE USER IS STANDING, which is this menu, not a log.
//
// The fields are declared here rather than imported: `lib/types` is another
// lane's file, and every field is optional, so an older daemon (or a type that
// has not caught up) renders the honest "no recipe" case instead of failing to
// compile.

/** `detect_ai_clis()`'s per-CLI recipe row. */
export interface CliRecipe {
  /** "mcp_http" | "mcp_stdio" | "none" — whatever the recipe verified. */
  method?: string;
  ok?: boolean;
  /** User-facing sentences. Never empty when `ok` is false. */
  limitations?: string[];
}

/** An AI CLI as the Launch menu sees it once recipes exist. */
export type LaunchCli = AiCli & { version?: string; recipe?: CliRecipe | null };

/** How a verified method reads to a person. Unknown methods print verbatim
 *  rather than being swallowed — a recipe naming a method this build has never
 *  heard of is information, not a reason to say nothing. */
export function recipeMethodWord(method: string): string {
  if (method === "mcp_http") return "over HTTP";
  if (method === "mcp_stdio") return "over stdio";
  return method;
}

/** The one sentence the CLI I am about to launch has coming to it. */
export function recipeNote(cli: LaunchCli): {
  headline: string;
  ready: boolean;
  limitations: string[];
} {
  const recipe = cli.recipe;
  // No recipe at all: an older daemon, or a catalog entry nobody wrote one for.
  // That CLI is not broken by this work — it simply launches the way it always
  // has, with no Jarvis capabilities, and saying so is cheaper than a mystery.
  if (!recipe) {
    return { headline: "Launches as-is — no Jarvis capabilities", ready: false, limitations: [] };
  }
  const limitations = (recipe.limitations ?? []).filter((l) => typeof l === "string" && l.trim());
  if (recipe.ok === false) {
    return {
      headline: "No Jarvis capabilities on this version",
      ready: false,
      // ok:false with nothing to show would render an empty warning, which
      // reads as "fine". Name the gap instead of implying there is none.
      limitations: limitations.length
        ? limitations
        : ["This version could not be prepared for Jarvis, and it did not say why."],
    };
  }
  const method = String(recipe.method ?? "").trim();
  if (!method || method === "none") {
    return { headline: "Launches as-is — no Jarvis capabilities", ready: false, limitations };
  }
  return {
    headline: `Jarvis capabilities ${recipeMethodWord(method)}`,
    ready: true,
    limitations,
  };
}

/**
 * The recipe state, rendered INSIDE the Launch row, before the click.
 *
 * Lifted out of the dropdown so it can be mounted on its own: jsdom cannot
 * render an xterm pane, so the house idiom is to unit-test the seam and
 * source-pin the call site (v1.163.0, v1.190.0, v1.194.0).
 */
export function LaunchRecipeNote({ cli }: { cli: LaunchCli }) {
  const note = recipeNote(cli);
  const tone = note.ready
    ? "text-emerald-300/80"
    : note.limitations.length
      ? "text-amber-300"
      : "text-zinc-500";
  return (
    <span data-testid={`launch-recipe-${cli.id}`} className="block">
      <span className={`block text-[10px] leading-relaxed ${tone}`}>{note.headline}</span>
      {note.limitations.map((line) => (
        <span key={line} className="block text-[10px] leading-relaxed text-amber-200">
          {line}
        </span>
      ))}
    </span>
  );
}

/** How long a pane's size must hold still before it is fitted and sent to
 *  the shell (v1.245.0): a dragged edge fires the ResizeObserver dozens of
 *  times a second, and every resize made a running TUI repaint. */
export const RESIZE_SETTLE_MS = 100;

/**
 * THE WAY BACK INTO THE CONVERSATION A RESTART ENDED (v1.245.0).
 *
 * A pane's shell is a child of the daemon, so an app update or a crash ends
 * the Claude/Codex running in it; the pane comes back as a fresh shell in the
 * same folder, and the daemon remembers which CLI was there (`resume_cli`).
 * One click types that CLI's own continue command (`claude --continue`,
 * `codex resume --last`) and presses Enter — the click IS the consent, the
 * command is shown on the button's tooltip, and Dismiss clears the offer.
 * A CLI with no known continue command gets no strip at all, rather than a
 * guess. Exported on its own so it can be tested without xterm.
 */
export function ResumeStrip({
  cliLabel,
  command,
  onResume,
  onDismiss,
}: {
  cliLabel: string;
  command: string;
  onResume: () => void;
  onDismiss: () => void;
}) {
  return (
    <div
      data-testid="resume-strip"
      className="flex shrink-0 items-center gap-2 border-b border-accent/20 bg-accent/[0.06] px-3 py-1 text-[11px] text-accent-soft"
    >
      <Play size={12} className="shrink-0" />
      <span className="min-w-0 flex-1 truncate">
        {cliLabel} was running here before Iron Jarvis restarted.
      </span>
      <button
        type="button"
        onClick={onResume}
        title={`Types “${command}” and presses Enter`}
        className="shrink-0 rounded-md border border-accent/40 px-2 py-0.5 font-medium text-accent-soft transition-colors hover:bg-accent/15"
      >
        Resume
      </button>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss the resume offer"
        className="shrink-0 text-zinc-500 transition-colors hover:text-zinc-300"
      >
        <X size={12} />
      </button>
    </div>
  );
}

// --- Screen snippets (v1.194.0) -------------------------------------------
// A ConPTY pane is a BYTE STREAM: there is no image channel to paste into. But
// every AI CLI we launch reads images OFF DISK from a path in the prompt, and
// the daemon runs on this same machine as the CLI child — so a path is genuinely
// shared. Capture → shrink to fit (lib/snippet) → POST the bytes → type the
// returned REFERENCE into the shell. No synthesized paste keystroke: Ctrl+V
// after Win+Shift+S is documented to do nothing in Claude Code on Windows.

/** One image waiting on the pane. It is ALWAYS visible as a chip — including
 *  when it failed, so a refusal can never read as "attached". */
type PendingSnip = {
  id: string;
  name: string;
  /** Byte size of what would actually be sent (post-shrink when ready). */
  bytes: number;
  /** Object URL for the thumbnail; "" until the shrink pipeline finishes. */
  url: string;
  /** True when the shrink pipeline re-encoded the image to fit the budget —
   *  surfaced on the chip, because quietly shrinking a screenshot is a lie. */
  recompressed: boolean;
  status: "preparing" | "ready" | "sending" | "failed";
  error?: string;
  /** The exact bytes that will be POSTed (post-shrink), once ready. */
  file?: File;
};

/** Compact human size for a chip. */
export function formatSnipBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 KB";
  if (n < 1024) return `${Math.round(n)} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** What the desktop clipboard bridge is telling us about the clipboard's image
 *  flavour. `unreadable` is a REPORTABLE outcome, not an absence — see
 *  `clipImageOutcome`. */
export type ClipImage =
  | { kind: "image"; file: File }
  | { kind: "unreadable" }
  | { kind: "none" };

/**
 * THE Ctrl+V DECISION, as a testable seam.
 *
 * TEXT WINS WHENEVER THE CLIPBOARD CARRIES TEXT. This ordering is the whole
 * point and it is NOT the obvious one: copying a range in Excel (or Word, or an
 * Outlook body) puts a BITMAP on the Windows clipboard ALONGSIDE the text —
 * that is exactly why Paste Special offers "Bitmap"/"Picture". Probing the
 * image side first would turn every "copy some cells, paste into the terminal"
 * into an image chip and the text would never arrive, which is a regression in
 * the thing people do all day. It would also force a synchronous multi-megapixel
 * `toPNG()` on the Electron MAIN process before any text could paste.
 *
 * A Win+Shift+S snip has NO text flavour, so the feature loses nothing: an
 * empty text read falls straight through to the image probe.
 *
 * A failure ANYWHERE (either probe throwing) must still leave the other path
 * working — a broken bridge that swallowed ordinary pasting would be far worse
 * than the missing feature.
 */
export async function resolvePaste(
  readText: () => Promise<string>,
  readImage: () => Promise<ClipImage>,
  onText: (text: string) => void,
  onImage: (file: File) => void,
  onUnreadable: () => void,
): Promise<"image" | "text" | "unreadable" | "nothing"> {
  let text = "";
  try {
    text = await readText();
  } catch {
    text = ""; // clipboard blocked — still worth probing the image side
  }
  if (text) {
    onText(text);
    return "text";
  }
  let image: ClipImage = { kind: "none" };
  try {
    image = await readImage();
  } catch {
    image = { kind: "none" }; // no image path available — nothing to paste
  }
  if (image.kind === "image") {
    onImage(image.file);
    return "image";
  }
  if (image.kind === "unreadable") {
    // The bridge told us there WAS an image and it could not encode it. Doing
    // nothing here is the silent failure this codebase refuses: the user pressed
    // Ctrl+V on a snip and would see no chip, no message, and no keystroke.
    onUnreadable();
    return "unreadable";
  }
  return "nothing";
}

/**
 * Decide whether a NATIVE paste event belongs to the snippet feature.
 *
 * Same rule as `resolvePaste`, enforced on the browser path: Chromium exposes an
 * Excel/Word copy as `text/plain` + `text/html` + the bitmap as a FILE item, so
 * claiming every paste that carries an image file would eat that text paste.
 * Only a paste with NO text flavour is ours.
 */
export function snipFilesFromPaste(e: ClipboardEvent): File[] {
  let text = "";
  try {
    text = e.clipboardData?.getData?.("text/plain") ?? "";
  } catch {
    text = ""; // hostile/absent clipboardData — fall through to the image check
  }
  if (text) return []; // TEXT WINS — xterm pastes it exactly as before
  return imageItemsFromPaste(e);
}

/** True when a drag carries files. ONE predicate shared by dragover and drop:
 *  the pane must never advertise a drop (ring + preventDefault) that it then
 *  hands back to the browser default — in the packaged app that reaches
 *  `will-navigate` → `shell.openExternal(file://…)` and the OS OPENS the file. */
export function dragCarriesFiles(dt: DataTransfer | null | undefined): boolean {
  return Array.from(dt?.types ?? []).includes("Files");
}

/**
 * Normalize whatever the desktop clipboard bridge hands back for an image into
 * a File, or null when the clipboard holds no image.
 *
 * Deliberately shape-tolerant: this crosses the Electron preload boundary
 * (`window.ironjarvis.clipboardReadImage`), which is untyped, and a mismatch
 * here must degrade to "no image, paste text as usual" rather than throw
 * inside a keydown handler and break ordinary pasting.
 */
export function snipFromClipboardImage(value: unknown, name?: string): File | null {
  if (!value) return null;
  const raw =
    typeof value === "string"
      ? value
      : ((value as Record<string, unknown>).png_b64 ??
        (value as Record<string, unknown>).base64 ??
        (value as Record<string, unknown>).b64 ??
        (value as Record<string, unknown>).data);
  if (typeof raw !== "string" || !raw) return null;
  // Tolerate a data: URL as well as bare base64.
  const b64 = raw.includes(",") ? raw.slice(raw.indexOf(",") + 1) : raw;
  try {
    const bin = atob(b64);
    if (!bin.length) return null;
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
    return new File([bytes], name || `snip-${Date.now()}.png`, { type: "image/png" });
  } catch {
    return null; // not base64 — treat as "no image"
  }
}

/**
 * The bridge's answer, classified into the three things it can actually mean.
 *
 * `desktop/main.js` deliberately returns `{error:"unreadable"}` when the
 * clipboard HELD an image that would not encode, with a comment saying it must
 * never be reported as "nothing copied". Collapsing that to `null` (which is
 * what a bare `snipFromClipboardImage` does) throws away the only signal that
 * distinguishes "you copied nothing" from "I couldn't read what you copied".
 */
export function clipImageOutcome(value: unknown, name?: string): ClipImage {
  if (!value) return { kind: "none" };
  const file = snipFromClipboardImage(value, name);
  if (file) return { kind: "image", file };
  const err = typeof value === "object" ? (value as Record<string, unknown>).error : undefined;
  if (typeof err === "string" && err) return { kind: "unreadable" };
  return { kind: "none" };
}

// The terminal itself — its options and theme, its socket, and the filter that
// keeps xterm's answers to REPLAYED queries out of the shell — lives in
// components/terminal/paneHost.ts (v1.243.0), because it now outlives this
// component. That file's header says why.

/** Best-effort utf-8 decode of ONE PTY frame for the page's peek strip
 *  (v1.213.0). A frame boundary may split a multi-byte sequence — the
 *  replacement chars that produces are stripped by paneStatusCore.stripAnsi,
 *  never shown. Anything undecodable is honestly "" (no output claimed). */
function decodeFrame(data: unknown): string {
  if (typeof data === "string") return data;
  if (typeof ArrayBuffer !== "undefined" && data instanceof ArrayBuffer) {
    try {
      return new TextDecoder("utf-8").decode(new Uint8Array(data));
    } catch {
      return "";
    }
  }
  return "";
}

export function TerminalPane({
  info,
  focused,
  paneName,
  draggable = true,
  onRenamed,
  onLaunched,
  onLaunchWithCapabilities,
  paneState,
  agentCli,
  paneStateLine,
  onFocus,
  onClose,
  onWriterReady,
  onOutput,
  models = [],
  aiClis = [],
  skills = [],
  otherTerminals = [],
}: {
  info: TerminalInfo;
  focused: boolean;
  /** The pane's human handle, when it has one — agents address panes by
   *  it, and it reads better in the header than the shell's name. */
  paneName?: string | null;
  /** False in the rail, where the pane fills its box and does not move. */
  draggable?: boolean;
  /** Told when the user renames this pane, so the page can update its own
   *  copy without waiting for the next activity poll. */
  onRenamed?: (name: string) => void;
  /** Told which CLI was just launched here, for the same reason. */
  onLaunched?: (cli: string) => void;
  /** Open a NEW pane already prepared for this harness (v1.238.0).
   *  A recipe puts the MCP address and a pane-scoped token into the
   *  harness's ENVIRONMENT, which the daemon merges before the shell is
   *  spawned — so it can only be asked for at pane creation. Launching in
   *  THIS pane types a command into a shell that is already running and
   *  can never receive them, and the token must never be typed, because a
   *  shell keeps history and scrollback. */
  onLaunchWithCapabilities?: (cli: string) => void;
  /** v1.217.0: what the agent occupying this pane is doing. */
  paneState?: PaneState | null;
  /** Which coding CLI Build believes occupies this pane. */
  agentCli?: string | null;
  paneStateLine?: string | null;
  onFocus: () => void;
  onClose: () => void;
  /** v1.207.0: receives this pane's "type into the live shell" writer on
   *  attach, and null on dispose. The writer is the SAME mechanism the
   *  v1.194 snippet path types with (raw text over the attach WebSocket) and
   *  is HONEST when down: writing to a closed/absent socket is a no-op that
   *  returns false, so the caller can tell whether the text landed. */
  onWriterReady?: (write: ((text: string) => boolean) | null) => void;
  /** v1.212.0: fired when this pane's PTY produced NEW output, throttled to
   *  at most one call per ~300ms — the page badges the view toggle so output
   *  isn't missed while the pane shows its chat layer. Not fired during the
   *  post-(re)connect replay window (the scrollback catch-up is old news).
   *  v1.213.0: carries the notifying frame's text (utf-8 best-effort decode)
   *  so the page can peek the last output line; frames suppressed by the
   *  throttle are simply not delivered — the peek is a glimpse, not a log. */
  onOutput?: (chunk: string) => void;
  /** Model catalog for the PER-PANE AI assist picker (from /models). */
  models?: ModelOption[];
  /** AI CLIs detected on this machine, for the "Launch" dropdown. */
  aiClis?: AiCli[];
  /** The discovered skill library — usable by ANY provider via the AI assist. */
  skills?: Skill[];
  /** All live terminals (self included; filtered here) — lets THIS pane's AI
   *  see what's happening in other panes when the user opts in. */
  otherTerminals?: { id: string; shell: string; cwd: string }[];
}) {
  const router = useRouter();
  const holderRef = useRef<HTMLDivElement | null>(null);
  // The live xterm instance, so we can refocus it after typing a launch command.
  const termRef = useRef<{ focus: () => void } | null>(null);
  // v1.232.0 (audit U13): "Clear scrollback" — wipes this pane's on-screen
  // history and repaints. Client-side only: the shell keeps running and the
  // daemon's own scrollback is untouched. Set once the xterm instance exists.
  const clearRef = useRef<(() => void) | null>(null);
  const [state, setState] = useState<ConnState>("connecting");
  // v1.226.0 (F-D-4): why the pane is "closed" — the shell EXITED (code 4000,
  // nothing to reconnect to) or the link was LOST (retries exhausted). Only
  // the latter offers a Reconnect action.
  const [lostLink, setLostLink] = useState(false);
  // The daemon's reachability per the shared /health poll. The host's
  // reconnect schedule reads it (keep retrying while the daemon is down), and
  // the host outlives this component, so the value is handed over to it.
  const { online: daemonOnline } = useDaemon();
  const daemonOnlineRef = useRef(daemonOnline);
  daemonOnlineRef.current = daemonOnline;
  // Set by the attach effect: the host's Reconnect (a clean attempt counter on
  // the SAME terminal — the replay lands on it). Null while none is adopted.
  const reconnectRef = useRef<(() => void) | null>(null);
  // This pane's terminal host (v1.243.0, components/terminal/paneHost): the
  // xterm and socket that OUTLIVE this component. Everything that types into
  // the shell — the AI bar's Run, Launch, snippets, the pane chat's writer —
  // goes through its send(), which refuses (false) on a socket that is down.
  const hostRef = useRef<PaneHost | null>(null);
  useEffect(() => {
    if (hostRef.current) hostRef.current.daemonOnline = daemonOnline;
  }, [daemonOnline]);

  // v1.212.0: output notifications for the page's view-toggle badge. The
  // socket effect below runs once per session id while the page hands a
  // fresh onOutput closure each render — read it through a ref at
  // ws.onmessage time so the callback never goes stale. The timestamp ref
  // is the ~300ms throttle's memory (see paneStatusCore.outputNotifyAt).
  const onOutputRef = useRef(onOutput);
  onOutputRef.current = onOutput;
  const lastOutputNotifyRef = useRef(0);

  // --- Per-pane AI assist (suggest-only; Run is an explicit click) ---------
  const [aiOpen, setAiOpen] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiResult, setAiResult] = useState<AIResult | null>(null);
  const [choice, setChoice] = useState(""); // "" = the app's default model
  // Skill for the assist: "" = Auto (search the library), "none" = off,
  // anything else = that exact skill. Works with EVERY provider (prompt-side).
  const [skillChoice, setSkillChoice] = useState("");
  // Cross-terminal sharing: other pane ids whose output THIS ask should see.
  const [ctxIds, setCtxIds] = useState<string[]>([]);
  const [ctxOpen, setCtxOpen] = useState(false);
  const [ctxCopied, setCtxCopied] = useState(false);
  const peers = otherTerminals.filter((t) => t.id !== info.id);

  function toggleCtx(id: string) {
    setCtxIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id].slice(-3),
    );
  }

  // Copy this pane's CLEAN context (ANSI-stripped) for pasting into any other
  // AI — a claude/codex CLI in another pane, or anything else.
  async function copyContext(e: React.MouseEvent) {
    e.stopPropagation();
    try {
      const res = await get<{ text: string }>(`/terminals/${info.id}/context`);
      const bridge = (
        window as unknown as {
          ironjarvis?: { clipboardWriteText?: (t: string) => Promise<unknown> };
        }
      ).ironjarvis;
      if (bridge?.clipboardWriteText) await bridge.clipboardWriteText(res.text);
      else await navigator.clipboard?.writeText?.(res.text);
      setCtxCopied(true);
      window.setTimeout(() => setCtxCopied(false), 2500);
    } catch (err) {
      setAiError(err instanceof ApiError ? err.message : String(err));
      setAiOpen(true);
    }
  }

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
        skill: skillChoice,
        include_terminals: ctxIds,
      });
      setAiResult(res);
    } catch (err) {
      setAiError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setAiBusy(false);
    }
  }

  // Turn THIS session's transcript into a repeatable workflow: the agent builds
  // it server-side, we stash it, then hop to the Workflows editor which loads it.
  const [wfBusy, setWfBusy] = useState(false);
  async function makeWorkflow(e: React.MouseEvent) {
    e.stopPropagation();
    if (wfBusy) return;
    setWfBusy(true);
    setAiError(null);
    try {
      const [provider, model] = choice ? choice.split("::") : ["", ""];
      const def = await post<{ name: string; description: string; steps: unknown[] }>(
        `/terminals/${info.id}/workflow`,
        { provider, model },
      );
      try {
        sessionStorage.setItem("ij_pending_workflow", JSON.stringify(def));
      } catch {
        /* private mode — the editor just won't auto-load */
      }
      router.push("/workflows");
    } catch (err) {
      // Surface the reason in the assist bar (e.g. "no output yet").
      setAiError(err instanceof ApiError ? err.message : String(err));
      setAiOpen(true);
    } finally {
      setWfBusy(false);
    }
  }

  // --- Launch an installed AI CLI (claude / codex / …) in THIS shell --------
  const [launchOpen, setLaunchOpen] = useState(false);
  const [launchHint, setLaunchHint] = useState<string | null>(null);
  // WHICH CLI THIS PANE IS RUNNING. launchCli has always known it and threw it
  // away after a 5s toast; the snippet route needs it to format the image
  // reference the way that CLI wants to be handed a path (mirrors Creative
  // Studio's `_studio_cli`). "" = a plain shell — the server falls back to a
  // bare quoted path.
  const [paneCli, setPaneCli] = useState("");
  // The pane's terminal outlives this component (v1.243.0) but this state
  // does not: seed it from what the daemon recorded at launch, so a snippet
  // sent after a return visit is still formatted for the CLI in the pane.
  useEffect(() => {
    if (agentCli) setPaneCli((cur) => cur || agentCli);
  }, [agentCli]);

  // --- naming this pane (v1.217.0) -----------------------------------------
  const [renaming, setRenaming] = useState(false);
  const [draftName, setDraftName] = useState("");
  const commitRename = useCallback(() => {
    setRenaming(false);
    const next = draftName.trim();
    if (next === (paneName || "")) return;
    // Optimistic: the header shows the new name immediately and the daemon is
    // told after. A rename that waits on a round-trip feels broken on a field
    // the user is still looking at, and the failure mode is cosmetic — the
    // next list refresh restores the truth.
    onRenamed?.(next);
    patch(`/terminals/${info.id}`, { name: next }).catch(() => {
      /* offline / pane gone — the list poll is the source of truth */
    });
  }, [draftName, paneName, info.id, onRenamed]);
  const installedClis: LaunchCli[] = aiClis.filter((c) => c.installed);
  const notInstalledClis = aiClis.filter((c) => !c.installed);

  function launchCli(cli: AiCli) {
    // Type the launch command WITHOUT a newline — the user presses Enter to
    // actually start it (a last look, same as the AI "Run" suggestion). A
    // shell that is not connected takes nothing, and nothing is recorded.
    if (!hostRef.current?.send(cli.command)) return;
    setPaneCli(cli.id); // remember it for snippet delivery
    // …and TELL THE DAEMON (v1.217.0). The launch is typed into an already
    // running shell, so the server never learns what started — which left the
    // pane-state classifier's "the catalog knows what it started" path
    // permanently unreachable and every launched CLI sniffed from scrollback.
    onLaunched?.(cli.id);
    patch(`/terminals/${info.id}`, { agent_cli: cli.id }).catch(() => {
      /* the classifier falls back to sniffing — a worse answer, not a wrong one */
    });
    termRef.current?.focus();
    setLaunchHint(cli.label);
    window.setTimeout(() => setLaunchHint(null), 5000);
  }

  // --- Resume after a restart (v1.245.0) — see ResumeStrip -----------------
  const [resumeGone, setResumeGone] = useState(false);
  const resumeLabel =
    aiClis.find((c) => c.id === info.resume_cli)?.label ?? info.resume_cli ?? "";
  const showResume = Boolean(info.resume_cli && info.resume_command) && !resumeGone;

  function resumeCli() {
    const cli = info.resume_cli;
    const command = info.resume_command;
    // The click IS the consent, so this one presses Enter (Launch leaves that
    // to the user). A shell that is not connected takes nothing; the strip
    // stays up so the click can be made again.
    if (!cli || !command || !hostRef.current?.send(`${command}\r`)) return;
    setResumeGone(true);
    setPaneCli(cli);
    onLaunched?.(cli);
    patch(`/terminals/${info.id}`, { agent_cli: cli, resume_cli: "" }).catch(() => {
      /* offline — the classifier sniffs the CLI back from the scrollback */
    });
    termRef.current?.focus();
  }

  function dismissResume() {
    setResumeGone(true);
    patch(`/terminals/${info.id}`, { resume_cli: "" }).catch(() => {
      /* offline — the offer returns on the next load, which is harmless */
    });
  }

  // --- Pending screen snippets ---------------------------------------------
  const [snips, setSnips] = useState<PendingSnip[]>([]);
  const [expandedSnip, setExpandedSnip] = useState<string | null>(null);
  const [snipSending, setSnipSending] = useState(false);
  const [snipNote, setSnipNote] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  // Every object URL we minted, so unmount can revoke them all.
  const snipUrls = useRef<Set<string>>(new Set());
  useEffect(
    () => () => {
      snipUrls.current.forEach((u) => URL.revokeObjectURL(u));
      snipUrls.current.clear();
    },
    [],
  );

  const dropSnipUrl = (url: string) => {
    if (!url) return;
    snipUrls.current.delete(url);
    try {
      URL.revokeObjectURL(url);
    } catch {
      /* already gone */
    }
  };

  /** Put a FAILED chip on the strip for something that never became a snippet
   *  at all (a clipboard image the app could not read, a non-image drop). The
   *  chip is the honest half of the feature: an attempt that produced nothing
   *  must still be visible and dismissable, not silence. */
  const pushFailedSnip = useCallback((name: string, error: string) => {
    const id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    setSnips((prev) => [
      ...prev,
      { id, name, bytes: 0, url: "", recompressed: false, status: "failed", error },
    ]);
  }, []);

  /** Take images the user pasted or dropped: chip them immediately (so nothing
   *  happens invisibly), then shrink each to fit the CLI budget. A refusal
   *  stays on screen as a FAILED chip — never a silent disappearance. */
  const acceptSnips = useCallback((files: File[]) => {
    if (!files.length) return;
    for (const file of files) {
      const id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
      const name = file.name || `snip-${Date.now()}.png`;
      setSnips((prev) => [
        ...prev,
        { id, name, bytes: file.size, url: "", recompressed: false, status: "preparing" },
      ]);
      void (async () => {
        try {
          const res = await shrinkToFit(file, CLI_IMAGE_BUDGET_BYTES);
          if (!res.ok) {
            // The two outcomes are different facts and are reported differently:
            // a budget refusal names the limit, a decode failure says it could
            // not READ the image. Either way nothing was attached, and we say so.
            const why =
              res.reason === "too-large"
                ? `Too big to attach — ${res.detail} Nothing was attached.`
                : `Couldn't read this image — ${res.detail} Nothing was attached.`;
            setSnips((prev) =>
              prev.map((s) => (s.id === id ? { ...s, status: "failed", error: why } : s)),
            );
            return;
          }
          const url = URL.createObjectURL(res.file);
          snipUrls.current.add(url);
          setSnips((prev) =>
            prev.map((s) =>
              s.id === id
                ? {
                    ...s,
                    status: "ready",
                    file: res.file,
                    name: res.file.name || s.name,
                    url,
                    bytes: res.bytes,
                    recompressed: res.recompressed,
                  }
                : s,
            ),
          );
        } catch (err) {
          setSnips((prev) =>
            prev.map((s) =>
              s.id === id
                ? { ...s, status: "failed", error: `Couldn't prepare this image: ${String(err)}` }
                : s,
            ),
          );
        }
      })();
    }
  }, []);

  function removeSnip(id: string) {
    setSnips((prev) => {
      const hit = prev.find((s) => s.id === id);
      if (hit) dropSnipUrl(hit.url);
      return prev.filter((s) => s.id !== id);
    });
    setExpandedSnip((cur) => (cur === id ? null : cur));
  }

  /** Send the ready snippets: POST the bytes to the daemon (which writes them
   *  next to this pane's work and formats a per-CLI reference), then TYPE that
   *  reference into the shell over the pane's own WebSocket. Deliberately NO
   *  trailing "\r" — the user types their sentence around the path. */
  async function sendSnips() {
    if (snipSending) return;
    const ready = snips.filter((s) => s.status === "ready" && s.file);
    if (!ready.length) return;
    if (!hostRef.current?.isOpen) {
      setSnips((prev) =>
        prev.map((s) =>
          s.status === "ready"
            ? { ...s, status: "failed", error: "Terminal isn't connected — nothing was attached." }
            : s,
        ),
      );
      return;
    }
    setSnipSending(true);
    try {
      for (const snip of ready) {
        setSnips((prev) =>
          prev.map((s) => (s.id === snip.id ? { ...s, status: "sending", error: undefined } : s)),
        );
        try {
          const content_b64 = await fileToBase64(snip.file as File);
          const res = await post<{
            path: string;
            name: string;
            bytes: number;
            reference: string;
            location?: string;
            note?: string;
          }>(`/terminals/${info.id}/snippet`, {
            filename: snip.name,
            content_b64,
            cli: paneCli,
          });
          const live = hostRef.current;
          if (!live || !live.isOpen) {
            setSnips((prev) =>
              prev.map((s) =>
                s.id === snip.id
                  ? {
                      ...s,
                      status: "failed",
                      error: `Saved to ${res.path}, but the terminal dropped — the path was NOT typed in.`,
                    }
                  : s,
              ),
            );
            continue;
          }
          const reference = res.reference || res.path;
          // NO trailing carriage return: typed in, not submitted.
          live.send(reference.endsWith(" ") ? reference : `${reference} `);
          // The daemon prefers the pane's own folder and SAYS SO when it had to
          // fall back to the uploads dir — a CLI confined to its workspace may
          // not be able to read that file, so the user has to know.
          if (res.note) {
            setSnipNote(res.note);
            window.setTimeout(() => setSnipNote(null), 10000);
          }
          dropSnipUrl(snip.url);
          setSnips((prev) => prev.filter((s) => s.id !== snip.id));
          termRef.current?.focus();
        } catch (err) {
          setSnips((prev) =>
            prev.map((s) =>
              s.id === snip.id
                ? {
                    ...s,
                    status: "failed",
                    error: err instanceof ApiError ? err.message : String(err),
                  }
                : s,
            ),
          );
        }
      }
    } finally {
      setSnipSending(false);
    }
  }

  function runSuggested() {
    // Type the command into the shell WITHOUT submitting it — the user presses
    // Enter themselves (a last look before anything executes). A shell that is
    // not connected takes nothing, and the suggestion stays on screen.
    if (!aiResult?.command || !hostRef.current?.send(aiResult.command)) return;
    setAiResult(null);
    setAiPrompt("");
  }

  useEffect(() => {
    const holder = holderRef.current;
    if (!holder || typeof window === "undefined") return;

    let disposed = false;
    // This mount's claim on the host. A later mount (a Rail ⇄ Canvas flip, a
    // return visit) adopts with its own, and this one's release is then
    // ignored — see PaneHost.adopt.
    const owner = {};
    let host: PaneHost | null = null;
    let term: import("@xterm/xterm").Terminal | null = null;
    let ro: ResizeObserver | null = null;
    let focusedOnce = false; // steal focus once per mount — a reconnect
    // mid-interaction would close an open dropdown/popup out from under the user

    // v1.207.0: hand the page this pane's "type into the live shell" writer —
    // the SAME mechanism the v1.194 snippet path uses (raw text over the
    // attach WebSocket; the daemon feeds it to the PTY as keystrokes, exactly
    // like term.onData). Registered once per mount and read through hostRef
    // at call time, so it survives reconnects without churning the registry;
    // unregistered (null) in the cleanup below. HONEST when down: the host's
    // send() writes nothing and returns false on a closed/absent socket, so
    // the caller can refuse instead of pretending the command ran.
    const writeToShell = (text: string): boolean => {
      const live = hostRef.current;
      return live ? live.send(text) : false;
    };
    onWriterReady?.(writeToShell);

    // Paste support. A terminal treats Ctrl+V as a control char (0x16), NOT
    // paste — so pasting looks broken. Wire it explicitly. term.paste() respects
    // bracketed-paste mode, so a multi-line prompt inserts as ONE block instead
    // of running line-by-line.
    // Prefer the desktop app's NATIVE clipboard (via the preload IPC bridge) —
    // it's never permission-gated; fall back to the Web Clipboard API in a plain
    // browser.
    const ijBridge = (
      window as unknown as {
        ironjarvis?: {
          clipboardReadText?: () => Promise<string>;
          clipboardWriteText?: (t: string) => Promise<unknown>;
          /** PNG bytes of a clipboard IMAGE, or null (desktop app only). */
          clipboardReadImage?: () => Promise<unknown>;
        };
      }
    ).ironjarvis;
    const readClip = (): Promise<string> =>
      ijBridge?.clipboardReadText
        ? ijBridge.clipboardReadText()
        : navigator.clipboard?.readText?.() ?? Promise.resolve("");
    const writeClip = (t: string): Promise<unknown> =>
      ijBridge?.clipboardWriteText
        ? ijBridge.clipboardWriteText(t)
        : navigator.clipboard?.writeText?.(t) ?? Promise.resolve();
    // Image side of the same bridge. Absent in a plain browser tab (there the
    // native paste EVENT carries the bytes, with no permission prompt), and it
    // must never reject — a throw here would take ordinary text paste with it.
    // Only reached when the clipboard has NO text (see `resolvePaste`), so an
    // Excel copy never pays for a multi-megapixel toPNG() on the main process.
    const readClipImage = (): Promise<ClipImage> =>
      ijBridge?.clipboardReadImage
        ? ijBridge
            .clipboardReadImage()
            .then((v) => clipImageOutcome(v))
            // A REJECTED probe is not an empty clipboard. Mapping it to "none"
            // would make a broken bridge (handler unregistered, or the main
            // process refusing an untrusted sender) look exactly like "you
            // didn't copy an image" — the user presses Ctrl+V, nothing happens,
            // and nothing says why. "unreadable" routes it to the same honest
            // chip a corrupt image gets. Text paste is unaffected either way:
            // this probe only runs on a text-LESS clipboard.
            .catch(() => ({ kind: "unreadable" }) as ClipImage)
        : Promise.resolve({ kind: "none" } as ClipImage);

    const pasteFromClipboard = () => {
      // TEXT FIRST, image only on a TEXT-LESS clipboard (v1.194.0) — see
      // `resolvePaste`. Ordinary pasting is byte-for-byte what it always was.
      void resolvePaste(
        readClip,
        readClipImage,
        (t) => {
          if (term) term.paste(t);
        },
        (img) => acceptSnips([img]),
        () =>
          pushFailedSnip(
            "clipboard image",
            "There was an image on the clipboard but it couldn't be read — nothing was attached.",
          ),
      );
    };
    // A native paste that DOES carry image data (plain browser, right-click
    // paste, Shift+Insert). Capture phase on the holder so we run before
    // xterm's own textarea handler; a paste carrying ANY text is left completely
    // alone — `snipFilesFromPaste` enforces that.
    const onPaste = (e: ClipboardEvent) => {
      const imgs = snipFilesFromPaste(e);
      if (!imgs.length) return; // text (or no image) — xterm handles it as before
      e.preventDefault();
      e.stopPropagation();
      acceptSnips(imgs);
    };
    const onContextMenu = (e: MouseEvent) => {
      // Right-click copies a selection if you have one, else pastes — the
      // familiar Windows-terminal gesture (no browser context menu here).
      e.preventDefault();
      const sel = term?.getSelection();
      if (sel) {
        writeClip(sel).catch(() => {});
        term?.clearSelection();
      } else {
        pasteFromClipboard();
      }
    };
    const onWheel = (e: WheelEvent) => {
      // Guarantee scrollback scrolling on the mouse wheel — capture it here so
      // it can't be swallowed by the container/react-rnd. Only in the NORMAL
      // buffer; full-screen TUI apps (alt-screen) own the wheel themselves.
      if (!term || term.buffer.active.type !== "normal") return;
      e.preventDefault();
      e.stopPropagation();
      const amount = e.deltaMode === 1 ? e.deltaY : e.deltaY / 40; // lines vs px
      const n = Math.trunc(amount);
      term.scrollLines(n !== 0 ? n : e.deltaY > 0 ? 1 : -1);
    };

    const doFit = () => {
      try {
        host?.fit.fit();
      } catch {
        /* container not measurable yet */
      }
    };

    const sendResize = (force = false) => {
      // v1.232.0: only the PRIMARY attach (visible pane, focused document)
      // may resize the PTY — see resizeGate.ts. A phone or a second tab
      // attaching to the same session must never reflow the desktop's
      // running session; the daemon keeps last-writer semantics.
      if (!resizeAllowed(holder)) return;
      if (host && term) {
        // v1.245.0: an unchanged size sends nothing — ConPTY reflows and a
        // running TUI repaints its whole screen on EVERY resize, same-size
        // included. Forced on open (a new attach must claim its size) and on
        // window focus (a phone may have resized the PTY in the meantime).
        const size = `${term.cols}x${term.rows}`;
        if (!force && host.sentSize === size) return;
        if (host.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }))) {
          host.sentSize = size;
        }
      }
    };

    // v1.245.0: a dragged pane or window edge fires the ResizeObserver dozens
    // of times a second, and each fit+resize made the TUI repaint. Fit and
    // resize ONCE, when the size has settled.
    let resizeTimer: ReturnType<typeof setTimeout> | null = null;
    const scheduleFit = () => {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        resizeTimer = null;
        doFit();
        sendResize();
      }, RESIZE_SETTLE_MS);
    };

    const onWinResize = () => scheduleFit();
    // Becoming the focused window makes this attach the primary one: claim
    // the size then, so a pane opened while the window was in the background
    // (the gate refused its open-time resize) gets its real size on return.
    const onWinFocus = () => {
      doFit();
      sendResize(true);
    };

    // Clipboard shortcuts: Ctrl/Cmd+V and Ctrl+Shift+V paste; Ctrl+Shift+C
    // copies a selection (plain Ctrl+C stays as the interrupt signal). The
    // host runs this through xterm's custom key handler while this pane holds
    // it, and drops it when the pane parks.
    const keyHandler = (e: KeyboardEvent): boolean => {
      if (e.type !== "keydown") return true;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && (e.key === "v" || e.key === "V")) {
        // THE IMAGE CASE HAS TO BE REACHABLE AT ALL (v1.194.0). This handler
        // used to preventDefault() unconditionally, which suppresses the
        // browser's native `paste` event entirely — so image bytes never
        // reached the pane no matter what else we wired up. (Which flavour
        // WINS is decided in `resolvePaste`/`snipFilesFromPaste`: text does.)
        if (ijBridge?.clipboardReadImage) {
          // Desktop app: the native clipboard is only reachable over IPC
          // (navigator.clipboard is permission-gated here). Text first, image
          // only when there is no text — see `resolvePaste`.
          e.preventDefault();
          pasteFromClipboard();
          return false; // don't also send the literal control char
        }
        // Plain browser: let the default paste proceed so `onPaste` above
        // sees the image bytes; a text-only paste falls through to xterm's
        // own paste handling (same term.paste, same bracketed-paste mode).
        return false; // xterm must still not emit the literal ^V
      }
      // v1.245.0: Ctrl+C WITH a selection copies it (the Windows Terminal
      // habit) instead of interrupting the CLI; with nothing selected it is
      // still ^C. Ctrl+Shift+C below keeps copying either way.
      if (mod && !e.shiftKey && (e.key === "c" || e.key === "C") && term?.hasSelection()) {
        e.preventDefault();
        writeClip(term?.getSelection() ?? "").catch(() => {});
        term?.clearSelection();
        return false;
      }
      if (mod && e.shiftKey && (e.key === "c" || e.key === "C")) {
        const sel = term?.getSelection();
        if (sel) {
          e.preventDefault();
          writeClip(sel).catch(() => {});
          return false;
        }
      }
      // Keyboard scrollback — Shift+PageUp / Shift+PageDown.
      if (e.shiftKey && e.key === "PageUp") {
        e.preventDefault();
        term?.scrollPages(-1);
        return false;
      }
      if (e.shiftKey && e.key === "PageDown") {
        e.preventDefault();
        term?.scrollPages(1);
        return false;
      }
      return true;
    };

    (async () => {
      let h: PaneHost;
      try {
        // THE TERMINAL OUTLIVES THIS COMPONENT (v1.243.0). A return visit — or
        // a Rail ⇄ Canvas flip — gets back the SAME xterm on the SAME socket:
        // nothing to replay, nothing to redraw, and the program in the pane
        // never noticed anyone left. Only a first visit builds one.
        h = await acquirePaneHost(info.id);
      } catch {
        return; // the pane was closed while its terminal was being built
      }
      if (disposed) return; // never adopted — it stays parked for the next mount
      host = h;
      term = h.term;
      hostRef.current = h;
      termRef.current = h.term; // expose for launch-command refocus
      const live = h.term; // narrowed here; the closure runs later
      clearRef.current = () => {
        try {
          live.clear();
          live.refresh(0, Math.max(0, live.rows - 1));
        } catch {
          /* disposed */
        }
      };
      h.daemonOnline = daemonOnlineRef.current;
      h.keyHandler = keyHandler;
      reconnectRef.current = () => h.reconnectNow();
      h.adopt(holder, owner, {
        onConn: (s, lost) => {
          setState(s);
          setLostLink(lost);
        },
        // The socket (re)opened: claim the size. The daemon's repaint wiggle
        // keys off an attach's first resize.
        onOpen: () => {
          doFit();
          sendResize(true); // a new attach claims its size even when unchanged
        },
        onOutput: (data, replaying) => {
          // v1.212.0: tell the page NEW output landed (throttled) so a pane
          // showing its chat layer can badge the terminal toggle. Every server
          // frame on this socket IS PTY output — the daemon only sends PTY
          // reads, the scrollback replay, the end-of-replay marker (which the
          // host consumes) and the exit note. The (re)attach replay is skipped
          // EXACTLY (v1.243.0): the daemon ends it with an empty frame and the
          // host says which frames came before it, so `replayGuardUntil` is no
          // longer a guess on the clock — "forever" while replaying, "never"
          // after. The ~300ms throttle and the page's view gate still apply.
          const replayGuardUntil = replaying ? Number.POSITIVE_INFINITY : 0;
          const at = outputNotifyAt(
            data,
            Date.now(),
            lastOutputNotifyRef.current,
            replayGuardUntil,
          );
          if (at !== null) {
            lastOutputNotifyRef.current = at;
            // Decode ONLY the notifying frame (v1.213.0) — the throttle already
            // decided the page wants a glimpse; suppressed frames cost nothing.
            onOutputRef.current?.(decodeFrame(data));
          }
        },
      });
      holder.addEventListener("contextmenu", onContextMenu);
      holder.addEventListener("wheel", onWheel, { passive: false, capture: true });
      holder.addEventListener("paste", onPaste, true);

      // FIT BEFORE CONNECT (v1.190.0). The daemon replays the session's whole
      // scrollback the moment a NEW socket opens, at whatever size this
      // terminal has right then — history wrapped into a default 80×24 buffer
      // never recovers, and no later fit can re-wrap it. Waiting for a stable
      // size removes the ordering from luck. A returning host is already
      // connected; the same wait keeps its first fit off a transitional size,
      // which would resize the PTY and make a running TUI redraw for nothing.
      // Capped wait: a hidden pane proceeds rather than hangs.
      await waitForStableSize(holder);
      if (disposed) return;
      doFit();
      h.settle();
      h.start(); // first visit: connect now; a live host: a no-op

      ro = new ResizeObserver(() => scheduleFit());
      ro.observe(holder);
      window.addEventListener("resize", onWinResize);
      window.addEventListener("focus", onWinFocus);

      if (!focusedOnce) {
        // One-shot either way: the shot is CONSUMED even when skipped, so a
        // later reconnect can never steal focus mid-interaction (the
        // original point of focusedOnce). Skipped when the holder isn't
        // actually visible (v1.206.0): a pane restored straight into chat
        // view keeps its terminal mounted under visibility:hidden, and an
        // invisible PTY grabbing keystrokes is a keylogger-shaped bug in
        // the desktop app. offsetParent misses visibility:hidden, so use
        // checkVisibility with the visibility option (both spellings — the
        // dictionary member was renamed checkVisibilityCSS →
        // visibilityProperty); default to visible where the API is absent.
        focusedOnce = true;
        const check = (
          holder as HTMLElement & {
            checkVisibility?: (opts?: Record<string, boolean>) => boolean;
          }
        ).checkVisibility;
        const holderVisible =
          typeof check === "function"
            ? check.call(holder, { checkVisibilityCSS: true, visibilityProperty: true })
            : true;
        if (holderVisible) term?.focus();
      }
    })();

    return () => {
      disposed = true;
      onWriterReady?.(null); // this mount is going away — no writer to offer
      reconnectRef.current = null;
      hostRef.current = null;
      termRef.current = null;
      clearRef.current = null;
      window.removeEventListener("resize", onWinResize);
      window.removeEventListener("focus", onWinFocus);
      holder.removeEventListener("contextmenu", onContextMenu);
      holder.removeEventListener("wheel", onWheel, { capture: true } as EventListenerOptions);
      holder.removeEventListener("paste", onPaste, true);
      if (resizeTimer) clearTimeout(resizeTimer);
      ro?.disconnect();
      // PARK, never dispose (v1.243.0): the shell, its socket and every line
      // it prints stay alive for the next visit. Closing the pane is the one
      // thing that ends them (the page's closeTerminal → disposePaneHost).
      host?.release(owner);
    };
    // Re-wire only when the session id changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info.id]);

  return (
    <div
      onMouseDown={onFocus}
      // Drop a screenshot anywhere on the pane. A drag carrying no FILES is left
      // entirely alone (no preventDefault), so nothing else changes.
      onDragOver={(e) => {
        if (!dragCarriesFiles(e.dataTransfer)) return;
        e.preventDefault();
        e.stopPropagation();
        setDragOver(true);
      }}
      onDragLeave={(e) => {
        // Only when the pointer actually LEAVES the pane. Keying on target-vs-
        // currentTarget identity left the accept ring stuck on forever whenever
        // the drag exited over a CHILD — and the terminal fills the pane, so
        // that is the normal case, not the edge one.
        const to = e.relatedTarget as Node | null;
        if (!to || !e.currentTarget.contains(to)) setDragOver(false);
      }}
      onDrop={(e) => {
        setDragOver(false);
        // WE ADVERTISED THIS DROP, SO WE CONSUME IT. Same predicate as
        // onDragOver above: it preventDefaulted and rang the pane for any drag
        // carrying files, and handing such a drop back to the browser default
        // reaches Electron's `will-navigate` → `shell.openExternal(file://…)`,
        // i.e. a dropped PDF/exe gets OPENED by the OS. preventDefault comes
        // BEFORE the non-image bail-out for exactly that reason.
        if (!dragCarriesFiles(e.dataTransfer)) return;
        e.preventDefault();
        e.stopPropagation();
        const files = imageFilesFromDrop(e.nativeEvent);
        if (!files.length) {
          const dropped = Array.from(e.dataTransfer?.files ?? []);
          pushFailedSnip(
            dropped[0]?.name || "dropped file",
            dropped.length > 1
              ? "Those aren't images — nothing was attached."
              : "That isn't an image — nothing was attached.",
          );
          return;
        }
        acceptSnips(files);
      }}
      className={`group relative flex h-full flex-col overflow-hidden rounded-2xl border bg-[#0a0c11] shadow-card transition-colors ${
        dragOver
          ? "border-accent shadow-glow-sm ring-2 ring-accent/40"
          : focused
            ? "border-accent/50 shadow-glow-sm ring-1 ring-accent/30"
            : "border-white/[0.07] hover:border-white/[0.14]"
      }`}
    >
      {/* Pane header: shell · cwd · connection state · close. The `ij-term-drag`
          class marks this as the drag handle for react-rnd on the Terminals
          page (buttons/selects inside are excluded via react-rnd's `cancel`). */}
      {/* The drag handle is CONDITIONAL (v1.218.0). In the rail a pane fills
          its box and cannot be moved, so `cursor-move` and the react-rnd
          handle class would both be promises the layout does not keep. */}
      <header
        className={`flex shrink-0 items-center gap-2 border-b border-white/[0.06] bg-ink-900/60 px-3 py-2 ${
          draggable ? "ij-term-drag cursor-move" : ""
        }`}
      >
        <TerminalIcon
          size={13}
          className={focused ? "text-accent" : "text-zinc-500"}
        />
        {/* THE PANE'S NAME, EDITABLE IN PLACE (v1.217.0). Agents address panes
            by name and the state summary lists them by name, so a name the
            user cannot set is a feature only an API caller has. Editing here
            rather than in a dialog because the name IS the header — a modal to
            change one word would be heavier than the thing it changes. */}
        {renaming ? (
          <input
            autoFocus
            aria-label="Pane name"
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            onMouseDown={(e) => e.stopPropagation()}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitRename();
              // Escape ABANDONS — a rename you cannot back out of makes the
              // field something people avoid clicking.
              if (e.key === "Escape") {
                setDraftName(paneName || "");
                setRenaming(false);
              }
            }}
            placeholder={info.shell}
            className="field w-28 shrink-0 py-0.5 font-mono text-[11px]"
          />
        ) : (
          <button
            type="button"
            data-testid="pane-name"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={() => {
              setDraftName(paneName || "");
              setRenaming(true);
            }}
            title={paneName ? "Rename this pane" : "Name this pane"}
            className="shrink-0 cursor-text rounded px-1 font-mono text-[11px] font-semibold text-zinc-200 hover:bg-white/[0.06]"
          >
            {paneName || info.shell}
          </button>
        )}
        {/* WHAT THE AGENT IN HERE IS DOING (v1.217.0). Next to the pane's own
            name, because it is a fact about this pane rather than about the
            connection — the ws state below still answers "is it attached".
            Renders nothing for `unknown`, so a plain shell keeps the header it
            has always had. */}
        {paneState && (
          <PaneStateChip state={paneState} cli={agentCli} line={paneStateLine} />
        )}
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
        {/* v1.232.0 (audit U13): a visible way out of a garbled replay (the
            one-word-per-line rehydrate) — clear the on-screen history and
            repaint. The shell and the daemon's scrollback are untouched. */}
        <button
          onClick={(e) => {
            e.stopPropagation();
            clearRef.current?.();
            termRef.current?.focus();
          }}
          aria-label="Clear scrollback"
          title="Clear scrollback — wipes this pane's on-screen history and repaints (the shell keeps running)"
          className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-accent/15 hover:text-accent-soft"
        >
          <Eraser size={13} />
        </button>
        <button
          onClick={makeWorkflow}
          disabled={wfBusy}
          title="Turn this session into a repeatable workflow"
          className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-accent/15 hover:text-accent-soft disabled:opacity-50"
        >
          {wfBusy ? <Loader2 size={13} className="animate-spin" /> : <Workflow size={13} />}
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation();
            setLaunchOpen((v) => !v);
          }}
          title="Launch an AI CLI in this terminal (Claude, Codex, …)"
          className={`grid h-5 w-5 shrink-0 place-items-center rounded-md transition-colors ${
            launchOpen
              ? "bg-accent/15 text-accent"
              : "text-zinc-500 hover:bg-accent/15 hover:text-accent-soft"
          }`}
        >
          <Rocket size={13} />
        </button>
        {info.degraded && (
          <span
            title="Basic shell (no full TTY) — commands run, but interactive TUI apps may not render. The full terminal returns after the next app update."
            className="inline-flex shrink-0 items-center rounded-full border border-amber-500/25 bg-amber-500/10 px-1.5 py-0.5 text-[9px] font-medium text-amber-300"
          >
            basic
          </span>
        )}
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

      {/* Launch dropdown — the AI CLIs actually installed on this machine.
          Picking one TYPES its command into the shell; the user presses Enter. */}
      {launchOpen && (
        <>
          <button
            aria-hidden
            tabIndex={-1}
            onClick={() => setLaunchOpen(false)}
            className="fixed inset-0 z-30 cursor-default"
          />
          <div className="absolute right-2 top-11 z-40 max-h-[70%] w-60 overflow-auto rounded-xl border border-white/10 bg-ink-900/95 p-1 shadow-card backdrop-blur">
            {installedClis.length === 0 && notInstalledClis.length === 0 && (
              <div className="px-2 py-2 text-[11px] text-zinc-500">Detecting…</div>
            )}
            {installedClis.length > 0 && (
              <div className="px-2 pb-0.5 pt-1 text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
                Installed — click to type, then Enter
              </div>
            )}
            {installedClis.map((c) => (
              <button
                key={c.id}
                onClick={() => {
                  launchCli(c);
                  setLaunchOpen(false);
                }}
                className="flex w-full flex-col gap-0.5 rounded-lg px-2 py-1.5 text-left text-[12px] text-zinc-200 transition-colors hover:bg-accent/10 hover:text-accent-soft"
              >
                <span className="flex w-full items-center justify-between gap-2">
                  <span className="flex items-center gap-2">
                    <Rocket size={12} className="text-accent-soft/80" />
                    <span className="font-medium">{c.label}</span>
                    {c.version ? (
                      <span
                        data-testid={`launch-version-${c.id}`}
                        className="font-mono text-[10px] text-zinc-600"
                      >
                        {c.version}
                      </span>
                    ) : null}
                  </span>
                  <span className="font-mono text-[10px] text-zinc-500">{c.command.trim()}</span>
                </span>
                <LaunchRecipeNote cli={c} />
              </button>
            ))}
            {/* THE SECOND DOOR (v1.238.0). A recipe hands the harness the MCP
                address and a pane-scoped token through the child ENVIRONMENT,
                which the daemon merges before the shell is spawned. Launching
                above types a command into a shell that is ALREADY RUNNING, so
                it can never receive them — and the token must never be typed,
                because a shell keeps history and scrollback. So the capable
                launch is a NEW pane, and the menu says so rather than quietly
                doing something different from what the row above it does. */}
            {onLaunchWithCapabilities &&
              installedClis.filter((c) => c.recipe && c.recipe.method !== "none").length > 0 && (
                <>
                  <div className="px-2 pb-0.5 pt-2 text-[10px] font-semibold uppercase tracking-wide text-zinc-600">
                    With Jarvis capabilities — opens a new pane
                  </div>
                  {installedClis
                    .filter((c) => c.recipe && c.recipe.method !== "none")
                    .map((c) => (
                      <button
                        key={`cap-${c.id}`}
                        data-testid={`launch-capable-${c.id}`}
                        onClick={() => {
                          onLaunchWithCapabilities(c.id);
                          setLaunchOpen(false);
                        }}
                        className="flex w-full flex-col gap-0.5 rounded-lg px-2 py-1.5 text-left text-[12px] text-zinc-200 transition-colors hover:bg-accent/10 hover:text-accent-soft"
                      >
                        <span className="flex w-full items-center justify-between gap-2">
                          <span className="flex items-center gap-2">
                            <Rocket size={12} className="text-accent-soft/80" />
                            <span className="font-medium">{c.label}</span>
                          </span>
                          <span className="font-mono text-[10px] text-zinc-500">new pane</span>
                        </span>
                        <LaunchRecipeNote cli={c} />
                      </button>
                    ))}
                </>
              )}
            {notInstalledClis.length > 0 && (
              <div className="px-2 pb-0.5 pt-2 text-[10px] font-semibold uppercase tracking-wide text-zinc-600">
                Not installed
              </div>
            )}
            {notInstalledClis.map((c) => (
              <a
                key={c.id}
                href={c.url}
                target="_blank"
                rel="noreferrer"
                title={`${c.label} isn't on your PATH — get it`}
                className="flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.04]"
              >
                <span>{c.label}</span>
                <ExternalLink size={11} />
              </a>
            ))}
          </div>
        </>
      )}
      {showResume && (
        <ResumeStrip
          cliLabel={resumeLabel}
          command={info.resume_command ?? ""}
          onResume={resumeCli}
          onDismiss={dismissResume}
        />
      )}
      {launchHint && (
        <div className="flex shrink-0 items-center gap-2 border-b border-accent/20 bg-accent/[0.06] px-3 py-1 text-[11px] text-accent-soft">
          <CornerDownLeft size={12} /> Press <span className="font-semibold">Enter</span> in the
          terminal to start {launchHint}.
        </div>
      )}

      {snipNote && (
        <div
          role="status"
          className="flex shrink-0 items-center gap-2 border-b border-amber-500/20 bg-amber-500/[0.06] px-3 py-1 text-[11px] text-amber-200"
        >
          <ImageIcon size={12} /> Saved outside this folder — {snipNote}
        </div>
      )}

      {/* Pending snippets. Renders ONLY when something is pending — a pane with
          no snippet looks exactly as it did before this feature existed. */}
      {snips.length > 0 && (
        <div
          data-testid="snip-strip"
          className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-white/[0.06] bg-ink-900/40 px-3 py-1.5"
        >
          {snips.map((s) => (
            <div
              key={s.id}
              className={`flex items-center gap-1.5 rounded-lg border px-1.5 py-1 ${
                s.status === "failed"
                  ? "border-rose-500/30 bg-rose-500/[0.07]"
                  : "border-white/10 bg-white/[0.03]"
              }`}
            >
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  if (s.url) setExpandedSnip(s.id);
                }}
                title={s.url ? "See it full size before you send it" : s.name}
                aria-label={`Preview ${s.name}`}
                className="shrink-0 overflow-hidden rounded border border-white/10"
              >
                {s.url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={s.url} alt={s.name} className="h-8 w-12 object-cover" />
                ) : (
                  <span className="grid h-8 w-12 place-items-center bg-black/40">
                    <ImageIcon size={12} className="text-zinc-600" />
                  </span>
                )}
              </button>
              <span className="flex min-w-0 flex-col leading-tight">
                <span className="max-w-[12rem] truncate font-mono text-[10px] text-zinc-300">
                  {s.name}
                </span>
                <span className="text-[9px] text-zinc-500">
                  {s.status === "preparing"
                    ? "preparing…"
                    : s.status === "sending"
                      ? "attaching…"
                      : formatSnipBytes(s.bytes)}
                  {s.status === "ready" && s.recompressed && (
                    <span
                      title={`Re-encoded to fit under ${formatSnipBytes(CLI_IMAGE_BUDGET_BYTES)} — the CLI would reject the original.`}
                      className="ml-1 text-amber-300"
                    >
                      · recompressed to fit
                    </span>
                  )}
                </span>
                {s.error && (
                  <span role="alert" className="max-w-[16rem] text-[9px] text-rose-300">
                    {s.error}
                  </span>
                )}
              </span>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  removeSnip(s.id);
                }}
                title="Remove this snippet"
                aria-label={`Remove ${s.name}`}
                className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-rose-500/15 hover:text-rose-300"
              >
                <X size={11} />
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              void sendSnips();
            }}
            disabled={snipSending || state !== "open" || !snips.some((s) => s.status === "ready")}
            title="Saves the image next to this terminal's work and TYPES its path in — press Enter yourself"
            className="btn-accent shrink-0 px-2 py-1 text-[11px]"
          >
            {snipSending ? (
              <Loader2 size={11} className="animate-spin" />
            ) : (
              <Paperclip size={11} />
            )}
            {snipSending ? "Attaching…" : "Insert path"}
          </button>
          {paneCli && (
            <span className="text-[9px] text-zinc-600">for {paneCli}</span>
          )}
        </div>
      )}

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
            {/* Dictate the request (offline Vosk in the desktop app, Web Speech in
                a browser). Speaking a plain-English ask beats typing shell syntax;
                the AI turns it into a command you still review + Run. */}
            <VoiceInput
              size="sm"
              onTranscript={(chunk) => setAiPrompt((p) => appendDictation(p, chunk))}
            />
            {/* Skill for this ask: Auto searches the whole discovered library
                (Claude + Codex + yours) — works with ANY provider. */}
            {skills.length > 0 && (
              <select
                aria-label="Skill for this ask"
                title="Apply a skill playbook from your library (Auto picks the best match)"
                value={skillChoice}
                onChange={(e) => setSkillChoice(e.target.value)}
                onMouseDown={(e) => e.stopPropagation()}
                className="field w-auto max-w-[9rem] shrink-0 py-1 text-[10px]"
              >
                <option value="">skill: auto</option>
                <option value="none">skill: none</option>
                {skills.map((s) => (
                  <option key={s.name} value={s.name} title={s.description}>
                    {s.name}
                  </option>
                ))}
              </select>
            )}
            {/* Share ANOTHER terminal's work into this ask (cross-pane context). */}
            {peers.length > 0 && (
              <div className="relative shrink-0">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setCtxOpen((v) => !v);
                  }}
                  title="Include another terminal's recent output in this ask"
                  className={`flex items-center gap-1 rounded-md border px-1.5 py-1 text-[10px] transition-colors ${
                    ctxIds.length
                      ? "border-accent/40 bg-accent/10 text-accent-soft"
                      : "border-white/10 text-zinc-400 hover:border-accent/30"
                  }`}
                >
                  <Layers size={11} />
                  {ctxIds.length ? `+${ctxIds.length} ctx` : "+ctx"}
                </button>
                {ctxOpen && (
                  <div className="absolute right-0 top-7 z-40 w-56 rounded-xl border border-white/10 bg-ink-900/95 p-1 shadow-card backdrop-blur">
                    <div className="px-2 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
                      Share context from…
                    </div>
                    {peers.map((t) => (
                      <button
                        key={t.id}
                        type="button"
                        onClick={() => toggleCtx(t.id)}
                        className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[11px] text-zinc-300 transition-colors hover:bg-accent/10"
                      >
                        <span
                          className={`grid h-3.5 w-3.5 shrink-0 place-items-center rounded border ${
                            ctxIds.includes(t.id)
                              ? "border-accent bg-accent/20 text-accent"
                              : "border-white/20 text-transparent"
                          }`}
                        >
                          <Check size={10} />
                        </span>
                        <span className="min-w-0">
                          <span className="block font-mono text-[10px] text-zinc-200">
                            {t.shell}
                          </span>
                          <span className="block truncate text-[10px] text-zinc-500">
                            {t.cwd}
                          </span>
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            {/* Copy this pane's clean context — paste it into claude/codex/anything. */}
            <button
              type="button"
              onClick={copyContext}
              title="Copy this terminal's context — paste it into another terminal's AI CLI (claude, codex…) or anywhere else"
              className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-accent/15 hover:text-accent-soft"
            >
              {ctxCopied ? <Check size={12} className="text-emerald-300" /> : <ClipboardCopy size={12} />}
            </button>
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
                {(aiResult.skills ?? []).map((s) => (
                  <span
                    key={s}
                    title="Skill playbook applied to this answer"
                    className="inline-flex items-center rounded-full border border-accent/25 bg-accent/[0.08] px-1.5 py-0.5 text-[9px] font-medium text-accent-soft"
                  >
                    skill: {s}
                  </span>
                ))}
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
                  <Plug size={13} /> {lostLink ? "Connection lost" : "Session closed"}
                  {/* v1.226.0: a lost link (not an exited shell) gets a visible
                      lever instead of a dead end. */}
                  {lostLink && (
                    <button
                      type="button"
                      onClick={() => reconnectRef.current?.()}
                      className="pointer-events-auto ml-1 rounded-md border border-rose-400/40 px-2 py-0.5 text-[11px] font-medium text-rose-100 transition-colors hover:bg-rose-500/20"
                    >
                      Reconnect
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
        )}
        {/* Full-size look before you send it. */}
        {(() => {
          const shown = snips.find((s) => s.id === expandedSnip && s.url);
          if (!shown) return null;
          return (
            <div
              role="dialog"
              aria-label={`Snippet ${shown.name}`}
              onClick={(e) => {
                e.stopPropagation();
                setExpandedSnip(null);
              }}
              className="absolute inset-0 z-50 grid cursor-zoom-out place-items-center bg-black/85 p-3 backdrop-blur-sm"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={shown.url}
                alt={shown.name}
                className="max-h-full max-w-full rounded-lg border border-white/10 object-contain"
              />
            </div>
          );
        })()}
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
