"use client";

/**
 * The settings cards under a chat reply (calm UI redesign S3/S4).
 *
 *   ✓ Setting changed   Telegram notifications  Off → On      [Undo]
 *   🔒 Connect Notion    [ •••••••• ]                [Save securely]
 *
 * A change card's Undo finds its ledger action (GET /config/changes/{id})
 * and reverses it through the ordinary undo route — the same Undo the
 * Activity page has. A credential card posts the pasted value STRAIGHT to the
 * daemon (POST /config/secret): it never becomes a chat message, a tool
 * argument or a line in the transcript, and the field is cleared on save.
 */

import { useState, type FormEvent } from "react";
import { Check, Eye, EyeOff, KeyRound, RotateCcw, Settings2 } from "lucide-react";
import { get, post } from "@/lib/api";
import { Button } from "@/components/ui";
import { showValue, type ConfigCard, type ConfigChangeCard, type SecretRequestCard } from "@/lib/configCards";

function ChangeCard({ card, onSettle }: { card: ConfigChangeCard; onSettle: (next: ConfigCard) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function undo() {
    setBusy(true);
    setError("");
    try {
      const found = await get<{ action_id: string; undone: boolean }>(
        `/config/changes/${encodeURIComponent(card.change_id)}`,
      );
      if (!found.undone) await post(`/undo/${encodeURIComponent(found.action_id)}`, {});
      onSettle({ ...card, undone: true });
    } catch {
      setError("Couldn't undo that — try again, or use Activity.");
    } finally {
      setBusy(false);
    }
  }

  if (card.undone) {
    return (
      <div data-testid="config-card-undone" className="text-[12px] text-zinc-500">
        <RotateCcw size={11} className="mr-1 inline align-[-1px]" aria-hidden />
        Undone — {card.label} is {showValue(card.old)} again.
      </div>
    );
  }
  return (
    <div
      data-testid="config-card-change"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-white/[0.08] bg-white/[0.02] px-3 py-2 text-[13px]"
    >
      <span className="inline-flex items-center gap-1.5 font-medium text-tone-success">
        <Check size={13} aria-hidden /> Setting changed
      </span>
      <span className="text-zinc-200" title={card.key}>
        {card.label}
      </span>
      <span className="text-zinc-400">
        {showValue(card.old)} → <span className="text-zinc-100">{showValue(card.new)}</span>
      </span>
      {card.restart && <span className="text-[12px] text-tone-warn">Takes full effect after a restart.</span>}
      <span className="ml-auto inline-flex items-center gap-2">
        <a href="/settings" className="text-[12px] text-zinc-500 hover:text-zinc-300">
          <Settings2 size={11} className="mr-1 inline align-[-1px]" aria-hidden />
          Settings
        </a>
        <button
          type="button"
          onClick={() => void undo()}
          disabled={busy}
          className="rounded-lg border border-white/10 px-2.5 py-1 text-[12px] text-zinc-200 hover:border-white/20 disabled:opacity-50"
        >
          Undo
        </button>
      </span>
      {error && (
        <p role="alert" className="basis-full text-[12px] text-tone-danger">
          {error}
        </p>
      )}
    </div>
  );
}

export function CredentialCard({
  card,
  onSettle,
}: {
  card: SecretRequestCard;
  onSettle: (next: ConfigCard) => void;
}) {
  const [value, setValue] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!value.trim()) return;
    setBusy(true);
    setError("");
    try {
      const res = await post<{ status: "stored" | "replaced"; action_id?: string }>("/config/secret", {
        name: card.name,
        value,
      });
      setValue(""); // never kept in the page once saved
      onSettle({ ...card, saved: res.status, ...(res.action_id ? { action_id: res.action_id } : {}) });
    } catch (err) {
      const detail = (err as { message?: string })?.message;
      setError(detail && !detail.includes(value) ? detail : "Couldn't save it — try again.");
    } finally {
      setBusy(false);
    }
  }

  async function undo() {
    if (!card.action_id) return;
    setBusy(true);
    try {
      await post(`/undo/${encodeURIComponent(card.action_id)}`, {});
      onSettle({ ...card, undone: true });
    } catch {
      setError("Couldn't undo that — try again, or use Activity.");
    } finally {
      setBusy(false);
    }
  }

  if (card.undone) {
    return (
      <div data-testid="credential-card-undone" className="text-[12px] text-zinc-500">
        <RotateCcw size={11} className="mr-1 inline align-[-1px]" aria-hidden />
        Undone — {card.label} is back to how it was.
      </div>
    );
  }
  if (card.saved) {
    return (
      <div
        data-testid="credential-card-saved"
        className="flex flex-wrap items-center gap-3 rounded-xl border border-white/[0.08] bg-white/[0.02] px-3 py-2 text-[13px]"
      >
        <span className="inline-flex items-center gap-1.5 font-medium text-tone-success">
          <Check size={13} aria-hidden /> {card.label} {card.saved === "replaced" ? "replaced" : "saved"} securely
        </span>
        {card.action_id && (
          <button
            type="button"
            onClick={() => void undo()}
            disabled={busy}
            className="ml-auto rounded-lg border border-white/10 px-2.5 py-1 text-[12px] text-zinc-200 hover:border-white/20 disabled:opacity-50"
          >
            Undo
          </button>
        )}
      </div>
    );
  }
  const fieldId = `secret-${card.request_id}`;
  return (
    <form
      data-testid="credential-card"
      onSubmit={save}
      className="max-w-xl space-y-2 rounded-xl border border-white/[0.1] bg-white/[0.02] p-3"
    >
      <div className="flex items-center gap-2 text-[13px] font-semibold text-zinc-100">
        <KeyRound size={14} className="text-accent-soft" aria-hidden />
        {card.label}
      </div>
      <p className="text-[12px] text-zinc-400">
        {card.why ? `${card.why} ` : ""}Stored encrypted on this PC and never shown in the chat.
      </p>
      <div className="flex items-center gap-1.5">
        <label htmlFor={fieldId} className="sr-only">
          {card.label}
        </label>
        <input
          id={fieldId}
          type={show ? "text" : "password"}
          autoComplete="off"
          spellCheck={false}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.stopPropagation()}
          placeholder="Paste it here"
          className="field min-w-0 flex-1 font-mono text-[13px]"
        />
        <button
          type="button"
          onClick={() => setShow((s) => !s)}
          aria-label={show ? "Hide" : "Show"}
          className="grid h-8 w-8 place-items-center rounded-lg text-zinc-400 hover:text-zinc-100"
        >
          {show ? <EyeOff size={14} /> : <Eye size={14} />}
        </button>
      </div>
      {card.help && <p className="text-[11px] text-zinc-500">{card.help}</p>}
      {error && (
        <p role="alert" className="text-[12px] text-tone-danger">
          {error}
        </p>
      )}
      <div className="flex justify-end">
        <Button type="submit" variant="primary" size="sm" disabled={busy || !value.trim()}>
          Save securely
        </Button>
      </div>
    </form>
  );
}

export function ConfigCards({
  cards,
  onSettle,
}: {
  cards: ConfigCard[];
  /** Store a card's new state on the message (Undo pressed, secret saved). */
  onSettle: (index: number, next: ConfigCard) => void;
}) {
  if (!cards.length) return null;
  return (
    <div data-testid="config-cards" className="ml-11 mt-2 space-y-2">
      {cards.map((c, i) =>
        c.kind === "change" ? (
          <ChangeCard key={c.change_id} card={c} onSettle={(n) => onSettle(i, n)} />
        ) : (
          <CredentialCard key={c.request_id} card={c} onSettle={(n) => onSettle(i, n)} />
        ),
      )}
    </div>
  );
}
