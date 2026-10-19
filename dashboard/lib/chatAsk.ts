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
