"use client";

/**
 * Iron-Proxy on the Connections page (v1.301.0).
 *
 * Iron-Proxy is the ACCOUNT MANAGER: which Claude / Codex / Grok accounts
 * exist, the order they are tried in, which one is parked until when. Iron
 * Jarvis keeps its own adapters and runs each call AS the first usable account
 * of that provider; a limit moves to the next account of the SAME provider,
 * never to another provider. The Iron-Proxy token never reaches this page —
 * every call goes through the daemon's `/iron-proxy` routes.
 *
 * Renders NOTHING when the daemon answers 404 (an older daemon without the
 * routes) or is offline (the page's OfflineHint owns that).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowDown,
  ArrowUp,
  Laptop,
  LogIn,
  Play,
  Plus,
  Power,
  RefreshCw,
  SquareTerminal,
  TriangleAlert,
  Users,
} from "lucide-react";
import { post, patch, del } from "@/lib/api";
import { useApi, type ApiState } from "@/lib/useApi";
import { useDocumentVisible } from "@/lib/useDocumentVisible";
import { Badge, Card, ConfirmButton, ErrorNote, LoaderInline, SuccessNote } from "@/components/ui";
import {
  ADDABLE_PROVIDERS,
  DEFAULT_USED_BY,
  adoptable,
  createdId,
  groupAccounts,
  isSnapshot,
  movedIds,
  providerLabel,
  stateChip,
  terminalsHref,
  usageLine,
  type IronProxyAccount,
  type IronProxySnapshot,
} from "@/lib/ironProxy";
import { CLI_ACCOUNT_PROVIDER, openedPaneId } from "@/lib/paneAccounts";

/** The providers whose accounts a Build pane can start a CLI on (v1.302.0). */
const BUILD_PROVIDERS = new Set(Object.values(CLI_ACCOUNT_PROVIDER));

/** How often the card re-reads `GET /iron-proxy` while on screen. */
export const IRON_PROXY_POLL_MS = 5000;
/** How often the card re-reads the FULL view, whose `discovered` list makes
 *  Iron-Proxy run every vendor CLI's status command (v1.302.0, review F3).
 *  The 5 s poll is the light read (`?discover=0`: same shape, no CLI checks). */
export const IRON_PROXY_DISCOVER_POLL_MS = 30_000;
/** The light read: status + accounts, `discovered: []`, no CLI runs. */
export const IRON_PROXY_LIGHT_PATH = "/iron-proxy?discover=0";
/** …and while it waits for a just-enabled Iron-Proxy to answer "running". */
export const IRON_PROXY_STARTING_POLL_MS = 1000;
/** Give up showing "Starting…" after this long (the daemon's own start wait is
 *  ~10 s and it records a reason in status.error when it fails). */
const STARTING_GIVE_UP_MS = 30_000;

/** v1.303.3: after Sign in, how often the card re-reads the light view, and
 *  for how long — so the chip flips to Ready without a reload. */
export const IRON_PROXY_SIGNIN_POLL_MS = 1000;
export const SIGNIN_WATCH_MS = 3 * 60_000;

/** The account whose sign-in the user started, kept per WINDOW: Sign in
 *  opens Build (this card unmounts), and the watch resumes when the user
 *  comes back to Connections within the 3 minutes. */
let signInWatch: { id: string; title: string; until: number } | null = null;

/** Test seam. */
export function resetSignInWatch() {
  signInWatch = null;
}

/** The account's status out of `POST …/check`'s answer (the account itself,
 *  or wrapped as `account` / `profile`, or a bare `{status}`). */
export function checkedStatus(res: unknown): string | null {
  if (!res || typeof res !== "object") return null;
  const r = res as Record<string, unknown>;
  const pick = (o: unknown): string | null => {
    if (!o || typeof o !== "object") return null;
    const x = o as Record<string, unknown>;
    const st = x.state as Record<string, unknown> | undefined;
    if (st && typeof st.status === "string") return st.status;
    return typeof x.status === "string" ? x.status : null;
  };
  return pick(r) ?? pick(r.account) ?? pick(r.profile);
}

/** The daemon's "the running Iron-Proxy is too old" sentence. */
export function isOutdatedError(error: string | null | undefined): boolean {
  return !!error && /older than|too old|outdated/i.test(error);
}

const enc = encodeURIComponent;

/**
 * A poll that never STACKS (v1.303.3 review): a tick is skipped while the
 * previous read of the same path is still in flight. `usePolledApi` starts a
 * new GET every tick regardless, and while Iron-Proxy was being replaced one
 * read took seconds — at the 1 s sign-in cadence that queued 10–15 GETs and
 * used up the browser's six connections to the daemon, stalling every other
 * page request. Otherwise it is `usePolledApi`: paused while the window is
 * hidden, one re-read on the hidden→visible edge, `reload()` still immediate.
 */
export function useSerialPolledApi<T>(path: string, intervalMs: number): ApiState<T> {
  const [tick, setTick] = useState(0);
  const state = useApi<T>(path, [tick]);
  const loadingRef = useRef(state.loading);
  loadingRef.current = state.loading;
  const visible = useDocumentVisible();
  useEffect(() => {
    if (!visible) return;
    const id = setInterval(() => {
      if (!loadingRef.current) setTick((t) => t + 1);
    }, intervalMs);
    return () => clearInterval(id);
  }, [intervalMs, visible]);
  const wasHiddenRef = useRef(false);
  useEffect(() => {
    if (!visible) {
      wasHiddenRef.current = true;
      return;
    }
    if (wasHiddenRef.current) {
      wasHiddenRef.current = false;
      if (!loadingRef.current) setTick((t) => t + 1);
    }
  }, [visible]);
  return state;
}

function errText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function IronProxyCard() {
  // `waiting` = the user switched it on and we have not yet seen a snapshot
  // taken AFTER that POST that says running (or says why not).
  const [waiting, setWaiting] = useState(false);
  // v1.303.3: the sign-in the user started (survives leaving the page).
  const [watch, setWatch] = useState(() =>
    signInWatch && signInWatch.until > Date.now() ? signInWatch : null,
  );
  const [signedIn, setSignedIn] = useState<string | null>(null);
  // v1.302.0 (review F3): status + accounts come from the LIGHT read every
  // 5 s (1 s while starting); only `discovered` needs the full view, which
  // makes Iron-Proxy run each vendor CLI's status command — every 30 s.
  const light = useSerialPolledApi<IronProxySnapshot>(
    IRON_PROXY_LIGHT_PATH,
    waiting
      ? IRON_PROXY_STARTING_POLL_MS
      : watch
        ? IRON_PROXY_SIGNIN_POLL_MS
        : IRON_PROXY_POLL_MS,
  );
  const full = useSerialPolledApi<IronProxySnapshot>("/iron-proxy", IRON_PROXY_DISCOVER_POLL_MS);
  const live = isSnapshot(light.data) ? light.data : full.data;
  const error = isSnapshot(live) ? null : light.error ?? full.error;
  const data = useMemo(() => {
    if (!isSnapshot(live)) return live;
    if (live === full.data || !isSnapshot(full.data)) return live;
    return { ...live, discovered: full.data.discovered ?? live.discovered ?? [] };
  }, [live, full.data]);
  const reloadLight = light.reload;
  const reloadFull = full.reload;
  const reload = () => {
    reloadLight();
    reloadFull();
  };
  // The snapshot object held when the enable POST returned: only a NEWER one
  // may end the wait (the held one predates the start).
  const staleRef = useRef<unknown>(null);
  useEffect(() => {
    if (!waiting || !isSnapshot(live) || live === staleRef.current) return;
    if (live.status.running || live.status.error) setWaiting(false);
  }, [waiting, live]);
  useEffect(() => {
    if (!waiting) return;
    const t = setTimeout(() => setWaiting(false), STARTING_GIVE_UP_MS);
    return () => clearTimeout(t);
  }, [waiting]);
  // v1.303.3: the watched account left "needs sign-in" -> say so ONCE, stop.
  useEffect(() => {
    if (!watch || !isSnapshot(live)) return;
    const a = (live.accounts ?? []).find((x) => x.id === watch.id);
    const s = a?.state?.status;
    if (s === "ready" || s === "active") {
      setSignedIn(`Signed in — ${a?.title ?? watch.title} is ready.`);
      signInWatch = null;
      setWatch(null);
    }
  }, [watch, live]);
  useEffect(() => {
    if (!watch) return;
    const t = setTimeout(() => {
      signInWatch = null;
      setWatch(null);
    }, Math.max(0, watch.until - Date.now()));
    return () => clearTimeout(t);
  }, [watch]);

  if (error?.status === 404) return null; // older daemon: no Iron-Proxy routes
  if (!isSnapshot(data)) {
    if (!error || error.status === 0) return null; // loading / offline
    return (
      <div id="iron-proxy-card">
        <Card title="Iron-Proxy accounts" icon={<Users size={15} />}>
          <ErrorNote>Could not load Iron-Proxy: {error.message}</ErrorNote>
        </Card>
      </div>
    );
  }
  return (
    <IronProxyBody
      snap={data}
      waiting={waiting}
      onEnablePosted={() => {
        staleRef.current = live;
        setWaiting(true);
      }}
      onEnableFailed={() => setWaiting(false)}
      reload={reload}
      signedIn={signedIn}
      onSignInStarted={(id, title) => {
        signInWatch = { id, title, until: Date.now() + SIGNIN_WATCH_MS };
        setSignedIn(null);
        setWatch(signInWatch);
      }}
      onClearSignedIn={() => setSignedIn(null)}
    />
  );
}

/** Mounted only once a real snapshot is in hand, so `useRouter` is never
 *  called on a page/test that has no Iron-Proxy (an older daemon). */
function IronProxyBody({
  snap,
  waiting,
  onEnablePosted,
  onEnableFailed,
  reload,
  signedIn = null,
  onSignInStarted,
  onClearSignedIn,
}: {
  snap: IronProxySnapshot;
  waiting: boolean;
  onEnablePosted: () => void;
  onEnableFailed: () => void;
  reload: () => void;
  /** v1.303.3: "Signed in — <title> is ready.", shown once. */
  signedIn?: string | null;
  onSignInStarted?: (id: string, title: string) => void;
  onClearSignedIn?: () => void;
}) {
  const router = useRouter();
  const { status } = snap;
  const accounts = snap.accounts ?? [];
  const groups = groupAccounts(accounts);
  const toAdopt = adoptable(snap.discovered ?? [], accounts);
  const usedBy = { ...DEFAULT_USED_BY, ...(snap.providers_used_by_jarvis ?? {}) };

  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const [addProvider, setAddProvider] = useState<string>(ADDABLE_PROVIDERS[0]);
  const [addTitle, setAddTitle] = useState("");
  const [added, setAdded] = useState<{ id: string; title: string } | null>(null);

  /** One request at a time; the daemon's `detail` sentence on failure. */
  async function run(key: string, fn: () => Promise<void>): Promise<boolean> {
    setBusy(key);
    setActionError(null);
    setNote(null);
    onClearSignedIn?.();
    try {
      await fn();
      return true;
    } catch (err) {
      setActionError(errText(err));
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function toggle() {
    if (status.enabled || waiting) {
      setStopping(true);
      await run("toggle", async () => {
        await post("/iron-proxy/disable");
      });
      onEnableFailed(); // stop waiting either way
      setStopping(false);
      reload();
      return;
    }
    const ok = await run("toggle", async () => {
      await post("/iron-proxy/enable");
    });
    if (ok) onEnablePosted();
    else onEnableFailed();
    reload();
  }

  async function signIn(id: string) {
    await run(`${id}:signin`, async () => {
      const res = await post<{ terminal_id?: string; name?: string }>(
        `/iron-proxy/accounts/${enc(id)}/signin`,
      );
      if (!res?.terminal_id) throw new Error("The daemon did not open a sign-in terminal.");
      // v1.303.3: watch a signed-out account so its chip flips to Ready on
      // its own (an account that is already usable has nothing to wait for).
      const acc = accounts.find((a) => a.id === id);
      if (acc?.state?.status === "unauthenticated") onSignInStarted?.(id, acc.title);
      router.push(terminalsHref(res.terminal_id));
    });
  }

  /** v1.303.3: ask Iron-Proxy to re-check one account's login now. */
  async function checkAgain(id: string, title: string) {
    let status: string | null = null;
    const ok = await run(`${id}:check`, async () => {
      status = checkedStatus(await post<unknown>(`/iron-proxy/accounts/${enc(id)}/check`));
    });
    if (!ok) return;
    reload();
    if (status === "unauthenticated") {
      setActionError("Still not signed in — finish the login in the Sign in pane.");
    } else if (status === "ready" || status === "active") {
      setNote(`Signed in — ${title} is ready.`);
    }
  }

  /** v1.302.0: start this account's CLI in a NEW Build pane and go there. */
  async function openInBuild(id: string) {
    await run(`${id}:open`, async () => {
      const res = await post<unknown>(`/iron-proxy/accounts/${enc(id)}/open`);
      const paneId = openedPaneId(res);
      if (!paneId) throw new Error("The daemon did not open a Build pane.");
      router.push(terminalsHref(paneId));
    });
  }

  async function act(key: string, fn: () => Promise<unknown>, done?: string) {
    const ok = await run(key, async () => {
      await fn();
    });
    if (ok && done) setNote(done);
    if (ok) reload();
  }

  async function addAccount() {
    const title = addTitle.trim();
    if (!title) return;
    let id: string | null = null;
    const ok = await run("add", async () => {
      const res = await post<unknown>("/iron-proxy/accounts", { provider: addProvider, title });
      id = createdId(res);
    });
    if (!ok) return;
    setAddTitle("");
    setAdded(id ? { id, title } : null);
    if (!id) setNote(`Added “${title}”. Sign it in from its row below.`);
    reload();
  }

  const starting = waiting || (status.enabled && !status.running && !status.error);
  const checked = stopping ? false : waiting || status.enabled;
  const anyBusy = busy !== null;

  const toggleEl = (
    <div className="flex items-center gap-2">
      <span className="text-[11px] text-zinc-500">
        {starting ? "Starting…" : status.running ? "On" : "Off"}
      </span>
      <button
        id="iron-proxy-toggle"
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label="Use Iron-Proxy accounts"
        disabled={busy === "toggle"}
        onClick={toggle}
        className={`relative h-6 w-11 shrink-0 rounded-full border transition-colors disabled:opacity-60 ${
          checked ? "border-accent/40 bg-accent/30" : "border-white/10 bg-white/[0.05]"
        }`}
      >
        <span
          className={`absolute top-1/2 h-4 w-4 -translate-y-1/2 rounded-full transition-all ${
            checked ? "left-[1.6rem] bg-accent shadow-glow-sm" : "left-1 bg-zinc-400"
          }`}
        />
      </button>
    </div>
  );

  return (
    <div id="iron-proxy-card">
      <Card title="Iron-Proxy accounts" icon={<Users size={15} />} right={toggleEl}>
        {/* Status line */}
        <div data-testid="iron-proxy-status" className="text-xs text-zinc-400">
          {status.running ? (
            <span className="flex flex-wrap items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_8px_2px_rgba(52,211,153,0.5)]" />
              <span className="font-medium text-emerald-300">Running</span>
              <span className="text-zinc-500">
                · {status.owned ? "started by Iron Jarvis" : "already running on this PC"}
                {status.version ? ` · v${status.version.replace(/^v/, "")}` : ""}
              </span>
            </span>
          ) : starting ? (
            <LoaderInline label="Starting…" />
          ) : status.enabled && status.error ? null : (
            <span className="text-zinc-500">
              Off — Iron Jarvis uses each CLI&apos;s own sign-in.
            </span>
          )}
        </div>

        {status.running && isOutdatedError(status.error) && (
          <div
            id="iron-proxy-outdated"
            role="alert"
            className="mt-3 flex items-start gap-2 rounded-xl border border-amber-500/40 bg-amber-500/[0.1] px-3 py-2.5 text-sm text-amber-100"
          >
            <TriangleAlert size={16} className="mt-0.5 shrink-0 text-amber-300" />
            <div>
              <div className="font-medium">{status.error}</div>
              <div className="mt-0.5 text-xs text-amber-200/80">
                Until then Iron Jarvis cannot pick accounts through it: Build&apos;s “as &lt;account&gt;”
                launches and account switching are refused.
              </div>
            </div>
          </div>
        )}
        {status.running && status.error && !isOutdatedError(status.error) && (
          <div className="mt-3">
            <ErrorNote>{status.error}</ErrorNote>
          </div>
        )}
        {status.enabled && !status.running && !waiting && status.error && (
          <div className="mt-3">
            <ErrorNote>{status.error}</ErrorNote>
          </div>
        )}
        {actionError && actionError !== status.error && (
          <div className="mt-3">
            <ErrorNote>{actionError}</ErrorNote>
          </div>
        )}
        {note && (
          <div className="mt-3">
            <SuccessNote>{note}</SuccessNote>
          </div>
        )}
        {signedIn && !note && (
          <div className="mt-3" data-testid="iron-proxy-signed-in">
            <SuccessNote>{signedIn}</SuccessNote>
          </div>
        )}
        {added && (
          <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-emerald-500/25 bg-emerald-500/[0.07] px-3 py-2 text-sm text-emerald-200">
            <span>Added “{added.title}”. Sign it in to use it.</span>
            <button
              type="button"
              className="btn-accent px-2.5 py-1 text-xs"
              disabled={anyBusy}
              onClick={() => {
                const id = added.id;
                setAdded(null);
                void signIn(id);
              }}
            >
              <LogIn size={13} /> Sign in now
            </button>
          </div>
        )}

        {status.running && (
          <div className="mt-4 space-y-4">
            {groups.length === 0 && (
              <p className="text-xs text-zinc-500">
                No accounts yet. Add one below, or use this PC&apos;s existing login.
              </p>
            )}
            {groups.map((g) => {
              const ids = g.accounts.map((a) => a.id);
              return (
                <div key={g.provider} data-testid={`iron-proxy-group-${g.provider}`}>
                  <div className="mb-1.5 flex items-baseline gap-2">
                    <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-400">
                      {providerLabel(g.provider)}
                    </span>
                    <span className="text-[11px] text-zinc-600">
                      {usedBy[g.provider]
                        ? `Iron Jarvis's ${usedBy[g.provider]} runs as these, top first`
                        : "Not used by Iron Jarvis"}
                    </span>
                  </div>
                  <div className="divide-y divide-white/[0.06] rounded-xl border border-white/[0.06] bg-white/[0.02]">
                    {g.accounts.map((a, i) => (
                      <AccountRow
                        key={a.id}
                        a={a}
                        first={i === 0}
                        last={i === g.accounts.length - 1}
                        busy={anyBusy}
                        onSignIn={() => signIn(a.id)}
                        onCheck={() => checkAgain(a.id, a.title)}
                        onOpen={() => openInBuild(a.id)}
                        onUnpark={() =>
                          act(`${a.id}:unpark`, () => post(`/iron-proxy/accounts/${enc(a.id)}/unpark`))
                        }
                        onMove={(dir) =>
                          act(`${a.id}:move`, () =>
                            post("/iron-proxy/accounts/reorder", {
                              provider: g.provider,
                              ids: movedIds(ids, i, dir),
                            }),
                          )
                        }
                        onEnable={() =>
                          act(`${a.id}:enable`, () =>
                            patch(`/iron-proxy/accounts/${enc(a.id)}`, { enabled: !a.enabled }),
                          )
                        }
                        onRemove={() =>
                          act(
                            `${a.id}:remove`,
                            () => del(`/iron-proxy/accounts/${enc(a.id)}`),
                            `Removed “${a.title}”.`,
                          )
                        }
                      />
                    ))}
                  </div>
                </div>
              );
            })}

            {toAdopt.length > 0 && (
              <div className="space-y-1.5">
                {toAdopt.map((d) => (
                  <div
                    key={`${d.provider}:${d.home}`}
                    data-testid={`iron-proxy-discovered-${d.provider}`}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-dashed border-white/10 px-3 py-2"
                  >
                    <div className="min-w-0 text-xs">
                      <div className="text-zinc-200">
                        {providerLabel(d.provider)} is already signed in on this PC
                        {d.signed_in ? "" : " (its CLI says it is signed out)"}
                      </div>
                      <div className="truncate font-mono text-[10px] text-zinc-600">{d.home}</div>
                    </div>
                    <button
                      type="button"
                      className="btn-ghost px-2.5 py-1 text-xs"
                      disabled={anyBusy}
                      onClick={() =>
                        act(
                          `adopt:${d.home}`,
                          () =>
                            post("/iron-proxy/accounts/adopt", {
                              provider: d.provider,
                              home: d.home,
                              ...(d.title ? { title: d.title } : {}),
                            }),
                          `Now using this PC's ${providerLabel(d.provider)} login.`,
                        )
                      }
                    >
                      <Laptop size={13} /> Use this PC&apos;s login
                    </button>
                  </div>
                ))}
              </div>
            )}

            <form
              className="flex flex-wrap items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                void addAccount();
              }}
            >
              <select
                aria-label="Provider"
                value={addProvider}
                onChange={(e) => setAddProvider(e.target.value)}
                className="field w-auto text-xs"
              >
                {ADDABLE_PROVIDERS.map((p) => (
                  <option key={p} value={p}>
                    {providerLabel(p)}
                  </option>
                ))}
              </select>
              <input
                aria-label="Account name"
                value={addTitle}
                onChange={(e) => setAddTitle(e.target.value)}
                placeholder="Name, e.g. Work Claude"
                className="field min-w-[10rem] flex-1 text-xs"
              />
              <button
                type="submit"
                disabled={anyBusy || !addTitle.trim()}
                className="btn-ghost px-3 py-1.5 text-xs"
              >
                {busy === "add" ? <LoaderInline label="Adding…" /> : (
                  <>
                    <Plus size={13} /> Add account
                  </>
                )}
              </button>
            </form>
          </div>
        )}

        <p className="mt-4 text-[11px] leading-relaxed text-zinc-600">
          When this is on, Iron Jarvis&apos;s Claude, Codex and Grok calls run as the first usable
          account of that provider. A limit moves the call to the next account of the SAME
          provider — never to another provider. API-key accounts are managed in Iron-Proxy, but
          Iron Jarvis keeps using its own keys.
        </p>
        <p id="iron-proxy-build-help" className="mt-1.5 text-[11px] leading-relaxed text-zinc-600">
          How accounts work in Build: a terminal pane keeps the account it started on — read
          “Several accounts in Build” in the Handbook (Help → Guides).
        </p>
      </Card>
    </div>
  );
}

function AccountRow({
  a,
  first,
  last,
  busy,
  onSignIn,
  onCheck,
  onOpen,
  onUnpark,
  onMove,
  onEnable,
  onRemove,
}: {
  a: IronProxyAccount;
  first: boolean;
  last: boolean;
  busy: boolean;
  onSignIn: () => void;
  onCheck: () => void;
  onOpen: () => void;
  onUnpark: () => void;
  onMove: (dir: -1 | 1) => void;
  onEnable: () => void;
  onRemove: () => Promise<void>;
}) {
  const chip = stateChip(a);
  const usage = usageLine(a.usage);
  const isCli = a.lane === "cli";
  const parked = a.state?.status === "parked";
  // v1.302.0: a CLI account of Claude/Codex/Grok can start its CLI in Build.
  // Parked / signed-out / switched-off accounts could not start a session, so
  // the button says why instead of opening a pane that is refused.
  const canOpen = isCli && BUILD_PROVIDERS.has(a.provider);
  const openBlocked = !a.enabled
    ? "This account is switched off — enable it first."
    : parked
      ? "This account is parked — unpark it, or wait until its limit resets."
      : a.state?.status === "unauthenticated"
        ? "Sign this account in first."
        : null;
  return (
    <div
      id={`iron-proxy-account-${a.id}`}
      data-status={a.state?.status ?? "unknown"}
      className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5"
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate text-sm font-medium text-zinc-100">{a.title}</span>
          <span
            data-testid="iron-proxy-chip"
            title={a.state?.parkedReason?.message ?? undefined}
          >
            <Badge value={chip.label} tone={chip.tone} keepCase />
          </span>
        </div>
        {!isCli && (
          <div className="mt-0.5 text-[11px] text-zinc-500">
            API key — managed in Iron-Proxy; Iron Jarvis uses its own keys.
          </div>
        )}
        {usage && (
          <div data-testid="iron-proxy-usage" className="mt-0.5 text-[11px] text-zinc-500">
            {usage}
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {isCli && (
          <button
            type="button"
            onClick={onSignIn}
            disabled={busy}
            className={`${a.state?.status === "unauthenticated" ? "btn-accent" : "btn-ghost"} px-2.5 py-1 text-xs`}
          >
            <LogIn size={13} /> Sign in
          </button>
        )}
        {isCli && a.state?.status === "unauthenticated" && (
          <button
            type="button"
            data-testid={`iron-proxy-check-${a.id}`}
            onClick={onCheck}
            disabled={busy}
            title="Ask Iron-Proxy to check this account's login again now"
            className="btn-ghost px-2.5 py-1 text-xs"
          >
            <RefreshCw size={13} /> Check again
          </button>
        )}
        {canOpen && (
          <button
            type="button"
            data-testid={`iron-proxy-open-${a.id}`}
            onClick={onOpen}
            disabled={busy || openBlocked !== null}
            title={openBlocked ?? "Open a new Build terminal with this account's CLI already running"}
            className="btn-ghost px-2.5 py-1 text-xs"
          >
            <SquareTerminal size={13} /> Open in Build
          </button>
        )}
        {parked && (
          <button type="button" onClick={onUnpark} disabled={busy} className="btn-ghost px-2.5 py-1 text-xs">
            <Play size={13} /> Unpark
          </button>
        )}
        <button
          type="button"
          aria-label={`Move ${a.title} up`}
          title="Try this account earlier"
          onClick={() => onMove(-1)}
          disabled={busy || first}
          className="btn-ghost px-1.5 py-1 text-xs"
        >
          <ArrowUp size={13} />
        </button>
        <button
          type="button"
          aria-label={`Move ${a.title} down`}
          title="Try this account later"
          onClick={() => onMove(1)}
          disabled={busy || last}
          className="btn-ghost px-1.5 py-1 text-xs"
        >
          <ArrowDown size={13} />
        </button>
        <button type="button" onClick={onEnable} disabled={busy} className="btn-ghost px-2.5 py-1 text-xs">
          <Power size={13} /> {a.enabled ? "Disable" : "Enable"}
        </button>
        <ConfirmButton
          onConfirm={onRemove}
          label="Remove"
          confirmLabel="Press again to remove"
          title="Remove this account from Iron-Proxy (press twice)"
        />
      </div>
    </div>
  );
}
