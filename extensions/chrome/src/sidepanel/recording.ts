// v1.458.0: "Record a task", the sidebar's half.
//
// Record starts recording in the tab the user is looking at (the worker and the
// page reader do the watching); a strip above the box says it is recording and
// how many steps it has, with Stop. Stop opens the REVIEW: the steps in words,
// each with a delete; every typed text or chosen option is "Ask each time"
// (the skill asks for a named value when it runs) unless the user types a
// fixed text there themselves; a password, payment or file field is always the
// user's to fill. Save sends the reviewed list to Iron Jarvis (`record_save`),
// which writes the skill; the answer comes back as the `recorded` event.
//
// Nothing here ever had a typed value to show: the page reader never reads
// one (content/recorder.ts). The only text a step carries out is what the user
// types into this review's own "Always type" box.

import { RECORDING_FIXED_TEXT_CHARS, RECORDING_NAME_CHARS } from "../protocol";

/** One step as the worker kept it. */
export interface RecordedStepIn {
  kind: string;
  url?: string;
  key?: string;
  sensitive?: boolean;
  target?: Record<string, unknown>;
}

/** One row of the review. */
export interface ReviewRow {
  step: RecordedStepIn;
  /** For a typed text or a chosen option: the value's name, and a fixed text or "". */
  placeholder: string;
  fixed: string;
}

function clip(value: unknown, n: number): string {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return text.length > n ? text.slice(0, n - 1) + "…" : text;
}

/** "button 'New invoice'", "textbox 'Customer'", "a link". */
export function elementWords(target: Record<string, unknown> | undefined): string {
  const t = target ?? {};
  const role = clip(t["role"], 30);
  const name = clip(t["name"], 60) || clip(t["text"], 60);
  const tag = clip(t["tag"], 20);
  const kind = role || (tag ? `${tag} element` : "element");
  return name ? `${kind} '${name}'` : `a ${kind}`;
}

/** Where an address points, without the scheme. */
export function shortUrl(url: unknown): string {
  try {
    const u = new URL(String(url ?? ""));
    return clip(u.host + (u.pathname === "/" ? "" : u.pathname), 80);
  } catch {
    return clip(url, 80);
  }
}

/** A step in plain words, as the review lists it. */
export function stepWords(step: RecordedStepIn): string {
  switch (step.kind) {
    case "navigate":
      return `Go to ${shortUrl(step.url)}`;
    case "arrive":
      return `Lands on ${shortUrl(step.url)}`;
    case "click":
      return `Click ${elementWords(step.target)}`;
    case "type":
      return step.sensitive
        ? `You type into ${elementWords(step.target)} yourself`
        : `Type into ${elementWords(step.target)}`;
    case "select":
      return `Choose in ${elementWords(step.target)}`;
    case "key":
      return step.target ? `Press ${step.key ?? ""} in ${elementWords(step.target)}` : `Press ${step.key ?? ""}`;
    case "file":
      return `You choose a file in ${elementWords(step.target)} yourself`;
    default:
      return step.kind;
  }
}

/** Whether a step carries a value the skill asks for (or a fixed text). */
export function takesValue(step: RecordedStepIn): boolean {
  return (step.kind === "type" && !step.sensitive) || step.kind === "select";
}

/** A value's name from the field's own name: "Customer name" -> "customer_name". */
export function placeholderFor(target: Record<string, unknown> | undefined, used: Set<string>): string {
  const t = target ?? {};
  let base = String(t["name"] || t["text"] || "value")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 30)
    .replace(/_+$/g, "");
  if (!base) {
    base = "value";
  }
  if (!/^[a-z]/.test(base)) {
    base = `value_${base}`;
  }
  let name = base;
  for (let n = 2; used.has(name); n += 1) {
    name = `${base}_${n}`;
  }
  used.add(name);
  return name;
}

/** The review's rows for the steps the worker kept. */
export function reviewRows(steps: RecordedStepIn[]): ReviewRow[] {
  const used = new Set<string>();
  return steps.map((step) => ({
    step,
    placeholder: takesValue(step) ? placeholderFor(step.target, used) : "",
    fixed: "",
  }));
}

/** The steps as Iron Jarvis takes them (`record_save`). */
export function saveSteps(rows: ReviewRow[]): Record<string, unknown>[] {
  return rows.map((row) => {
    const s = row.step;
    const out: Record<string, unknown> = { kind: s.kind };
    if (s.url !== undefined) out["url"] = s.url;
    if (s.key !== undefined) out["key"] = s.key;
    if (s.target !== undefined) out["target"] = s.target;
    if (s.sensitive) out["sensitive"] = true;
    if (takesValue(s)) {
      out["placeholder"] = row.placeholder;
      const fixed = row.fixed.trim();
      if (fixed) out["fixed"] = fixed;
    }
    return out;
  });
}

/** What a recording that could not start says, by the add-on's refusal code. */
export function recordRefusal(code: string): string {
  if (code === "BUSY") {
    return "A recording is already running in another tab. Stop it there first.";
  }
  if (code === "PERMISSION_DENIED") {
    return "Recording needs site access in this browser. Press Grant site access, then try again.";
  }
  if (code === "UNSUPPORTED_PAGE") {
    return "This page is closed to add-ons (a browser page or an add-on store). Open the website you want to record first.";
  }
  if (code === "PAGE_FAILED_TO_LOAD") {
    return "This page did not load, so there is nothing to record yet.";
  }
  return "Could not start recording in this tab.";
}

/** Why a recording ended by itself, in words. */
export function endedWords(reason: string): string {
  if (reason === "tab closed") {
    return "The recording stopped because its tab closed.";
  }
  if (reason === "idle") {
    return "The recording stopped after 15 minutes with nothing happening.";
  }
  return "The recording stopped.";
}

// --- the controller -----------------------------------------------------------

export interface RecordingDeps {
  /** Send the reviewed task to Iron Jarvis (`record_save`); false = not sent. */
  save: (params: Record<string, unknown>) => Promise<boolean>;
  say: (who: string, text: string) => void;
}

const $ = (id: string): HTMLElement | null => document.getElementById(id);

export class RecordingPanel {
  private rows: ReviewRow[] = [];
  private saving = false;

  constructor(private readonly deps: RecordingDeps) {
    $("record")?.addEventListener("click", () => {
      if (document.body.dataset["recording"] === "on") {
        void this.stop();
      } else {
        void this.start();
      }
    });
    $("record-stop")?.addEventListener("click", () => void this.stop());
    $("review-save")?.addEventListener("click", () => void this.save());
    $("review-discard")?.addEventListener("click", () => this.closeReview());
  }

  /** On open: a recording running in this browser shows its strip again; one
   *  that ended by itself opens its review. */
  async resume(): Promise<void> {
    type Status = { recording?: boolean; ended?: string; count?: number } | null;
    let status: Status = null;
    try {
      status = (await chrome.runtime.sendMessage({ kind: "record_status" })) as Status;
    } catch {
      return;
    }
    if (status?.recording) {
      this.setRecording(true, Number(status.count ?? 0));
    } else if (status?.ended) {
      this.deps.say("tool", endedWords(String(status.ended)));
      await this.stop();
    }
  }

  setRecording(on: boolean, count = 0): void {
    document.body.dataset["recording"] = on ? "on" : "off";
    const strip = $("recording");
    if (strip) strip.hidden = !on;
    this.paintCount(count);
    const button = $("record");
    if (button) {
      button.setAttribute("aria-pressed", on ? "true" : "false");
      button.title = on
        ? "Stop recording and review the steps"
        : "Record a task: do it once in this tab, and Jarvis keeps it as a skill. What you type is never recorded.";
    }
  }

  paintCount(count: number): void {
    const text = $("recording-text");
    if (text) {
      text.textContent =
        count <= 1
          ? "Recording this tab — do the task, then press Stop."
          : `Recording this tab · ${count} steps`;
    }
  }

  /** The worker's messages about the recording. */
  onWorkerMessage(kind: string, body: Record<string, unknown>): boolean {
    if (kind === "record_progress") {
      if (document.body.dataset["recording"] === "on") {
        this.paintCount(Number(body["count"] ?? 0));
      }
      return true;
    }
    if (kind === "record_ended") {
      if (document.body.dataset["recording"] === "on") {
        this.deps.say("tool", endedWords(String(body["reason"] ?? "")));
        void this.stop();
      }
      return true;
    }
    return false;
  }

  async start(): Promise<void> {
    let windowId: number | undefined;
    try {
      windowId = (await chrome.windows?.getCurrent?.())?.id;
    } catch {
      windowId = undefined;
    }
    type Reply = { started?: boolean; code?: string; count?: number } | null;
    let reply: Reply = null;
    try {
      reply = (await chrome.runtime.sendMessage({ kind: "record_start", windowId })) as Reply;
    } catch {
      reply = null;
    }
    if (reply?.started) {
      this.closeReview();
      this.setRecording(true, Number(reply.count ?? 0));
      return;
    }
    this.setRecording(false);
    this.deps.say("error", recordRefusal(String(reply?.code ?? "")));
  }

  async stop(): Promise<void> {
    type Reply = { found?: boolean; steps?: unknown; dropped?: number } | null;
    let reply: Reply = null;
    try {
      reply = (await chrome.runtime.sendMessage({ kind: "record_stop" })) as Reply;
    } catch {
      reply = null;
    }
    this.setRecording(false);
    const steps = Array.isArray(reply?.steps) ? (reply?.steps as RecordedStepIn[]) : [];
    const acting = steps.filter((s) => s.kind !== "navigate" && s.kind !== "arrive");
    if (!reply?.found || acting.length === 0) {
      this.deps.say("tool", "Nothing was recorded. Press Record, do the task in the tab, then press Stop.");
      return;
    }
    if (Number(reply.dropped ?? 0) > 0) {
      this.deps.say("tool", `The recording kept its first ${steps.length} steps; later ones were left out.`);
    }
    this.openReview(steps);
  }

  openReview(steps: RecordedStepIn[]): void {
    this.rows = reviewRows(steps);
    const review = $("review");
    if (review) review.hidden = false;
    document.body.dataset["reviewing"] = "on";
    const name = $("review-name") as HTMLInputElement | null;
    if (name) {
      name.maxLength = RECORDING_NAME_CHARS;
      name.value = "";
      name.focus();
    }
    const desc = $("review-description") as HTMLInputElement | null;
    if (desc) desc.value = "";
    this.note("");
    this.paintRows();
  }

  closeReview(): void {
    this.rows = [];
    const review = $("review");
    if (review) review.hidden = true;
    document.body.dataset["reviewing"] = "off";
    const list = $("review-steps");
    if (list) list.textContent = "";
  }

  private note(text: string, tone = ""): void {
    const node = $("review-note");
    if (node) {
      node.textContent = text;
      node.dataset["tone"] = tone;
    }
  }

  private paintRows(): void {
    const list = $("review-steps");
    if (!list) return;
    list.textContent = "";
    this.rows.forEach((row, index) => {
      const item = document.createElement("li");
      item.className = "review-step";
      const head = document.createElement("div");
      head.className = "review-head";
      const words = document.createElement("span");
      words.className = "review-words";
      words.textContent = stepWords(row.step);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "review-remove";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Delete step ${index + 1}`);
      remove.title = "Delete this step";
      remove.addEventListener("click", () => {
        this.rows.splice(index, 1);
        this.paintRows();
      });
      head.append(words, remove);
      item.append(head);
      if (takesValue(row.step)) {
        item.append(this.valueControls(row, index));
      }
      list.append(item);
    });
  }

  /** "Ask each time as {{name}}" or "Always type: [ ]" — the user's choice. */
  private valueControls(row: ReviewRow, index: number): HTMLElement {
    const box = document.createElement("div");
    box.className = "review-value";
    const ask = document.createElement("label");
    ask.className = "review-ask";
    const askWords = document.createElement("span");
    askWords.textContent = "Ask each time, as";
    const name = document.createElement("input");
    name.type = "text";
    name.value = row.placeholder;
    name.maxLength = 40;
    name.setAttribute("aria-label", `Name of the value for step ${index + 1}`);
    name.addEventListener("input", () => {
      row.placeholder = name.value;
    });
    ask.append(askWords, name);
    const fixed = document.createElement("input");
    fixed.type = "text";
    fixed.className = "review-fixed";
    fixed.placeholder = row.step.kind === "select" ? "…or always choose" : "…or always type";
    fixed.maxLength = RECORDING_FIXED_TEXT_CHARS;
    fixed.autocomplete = "off";
    fixed.value = row.fixed;
    fixed.setAttribute("aria-label", `Fixed text for step ${index + 1}`);
    fixed.title = "Leave empty to be asked each time. Never put a password or a key here.";
    fixed.addEventListener("input", () => {
      row.fixed = fixed.value;
    });
    box.append(ask, fixed);
    return box;
  }

  /** The `recorded` event: saved, or why not. */
  onRecorded(payload: Record<string, unknown>): void {
    this.saving = false;
    const text = typeof payload["text"] === "string" ? (payload["text"] as string) : "";
    if (payload["ok"] === true) {
      this.closeReview();
      this.deps.say("tool", text || "Saved.");
      return;
    }
    this.note(text || "The recording was not saved.", "error");
  }

  async save(): Promise<void> {
    if (this.saving) return;
    const name = (($("review-name") as HTMLInputElement | null)?.value ?? "").trim();
    if (!name) {
      this.note("Give the task a name first.", "error");
      return;
    }
    if (!this.rows.some((r) => r.step.kind !== "navigate" && r.step.kind !== "arrive")) {
      this.note("There is nothing left to save. Record the task again.", "error");
      return;
    }
    const description = (($("review-description") as HTMLInputElement | null)?.value ?? "").trim();
    this.saving = true;
    this.note("Saving…");
    const sent = await this.deps.save({
      name,
      description,
      steps: saveSteps(this.rows),
    });
    if (!sent) {
      this.saving = false;
      this.note("Iron Jarvis is not connected, so nothing was saved. Your steps are still here.", "error");
    }
  }
}
