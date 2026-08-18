/**
 * SetupCard with its disclosure state supplied, the way the Agents page
 * supplies it (v1.185.0).
 *
 * The card used to own that state internally, hydrated from localStorage —
 * which is why several suites seed `ij_agents_setup_open` in `beforeEach` and
 * why their expand helpers had to be idempotent (an unconditional click closed
 * a card that had persisted itself open). The page now owns the value and
 * passes it down, so there is one source of truth instead of two agreeing by
 * handshake.
 *
 * This harness holds it in React state rather than passing a constant, so the
 * card's own toggle keeps WORKING in the tests that press it. Passing a fixed
 * `open` would quietly turn every one of those clicks into a no-op and leave
 * the assertions passing for the wrong reason.
 */
import { useState } from "react";

import { SetupCard } from "@/components/agents/SetupCard";

type CardProps = React.ComponentProps<typeof SetupCard>;

/**
 * `initialOpen` defaults to FALSE because that is what the card did: it
 * rendered collapsed and hydrated from storage after mount, so every suite
 * here reaches its rows by pressing "Set up agents" (some idempotently, some
 * with a single unconditional click). Defaulting to open would turn the
 * unconditional ones into a CLOSE and hide the very rows they assert on.
 */
export function SetupCardHarness({
  initialOpen = false,
  ...props
}: Omit<CardProps, "open" | "onOpenChange"> & { initialOpen?: boolean }) {
  const [open, setOpen] = useState(initialOpen);
  return <SetupCard {...props} open={open} onOpenChange={setOpen} />;
}
