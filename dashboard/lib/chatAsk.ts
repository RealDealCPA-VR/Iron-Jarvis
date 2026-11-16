/**
 * Where a question goes: Chat, with the text waiting in the composer
 * (`/chat?ask=`; the page fills the box and never sends it — the consent
 * rule). v1.355.0: lifted out of components/onboarding/FirstRunStrip so a
 * light surface (the chat home's line) can link a question without pulling
 * the onboarding card and its motion code into its bundle.
 * FirstRunStrip.chatAskHref IS this function (a re-export since the v1.355.0
 * integrate step), so the URL keeps ONE definition.
 * dashboard/__tests__/background-names-v13550 pins the two equal.
 */
export function chatAskHref(text: string): string {
  return `/chat?ask=${encodeURIComponent(text)}`;
}

/**
 * The same door, for a link drawn ON the chat surface (`here` = the page's
 * pathname). "/" and "/chat" are two addresses of the ONE chat page
 * (app/page.tsx renders it at "/"). The page reads `?ask=` ONCE, when it
 * mounts, and a link to the route you are already on only changes the
 * query: the page stays mounted and the box stays empty. So from "/chat" the
 * link goes to "/?ask=…" (and from "/" to "/chat?ask=…", which is
 * `chatAskHref`): a fresh chat mounts with the question waiting, exactly as
 * it does from any other page.
 */
export function chatAskHrefFrom(here: string, text: string): string {
  const path = (here || "").replace(/\/+$/, "") || "/";
  if (path === "/chat") return `/?ask=${encodeURIComponent(text)}`;
  return chatAskHref(text);
}

/**
 * v1.506.0 (north-star fix plan 6.5, decision D11): the desktop's hotkey
 * quick window goes through CHAT. desktop/main.js opens the main window on
 * `/chat?new=1&quick=1` and keeps the typed words in the MAIN process; the
 * page takes them ONCE over the shell's bridge and sends them as the first
 * message of a fresh chat. The Enter the user pressed in the quick window is
 * the send, so this is the one landing that sends — and that is exactly why
 * the words never ride in the URL: a link anywhere (a reply, a page door)
 * could carry `?ask=` words, and `?ask=` only fills the box.
 */
export const QUICK_ASK_PARAM = "quick";

/** The quick window's words, or "" — read only for `?quick=1`, only inside
 *  the desktop app, and never throws (the shell refusing the sender, or an
 *  older shell without the bridge, is simply nothing waiting). */
export async function takeQuickAsk(search: string): Promise<string> {
  try {
    if (new URLSearchParams(search).get(QUICK_ASK_PARAM) !== "1") return "";
    const bridge = (window as unknown as { ironjarvis?: { quickAsk?: { take?: unknown } } }).ironjarvis
      ?.quickAsk;
    if (!bridge || typeof bridge.take !== "function") return "";
    const words = await (bridge.take as () => Promise<unknown>)();
    return typeof words === "string" ? words.trim() : "";
  } catch {
    return "";
  }
}
