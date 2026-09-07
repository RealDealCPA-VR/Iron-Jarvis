// The popup's whole behaviour: ask the service worker what is true, render it.
//
// The popup holds no socket. It cannot: there is exactly one live connection per
// browser (D08), so a socket opened here would replace the worker's, take the
// session, and die the moment the panel closed — leaving the daemon's card
// "connected" to a window that no longer exists. Every fact below comes from the
// worker, and the two buttons send it a message.
//
// Every state renders its own word, and none of them is inferred. `access` is shown
// only when the daemon has actually told the add-on what mode it is in (the
// `browser.ready` frame carries it); the add-on cannot read `browser_access` itself,
// and printing a confident "Interactive" that the add-on guessed would be a lie the
// user acts on. When it is unknown the panel SAYS it is unknown and where the answer
// lives — never a placeholder that reads like a value.
//
// The one button's WORD comes from `toggleAction` in the bridge, which is also what
// the service worker switches on. That is deliberate: this file used to label the
// offline state "Disconnect", and pressing it persisted a suspend flag the user had
// no way to explain and no other surface reported.

import { TOGGLE_LABELS, toggleAction, type BridgeStatus } from "../bridge/socket";

interface Rendered {
  tone: "connected" | "waiting" | "off";
  word: string;
  note: string;
  toggle: string;
}

const ACCESS_WORDS: Record<string, string> = {
  off: "Off",
  read_only: "Read only",
  interactive: "Interactive",
};

/** The words for a state with no `access` reported yet. Absence, named as absence. */
export const ACCESS_UNKNOWN = "Unknown — set in Jarvis";

/**
 * State -> the words the user reads. One row per state, so none can go unwritten.
 *
 * The toggle label is NOT written per branch. It is derived from `toggleAction`, the
 * same function the worker uses to decide what the press does, so the button always
 * describes what it will actually do.
 */
export function describe(status: BridgeStatus): Rendered {
  const toggle = TOGGLE_LABELS[toggleAction(status.state)];
  switch (status.state) {
    case "connected":
      return status.hostPermission
        ? { tone: "connected", word: "Connected", note: "", toggle }
        : {
            tone: "waiting",
            word: "Connected — no site access",
            note: "Open Jarvis and press Grant site access to let it see your open tabs.",
            toggle,
          };
    case "pairing":
      return {
        tone: "waiting",
        word: "Waiting for Iron Jarvis",
        note: "Open Jarvis to connect your browser.",
        toggle,
      };
    case "replaced":
      return {
        tone: "waiting",
        word: "Another browser is connected",
        note: status.lastError || "Iron Jarvis is working with a different browser right now.",
        toggle,
      };
    case "suspended":
      return {
        tone: "off",
        word: "Disconnected",
        note: "This browser will stay disconnected until you press Connect.",
        toggle,
      };
    case "offline":
    default:
      return {
        tone: "off",
        word: "Not connected",
        note: status.lastError || "Open Jarvis to connect your browser.",
        toggle,
      };
  }
}

/** The word for a reported access mode, or the mode itself if it is a newer one. */
export function accessWord(access: string): string {
  if (!access) {
    return ACCESS_UNKNOWN;
  }
  return ACCESS_WORDS[access] ?? access;
}

const el = {
  state: document.getElementById("state"),
  word: document.getElementById("state-word"),
  note: document.getElementById("note"),
  access: document.getElementById("access"),
  tab: document.getElementById("tab"),
  open: document.getElementById("open") as HTMLButtonElement | null,
  toggle: document.getElementById("toggle") as HTMLButtonElement | null,
};

function paint(status: BridgeStatus, tabLabel: string): void {
  const view = describe(status);
  if (el.state) {
    el.state.dataset["tone"] = view.tone;
  }
  if (el.word) {
    el.word.textContent = view.word;
  }
  if (el.note) {
    el.note.textContent = view.note;
  }
  if (el.access) {
    // The real mode when the daemon reported one, and an explicit "Unknown"
    // otherwise. An older daemon sends `browser.ready` with no `access` at all, and
    // a bare "Set in Jarvis" sitting where a mode belongs reads as the mode.
    el.access.textContent = accessWord(status.access);
  }
  if (el.tab) {
    el.tab.textContent = tabLabel;
  }
  if (el.toggle) {
    // No label means no action, so the button is not shown at all rather than
    // offering something that would do nothing (D28's disconnected panel has one
    // button). `hidden` and not display, so re-showing it needs no style bookkeeping.
    el.toggle.textContent = view.toggle;
    el.toggle.hidden = view.toggle === "";
  }
}

/**
 * The current tab's title, or the reason there isn't one.
 *
 * Read here rather than over the socket because the popup can answer it without a
 * round trip, and because it stays true when the socket is down: a panel that shows
 * no tab at all while Chrome plainly has one open reads as a broken add-on rather
 * than as a disconnected one.
 */
async function currentTabLabel(hostPermission: boolean): Promise<string> {
  if (!hostPermission) {
    return "Site access not granted";
  }
  try {
    const found = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    const tab = found[0];
    return tab?.title || tab?.url || "No tab";
  } catch {
    return "No tab";
  }
}

async function refresh(): Promise<void> {
  let status: BridgeStatus;
  try {
    status = (await chrome.runtime.sendMessage({ kind: "status" })) as BridgeStatus;
  } catch {
    // The worker was evicted and has not woken yet. Saying so beats a blank panel;
    // the message above is itself what wakes it, so reopening the popup works.
    paint(
      {
        state: "offline",
        paired: false,
        hostPermission: false,
        access: "",
        lastError: "Waking up. Close and reopen this panel.",
        pendingCommands: 0,
      },
      "—",
    );
    return;
  }
  paint(status, await currentTabLabel(status.hostPermission));
}

el.open?.addEventListener("click", () => {
  void chrome.runtime.sendMessage({ kind: "open_jarvis" }).then(() => window.close());
});

el.toggle?.addEventListener("click", () => {
  el.toggle?.setAttribute("disabled", "true");
  void chrome.runtime
    .sendMessage({ kind: "toggle_connection" })
    .then(() => refresh())
    .finally(() => el.toggle?.removeAttribute("disabled"));
});

void refresh();
