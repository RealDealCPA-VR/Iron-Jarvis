"use client";

/**
 * The App ID / App secret boxes for a Drive / OneDrive / Dropbox sign-in
 * (v1.449.0, readiness Wave C item 22 — ease-of-use review item 16).
 *
 * ONE form, drawn on the Directory card AND the Connections card while the
 * daemon says no OAuth client is registered (`oauth_client_configured ===
 * false`): the vendor-console step in plain words, the redirect address with
 * Copy (the user pastes it into the vendor's console), then two boxes whose
 * Save writes the vault names the daemon reads (lib/oauthApp.ts — pinned
 * equal to `platform._oauth_app`). Nothing is typed by its internal name any
 * more; the names are still SAID, quietly, so a person who looks in Secrets
 * knows what they are looking at.
 *
 * The secret box is a password field and its value never reaches the screen
 * again after Save (the daemon returns no secret value). A Save with no id
 * does nothing; a secret alone is refused in words (the id is what the
 * vendor uses to know which app is asking).
 */

import { useState } from "react";
import { Check, Copy, ExternalLink } from "lucide-react";
import { post, ApiError } from "@/lib/api";
import { copyPlain } from "@/components/settings/MaintenanceTools";
import {
  VENDOR_CONSOLES,
  oauthClientIdName,
  oauthClientSecretName,
  oauthRedirectAddress,
  vendorConsoleSentence,
} from "@/lib/oauthApp";

export const OAUTH_APP_SAVED = "Saved. Press Connect to sign in.";
export const OAUTH_APP_NEEDS_ID = "Paste the app’s ID first — the secret alone can’t sign in.";

export function OAuthAppForm({
  provider,
  appName,
  onSaved,
  testId = `oauth-app-form-${provider}`,
}: {
  /** The connection's provider id (`google_drive`, `onedrive`, `dropbox`). */
  provider: string;
  /** The vendor's name as the card shows it ("Dropbox"). */
  appName: string;
  /** Called after a successful save, so the card re-reads its row. */
  onSaved?: () => void;
  testId?: string;
}) {
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "ok" | "warn"; text: string } | null>(null);
  const [copied, setCopied] = useState<"copied" | "unavailable" | null>(null);
  const redirect = oauthRedirectAddress(provider);
  const console_ = VENDOR_CONSOLES[provider];

  async function copyRedirect() {
    const r = await copyPlain(redirect);
    setCopied(r);
    window.setTimeout(() => setCopied(null), 2500);
  }

  async function save(e?: React.FormEvent) {
    e?.preventDefault();
    if (busy) return;
    const id = clientId.trim();
    const sec = secret.trim();
    if (!id) {
      setNote({ tone: "warn", text: OAUTH_APP_NEEDS_ID });
      return;
    }
    setBusy(true);
    setNote(null);
    try {
      await post("/secrets", {
        name: oauthClientIdName(provider),
        value: id,
        kind: "oauth_client",
        description: `${appName} app ID (for its sign-in)`,
      });
      if (sec) {
        await post("/secrets", {
          name: oauthClientSecretName(provider),
          value: sec,
          kind: "oauth_client",
          description: `${appName} app secret (for its sign-in)`,
        });
      }
      setClientId("");
      setSecret("");
      setNote({ tone: "ok", text: OAUTH_APP_SAVED });
      onSaved?.();
    } catch (err) {
      setNote({ tone: "warn", text: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={save}
      data-testid={testId}
      className="space-y-2 rounded-xl border border-white/[0.08] bg-white/[0.02] px-3 py-2.5 text-[12px] leading-relaxed text-zinc-300"
    >
      <p data-testid={`${testId}-step`}>{vendorConsoleSentence(provider, appName)}</p>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] text-zinc-500">Redirect address to allow:</span>
        <code
          data-testid={`${testId}-redirect`}
          className="break-all rounded bg-ink-900/80 px-1.5 py-0.5 font-mono text-[11px] text-zinc-200"
        >
          {redirect}
        </code>
        <button
          type="button"
          onClick={() => void copyRedirect()}
          data-testid={`${testId}-copy`}
          className="inline-flex items-center gap-1 rounded-md border border-white/[0.08] px-1.5 py-0.5 text-[11px] text-zinc-300 transition-colors hover:text-accent-soft"
          title="Copy the redirect address"
        >
          {copied === "copied" ? <Check size={11} aria-hidden /> : <Copy size={11} aria-hidden />}
          {copied === "copied" ? "Copied" : copied === "unavailable" ? "Select and copy it" : "Copy"}
        </button>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="block text-[11px] text-zinc-400">
          App ID
          <input
            type="text"
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            placeholder={`The ID ${console_?.name ?? appName} gave the app`}
            data-testid={`${testId}-id`}
            className="field mt-1 font-mono text-xs"
          />
        </label>
        <label className="block text-[11px] text-zinc-400">
          App secret <span className="text-zinc-600">(if it has one)</span>
          <input
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            autoComplete="off"
            placeholder="Kept encrypted on this PC"
            data-testid={`${testId}-secret`}
            className="field mt-1 font-mono text-xs"
          />
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="submit"
          disabled={busy || !clientId.trim()}
          data-testid={`${testId}-save`}
          className="btn-soft py-1.5 text-xs"
        >
          {busy ? "Saving…" : "Save app details"}
        </button>
        {console_ && (
          <a
            href={console_.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-[11px] text-zinc-500 transition-colors hover:text-accent-soft"
          >
            Open {console_.name}&apos;s console <ExternalLink size={11} aria-hidden />
          </a>
        )}
      </div>
      {note && (
        <p
          role="status"
          data-testid={`${testId}-note`}
          className={`text-[11px] ${note.tone === "ok" ? "text-tone-success" : "text-tone-warn"}`}
        >
          {note.text}
        </p>
      )}
      <p className="text-[10.5px] text-zinc-600">
        Kept in Secrets as <code className="font-mono">{oauthClientIdName(provider)}</code> and{" "}
        <code className="font-mono">{oauthClientSecretName(provider)}</code>.
      </p>
    </form>
  );
}
