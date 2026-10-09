"use client";

/**
 * The composer held a message because it looks like a key or token (calm UI
 * redesign S4, AUDIT Q9). Sent as a message it would sit in the chat history,
 * the model's context and every later turn — so it is held here, and the
 * value can go straight to the encrypted vault instead (POST /config/secret,
 * the same door the credential card uses). The rest of the message stays in
 * the box with the key taken out.
 */

import { useEffect, useState, type FormEvent } from "react";
import { KeyRound } from "lucide-react";
import { get, post } from "@/lib/api";
import { Button } from "@/components/ui";
import { extractSecret, guessSecretName } from "@/lib/configCards";

interface SecretKind {
  key: string;
  label: string;
  pattern: boolean;
}

const FALLBACK_KINDS: SecretKind[] = [
  { key: "connection.{provider}", label: "API key for a model provider", pattern: true },
  { key: "channel.{name}", label: "A notification channel's token", pattern: true },
  { key: "app.{name}", label: "An app's token", pattern: true },
  { key: "secret.{name}", label: "A named secret", pattern: true },
];

export function SecretPasteNotice({
  message,
  onSaved,
  onSendAnyway,
  onCancel,
}: {
  message: string;
  /** The value is stored; `rest` is the message with the key taken out. */
  onSaved: (rest: string, label: string) => void;
  onSendAnyway: () => void;
  onCancel: () => void;
}) {
  const value = extractSecret(message);
  const guess = guessSecretName(value);
  const [open, setOpen] = useState(false);
  const [kinds, setKinds] = useState<SecretKind[]>(FALLBACK_KINDS);
  const [kind, setKind] = useState(guess.key);
  const [arg, setArg] = useState(guess.arg);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    let live = true;
    get<{ secrets?: SecretKind[] }>("/settings/schema")
      .then((s) => {
        if (live && Array.isArray(s?.secrets) && s.secrets.length) setKinds(s.secrets);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [open]);

  const chosen = kinds.find((k) => k.key === kind) ?? kinds[0];
  const name = chosen?.pattern ? chosen.key.replace(/\{[^}]+\}/, arg.trim()) : chosen?.key ?? "";
  const argOk = !chosen?.pattern || /^[A-Za-z0-9_-]{1,64}$/.test(arg.trim());

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!value || !argOk) return;
    setBusy(true);
    setError("");
    try {
      await post("/config/secret", { name, value });
      onSaved(message.replace(value, "").replace(/\bBearer\s*$/, "").replace(/\s{2,}/g, " ").trim(), chosen?.label ?? name);
    } catch (err) {
      const detail = (err as { message?: string })?.message;
      setError(detail && !detail.includes(value) ? detail : "Couldn't save it — try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    // v1.326.0 (calm chat): it sits in the notices tray above the composer
    // card, whose rows draw their own hairline between them.
    <div data-testid="secret-held" role="alert" className="space-y-2 px-3 py-2 text-[12px] text-zinc-300">
      <p className="flex items-start gap-1.5">
        <KeyRound size={13} className="mt-0.5 shrink-0 text-tone-warn" aria-hidden />
        <span>
          That looks like a key or token. Sent as a message it would stay in this chat&apos;s history.
          Save it securely instead — it is stored encrypted on this PC and never shown in the chat.
        </span>
      </p>
      {!open ? (
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" size="sm" data-testid="secret-held-save" onClick={() => setOpen(true)}>
            Save it securely
          </Button>
          <button
            type="button"
            data-testid="secret-held-send"
            onClick={onSendAnyway}
            className="rounded-lg border border-white/10 px-2.5 py-1 text-zinc-300 hover:border-white/20"
          >
            Send anyway
          </button>
          <button type="button" onClick={onCancel} className="px-2 py-1 text-zinc-500 hover:text-zinc-300">
            Cancel
          </button>
        </div>
      ) : (
        <form onSubmit={save} className="flex flex-wrap items-center gap-2" data-testid="secret-held-form">
          <label className="sr-only" htmlFor="secret-held-kind">
            What is it?
          </label>
          <select
            id="secret-held-kind"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
            className="field py-1 text-[12px]"
          >
            {kinds.map((k) => (
              <option key={k.key} value={k.key}>
                {k.label}
              </option>
            ))}
          </select>
          {chosen?.pattern && (
            <>
              <label className="sr-only" htmlFor="secret-held-arg">
                Which one?
              </label>
              <input
                id="secret-held-arg"
                value={arg}
                onChange={(e) => setArg(e.target.value)}
                onKeyDown={(e) => e.stopPropagation()}
                placeholder="e.g. openai, telegram, notion"
                className="field w-40 py-1 text-[12px]"
              />
            </>
          )}
          <Button type="submit" variant="primary" size="sm" disabled={busy || !argOk}>
            Save securely
          </Button>
          <button type="button" onClick={onCancel} className="px-2 py-1 text-zinc-500 hover:text-zinc-300">
            Cancel
          </button>
          {error && (
            <p role="alert" className="basis-full text-tone-danger">
              {error}
            </p>
          )}
        </form>
      )}
    </div>
  );
}
