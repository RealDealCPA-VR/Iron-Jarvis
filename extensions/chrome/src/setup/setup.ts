// The setup page's one button.
//
// `chrome.permissions.request()` is called DIRECTLY inside the click handler, with
// no `await` before it. That is not style: Chrome ties the call to the user gesture
// that is still on the stack, and an `await` first — reading the current state,
// say — loses the gesture and the call rejects with "This function must be called
// during a user gesture". The prompt then never appears and the button looks broken.
//
// The outcome is reported honestly in three separate cases, because they need
// different next steps from the user: granted, declined at Chrome's prompt, or the
// call itself failed. A single "something went wrong" would leave the user pressing
// the same button.

import { requestHostPermission } from "../background/hostperms";

/**
 * v1.449.0 (readiness Wave C, item 31): the page names THE BROWSER IT RUNS
 * IN, never one browser for everybody. Edge's user agent carries "Edg/";
 * anything else Chromium is read as Chrome, and an unknown one keeps the
 * neutral words. The same decision the dashboard's browserWords makes from
 * the add-on's hello, made here from the page's own navigator — no await, no
 * message to the worker, so the user gesture below is untouched.
 */
interface BrowserWords {
  /** Sentence-opening: "Chrome", "Edge", "Your browser". */
  name: string;
  /** Mid-sentence: "Chrome", "Edge", "your browser". */
  mid: string;
  possessive: string;
  extensionsPage: string;
}

function browserWordsFor(userAgent: string, brands: readonly string[] = []): BrowserWords {
  const ua = userAgent.toLowerCase();
  const brandText = brands.join(" ").toLowerCase();
  if (ua.includes("edg/") || brandText.includes("microsoft edge")) {
    return { name: "Edge", mid: "Edge", possessive: "Edge's", extensionsPage: "edge://extensions" };
  }
  if (ua.includes("chrome/") || brandText.includes("google chrome") || brandText.includes("chromium")) {
    return { name: "Chrome", mid: "Chrome", possessive: "Chrome's", extensionsPage: "chrome://extensions" };
  }
  return {
    name: "Your browser",
    mid: "your browser",
    possessive: "your browser's",
    extensionsPage: "its add-ons page",
  };
}

function fillBrowserWords(words: BrowserWords): void {
  for (const el of Array.from(document.querySelectorAll<HTMLElement>("[data-browser]"))) {
    const which = el.dataset["browser"];
    if (which === "name") el.textContent = words.name;
    else if (which === "possessive") el.textContent = words.possessive;
    else if (which === "addons-page") el.textContent = words.extensionsPage;
  }
}

const brandList = (
  (navigator as Navigator & { userAgentData?: { brands?: { brand: string }[] } }).userAgentData?.brands ?? []
).map((b) => b.brand);
const browser = browserWordsFor(navigator.userAgent, brandList);
fillBrowserWords(browser);

const button = document.getElementById("grant") as HTMLButtonElement | null;
const outcome = document.getElementById("outcome");

function report(tone: "" | "granted" | "refused" | "error", text: string): void {
  if (!outcome) {
    return;
  }
  // An empty tone is the neutral, in-progress colour. Reusing the amber "refused"
  // tone for "waiting" would flash a warning colour at a user who has done nothing
  // wrong, and the two states would be indistinguishable at a glance.
  outcome.dataset["tone"] = tone;
  outcome.textContent = text;
}

/**
 * Tell the service worker what happened, so the daemon learns without polling.
 *
 * A failure to reach the worker is swallowed on purpose: the grant itself already
 * succeeded, and Chrome's own `permissions.onAdded` event will reach the worker on
 * its next wake regardless. Turning a delivery hiccup into a red message here would
 * tell the user the grant failed when it did not.
 */
async function tellWorker(granted: boolean): Promise<void> {
  try {
    await chrome.runtime.sendMessage({ kind: "host_permission_result", granted });
  } catch {
    // Intentionally silent; see above.
  }
}

button?.addEventListener("click", () => {
  if (!button) {
    return;
  }
  button.disabled = true;
  report("", `Waiting for ${browser.mid}…`);
  // No await before this call — the user gesture must still be on the stack.
  requestHostPermission()
    .then(async (granted) => {
      if (granted) {
        report(
          "granted",
          "Site access granted. Iron Jarvis can now read and act in the pages you open, asking you first.",
        );
        button.disabled = true;
        await tellWorker(true);
        return;
      }
      report(
        "refused",
        `${browser.name} declined the request, so nothing changed. Press the button again if you meant to allow it.`,
      );
      button.disabled = false;
      await tellWorker(false);
    })
    .catch((err: unknown) => {
      const detail = err instanceof Error ? err.message : String(err);
      report("error", `${browser.name} refused the request: ${detail}`);
      button.disabled = false;
    });
});
