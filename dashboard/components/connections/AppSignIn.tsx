"use client";

/**
 * An app that SIGNS IN on this PC (v1.356.0, G60) — Microsoft 365 first.
 *
 * Two small pieces for the Directory card, both in the calm look the card's
 * other rows already use (no new boxes, theme tokens only):
 *
 * - `ChoiceField` — a field the user ANSWERS rather than types (the daemon
 *   sends it with `options`). Microsoft 365 asks "Which Microsoft account?":
 *   a work or school account and a personal one sign in through different
 *   Microsoft authorities, so the answer is part of the connection. Native
 *   radios in a fieldset: arrow keys move between answers, a screen reader
 *   hears the question as the group's name.
 *
 * - `AppSignIn` — the card's "Sign in to Microsoft" press. It opens a Build
 *   pane running the app's own sign-in (`lib/cliSignIn.launchAppSignIn`, the
 *   same door the Accounts rows' Sign in uses; the job is named by the
 *   daemon's row, `signin.cli`, never a command). That sign-in prints a web
 *   address and a code; the user finishes there. The connected app sees the
 *   sign-in without a restart (its server re-reads its saved sign-in before
 *   every use), so the press is the whole job.
 *
 * Its own file, not lib/api.ts (mocked wholesale by ~70 test files) and not
 * DirectoryPage (another lane's file); the Directory mounts both.
 */

import Link from "next/link";
import { useId, useState } from "react";
import { Loader2, LogIn } from "lucide-react";
import { launchAppSignIn, paneHref } from "@/lib/cliSignIn";
import { CALM_ACTION } from "@/components/connections/endpointChip";

/** One answer to a choice field (GET /connectors `fields[].options`). */
export interface FieldOption {
  value: string;
  label: string;
  help?: string;
}

/** What a saved app's Sign in press needs (GET /connectors row `signin`):
 *  the Build set-up job to open, the press's words, the account kind. */
export interface AppSigninRow {
  cli: string;
  label: string;
  account?: string;
}

/** The link look the Accounts rows use for "Open the Build pane". */
const ROW_LINK =
  "font-medium text-accent-soft underline decoration-accent/40 underline-offset-2 transition-colors hover:text-accent";

/** A choice field, drawn as the card's other fields are: a small label, the
 *  answers, the field's help under them. */
export function ChoiceField({
  name,
  label,
  help,
  options,
  value,
  optional = false,
  autoFocus = false,
  onChange,
}: {
  name: string;
  label: string;
  help?: string;
  options: FieldOption[];
  value: string;
  optional?: boolean;
  autoFocus?: boolean;
  onChange: (value: string) => void;
}) {
  const group = `${useId()}-${name}`;
  return (
    <fieldset data-testid={`choice-${name}`} className="space-y-1">
      <legend className="text-[11px] font-medium text-zinc-400">
        {label}
        {!optional && <span className="ml-0.5 text-tone-danger">*</span>}
        {optional && <span className="ml-1 text-zinc-600">(optional)</span>}
      </legend>
      {options.map((o, i) => (
        <label
          key={o.value}
          className="flex cursor-pointer items-start gap-2.5 rounded-lg px-1 py-1 text-[12px] transition-colors hover:bg-white/[0.03]"
        >
          <input
            type="radio"
            name={group}
            value={o.value}
            checked={value === o.value}
            onChange={() => onChange(o.value)}
            autoFocus={autoFocus && i === 0}
            data-testid={`choice-${name}-${o.value}`}
            className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-accent"
          />
          <span>
            <span className="text-zinc-200">{o.label}</span>
            {o.help && <span className="block text-[11px] text-zinc-500">{o.help}</span>}
          </span>
        </label>
      ))}
      {help && <span className="block text-[11px] leading-relaxed text-zinc-500">{help}</span>}
    </fieldset>
  );
}

/** The one sentence under a saved app, beside the press. True before AND
 *  after a sign-in: the daemon cannot see the app's sign-in without asking
 *  the app, so the card never claims either way. */
export function signInLine(signin: Pick<AppSigninRow, "account">): string {
  const who = (signin.account ?? "").trim();
  return who
    ? `It works once you sign in with your ${who.charAt(0).toLowerCase()}${who.slice(1)}.`
    : "It works once you sign in.";
}

/** What the pane shows, said after the press. */
export const PANE_OPENED_LINE =
  "A Build pane opened with a web address and a code. Open the address, type the code and sign in there.";

/** The card's Sign in press. `id` is the card's id (test ids). */
export function AppSignIn({ id, signin }: { id: string; signin: AppSigninRow }) {
  const [busy, setBusy] = useState(false);
  // undefined = not pressed yet; null = opened, pane id unknown.
  const [paneId, setPaneId] = useState<string | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      setPaneId(await launchAppSignIn(signin.cli));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid={`connector-signin-${id}`} className="text-[11px] leading-relaxed text-zinc-500">
      {paneId !== undefined ? (
        <span data-testid={`connector-signin-hint-${id}`}>
          {PANE_OPENED_LINE}{" "}
          <Link href={paneHref(paneId)} className={ROW_LINK}>
            Open the Build pane
          </Link>
        </span>
      ) : (
        <>
          <span className="block">{signInLine(signin)}</span>
          <button
            type="button"
            data-testid={`connector-signin-press-${id}`}
            onClick={() => void start()}
            disabled={busy}
            className={`-ml-2.5 ${CALM_ACTION}`}
          >
            {busy ? (
              <Loader2 size={13} className="animate-spin motion-reduce:animate-none" aria-hidden />
            ) : (
              <LogIn size={13} aria-hidden />
            )}
            {signin.label}
          </button>
        </>
      )}
      {error && (
        <span role="alert" data-testid={`connector-signin-error-${id}`} className="mt-0.5 block text-tone-danger">
          {error}
        </span>
      )}
    </div>
  );
}
