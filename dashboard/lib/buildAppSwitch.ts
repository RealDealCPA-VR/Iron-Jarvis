/**
 * v1.545.0 — Build › Code: one session goes on with ANOTHER coding tool
 * (`POST /build/app/sessions/{id}/switch-tool`; hosted/tool_switch.py).
 *
 * The switch is two presses. The first asks (`confirm: false`): the daemon
 * answers a PLAN and changes nothing; its `lines` are sentences it wrote for
 * the user to read (the account, the mode, where the summary goes, the
 * programs that stop) and are shown exactly as given. The second
 * (`confirm: true`, the same tool, model and effort) switches: the answer is
 * the session's new row and the one line now in the conversation.
 *
 * The new tool is given a summary of the conversation with the user's NEXT
 * message, never as a turn of its own:
 * - the view's `handoff_pending` says one waits (`decodeHandoffPending`);
 * - the conversation's `handoff` event (lib/buildApp) is the exact text the
 *   tool was given, drawn folded under the message it rode.
 *
 * Both presses also carry what this user would START a new session of that
 * tool with in this folder (`prefer_model`, `prefer_effort`:
 * `switchPreference`). The daemon uses it only when no model or effort was
 * picked and the tool does not carry on its own earlier conversation.
 *
 * The second press does exactly what the first press showed: every plan
 * carries a `token`, sent back as `expect` with `confirm: true`. If the switch
 * would now do something else the daemon refuses (`code: "changed"`), changes
 * nothing, and the screen asks again by itself so the new lines are read
 * before a fresh press.
 *
 * The view's `switches_tool` (lib/buildApp `switchesTool`) says whether the
 * switch is offered at all: an older Iron Jarvis says nothing, a helper
 * session says no, and the tool then stays fixed as before.
 */

import { post } from "@/lib/api";
import {
  decodeCodeEvent,
  decodeCodeSession,
  effortsFor,
  toolLabel,
  type CodeEvent,
  type CodeSession,
  type CodingTool,
  type ConvItem,
} from "@/lib/buildApp";
import type { ToolDefaults } from "@/lib/folderDefaults";

// ── the words ───────────────────────────────────────────────────────────────

export function switchHeading(label: string): string {
  return `Switch to ${label}?`;
}
export function switchPress(label: string): string {
  return `Switch to ${label}`;
}
export const SWITCH_CANCEL = "Cancel";
/** The one press under a refusal (there is nothing to cancel). */
export const SWITCH_CLOSE = "Close";
/** The heading over the daemon's own sentence when the first press was
 *  refused (a common, ordinary answer: the mode must change first, the tool
 *  is not signed in, a reply runs). Nothing was changed. */
export function switchRefusedHeading(label: string): string {
  return `Not switched to ${label}`;
}
export const SWITCH_CHECKING = "Checking what this switch would change…";
export const SWITCHING = "Switching…";
/** Said when a refusal carried no sentence of its own. */
export const SWITCH_FAILED = "The coding tool could not be switched. Nothing was changed.";
/** Said above the new lines when the daemon's "changed" refusal carried no
 *  sentence of its own. */
export const SWITCH_CHANGED =
  "What this switch would do has changed since you looked, so nothing was changed. Look again, then switch.";
/** Said when the answer to the first press could not be read. */
export const SWITCH_PLAN_UNREAD = "Iron Jarvis's answer about this switch could not be read. Nothing was changed.";
/** Said when the answer to the second press could not be read (the session is
 *  read again, so the screen shows how it stands). */
export const SWITCH_ANSWER_UNREAD =
  "Iron Jarvis's answer to the switch could not be read. This session was read again and shows how it stands now.";
/** Said when the second press got NO answer the daemon wrote (the connection
 *  dropped, Iron Jarvis hit a problem): the switch may have gone through all
 *  the same, so nothing is claimed and the session is read again. */
export const SWITCH_ANSWER_LOST =
  "The answer to the switch did not arrive, so it may or may not have gone through. This session was read again and shows how it stands now.";
/** After a second press was refused because the plan changed, the press that
 *  switches does nothing for this long once the new lines are shown (a
 *  double-click must not confirm what was not read; the mode chip's
 *  FULL_ARM_GUARD_MS is the same idea). */
export const SWITCH_REARM_GUARD_MS = 400;

/** The limit card's press. It opens the session's list of coding tools; it
 *  switches nothing by itself. */
export const CONTINUE_ON_ANOTHER_TOOL = "Continue on another tool";
export const CONTINUE_ON_ANOTHER_TOOL_TITLE =
  "Choose another coding tool for this session. Nothing changes until you confirm.";

/** The folded row under the message a summary rode. */
export function handoffRowWords(toLabel: string, resumes: boolean): string {
  return resumes ? `Summary of what happened since, given to ${toLabel}` : `Summary given to ${toLabel}`;
}
/** The quiet line above the message box while a summary waits. */
export function handoffWaitingWords(toLabel: string, resumes: boolean): string {
  return resumes
    ? `${toLabel} gets a summary of what happened since with your next message.`
    : `${toLabel} gets a summary of this conversation with your next message.`;
}
export const HANDOFF_COPY = "Copy";
export const HANDOFF_COPIED = "Copied";
export const HANDOFF_COPY_FAILED = "Couldn't copy";
export const HANDOFF_ROW_TITLE = "The exact text the coding tool was given with this message";

// ── the plan ────────────────────────────────────────────────────────────────

/** How many of the daemon's sentences the confirmation shows, and how long
 *  one may be. */
export const SWITCH_LINES_MAX = 12;
export const SWITCH_LINE_CHARS = 400;
export const SWITCH_STOPS_MAX = 20;

export interface SwitchPlan {
  tool: string;
  label: string;
  from: string;
  fromLabel: string;
  account: string;
  accountName: string;
  mode: string;
  modeLabel: string;
  model: string;
  effort: string;
  /** The tool carries on its own earlier conversation (a switch back). */
  resumes: boolean;
  /** The summary is sent to the tool's own service (false = not known). */
  cloud: boolean;
  /** There is something to hand over (false = the daemon found nothing: its
   *  own line says so). True unless the daemon said false. */
  summary: boolean;
  /** The daemon's own sentences, shown one per line exactly as given. */
  lines: string[];
  /** The programs that stop with the old tool (named in `lines` too). */
  stops: string[];
  /** Names exactly what this plan would do: sent back with the second press
   *  (`expect`), so it does what was shown or nothing ("" = none given). */
  token: string;
}

/** How long a plan's token may be (a longer one is not a token: none). */
export const SWITCH_TOKEN_CHARS = 64;

function str(v: unknown, cap: number): string {
  return typeof v === "string" ? v.slice(0, cap) : "";
}

function obj(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function sentences(v: unknown, most: number, chars: number): string[] {
  if (!Array.isArray(v)) return [];
  const out: string[] = [];
  for (const x of v) {
    if (typeof x !== "string") continue;
    const line = x.trim().slice(0, chars);
    if (line) out.push(line);
    if (out.length >= most) break;
  }
  return out;
}

/** A plan by whitelist; null when it does not name the tool it is for, or
 *  when it carries a token this screen cannot send back as it is (the second
 *  press would then go out unguarded: such a plan is not confirmed at all).
 *  A plan with NO token (an older Iron Jarvis) is a plan: it switches without
 *  `expect`, as before. */
export function decodeSwitchPlan(raw: unknown): SwitchPlan | null {
  const r = obj(raw);
  const tool = r ? str(r.tool, 64).trim() : "";
  if (!r || !tool) return null;
  const given = r.token !== undefined && r.token !== null;
  const token = typeof r.token === "string" && r.token.length <= SWITCH_TOKEN_CHARS ? r.token : "";
  if (given && !token) return null;
  const from = str(r.from, 64).trim();
  return {
    tool,
    label: str(r.label, 80).trim() || toolLabel(tool),
    from,
    fromLabel: str(r.from_label, 80).trim() || (from ? toolLabel(from) : ""),
    account: str(r.account, 200),
    accountName: str(r.account_name, 200),
    mode: str(r.mode, 64),
    modeLabel: str(r.mode_label, 120),
    model: str(r.model, 200),
    effort: str(r.effort, 40),
    resumes: r.resumes === true,
    cloud: r.cloud === true,
    summary: r.summary !== false,
    lines: sentences(r.lines, SWITCH_LINES_MAX, SWITCH_LINE_CHARS),
    stops: sentences(r.stops, SWITCH_STOPS_MAX, 200),
    token,
  };
}

/** Is this a REFUSAL the daemon wrote (a 4xx with a body of its own: nothing
 *  was changed)? Anything else that ends a second press — no answer at all, a
 *  dropped connection, a problem inside Iron Jarvis (5xx) — says nothing
 *  about whether the switch went through. */
export function isSwitchRefusal(e: unknown): boolean {
  const o = obj(e);
  const status = o ? o.status : null;
  return typeof status === "number" && status >= 400 && status < 500 && !!obj(o?.body);
}

/** Is this the daemon's refusal of a second press whose plan no longer holds
 *  (409 `{code: "changed"}`: nothing was changed)? Read off the refusal's own
 *  body (lib/api ApiError `body`). */
export function isChangedRefusal(e: unknown): boolean {
  const body = obj(obj(e)?.body);
  return !!body && body.code === "changed";
}

/** What either press answers. `switched` is true ONLY when the daemon said
 *  so; `session` is the session's new row then (null when it could not be
 *  read: the caller reads the session again). */
export interface SwitchAnswer {
  switched: boolean;
  plan: SwitchPlan | null;
  session: CodeSession | null;
  note: string;
  /** The line the switch wrote in the conversation (the very event), so it
   *  is shown at once; null when the answer carries none. */
  notice: Extract<CodeEvent, { type: "notice" }> | null;
}

/** Null for anything that is neither a plan nor a switch. */
export function decodeSwitchAnswer(raw: unknown): SwitchAnswer | null {
  const r = obj(raw);
  if (!r) return null;
  const switched = r.switched === true;
  const plan = decodeSwitchPlan(r.plan);
  if (!switched && !plan) return null;
  return {
    switched,
    plan,
    session: switched ? decodeCodeSession(r.session) : null,
    note: str(r.note, 1_000).trim(),
    notice: switched ? switchNotice(r.notice) : null,
  };
}

/** Only the line a switch wrote (a `notice` with its `switch` mark). */
function switchNotice(raw: unknown): Extract<CodeEvent, { type: "notice" }> | null {
  const ev = decodeCodeEvent(raw);
  return ev && ev.type === "notice" && ev.switch ? ev : null;
}

// ── the call ────────────────────────────────────────────────────────────────

/** What is asked for: the tool, and the model and effort only when the user
 *  picked one ("" = the tool's own default, picked on purpose; absent = not
 *  picked, the daemon decides). */
export interface SwitchChoice {
  tool: string;
  model?: string;
  effort?: string;
}

/** (What the user would start with is no part of what they picked: it is not
 *  compared.) */
export function sameSwitchChoice(a: SwitchChoice, b: SwitchChoice): boolean {
  return a.tool === b.tool && a.model === b.model && a.effort === b.effort;
}

/** What this user would start a NEW session of the tool with in this folder
 *  (a field is absent when nothing is known: never ""). It rides both presses
 *  beside the choice and is never a pick: a model or effort the user picked
 *  wins, and a tool that carries on its own earlier conversation keeps that
 *  conversation's. */
export interface SwitchPreference {
  model?: string;
  effort?: string;
}

/** What a NEW session of `tool` starts with in a folder — the ONE rule, used
 *  by the start composer (StartComposer.tsx) and by a switch's preference
 *  below, so the two cannot drift: the folder's defaults when they are for
 *  THIS tool (its model while the tool still lists it; a folder default that
 *  names no usable model borrows none), else what the tool last started with
 *  on this PC (`last`: StartComposer's `lastPick(tool)`). The effort is what
 *  is WANTED: the caller keeps it only while the model in use takes it. */
export function toolStartWants(
  tool: Pick<CodingTool, "id" | "models">,
  folder: ToolDefaults | null,
  last: { model: string; effort: string },
): { model: string; effort: string } {
  const d = folder && folder.harness === tool.id ? folder : null;
  if (!d) return { model: last.model, effort: last.effort };
  return { model: d.model && tool.models.some((m) => m.id === d.model) ? d.model : "", effort: d.effort ?? "" };
}

/** A switch's preference: `toolStartWants`, with the effort kept only while
 *  that model takes it, and nothing said where nothing is known. */
export function switchPreference(
  tool: CodingTool | null,
  folder: ToolDefaults | null,
  last: { model: string; effort: string },
): SwitchPreference {
  if (!tool) return {};
  const want = toolStartWants(tool, folder, last);
  const effort = want.effort && effortsFor(tool, want.model).includes(want.effort) ? want.effort : "";
  return { ...(want.model ? { model: want.model } : {}), ...(effort ? { effort } : {}) };
}

/** The request's body: `confirm: false` asks, `confirm: true` switches.
 *  `expect` (the shown plan's token) rides ONLY the switch. */
export function switchBody(
  choice: SwitchChoice,
  confirm: boolean,
  prefer: SwitchPreference = {},
  expect = "",
): Record<string, unknown> {
  return {
    tool: choice.tool,
    ...(choice.model !== undefined ? { model: choice.model } : {}),
    ...(choice.effort !== undefined ? { effort: choice.effort } : {}),
    ...(prefer.model ? { prefer_model: prefer.model } : {}),
    ...(prefer.effort ? { prefer_effort: prefer.effort } : {}),
    confirm,
    ...(confirm && expect ? { expect } : {}),
  };
}

/** Ask (`confirm: false`: nothing changes) or switch (`confirm: true`). A
 *  refusal rejects with the daemon's own sentence (lib/api ApiError). */
export function switchTool(
  sessionId: string,
  choice: SwitchChoice,
  confirm: boolean,
  prefer: SwitchPreference = {},
  expect = "",
): Promise<unknown> {
  const id = encodeURIComponent(sessionId);
  return post<unknown>(`/build/app/sessions/${id}/switch-tool`, switchBody(choice, confirm, prefer, expect));
}

// ── the summary that waits ──────────────────────────────────────────────────

export interface HandoffPending {
  to: string;
  toLabel: string;
  from: string;
  fromLabel: string;
  resumes: boolean;
}

/** The view's `handoff_pending` (null = none waits, or an older Iron Jarvis). */
export function decodeHandoffPending(raw: unknown): HandoffPending | null {
  const p = obj(obj(raw)?.handoff_pending);
  const to = p ? str(p.to, 64).trim() : "";
  if (!p || !to) return null;
  const from = str(p.from, 64).trim();
  return {
    to,
    toLabel: str(p.to_label, 80).trim() || toolLabel(to),
    from,
    fromLabel: str(p.from_label, 80).trim() || (from ? toolLabel(from) : ""),
    resumes: p.resumes === true,
  };
}

/** What waits right after a switch, read off its plan (the view says the same
 *  on its next read): the summary for the tool switched to, or nothing when
 *  the daemon found nothing to hand over. */
export function pendingFromPlan(plan: SwitchPlan | null): HandoffPending | null {
  if (!plan || !plan.summary) return null;
  return { to: plan.tool, toLabel: plan.label, from: plan.from, fromLabel: plan.fromLabel, resumes: plan.resumes };
}

export function sameHandoffPending(a: HandoffPending | null, b: HandoffPending | null): boolean {
  if (!a || !b) return a === b;
  return a.to === b.to && a.toLabel === b.toLabel && a.from === b.from && a.fromLabel === b.fromLabel && a.resumes === b.resumes;
}

// ── which tool wrote a row ──────────────────────────────────────────────────

/** The coding tool a row names ("" = none, or the record does not say: the
 *  session's tool). */
export function rowToolOf(it: ConvItem): string {
  if (it.kind === "done") return it.receipt?.tool ?? "";
  if (it.kind === "user" || it.kind === "steer") return it.tool ?? "";
  return "";
}

/** Did more than one coding tool write this conversation? True once a switch
 *  line or a summary row is on screen, or a row names a tool that is not the
 *  session's. Each reply's line then names its own tool; a conversation one
 *  tool wrote reads exactly as it always did. */
export function severalToolsWrote(items: readonly ConvItem[], sessionTool: string): boolean {
  for (const it of items) {
    if (it.kind === "handoff" || (it.kind === "notice" && it.switch)) return true;
    const t = rowToolOf(it);
    if (t && sessionTool && t !== sessionTool) return true;
  }
  return false;
}
