// Build-chat pane engine — the PURE half (v1.206.0).
//
// PaneChat.tsx is the room; this module is everything about the room that can
// be tested without rendering it: the localStorage key scheme, the thread
// title, cwd→project grounding, the /chat/stream turn body, and the
// setup-snapshot merge that keeps autosaves from clobbering a stored setup.
//
// CONTRACTS COPIED (not the code) from app/chat/page.tsx:
//  - turn body: POST /chat/stream {messages, provider?, attachments?,
//    workspace_dir, project_id?, auto_tools} — messages are role+content only.
//  - thread save: PUT /chat/threads/{id|"new"} {messages, title?, setup?};
//    bubbles ride the wire VERBATIM (the daemon stores them as-is), so PaneMsg
//    keeps the big chat page's field names and a pane thread opened there
//    renders its receipts/doors unchanged.
//  - setup: a save must NEVER put empties over keys this pane does not manage
//    (tools/connectors/skill/documents/approval_mode) — mergeSetup carries the
//    stored snapshot forward and overwrites only workspace_dir/provider/model.

import type { TurnAdapted, TurnRoute } from "@/components/chat/TurnReceipt";
import type { Door } from "@/components/chat/DoorsStrip";
import type { ProviderHealth } from "@/lib/types";

// ------------------------------------------------------------------ messages

/** One bubble, wire-compatible with the chat page's ChatMessage (a subset:
 *  same names, same shapes — never invent a pane-only field, the thread is
 *  shared state the big chat page may open later). */
export interface PaneMsg {
  role: "user" | "assistant";
  content: string;
  /** Display names of files attached to this (user) message — footer chips. */
  attachmentNames?: string[];
  /** Uploaded paths of those attachments. */
  attachmentPaths?: string[];
  /** Registry tools the reply actually ran (assistant messages). */
  toolsUsed?: string[];
  /** Armed tools the engine refused this turn (assistant messages). */
  deniedTools?: string[];
  /** ABSOLUTE paths of files this turn created/edited. */
  documents?: string[];
  /** SERVER-side route disclosure — who actually answered (v1.165.0). */
  route?: TurnRoute;
  /** The capability envelope bent this turn (v1.202.0). */
  adapted?: TurnAdapted;
  /** SERVER-derived doors into surfaces this turn touched (v1.199.0). */
  doors?: Door[];
  /** Reply cut off mid-stream (error with a streamed partial) — marked so a
   *  partial answer never looks complete. */
  interrupted?: boolean;
}

// ------------------------------------------------------------ thread + title

export const PANE_THREAD_PREFIX = "ij.pane.thread.";

/** The localStorage slot holding this pane's thread id — one thread per pane. */
export function paneThreadKey(paneId: string): string {
  return `${PANE_THREAD_PREFIX}${paneId}`;
}

/** Last path segment — both separators (the daemon and the pane both speak
 *  Windows paths; the tests speak slashes). Falls back to the raw string. */
export function paneBasename(p: string): string {
  const parts = p.split(/[/\\]/).filter((s) => s.length > 0);
  return parts.length > 0 ? parts[parts.length - 1] : p;
}

/** The thread's name: "Build: <folder>". Sent once, on CREATE only — a later
 *  rename by the user (in the chat page's sidebar) must not be clobbered. */
export function paneTitle(cwd: string): string {
  return `Build: ${paneBasename(cwd) || cwd}`;
}

// --------------------------------------------------------- project grounding

export interface PaneProjectOption {
  id: string;
  name: string;
  root?: string;
}

/** Normalise a directory for prefix comparison: forward slashes, no trailing
 *  separator, lowercased — Windows paths are case-insensitive and the panes
 *  run on win32 (a posix false-positive here costs a project CHIP, not data). */
function normDir(p: string): string {
  return p.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

/**
 * The project whose root contains `cwd` (path-prefix at a segment boundary —
 * "C:\work" must not claim "C:\workshop"), or null. The MOST SPECIFIC root
 * wins when projects nest.
 */
export function projectForCwd(
  cwd: string,
  projects: readonly PaneProjectOption[],
): PaneProjectOption | null {
  const target = normDir(cwd);
  if (!target) return null;
  let best: PaneProjectOption | null = null;
  let bestLen = -1;
  for (const p of projects) {
    const root = typeof p.root === "string" ? normDir(p.root) : "";
    if (!root) continue;
    if (target !== root && !target.startsWith(`${root}/`)) continue;
    if (root.length > bestLen) {
      best = p;
      bestLen = root.length;
    }
  }
  return best;
}

// -------------------------------------------------------------- engine picker

export interface EngineOption {
  id: string;
  label: string;
}

/** Friendly names for the subscription CLIs; every other provider shows its id. */
const ENGINE_LABELS: Record<string, string> = {
  "claude-cli": "Claude (your login)",
  "codex-cli": "Codex (your login)",
};

export function engineLabel(provider: string): string {
  return ENGINE_LABELS[provider] ?? provider;
}

/** The compact picker's rows: AVAILABLE providers from /health, deduped in
 *  daemon order. "Default" (omit provider) is the caller's own first row. */
export function engineOptions(
  providers: readonly ProviderHealth[] | null | undefined,
): EngineOption[] {
  const out: EngineOption[] = [];
  const seen = new Set<string>();
  for (const p of providers ?? []) {
    if (!p || !p.available || !p.provider || seen.has(p.provider)) continue;
    seen.add(p.provider);
    out.push({ id: p.provider, label: engineLabel(p.provider) });
  }
  return out;
}

// ----------------------------------------------------------------- turn body

/** What one pane turn POSTs to /chat/stream (the chat page's ChatRequestBody
 *  contract, narrowed to what a Build pane sends). A type alias so it stays
 *  assignable to the stream hook's `run(body: unknown)`. */
export type PaneTurnBody = {
  messages: { role: "user" | "assistant"; content: string }[];
  provider?: string;
  model?: string;
  attachments?: string[];
  tools?: string[];
  workspace_dir: string;
  project_id?: string;
  auto_tools: true;
  approval_mode?: string;
};

/** The /chat contract's tool cap (the server truncates at six). */
export const PANE_MAX_TOOLS = 6;

export interface PaneTurnArgs {
  history: readonly PaneMsg[];
  cwd: string;
  /** "" = Default (omit provider — the daemon routes). */
  provider?: string;
  /** The thread's pinned model, riding WITH the provider — a pinned
   *  lmstudio::qwen-14b thread must not silently run the default model while
   *  the setup keeps claiming the pin (BC1 D5). "" = no pin, omit. */
  model?: string;
  /** Uploaded document paths riding this turn. */
  attachments?: readonly string[];
  /** Tools armed for this conversation (approval-card grants + the thread's
   *  stored armed set) — riding them is what makes "Allow for this
   *  conversation" actually stop asking here (BC1 D1). */
  tools?: readonly string[];
  projectId?: string | null;
  /** The thread's stored approval posture. Absent/default = omit — the
   *  daemon's default posture must not be overridden by an empty echo; a
   *  stored always_ask/yolo MUST ride or the pane silently downgrades the
   *  consent the user set in /chat (BC1 D4). */
  approvalMode?: string;
}

export function buildTurnBody(a: PaneTurnArgs): PaneTurnBody {
  return {
    // Full conversation every turn — the backend is stateless here.
    messages: a.history.map(({ role, content }) => ({ role, content })),
    ...(a.provider ? { provider: a.provider } : {}),
    ...(a.model ? { model: a.model } : {}),
    ...(a.attachments && a.attachments.length
      ? { attachments: [...a.attachments] }
      : {}),
    ...(a.tools && a.tools.length
      ? { tools: [...a.tools].slice(0, PANE_MAX_TOOLS) }
      : {}),
    // The pane's whole point: every turn is grounded in the pane's folder.
    workspace_dir: a.cwd,
    // Context spine: only when the cwd sits under a project root.
    ...(a.projectId ? { project_id: a.projectId } : {}),
    // Seamless arming — the daemon fills safe tool slots from the request.
    auto_tools: true,
    // Only a NON-default posture rides (the big page's contract: the daemon
    // stores nothing for the default, and echoing it adds nothing).
    ...(a.approvalMode && a.approvalMode !== "approve_for_me"
      ? { approval_mode: a.approvalMode }
      : {}),
  };
}

/** Union the pane's own approval-card grants into an armed-tool list, deduped
 *  and capped at the server's six — stored tools first (they were armed
 *  first), so a grant never silently pushes a stored tool off the end unless
 *  the cap forces it. */
export function unionTools(
  stored: readonly string[] | undefined,
  grants: readonly string[],
): string[] {
  return [...new Set([...(stored ?? []), ...grants])].slice(0, PANE_MAX_TOOLS);
}

// -------------------------------------------------------------- setup merge

/** The daemon's per-thread setup snapshot (the chat page's ThreadSetup shape).
 *  The pane manages three keys; everything else is somebody else's state. */
export interface PaneThreadSetup {
  tools?: string[];
  connectors?: string[];
  documents?: string[];
  skill?: string;
  workspace_dir?: string;
  provider?: string;
  model?: string;
  approval_mode?: string;
}

/**
 * The setup snapshot a pane save carries. THE GUARD: `base` is the setup the
 * thread GET returned (or null on a fresh thread) and is spread first, so keys
 * this pane does not manage — armed tools, connectors, skill, documents,
 * approval posture — ride forward VERBATIM instead of being clobbered with
 * empties. The pane then overwrites only its own three keys:
 *  - workspace_dir: always this pane's cwd (the binding IS the feature);
 *  - provider: the picker's current value ("" = Default, deliberately);
 *  - model: kept from the stored setup while the provider is unchanged,
 *    cleared when the user picked a DIFFERENT provider (a stale model id
 *    against a new provider is a routing error waiting to happen).
 */
export function mergeSetup(
  base: PaneThreadSetup | null | undefined,
  cwd: string,
  provider: string,
): PaneThreadSetup {
  const b = base ?? {};
  const storedProvider = b.provider ?? "";
  return {
    ...b,
    workspace_dir: cwd,
    provider,
    model: provider === storedProvider ? (b.model ?? "") : "",
  };
}

// -------------------------------------------------------------- file reading

/** File → bare base64 (no data: prefix) — the /documents/upload contract. */
export function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("could not read file"));
    reader.onload = () => {
      const res = String(reader.result);
      const comma = res.indexOf(",");
      resolve(comma >= 0 ? res.slice(comma + 1) : res);
    };
    reader.readAsDataURL(file);
  });
}

// Attachment limits — the chat page's numbers, kept in step by value.
export const PANE_MAX_ATTACHMENTS = 4;
export const PANE_MAX_FILE_BYTES = 20 * 1024 * 1024; // 20 MB
