// A stored cron read BACK into the schedule editor's choices (v1.449.0, Wave D
// item 35). Cron stays the stored form; the user never has to read one. A
// pattern this reader cannot name is "Custom schedule (advanced)" and the
// editor shows the string in a fold.
//
// Pure: no React, no fetch. The five fields are minute, hour, day-of-month,
// month, day-of-week; this reads the shapes a person means by "repeat":
//   * * * * *        every minute
//   */N * * * *      every N minutes
//   M * * * *        hourly, at minute M
//   M H * * *        daily at H:M
//   M H * * D[,D]    weekly on those days at H:M (ranges like 1-5 too)
//   M H D * *        monthly on day D at H:M
// Anything else (a month field, a day range in the month, a list of hours,
// names like MON) is not read — never guessed.

export type RepeatKind = "minute" | "minutes" | "hourly" | "daily" | "weekly" | "monthly";

export interface CronPlan {
  kind: RepeatKind;
  /** "minutes": every N minutes (2..59). */
  everyMinutes: number;
  /** The minute of the hour (0..59); hourly/daily/weekly/monthly. */
  minute: number;
  /** The hour (0..23); daily/weekly/monthly. */
  hour: number;
  /** Weekly: the days, 0 = Sunday … 6 = Saturday, ascending, never empty. */
  days: number[];
  /** Monthly: the day of the month (1..31). */
  dayOfMonth: number;
}

/** Short day words by cron number (0 = Sunday; cron's 7 is read as 0). */
export const DAY_WORDS: readonly string[] = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** The order the editor shows the day boxes in (Monday first). */
export const DAY_ORDER: readonly number[] = [1, 2, 3, 4, 5, 6, 0];

/** The repeat kinds in the order the editor offers them. "Every minute" is
 *  LAST on purpose (the add form's presets end on it too, with a warning). */
export const REPEAT_KINDS: readonly { kind: RepeatKind; label: string }[] = [
  { kind: "minutes", label: "Every few minutes" },
  { kind: "hourly", label: "Hourly" },
  { kind: "daily", label: "Daily" },
  { kind: "weekly", label: "Weekly" },
  { kind: "monthly", label: "Monthly" },
  { kind: "minute", label: "Every minute" },
];

/** The editor's "Repeat" option for a cron this reader cannot name. */
export const CUSTOM_SCHEDULE_WORDS = "Custom schedule (advanced)";

const WEEKDAYS = [1, 2, 3, 4, 5];

function int(field: string, lo: number, hi: number): number | null {
  if (!/^\d{1,2}$/.test(field)) return null;
  const n = Number(field);
  return n >= lo && n <= hi ? n : null;
}

/** A day-of-week field: lists and ranges of 0..7 (7 = Sunday), or null. */
function daysOf(field: string): number[] | null {
  const out = new Set<number>();
  for (const piece of field.split(",")) {
    const m = /^(\d)(?:-(\d))?$/.exec(piece);
    if (!m) return null;
    const a = Number(m[1]);
    const b = m[2] === undefined ? a : Number(m[2]);
    if (a > 7 || b > 7 || b < a) return null;
    for (let d = a; d <= b; d++) out.add(d === 7 ? 0 : d);
  }
  if (out.size === 0) return null;
  return Array.from(out).sort((x, y) => x - y);
}

/** Read a stored cron into the editor's choices, or null when it cannot be
 *  named (the editor then shows "Custom schedule (advanced)"). */
export function parseCron(cron: string): CronPlan | null {
  const parts = (cron ?? "").trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const [m, h, dom, mon, dow] = parts;
  if (mon !== "*") return null;
  const base: CronPlan = { kind: "daily", everyMinutes: 15, minute: 0, hour: 9, days: WEEKDAYS, dayOfMonth: 1 };
  if (m === "*" && h === "*" && dom === "*" && dow === "*") return { ...base, kind: "minute" };
  const every = /^\*\/(\d{1,2})$/.exec(m);
  if (every && h === "*" && dom === "*" && dow === "*") {
    const n = Number(every[1]);
    return n >= 2 && n <= 59 ? { ...base, kind: "minutes", everyMinutes: n } : null;
  }
  const minute = int(m, 0, 59);
  if (minute === null) return null;
  if (h === "*" && dom === "*" && dow === "*") return { ...base, kind: "hourly", minute };
  const hour = int(h, 0, 23);
  if (hour === null) return null;
  if (dom === "*" && dow === "*") return { ...base, kind: "daily", minute, hour };
  if (dom === "*") {
    const days = daysOf(dow);
    if (!days) return null;
    // Every day of the week IS daily; said so, never as a seven-box week.
    if (days.length === 7) return { ...base, kind: "daily", minute, hour };
    return { ...base, kind: "weekly", minute, hour, days };
  }
  if (dow === "*") {
    const day = int(dom, 1, 31);
    return day === null ? null : { ...base, kind: "monthly", minute, hour, dayOfMonth: day };
  }
  return null;
}

/** The cron the editor's choices mean. The inverse of `parseCron` for every
 *  plan it reads (pinned). */
export function buildCron(plan: CronPlan): string {
  const m = String(plan.minute);
  const h = String(plan.hour);
  switch (plan.kind) {
    case "minute":
      return "* * * * *";
    case "minutes":
      return `*/${plan.everyMinutes} * * * *`;
    case "hourly":
      return `${m} * * * *`;
    case "daily":
      return `${m} ${h} * * *`;
    case "weekly":
      return `${m} ${h} * * ${daysField(plan.days)}`;
    case "monthly":
      return `${m} ${h} ${plan.dayOfMonth} * *`;
  }
}

/** Days as cron writes them: consecutive runs become ranges (1-5), the rest a
 *  list (1,3,5); Sunday is 0. */
function daysField(days: number[]): string {
  const sorted = Array.from(new Set(days)).sort((a, b) => a - b);
  const pieces: string[] = [];
  let i = 0;
  while (i < sorted.length) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++;
    pieces.push(j - i >= 2 ? `${sorted[i]}-${sorted[j]}` : sorted.slice(i, j + 1).join(","));
    i = j + 1;
  }
  return pieces.join(",");
}

/** "08:00" for the time box. */
export function timeValue(plan: CronPlan): string {
  return `${String(plan.hour).padStart(2, "0")}:${String(plan.minute).padStart(2, "0")}`;
}

/** The hour and minute a time box holds ("08:05" → {8, 5}), or null. */
export function readTime(value: string): { hour: number; minute: number } | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec((value ?? "").trim());
  if (!m) return null;
  const hour = Number(m[1]);
  const minute = Number(m[2]);
  if (hour > 23 || minute > 59) return null;
  return { hour, minute };
}

function clock(plan: CronPlan): string {
  const h12 = plan.hour % 12 === 0 ? 12 : plan.hour % 12;
  return `${h12}:${String(plan.minute).padStart(2, "0")} ${plan.hour < 12 ? "AM" : "PM"}`;
}

/** The plan in one plain phrase ("Weekdays at 8:00 AM", "Every 15 minutes"). */
export function planWords(plan: CronPlan): string {
  switch (plan.kind) {
    case "minute":
      return "Every minute";
    case "minutes":
      return `Every ${plan.everyMinutes} minutes`;
    case "hourly":
      return plan.minute === 0 ? "Every hour" : `Hourly at :${String(plan.minute).padStart(2, "0")}`;
    case "daily":
      return `Daily at ${clock(plan)}`;
    case "weekly": {
      const isWeekdays = plan.days.length === 5 && WEEKDAYS.every((d) => plan.days.includes(d));
      const who = isWeekdays ? "Weekdays" : plan.days.map((d) => DAY_WORDS[d]).join(", ");
      return `${who} at ${clock(plan)}`;
    }
    case "monthly":
      return `Monthly on the ${ordinal(plan.dayOfMonth)} at ${clock(plan)}`;
  }
}

function ordinal(n: number): string {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return `${n}${s[(v - 20) % 10] ?? s[v] ?? s[0]}`;
}
