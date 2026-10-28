/**
 * How hands-off Jarvis is (v1.402.0, the autonomy metrics) — the words for
 * `GET /autonomy/metrics` (`eval/autonomy.py` on the daemon is the one
 * definition of a task, of success and of "avoidable"). Pure: decode the
 * answer defensively, then say the user's three numbers in plain words.
 */

export interface AutonomyMetrics {
  days: number;
  tasks: number;
  successful: number;
  hands_off: number;
  hands_off_pct: number;
  interruptions: {
    total: number;
    avoidable: number;
    per_task: number;
    avoidable_per_task: number;
  };
  nudges: {
    human: number;
    per_successful_task: number;
    system: { total: number; by_kind: Record<string, number> };
  };
  error?: string;
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
}

function obj(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

/** The answer, every number checked (an odd value reads as 0); null for a
 *  body that is not the metrics at all. */
export function decodeMetrics(raw: unknown): AutonomyMetrics | null {
  const r = obj(raw);
  if (typeof r.tasks !== "number") return null;
  const i = obj(r.interruptions);
  const n = obj(r.nudges);
  const sys = obj(n.system);
  const byKind: Record<string, number> = {};
  for (const [k, v] of Object.entries(obj(sys.by_kind))) {
    if (num(v) > 0) byKind[k] = Math.floor(num(v));
  }
  return {
    days: num(r.days) || 30,
    tasks: Math.floor(num(r.tasks)),
    successful: Math.floor(num(r.successful)),
    hands_off: Math.floor(num(r.hands_off)),
    hands_off_pct: num(r.hands_off_pct),
    interruptions: {
      total: Math.floor(num(i.total)),
      avoidable: Math.floor(num(i.avoidable)),
      per_task: num(i.per_task),
      avoidable_per_task: num(i.avoidable_per_task),
    },
    nudges: {
      human: Math.floor(num(n.human)),
      per_successful_task: num(n.per_successful_task),
      system: { total: Math.floor(num(sys.total)), by_kind: byKind },
    },
    ...(typeof r.error === "string" && r.error ? { error: r.error } : {}),
  };
}

/** "1.4", "2", "0.3" — one decimal, none when it is a whole number. */
export function amount(n: number): string {
  const r = Math.round(n * 10) / 10;
  return Number.isInteger(r) ? String(r) : r.toFixed(1);
}

function times(n: number): string {
  return `${amount(n)} ${Math.round(n * 10) / 10 === 1 ? "time" : "times"}`;
}

/** Metric 1: how often it stopped to ask. */
export function askedWords(m: AutonomyMetrics): string {
  if (m.interruptions.total === 0) return "Never stopped to ask you";
  const avoid =
    m.interruptions.avoidable === 0
      ? "none could have been avoided"
      : `${amount(m.interruptions.avoidable_per_task)} could have been avoided`;
  return `Asked you ${times(m.interruptions.per_task)} per task (${avoid})`;
}

/** Metric 2: how often the person had to push it on. */
export function nudgedWords(m: AutonomyMetrics): string {
  if (m.successful === 0) return "No finished tasks to count nudges on yet";
  if (m.nudges.per_successful_task === 0) return "You never had to nudge it on a finished task";
  return `You nudged it ${times(m.nudges.per_successful_task)} per finished task`;
}

/** Metric 3: the share finished with no help. */
export function handsOffWords(m: AutonomyMetrics): string {
  return `${amount(m.hands_off_pct)}% of tasks finished with no help from you`;
}

/** What each system nudge kind (`core/nudges.NUDGE_KINDS`) says. */
const SYSTEM_WORDS: Record<string, [string, string]> = {
  plan_run: ["approved plan ran by itself", "approved plans ran by themselves"],
  step_limit_continue: ["run carried on past its step budget", "runs carried on past their step budget"],
  rounds_extended: ["turn took more rounds by itself", "turns took more rounds by themselves"],
  closing_question: ["offer to do more it took up instead of asking", "offers to do more it took up instead of asking"],
};

/** The system's own nudges on one quiet line; "" when there were none. */
export function systemWords(m: AutonomyMetrics): string {
  const total = m.nudges.system.total;
  if (total <= 0) return "";
  const parts = Object.entries(m.nudges.system.by_kind)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([kind, n]) => {
      const words = SYSTEM_WORDS[kind];
      return words ? `${n} ${n === 1 ? words[0] : words[1]}` : `${n} other`;
    });
  return `Jarvis also moved work on by itself ${total === 1 ? "once" : `${total} times`}${
    parts.length ? ` (${parts.join(", ")})` : ""
  }.`;
}
