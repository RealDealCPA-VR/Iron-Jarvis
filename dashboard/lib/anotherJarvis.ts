/**
 * Another computer running Iron Jarvis (v1.357.0, G61) — the words and the
 * shape of THIS computer's task door, in one place.
 *
 * The daemon side is `agents/task_door.py` (GET /agents/task-door, POST
 * /agents/task-door/enable|disable, the door itself at TASK_DOOR_PATH).
 * Another computer registers this one through its OWN remote-agent form
 * (http-task, this address, this token) and "@desk do X" in its chat runs
 * here; the answer goes back to the chat it came from.
 *
 * Kept OUT of lib/api.ts on purpose (~71 test files mock that module
 * wholesale): pure helpers only, no fetches here.
 */

/** The door's leaf — `auth.TASK_DOOR_PATH` on the daemon, pinned equal. */
export const TASK_DOOR_PATH = "/agents/task-door/task";

/** What the daemon says about the door (never the token). */
export interface TaskDoorStatus {
  enabled: boolean;
  /** What this computer calls the one that sends it tasks. */
  caller: string;
  /** The address to give the other computer. */
  url: string;
  enabled_at?: string | null;
  last_task_at?: string | null;
  last_reply?: { at?: string; ok?: boolean; detail?: string } | null;
  /** The reachability verdict — the same sentence the remote inbound door
   *  gives. Null while the door is off. */
  reachable?: { host_allowed?: boolean; note?: string } | null;
  /** Only on the answer that turned the door on: shown ONCE. */
  token?: string;
}

/** A defensive read of the daemon's answer; junk becomes "off". */
export function decodeDoor(raw: unknown): TaskDoorStatus {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const reach = r.reachable && typeof r.reachable === "object" ? (r.reachable as Record<string, unknown>) : null;
  const last = r.last_reply && typeof r.last_reply === "object" ? (r.last_reply as Record<string, unknown>) : null;
  return {
    enabled: r.enabled === true,
    caller: typeof r.caller === "string" ? r.caller : "",
    url: typeof r.url === "string" ? r.url : "",
    enabled_at: typeof r.enabled_at === "string" ? r.enabled_at : null,
    last_task_at: typeof r.last_task_at === "string" ? r.last_task_at : null,
    last_reply: last
      ? {
          at: typeof last.at === "string" ? last.at : undefined,
          ok: last.ok === true,
          detail: typeof last.detail === "string" ? last.detail : "",
        }
      : null,
    reachable: reach
      ? {
          host_allowed: reach.host_allowed === true,
          note: typeof reach.note === "string" ? reach.note : "",
        }
      : null,
    token: typeof r.token === "string" && r.token ? r.token : undefined,
  };
}

/** v1.358.0 (G61): the setting that lets another computer reach this one —
 *  `lan_access` in settings/schema.py (the Config field, desktop/main.js
 *  LAN_ACCESS_KEY and routes/agents.LAN_ACCESS_KEY, pinned equal). The
 *  daemon's reachability sentence names it in words; the card offers the one
 *  press that opens it (Settings reads `?focus=` on mount and brings the row
 *  into view). */
export const LAN_SETTING_KEY = "lan_access";
export const LAN_SETTING_HREF = `/settings?focus=${LAN_SETTING_KEY}`;
export const LAN_SETTING_LINK = "Open the setting";

/** Said quietly when Iron Jarvis ACCEPTS requests at the address shown (the
 *  daemon's Host-guard verdict). It cannot see Windows Firewall, so it never
 *  claims more than that, and says where to look if one still cannot get
 *  through (v1.358.0 review). */
export const REACHABLE_LINE =
  "Iron Jarvis accepts requests at this address from your network. If another computer still cannot reach it, allow Iron Jarvis in Windows Firewall.";

/** The card's title — the Agents › Your team tile. */
export const CARD_TITLE = "Another computer running Jarvis";

/** What the door is, said once while it is off. */
export const OFF_EXPLAINER =
  "Let another computer running Iron Jarvis hand this one tasks. Turn it on here, " +
  "then add this computer on the other one as a remote agent with the address and " +
  "token shown below. Its tasks run here on their own: anything that would need " +
  "someone's OK at this computer is not done, and each answer goes back to the chat " +
  "it came from.";

/** The name field's label and its example. */
export const CALLER_LABEL = "What do you call the other computer?";
export const CALLER_PLACEHOLDER = "laptop";

/** The one primary while off. */
export const TURN_ON = "Turn on";

/** The token box's headline (the token is never shown again). */
export const TOKEN_ONCE = "Copy the token. It is shown once.";

/** Where the address and token go, on the OTHER computer. */
export function addThisComputerSteps(caller: string): string {
  const who = caller.trim() || "the other computer";
  return (
    `On ${who}: Agents, Your team, New & manage, Connect a remote agent. Give this computer a name ` +
    // v1.358.0 (integrate, G61): the form picks the kind for a door address.
    "(for example desk), paste this address as the Base URL (the task API, http-task, is " +
    "picked for you) and the token as the secret. Then “@desk” in its chat hands this " +
    "computer the task, and the answer comes back to that chat."
  );
}

/** The quiet status line while on. */
export function onLine(status: TaskDoorStatus): string {
  const who = status.caller.trim() || "another computer";
  return `On. Tasks from ${who} run here.`;
}

/** What happened last, in words (empty when nothing has arrived yet). */
export function lastLine(status: TaskDoorStatus, ago: (iso: string) => string): string {
  if (!status.last_task_at) return "No task has arrived yet.";
  const parts = [`Last task ${ago(status.last_task_at)}.`];
  const reply = status.last_reply;
  if (reply && reply.ok) parts.push("Its answer was sent back.");
  return parts.join(" ");
}

/** A failed answer, named (the daemon's own plain reason). Empty when the
 *  last answer arrived, or none was attempted. */
export function replyFailedLine(status: TaskDoorStatus): string {
  const reply = status.last_reply;
  if (!reply || reply.ok) return "";
  const why = (reply.detail || "").trim();
  return why ? `The last answer could not be sent back: ${why}.` : "The last answer could not be sent back.";
}
