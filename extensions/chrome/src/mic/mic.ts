// The microphone page's one button (v1.272.0).
//
// `getUserMedia` is called DIRECTLY inside the click handler, with no `await`
// before it — the same rule setup.ts holds for `chrome.permissions.request()`:
// the browser ties the prompt to the user gesture still on the stack, and an
// `await` first loses it, so the prompt never appears and the button looks
// broken.
//
// Why a page at all: a side panel calling `getUserMedia` gets NotAllowedError
// and NO prompt — Chromium shows that prompt for a page in a tab. The permission
// belongs to the add-on's origin, so once it is granted here the sidebar has it.
//
// Nothing is recorded: the stream is stopped the moment it is granted. The
// outcome is reported in three separate cases — granted, refused (or blocked
// earlier, which looks identical from here and needs the site-info remedy), or
// the call itself failed — because each needs a different next step.

const button = document.getElementById("allow") as HTMLButtonElement | null;
const outcome = document.getElementById("outcome");

function report(tone: "" | "granted" | "refused" | "error", text: string): void {
  if (!outcome) {
    return;
  }
  outcome.dataset["tone"] = tone;
  outcome.textContent = text;
}

/**
 * Tell the service worker what happened, so the sidebar can say so at once.
 *
 * A failure to reach the worker is swallowed on purpose: the grant itself already
 * succeeded and the next press of the sidebar's microphone will simply work.
 */
async function tellWorker(granted: boolean): Promise<void> {
  try {
    await chrome.runtime.sendMessage({ kind: "mic_permission_result", granted });
  } catch {
    // Intentionally silent; see above.
  }
}

button?.addEventListener("click", () => {
  if (!button) {
    return;
  }
  const media = navigator.mediaDevices;
  if (!media || typeof media.getUserMedia !== "function") {
    report("error", "This browser cannot open a microphone from an add-on page.");
    return;
  }
  button.disabled = true;
  report("", "Waiting for your browser…");
  // No await before this call — the user gesture must still be on the stack.
  media
    .getUserMedia({ audio: true })
    .then(async (stream) => {
      stream.getTracks().forEach((track) => track.stop());
      report("granted", "Microphone allowed. Go back to the sidebar and press the microphone.");
      await tellWorker(true);
    })
    .catch(async (err: unknown) => {
      const name = err instanceof DOMException ? err.name : "";
      button.disabled = false;
      if (name === "NotAllowedError") {
        report(
          "refused",
          "Your browser did not allow it. If no prompt appeared, the microphone is blocked for " +
            "this add-on: click the icon left of the address bar, set Microphone to Allow, then " +
            "press the button again.",
        );
      } else if (name === "NotFoundError") {
        report("error", "No microphone was found on this computer.");
      } else {
        report("error", `The microphone could not be opened (${name || "unknown error"}).`);
      }
      await tellWorker(false);
    });
});
