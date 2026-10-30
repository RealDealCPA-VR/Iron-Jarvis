"use client";

/**
 * BUILD › CODE: THE START (v1.412.0). The user: "to start a new session it
 * requires that i first select a folder and a harness choice. Id prefer if it
 * instead started automatically with a no folder chosen in the chat bar with a
 * simple choose selector for folder as well as a choose selector for the
 * harness."
 *
 * So the composer is there at once (no session open, or New session), ready
 * to type, and under it ONE quiet row of chips:
 *
 *   Folder   "No folder" until chosen; Choose… opens Windows' own folder
 *            picker in the desktop app (the in-app tree in a browser) —
 *            components/folders/FolderChip.
 *   Tool     Claude Code / Codex / Pi: the last one used, else the first one
 *            installed (and signed in, as far as is known).
 *   Account  only when the tool has MORE THAN ONE login (Iron-Proxy's
 *            accounts for Claude Code / Codex) — picking such a tool ASKS
 *            which account at once; the last one used for that tool is the
 *            default. One login, or Pi's own sign-in, never asks.
 *   Mode     the tool's modes (Full access still takes two presses).
 *   Model    and Effort — effort only when the tool lists levels.
 *
 * Send makes the session with those choices (POST /build/app/sessions — the
 * one create path) and hands the words to the session, which sends them as its
 * first message (BuildCodeView → SessionPane `firstMessage`): one press. With
 * no folder, the daemon makes the session a folder of its own with that first
 * message (`<chat files>/<date> <first words>`, the v1.379.0 rule) and takes it
 * back when the message never starts. A folder the daemon refuses is said on
 * the folder chip and nothing is started.
 *
 * v1.413.0: pictures and files go with the first message — paste, drop or
 * the paperclip (components/harness/HarnessAttachments, the chat's own
 * upload contract); Send waits for an upload still going up, and a picture
 * alone is a first message.
 */

import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from "react";
import { ArrowUp, Check, ChevronDown, Code2, Loader2, UserRound } from "lucide-react";
import { ApiError, post } from "@/lib/api";
import { FolderChip } from "@/components/folders/FolderChip";
import { HARNESS_COLUMN, HARNESS_COMPOSER_BOX } from "@/components/harness/harnessLook";
import { ModeChip } from "@/components/buildCode/ModeChip";
import {
  AttachButton,
  AttachmentChips,
  pastedFiles,
  useHarnessAttachments,
} from "@/components/harness/HarnessAttachments";
import {
  OWN_SIGN_IN_TITLE,
  accountStatusWords,
  decodeCodeSession,
  effortsFor,
  ownSignInWords,
  toolUnavailable,
  type CodeSession,
  type CodingTool,
} from "@/lib/buildApp";

/** Remembered on this PC: the tool last started, and per tool its account. */
export const LAST_TOOL_KEY = "ij.code.lastTool";
export const LAST_ACCOUNT_KEY = "ij.code.lastAccount.";

function readKept(key: string): string {
  try {
    return window.localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}
function keep(key: string, value: string): void {
  try {
    if (value) window.localStorage.setItem(key, value);
  } catch {
    /* a private window: nothing is remembered */
  }
}

/** The tool a new session starts on: the last one used while it can start,
 *  else the first that can. "" while nothing can. */
export function defaultTool(tools: readonly CodingTool[] | null, last: string): string {
  if (!tools) return "";
  const can = tools.filter((t) => !toolUnavailable(t));
  if (last && can.some((t) => t.id === last)) return last;
  return can[0]?.id ?? "";
}

/** The accounts a tool can be asked about: its Iron-Proxy logins. Pi signs in
 *  by itself (none). */
export function accountChoices(tool: CodingTool | null): CodingTool["accounts"] {
  if (!tool || tool.ownSignIn) return [];
  return tool.accounts;
}

/** Does choosing this tool ASK which account? Only with more than one login. */
export function asksForAccount(tool: CodingTool | null): boolean {
  return accountChoices(tool).length > 1;
}

/** The account a new session on `tool` starts on: the last one used for it
 *  while it is listed and usable. "" = not chosen yet — with more than one
 *  login the composer ASKS (v1.412.0 review: the default tool never asked,
 *  and a session silently went out on the first account); with one login (or
 *  none) the daemon decides. */
export function defaultAccount(tool: CodingTool | null, last: string): string {
  const list = accountChoices(tool);
  if (list.length <= 1) return "";
  const usable = list.filter((a) => accountStatusWords(a.status).usable);
  if (last && usable.some((a) => a.id === last)) return last;
  return "";
}

/** v1.412.0 review: why none of the tool's logins can start a session (all
 *  signed out, at their limit or turned off), in words; null when one can. */
export function noUsableAccount(tool: CodingTool | null): string | null {
  const list = accountChoices(tool);
  if (list.length <= 1 || list.some((a) => accountStatusWords(a.status).usable)) return null;
  const why = Array.from(new Set(list.map((a) => accountStatusWords(a.status).words).filter(Boolean)));
  return `No ${tool?.label ?? ""} account can start a session now: ${why.join(", ") || "none is usable"}.`;
}

const CHIP_BTN =
  "inline-flex h-7 max-w-[14rem] items-center gap-1.5 rounded-lg px-2 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50";
const CHIP_SELECT =
  "h-7 max-w-[12rem] cursor-pointer truncate rounded-lg border-0 bg-transparent px-1.5 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50";
const MENU = "absolute left-0 top-full z-30 mt-1 w-72 max-w-[calc(100vw-2rem)] rounded-xl bg-ink-800 p-1 shadow-card";
const MENU_ROW =
  "flex w-full items-start gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] transition-colors enabled:hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-50";

/** A menu that opens under its chip stays inside the window (a 390 px phone:
 *  the chip may sit at the row's right end) — moved left or right by however
 *  much it would stick out, measured once per opening. */
function useInsideWindow(ref: RefObject<HTMLDivElement | null>, open: boolean): number {
  const [shift, setShift] = useState(0);
  useLayoutEffect(() => {
    if (!open) {
      setShift(0);
      return;
    }
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const right = window.innerWidth - 8;
    let next = 0;
    if (r.right > right) next = right - r.right;
    if (r.left + next < 8) next = 8 - r.left;
    setShift(next);
  }, [open, ref]);
  return shift;
}

/** The chips a refusal can be said on (besides the folder chip). */
const CHIP_FIELDS = new Set(["tool", "account", "mode", "model", "effort"]);

/** v1.412.0 review: which choice the daemon's refusal is about (`field` in
 *  its JSON answer), "" when none. */
export function refusalField(e: unknown): string {
  if (!(e instanceof ApiError)) return "";
  const body = e.body as { field?: unknown } | undefined;
  return body && typeof body.field === "string" ? body.field : "";
}

function ChipProblem({ field, problem }: { field: string; problem: { field: string; words: string } | null }) {
  if (!problem || problem.field !== field) return null;
  return (
    <span role="alert" data-testid={`code-start-${field}-problem`} className="basis-full px-2 text-[12px] text-tone-danger">
      {problem.words}
    </span>
  );
}

function errorWords(e: unknown, fallback: string): string {
  if (e instanceof ApiError && e.message) return e.message;
  if (e instanceof Error && e.message) return e.message;
  return fallback;
}

export function StartComposer({
  tools,
  toolsError,
  startFolder = null,
  startAt = null,
  workRoot = "",
  onStarted,
}: {
  /** GET /build/app/harnesses (null while it is read). */
  tools: readonly CodingTool[] | null;
  toolsError: string | null;
  /** The folder this start is on (a folder heading's "+"); null = none. */
  startFolder?: string | null;
  /** Where Choose… opens when no folder is chosen (the folder in front, else
   *  Build's last folder). */
  startAt?: string | null;
  /** Where a session with no folder keeps its files (the chat files folder). */
  workRoot?: string;
  /** The session was made: open it and send `text` (and `files`, the
   *  uploaded pictures' paths) as its first message. */
  onStarted: (session: CodeSession, text: string, files?: string[]) => void;
}) {
  const [text, setText] = useState("");
  const [folder, setFolder] = useState<string | null>(startFolder);
  const [folderProblem, setFolderProblem] = useState<string | null>(null);
  const [toolId, setToolId] = useState("");
  const [toolOpen, setToolOpen] = useState(false);
  const [account, setAccount] = useState("");
  const [accountOpen, setAccountOpen] = useState(false);
  const [mode, setMode] = useState<{ id: string; confirm: boolean }>({ id: "", confirm: false });
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // v1.412.0 review: a refusal about one choice is said on THAT chip (the
  // daemon's `field`), never on the folder chip by default.
  const [chipProblem, setChipProblem] = useState<{ field: string; words: string } | null>(null);
  const boxRef = useRef<HTMLTextAreaElement | null>(null);
  /** v1.413.0: pictures and files for the first message (uploaded). */
  const att = useHarnessAttachments();
  // A start held for an upload runs this render's `start` when it settles.
  const startRef = useRef<() => Promise<void>>(async () => undefined);
  const toolMenuRef = useRef<HTMLDivElement | null>(null);
  const accountMenuRef = useRef<HTMLDivElement | null>(null);
  const toolShift = useInsideWindow(toolMenuRef, toolOpen);
  const accountShift = useInsideWindow(accountMenuRef, accountOpen);

  // The tool: the last one used, else the first that can start (once read).
  useEffect(() => {
    if (toolId || !tools) return;
    const first = defaultTool(tools, readKept(LAST_TOOL_KEY));
    if (first) setToolId(first);
  }, [tools, toolId]);
  const tool = useMemo(() => tools?.find((t) => t.id === toolId) ?? null, [tools, toolId]);
  // A tool's own defaults whenever the tool changes. v1.412.0 review: a tool
  // with several logins and no account remembered for it ASKS at once — the
  // default tool too (only a tool the user picked used to ask).
  useEffect(() => {
    const kept = defaultAccount(tool, tool ? readKept(LAST_ACCOUNT_KEY + tool.id) : "");
    setAccount(kept);
    if (asksForAccount(tool) && !kept && !noUsableAccount(tool)) setAccountOpen(true);
    setMode({ id: tool?.modes[0]?.id ?? "", confirm: false });
    setModel("");
    setEffort("");
    setChipProblem(null);
  }, [tool]);
  // v1.412.0 review: the effort levels are the chosen MODEL's; an effort the
  // model does not take is dropped (the chip hides when it takes none).
  const efforts = useMemo(() => effortsFor(tool, model), [tool, model]);
  useEffect(() => {
    if (effort && !efforts.includes(effort)) setEffort("");
  }, [efforts, effort]);

  useEffect(() => {
    boxRef.current?.focus({ preventScroll: true });
  }, []);

  const choices = accountChoices(tool);
  const showAccount = asksForAccount(tool);
  const noAccount = noUsableAccount(tool);
  // v1.412.0 review: a tool with several logins never starts with none
  // chosen (the daemon would pick one the user did not choose).
  const accountMissing = showAccount && !account;
  const unavailable = tool ? toolUnavailable(tool) : null;
  const ready = !!tool && !unavailable && !accountMissing && (!!text.trim() || att.files.length > 0) && !busy;

  function chooseTool(id: string) {
    setToolOpen(false);
    if (id === toolId) return;
    setToolId(id);
    const next = tools?.find((t) => t.id === id) ?? null;
    // A tool with more than one login asks which one, at once.
    setAccountOpen(asksForAccount(next));
  }

  async function start() {
    // v1.413.0: a picture still going up holds the start (it goes WITH it).
    if (att.busyRef.current) {
      att.whenSettled(() => void startRef.current());
      return;
    }
    const words = (boxRef.current?.value ?? text).trim();
    const files = att.filesRef.current.map((f) => f.path);
    if (!tool || unavailable || (!words && !files.length) || busy) return;
    if (accountMissing) {
      if (!noAccount) setAccountOpen(true);
      return;
    }
    setBusy(true);
    setError(null);
    setFolderProblem(null);
    setChipProblem(null);
    try {
      const raw = await post<unknown>("/build/app/sessions", {
        harness: tool.id,
        ...(folder ? { folder } : {}),
        ...(account ? { account } : {}),
        ...(mode.id ? { mode: mode.id } : {}),
        ...(mode.confirm ? { confirm: true } : {}),
        ...(model ? { model } : {}),
        ...(effort && efforts.includes(effort) ? { effort } : {}),
      });
      const made = decodeCodeSession(raw);
      if (!made) throw new Error("The session could not be made.");
      keep(LAST_TOOL_KEY, tool.id);
      if (account) keep(LAST_ACCOUNT_KEY + tool.id, account);
      onStarted(made, words, files);
    } catch (e) {
      const words2 = errorWords(e, "The session could not be made.");
      // v1.412.0 review: each refusal on the chip it is about — the daemon
      // names it (`field`); one it does not name is said under the composer.
      const field = refusalField(e);
      if (field === "folder") setFolderProblem(words2);
      else if (field && CHIP_FIELDS.has(field)) setChipProblem({ field, words: words2 });
      else setError(words2);
      setBusy(false);
    }
  }

  startRef.current = start;

  const onKey = (e: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void start();
    }
  };

  const toolWords = !tools
    ? toolsError
      ? "No coding tools"
      : "Looking for coding tools…"
    : tool
      ? tool.label
      : "No coding tool found";
  const accountName = noAccount
    ? "No account can start it"
    : (choices.find((a) => a.id === account)?.name ?? "Choose an account");

  return (
    <div data-testid="code-start" className="flex h-full flex-col justify-center overflow-y-auto px-4 py-6">
      <div className={`${HARNESS_COLUMN} w-full space-y-2`}>
        <p className="flex items-center gap-2 px-1 text-[14px] text-zinc-300">
          <Code2 size={16} aria-hidden="true" className="text-zinc-500" />
          Start a session with Claude Code, Codex or Pi
        </p>
        <div
          data-testid="code-start-composer"
          className="relative rounded-[20px] bg-ink-800 shadow-[0_0_0_0.5px_rgb(var(--white)/0.12)]"
          onDragOver={(e) => {
            if (e.dataTransfer?.types?.includes("Files")) e.preventDefault();
          }}
          onDrop={(e) => {
            const dropped = Array.from(e.dataTransfer?.files ?? []);
            if (!dropped.length) return;
            e.preventDefault();
            void att.add(dropped);
          }}
        >
          <AttachmentChips files={att.files} uploading={att.uploading} onRemove={att.remove} />
          {att.problem ? (
            <p data-testid="code-start-attach-problem" role="alert" className="px-3 pt-1 text-[12px] text-tone-warn">
              {att.problem}
            </p>
          ) : null}
          <textarea
            ref={boxRef}
            data-testid="code-start-box"
            aria-label="Describe a task or ask a question"
            placeholder="Describe a task or ask a question"
            rows={3}
            value={text}
            disabled={busy}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKey}
            onPaste={(e) => {
              // A pasted picture is attached; pasted text is typed.
              const pasted = pastedFiles(e.clipboardData);
              if (!pasted.length) return;
              e.preventDefault();
              void att.add(pasted, { pasted: true });
            }}
            className={HARNESS_COMPOSER_BOX}
          />
          <div className="flex items-center justify-between px-2 pb-2">
            <AttachButton onPick={(picked) => void att.add(picked)} disabled={busy} />
            <button
              type="button"
              data-testid="code-start-send"
              disabled={!ready}
              onClick={() => void start()}
              aria-label="Start the session and send"
              title={unavailable ?? noAccount ?? (accountMissing ? "Choose an account first" : "Send (Enter)")}
              className="grid h-8 w-8 place-items-center rounded-full bg-accent text-ink-950 transition-opacity disabled:opacity-40"
            >
              {busy ? <Loader2 size={14} className="animate-spin" /> : <ArrowUp size={15} aria-hidden="true" />}
            </button>
          </div>
        </div>

        <div data-testid="code-start-chips" className="flex min-w-0 flex-wrap items-start gap-x-1 gap-y-1 px-1">
          <FolderChip
            folder={folder}
            onPick={(p) => {
              setFolder(p);
              setFolderProblem(null);
            }}
            onClear={() => {
              setFolder(null);
              setFolderProblem(null);
            }}
            problem={folderProblem}
            disabled={busy}
            startAt={startFolder ?? startAt}
            noFolderTitle={
              workRoot
                ? `No folder: the session gets a folder of its own in ${workRoot} with your first message.`
                : "No folder: the session gets a folder of its own with your first message."
            }
            testId="code-start-folder"
          />

          <div className="relative">
            <button
              type="button"
              data-testid="code-start-tool"
              aria-haspopup="menu"
              aria-expanded={toolOpen}
              disabled={busy || !tools || tools.length === 0}
              onClick={() => {
                setToolOpen((v) => !v);
                setAccountOpen(false);
              }}
              title="The coding tool this session runs"
              className={CHIP_BTN}
            >
              <Code2 size={13} aria-hidden="true" className="shrink-0" />
              <span className="truncate">{toolWords}</span>
              <ChevronDown size={12} aria-hidden="true" className="shrink-0" />
            </button>
            <ChipProblem field="tool" problem={chipProblem} />
            {toolOpen && tools ? (
              <div
                ref={toolMenuRef}
                role="menu"
                data-testid="code-start-tool-menu"
                style={toolShift ? { transform: `translateX(${toolShift}px)` } : undefined}
                className={MENU}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    e.preventDefault();
                    setToolOpen(false);
                  }
                }}
              >
                {tools.map((t) => {
                  const why = toolUnavailable(t);
                  return (
                    <button
                      key={t.id}
                      type="button"
                      role="menuitemradio"
                      aria-checked={t.id === toolId}
                      data-testid={`code-start-tool-${t.id}`}
                      data-available={why ? "false" : "true"}
                      disabled={!!why}
                      onClick={() => chooseTool(t.id)}
                      className={`${MENU_ROW} ${t.id === toolId ? "text-zinc-100" : "text-zinc-300"}`}
                    >
                      <span className="mt-0.5 w-3.5 shrink-0">{t.id === toolId ? <Check size={13} aria-hidden="true" /> : null}</span>
                      <span className="min-w-0">
                        <span className="block">{t.label}</span>
                        {why ? (
                          <span data-testid={`code-start-tool-why-${t.id}`} className="block text-[12px] text-zinc-500">
                            {why}
                          </span>
                        ) : null}
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : null}
          </div>

          {tool && tool.ownSignIn ? (
            <span
              data-testid="code-start-account-own"
              title={OWN_SIGN_IN_TITLE}
              className="inline-flex h-7 items-center gap-1.5 px-2 text-[12px] text-zinc-500"
            >
              <UserRound size={13} aria-hidden="true" />
              {ownSignInWords(tool.id, tools)}
            </span>
          ) : null}
          {showAccount ? (
            <div className="relative">
              <button
                type="button"
                data-testid="code-start-account"
                aria-haspopup="menu"
                aria-expanded={accountOpen}
                disabled={busy || !!noAccount}
                onClick={() => {
                  setAccountOpen((v) => !v);
                  setToolOpen(false);
                }}
                title={noAccount ?? `The ${tool?.label ?? ""} account this session runs on`}
                className={CHIP_BTN}
              >
                <UserRound size={13} aria-hidden="true" className="shrink-0" />
                <span className="truncate">{accountName}</span>
                <ChevronDown size={12} aria-hidden="true" className="shrink-0" />
              </button>
              <ChipProblem field="account" problem={chipProblem} />
              {accountOpen ? (
                <div
                  ref={accountMenuRef}
                  role="menu"
                  aria-label={`Which ${tool?.label ?? ""} account?`}
                  data-testid="code-start-account-menu"
                  style={accountShift ? { transform: `translateX(${accountShift}px)` } : undefined}
                  className={MENU}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") {
                      e.preventDefault();
                      setAccountOpen(false);
                    }
                  }}
                >
                  <p className="px-2.5 pb-1 pt-1.5 text-[12px] text-zinc-500">Which {tool?.label} account?</p>
                  {choices.map((a) => {
                    const st = accountStatusWords(a.status);
                    return (
                      <button
                        key={a.id}
                        type="button"
                        role="menuitemradio"
                        aria-checked={a.id === account}
                        data-testid={`code-start-account-${a.id}`}
                        disabled={!st.usable}
                        onClick={() => {
                          setAccount(a.id);
                          setAccountOpen(false);
                          boxRef.current?.focus({ preventScroll: true });
                        }}
                        className={`${MENU_ROW} ${a.id === account ? "text-zinc-100" : "text-zinc-300"}`}
                      >
                        <span className="mt-0.5 w-3.5 shrink-0">{a.id === account ? <Check size={13} aria-hidden="true" /> : null}</span>
                        <span className="min-w-0">
                          <span className="block truncate">{a.name}</span>
                          {st.words ? <span className="block text-[12px] text-zinc-500">{st.words}</span> : null}
                        </span>
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </div>
          ) : null}

          {tool ? (
            <ModeChip
              tool={tool}
              value={mode.id}
              onChange={(id, confirm) => setMode({ id, confirm })}
              disabled={busy}
              testId="code-start-mode"
            />
          ) : null}
          <ChipProblem field="mode" problem={chipProblem} />

          {tool && tool.models.length ? (
            <select
              aria-label={`${tool.label} model`}
              data-testid="code-start-model"
              value={model}
              disabled={busy}
              onChange={(e) => setModel(e.target.value)}
              aria-invalid={chipProblem?.field === "model" || undefined}
              className={CHIP_SELECT}
            >
              {tool.models.some((m) => m.id === "") ? null : <option value="">{tool.label}&apos;s default</option>}
              {tool.models.map((m) => (
                <option key={m.id || "default"} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
          ) : null}
          <ChipProblem field="model" problem={chipProblem} />
          {tool && efforts.length ? (
            <select
              aria-label="Effort"
              data-testid="code-start-effort"
              value={effort}
              disabled={busy}
              onChange={(e) => setEffort(e.target.value)}
              aria-invalid={chipProblem?.field === "effort" || undefined}
              className={CHIP_SELECT}
            >
              <option value="">Default effort</option>
              {efforts.map((x) => (
                <option key={x} value={x}>
                  {x.charAt(0).toUpperCase() + x.slice(1)} effort
                </option>
              ))}
            </select>
          ) : null}
          <ChipProblem field="effort" problem={chipProblem} />
        </div>

        {toolsError ? (
          <p role="alert" data-testid="code-start-tools-error" className="px-2 text-[12px] text-tone-danger">
            {toolsError}
          </p>
        ) : unavailable ? (
          <p data-testid="code-start-unavailable" className="px-2 text-[12px] text-zinc-500">
            {unavailable}
          </p>
        ) : noAccount ? (
          <p data-testid="code-start-no-account" className="px-2 text-[12px] text-zinc-500">
            {noAccount}
          </p>
        ) : null}
        {error ? (
          <p role="alert" data-testid="code-start-error" className="px-2 text-[12px] text-tone-danger">
            {error}
          </p>
        ) : null}
      </div>
    </div>
  );
}

export default StartComposer;
