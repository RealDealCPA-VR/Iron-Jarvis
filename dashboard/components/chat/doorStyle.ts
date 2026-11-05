/**
 * The look of one door under a chat reply (v1.199.0), shared by the link a
 * door usually is (components/chat/DoorsStrip) and the Sign in press a
 * page-step door is drawn through (v1.436.0: components/connections/
 * CliSignInPress, variant "door") — so a press reads as one of the doors.
 * Its own file: CliSignInPress cannot import DoorsStrip (DoorsStrip draws it).
 */
export const DOOR_PILL =
  "inline-flex max-w-full items-center gap-1.5 rounded-full border border-white/[0.08] bg-white/[0.02] px-2.5 py-1 text-[11px] text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft";
