"use client";

/**
 * Your other computers (v1.497.0, vision plan leap 7, Phase 1).
 *
 * Settings › Connections. Three parts, in the order a person needs them:
 *
 * 1. Whether other computers can reach this one (the `lan_access` setting,
 *    which this card controls: SettingsHome CONTROLLED_IN_SECTION). Turning
 *    it on or off saves through the one settings writer and asks the desktop
 *    app for a tidy restart, because the desktop app applies it when it starts
 *    Iron Jarvis (the owner's decision 1: one confirmation, restart included).
 * 2. The paired computers: when each was last heard from, Check, Remove.
 * 3. Pairing: "Show a pairing code" here, or "Use a pairing code" shown by the
 *    other computer.
 *
 * Reads: on mount (then one Check of every paired computer), after a
 * reconnect (the restart), after an answer, on `peer.paired` / `peer.removed`
 * (the other computer typed this one's code), and when a shown code runs
 * out. Never on a clock.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Monitor } from "lucide-react";
import { ApiError, del, get, post, put } from "@/lib/api";
import { useDaemonEpoch } from "@/lib/daemonEpoch";
import { requestTidyRestart } from "@/lib/desktopShell";
import { useEvents } from "@/lib/useEvents";
import { timeAgo } from "@/lib/format";
import { fillInFirst, whyDisabled } from "@/lib/whyDisabled";
import { Button, Card, ConfirmButton, Field, LoaderInline } from "@/components/ui";
import { LoadFailedLine } from "@/components/LoadFailedLine";
import {
  ADDRESS_LABEL,
  ADDRESS_PLACEHOLDER,
  ANSWERS_AFTER_RESTART,
  ANSWERS_CANNOT_COME_BACK,
  CALL_IT_LABEL,
  CALL_IT_PLACEHOLDER,
  CANCEL_CODE,
  CHECK,
  CODE_LABEL,
  CODE_NOT_SIX_DIGITS,
  CODE_PLACEHOLDER,
  CODE_TRIED_OUT,
  FIREWALL_LINE,
  NEEDS_NETWORK,
  NEEDS_RESTART,
  NETWORK_OFF,
  NETWORK_ON,
  NETWORK_SAVED,
  NETWORK_SETTING_KEY,
  NO_PEERS,
  PAIR,
  PAIRING,
  PEER_EVENT_TYPES,
  REMOVE,
  RESTARTING,
  RESTART_NOW,
  RESTART_NO_BRIDGE,
  RESTART_REFUSED,
  SECTION_LABEL,
  SHOW_CODE,
  TAILSCALE_LINE,
  TASKS_LINE,
  THIS_NAME_LABEL,
  TURN_OFF,
  TURN_OFF_TITLE,
  TURN_ON,
  USE_CODE,
  codeEndsWords,
  codeSteps,
  decodePeers,
  peerStatusLine,
  removeTitle,
  typedCode,
  type Peer,
  type PeersView,
} from "@/lib/otherComputers";

/** A burst of events is one read. */
const EVENT_REREAD_MS = 600;

function failureWords(err: unknown, fallback: string): string {
  if (err instanceof ApiError && err.message) return err.message;
  return fallback;
}

function PeerRow({
  peer,
  onChecked,
  onRemoved,
}: {
  peer: Peer;
  onChecked: (view: unknown) => void;
  onRemoved: (said: string) => void;
}) {
  const [checking, setChecking] = useState(false);
  const [problem, setProblem] = useState("");
  const status = peerStatusLine(peer, timeAgo);
  const name = encodeURIComponent(peer.name);

  async function check() {
    setChecking(true);
    setProblem("");
    try {
      onChecked(await post<unknown>(`/peers/${name}/check`, {}));
    } catch (err) {
      setProblem(failureWords(err, "Couldn't check that computer. Try again."));
    } finally {
      setChecking(false);
    }
  }

  async function remove() {
    setProblem("");
    try {
      const out = await del<{ said?: unknown }>(`/peers/${name}`);
      onRemoved(typeof out?.said === "string" ? out.said : "Removed.");
    } catch (err) {
      setProblem(failureWords(err, "Couldn't remove that computer. Try again."));
    }
  }

  return (
    <li data-testid={`peer-${peer.name}`} className="py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-medium text-zinc-100">@{peer.name}</span>
        {peer.theirName && peer.theirName !== peer.name && (
          <span className="text-[12px] text-zinc-500">({peer.theirName})</span>
        )}
        <span className="text-[12px] text-zinc-500">{peer.address}</span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            onClick={() => void check()}
            disabled={checking}
            data-testid={`peer-check-${peer.name}`}
            title={`Ask ${peer.name} whether it still answers`}
            className="rounded-md px-2 py-1 text-[12px] font-medium text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 disabled:opacity-50"
          >
            {checking ? <LoaderInline label="Checking…" /> : CHECK}
          </button>
          <ConfirmButton
            variant="calm"
            onConfirm={remove}
            label={REMOVE}
            title={removeTitle(peer.name)}
            testId={`peer-remove-${peer.name}`}
          />
        </span>
      </div>
      <p
        data-testid={`peer-status-${peer.name}`}
        className={`mt-1 text-[12px] leading-relaxed ${status.warn ? "text-tone-warn" : "text-zinc-500"}`}
      >
        {status.words}
      </p>
      {problem && (
        <p role="alert" className="mt-1 text-[12px] text-tone-danger">
          {problem}
        </p>
      )}
    </li>
  );
}

export function OtherComputers() {
  const [view, setView] = useState<PeersView | null>(null);
  const [failure, setFailure] = useState<ApiError | null>(null);
  const [supported, setSupported] = useState(true);
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState<"" | "network" | "code" | "join">("");
  const [joining, setJoining] = useState(false);
  const [address, setAddress] = useState("");
  const [code, setCode] = useState("");
  const [callIt, setCallIt] = useState("");
  const [thisName, setThisName] = useState<string | null>(null);
  const epoch = useDaemonEpoch();
  const seq = useRef(0);
  const checked = useRef(false);

  const load = useCallback(async () => {
    const mine = ++seq.current;
    try {
      const raw = await get<unknown>("/peers");
      if (mine !== seq.current) return null;
      const next = decodePeers(raw);
      setView(next);
      setFailure(null);
      setSupported(true);
      return next;
    } catch (err) {
      if (mine !== seq.current) return null;
      // An older Iron Jarvis has no pairing: show nothing, never a broken control.
      if (err instanceof ApiError && err.status === 404) {
        setSupported(false);
        return null;
      }
      setFailure(err instanceof ApiError ? err : new ApiError(String(err), 0));
      return null;
    }
  }, []);

  /** Take a view an answer returned (it is the same shape as GET /peers). */
  const take = useCallback((raw: unknown) => {
    const r = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
    // A per-peer check answers {ok, peer}; everything else answers the view.
    if ("peers" in r) {
      seq.current += 1;
      setView(decodePeers(raw));
    } else {
      void load();
    }
  }, [load]);

  // Iron Jarvis came back (review, v1.497.0): "Restarting Iron Jarvis…" is no
  // longer true. Only that note is taken back; a refused restart's words stay.
  const shownEpoch = useRef(epoch);
  useEffect(() => {
    if (epoch === shownEpoch.current) return;
    shownEpoch.current = epoch;
    setNote((said) => (said === RESTARTING ? "" : said));
  }, [epoch]);

  // Mount, and the daemon coming back (after the restart this card asked for).
  useEffect(() => {
    void (async () => {
      const first = await load();
      // Once per mount: ask every paired computer whether it still answers,
      // so "Last heard from" is true when the page opens.
      if (first && first.peers.length && !checked.current) {
        checked.current = true;
        try {
          take(await post<unknown>("/peers/check", {}));
        } catch {
          // The list is still right; each row keeps its last answer.
        }
      }
    })();
  }, [load, take, epoch]);

  // The other computer typed this one's code, or a pairing ended.
  const { events } = useEvents(5, { types: PEER_EVENT_TYPES });
  const newest = events[0]?.id ?? "";
  const newestType = events[0]?.type ?? "";
  useEffect(() => {
    if (!newest) return;
    // The code this computer shows was typed wrong too often (review,
    // v1.497.0): say so, and the re-read takes the dead code down.
    if (newestType === "peer.code_ended") setNote(CODE_TRIED_OUT);
    const t = setTimeout(() => void load(), EVENT_REREAD_MS);
    return () => clearTimeout(t);
  }, [newest, newestType, load]);

  // A shown code runs out: read once at that moment, so it is not left up.
  const codeEnds = view?.code?.expiresAt ?? "";
  useEffect(() => {
    if (!codeEnds) return;
    const wait = Date.parse(codeEnds) - Date.now();
    if (!Number.isFinite(wait)) return;
    // A browser fires a timer longer than 2^31-1 ms at once; a code lives ten
    // minutes, so the cap only matters for a clock far off.
    const t = setTimeout(() => void load(), Math.min(Math.max(0, wait) + 500, 2_147_483_647));
    return () => clearTimeout(t);
  }, [codeEnds, load]);

  async function restartToApply() {
    setNote(RESTARTING);
    const tidy = await requestTidyRestart();
    if (tidy === "no-bridge") setNote(RESTART_NO_BRIDGE);
    else if (tidy === "refused") setNote(RESTART_REFUSED);
  }

  async function setNetwork(on: boolean) {
    setBusy("network");
    setProblem("");
    setNote("");
    try {
      await put("/settings/values", { values: { [NETWORK_SETTING_KEY]: on } });
      await restartToApply();
      void load();
    } catch (err) {
      setProblem(failureWords(err, "Couldn't change the setting. Try again."));
    } finally {
      setBusy("");
    }
  }

  async function showCode() {
    setBusy("code");
    setProblem("");
    setNote("");
    try {
      take(await post<unknown>("/peers/code", thisName !== null ? { this_name: thisName } : {}));
      setThisName(null);
    } catch (err) {
      setProblem(failureWords(err, "Couldn't make a pairing code. Try again."));
    } finally {
      setBusy("");
    }
  }

  async function cancelCode() {
    setProblem("");
    try {
      take(await post<unknown>("/peers/code/cancel", {}));
    } catch (err) {
      setProblem(failureWords(err, "Couldn't cancel the code. Try again."));
    }
  }

  async function join() {
    setBusy("join");
    setProblem("");
    setNote("");
    try {
      const out = await post<Record<string, unknown>>("/peers/join", {
        address: address.trim(),
        code: typedCode(code),
        name: callIt.trim(),
        this_name: thisName ?? "",
      });
      setNote(typeof out?.said === "string" ? out.said : "Paired.");
      setAddress("");
      setCode("");
      setCallIt("");
      setThisName(null);
      setJoining(false);
      take(out);
    } catch (err) {
      setProblem(failureWords(err, "Couldn't pair with that computer. Try again."));
    } finally {
      setBusy("");
    }
  }

  if (!supported) return null;

  const net = view?.network;
  // Saved on but not in effect yet: what is missing is the restart, not the
  // setting (review, v1.497.0).
  const needsNetwork = net?.saved ? NEEDS_RESTART : NEEDS_NETWORK;
  const name = thisName ?? view?.thisName ?? "";
  const canJoin = !!address.trim() && !!typedCode(code) && busy === "";
  // A greyed-out Pair says why (Wave D, lib/whyDisabled); busy speaks for itself.
  const missing = [!address.trim() && ADDRESS_LABEL, !code.trim() && CODE_LABEL].filter(
    (l): l is string => !!l,
  );
  const pairWhy =
    busy !== ""
      ? null
      : whyDisabled(fillInFirst(missing, "pair"), !typedCode(code) && CODE_NOT_SIX_DIGITS);

  return (
    <Card title={SECTION_LABEL} icon={<Monitor className="h-4 w-4" />}>
      <div className="space-y-4" data-testid="other-computers">
        <p className="text-[13px] leading-relaxed text-zinc-400">{TASKS_LINE}</p>

        {/* Always mounted, so a screen reader hears the words when they come. */}
        <p role="status" data-testid="other-computers-note" className="min-h-5 text-[13px] text-zinc-300">
          {note}
        </p>
        {problem && (
          <p role="alert" data-testid="other-computers-problem" className="text-[13px] text-tone-danger">
            {problem}
          </p>
        )}

        {failure && !view && (
          <LoadFailedLine thing="Your other computers" error={failure} onRetry={() => void load()} />
        )}

        {view && net && (
          <>
            <section aria-label="Reaching this computer" data-testid="other-computers-network" className="space-y-2">
              {net.applied ? (
                <>
                  <p className="text-[13px] text-zinc-300">{NETWORK_ON}</p>
                  <ul className="flex flex-wrap gap-2" data-testid="other-computers-addresses">
                    {net.addresses.map((a) => (
                      <li key={a}>
                        <code className="rounded-md bg-ink-900/80 px-2 py-1 font-mono text-[12px] text-zinc-100">
                          {a}
                        </code>
                      </li>
                    ))}
                  </ul>
                  <p className="text-[12px] text-zinc-500">{TAILSCALE_LINE}</p>
                  <p className="text-[12px] text-zinc-500">{FIREWALL_LINE}</p>
                  <ConfirmButton
                    variant="calm"
                    onConfirm={() => setNetwork(false)}
                    label={TURN_OFF}
                    confirmLabel="Press again to turn off and restart"
                    title={TURN_OFF_TITLE}
                    testId="other-computers-network-off"
                  />
                </>
              ) : net.saved ? (
                <>
                  <p className="text-[13px] text-zinc-300">{NETWORK_SAVED}</p>
                  <Button
                    variant="primary"
                    size="sm"
                    data-testid="other-computers-restart"
                    onClick={() => void restartToApply()}
                  >
                    {RESTART_NOW}
                  </Button>
                </>
              ) : (
                <>
                  <p className="text-[13px] leading-relaxed text-zinc-300">{NETWORK_OFF}</p>
                  <ConfirmButton
                    onConfirm={() => setNetwork(true)}
                    label={TURN_ON}
                    confirmLabel="Press again to turn on and restart"
                    title="Let other computers on your network reach this Iron Jarvis. Iron Jarvis restarts"
                    testId="other-computers-network-on"
                  />
                </>
              )}
            </section>

            <section aria-label="Paired computers" className="space-y-1">
              {view.peers.length ? (
                <ul className="divide-y hairline" data-testid="other-computers-list">
                  {view.peers.map((p) => (
                    <PeerRow
                      key={p.name}
                      peer={p}
                      onChecked={(raw) => take(raw)}
                      onRemoved={(said) => {
                        setNote(said);
                        void load();
                      }}
                    />
                  ))}
                </ul>
              ) : (
                <p data-testid="other-computers-empty" className="text-[13px] text-zinc-500">
                  {NO_PEERS}
                </p>
              )}
            </section>

            <section aria-label="Pair with another computer" className="space-y-3">
              <Field label={THIS_NAME_LABEL} hint="The other computer calls this one by it.">
                <input
                  data-testid="other-computers-this-name"
                  value={name}
                  maxLength={40}
                  onChange={(e) => setThisName(e.target.value)}
                  autoComplete="off"
                  className="field w-full max-w-xs text-[13px]"
                />
              </Field>

              {view.code ? (
                <div data-testid="other-computers-code" className="rounded-lg bg-accent/[0.06] px-3 py-3">
                  <p className="font-mono text-[28px] font-semibold tracking-[0.2em] text-zinc-100" aria-label={`The code is ${view.code.code.split("").join(" ")}`}>
                    {view.code.shown}
                  </p>
                  <p className="mt-1 text-[12px] text-zinc-400">
                    {codeSteps(net.addresses)} {codeEndsWords(view.code.expiresAt)}
                  </p>
                  <button type="button" onClick={() => void cancelCode()} className="btn-ghost mt-1 py-0.5 text-[12px]">
                    {CANCEL_CODE}
                  </button>
                </div>
              ) : (
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    variant="primary"
                    size="sm"
                    data-testid="other-computers-show-code"
                    disabled={!net.applied || busy !== ""}
                    title={net.applied ? undefined : needsNetwork}
                    onClick={() => void showCode()}
                  >
                    {busy === "code" ? "…" : SHOW_CODE}
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    data-testid="other-computers-use-code"
                    aria-expanded={joining}
                    onClick={() => setJoining((v) => !v)}
                  >
                    {USE_CODE}
                  </Button>
                </div>
              )}
              {!view.code && !net.applied && (
                <p data-testid="other-computers-needs-network" className="text-[12px] text-zinc-500">
                  {needsNetwork}
                </p>
              )}

              {joining && !view.code && (
                <form
                  data-testid="other-computers-join"
                  className="space-y-3 rounded-lg border hairline p-3"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (canJoin) void join();
                  }}
                >
                  <Field label={ADDRESS_LABEL}>
                    <input
                      data-testid="other-computers-address"
                      value={address}
                      onChange={(e) => setAddress(e.target.value)}
                      placeholder={ADDRESS_PLACEHOLDER}
                      autoComplete="off"
                      inputMode="url"
                      className="field w-full max-w-xs text-[13px]"
                    />
                  </Field>
                  <Field label={CODE_LABEL}>
                    <input
                      data-testid="other-computers-code-input"
                      value={code}
                      onChange={(e) => setCode(e.target.value)}
                      placeholder={CODE_PLACEHOLDER}
                      autoComplete="one-time-code"
                      inputMode="numeric"
                      maxLength={11}
                      className="field w-full max-w-[10rem] font-mono text-[13px]"
                    />
                  </Field>
                  <Field label={CALL_IT_LABEL}>
                    <input
                      data-testid="other-computers-call-it"
                      value={callIt}
                      onChange={(e) => setCallIt(e.target.value)}
                      placeholder={CALL_IT_PLACEHOLDER}
                      maxLength={40}
                      autoComplete="off"
                      className="field w-full max-w-xs text-[13px]"
                    />
                  </Field>
                  {!net.applied && (
                    <p data-testid="other-computers-no-answers" className="text-[12px] text-tone-warn">
                      {net.saved ? ANSWERS_AFTER_RESTART : ANSWERS_CANNOT_COME_BACK}
                    </p>
                  )}
                  <Button
                    type="submit"
                    variant="primary"
                    size="sm"
                    data-testid="other-computers-pair"
                    disabled={!canJoin}
                    title={canJoin ? undefined : pairWhy ?? undefined}
                  >
                    {busy === "join" ? PAIRING : PAIR}
                  </Button>
                </form>
              )}
            </section>
          </>
        )}
      </div>
    </Card>
  );
}

export default OtherComputers;
