/**
 * The app a Drive / OneDrive / Dropbox sign-in needs (v1.449.0, readiness
 * Wave C item 22).
 *
 * Those three sign in through an OAuth app the USER registers in the vendor's
 * own console (there is no public client to ride for storage). Until this
 * file the cards told the user to open Secrets and save the app's id under
 * the exact name `google_drive_oauth_client_id` — one typo and the sign-in
 * kept failing. The card now holds the boxes itself and saves under the
 * names the daemon reads, so nobody types an internal name.
 *
 * THE NAMES ARE THE DAEMON'S: `platform._oauth_app` reads
 * `<provider>_oauth_client_id` / `<provider>_oauth_client_secret` and, with
 * a client id saved, redirects to `http://localhost:8787/oauth/<provider>/callback`
 * (the daemon's own callback on the packaged port). A Python pin
 * (`tests/test_oauth_app_boxes_v14490.py`) holds the three equal to this
 * file — change them together.
 *
 * Import-free on purpose (the two pages and the tests read it).
 */

/** The vault name the daemon reads the app's id from. */
export function oauthClientIdName(provider: string): string {
  return `${provider}_oauth_client_id`;
}

/** The vault name the daemon reads the app's secret from. */
export function oauthClientSecretName(provider: string): string {
  return `${provider}_oauth_client_secret`;
}

/** The redirect address the user must allow in the vendor's console: the
 *  daemon's own callback, on the port the packaged app listens on. */
export function oauthRedirectAddress(provider: string): string {
  return `http://localhost:8787/oauth/${provider}/callback`;
}

/** The vendor's console (and its name) for each app's registration.
 *  `redirectBox: false` (v1.455.0, Wave H item 27): Google's Desktop app
 *  client has no box for a redirect address — Google lets it return to an
 *  address on this PC by itself — so the card must not tell the user to
 *  "allow" one there (the review found a user would stall on it). */
export const VENDOR_CONSOLES: Record<string, { name: string; url: string; where: string; redirectBox: boolean }> = {
  google_drive: {
    name: "Google",
    url: "https://console.cloud.google.com/apis/credentials",
    where: "Google Cloud Console, as a “Desktop app” OAuth client",
    redirectBox: false,
  },
  onedrive: {
    name: "Microsoft",
    url: "https://entra.microsoft.com",
    where: "Microsoft Entra, with a “Mobile and desktop” platform",
    redirectBox: true,
  },
  dropbox: {
    name: "Dropbox",
    url: "https://www.dropbox.com/developers/apps",
    where: "the Dropbox App Console",
    redirectBox: true,
  },
};

/** The one sentence that says the vendor-console step in plain words. The
 *  whole walk-through (consent screen, test users, the API switch) is the
 *  Help guide the form links (`lib/directoryLinks.ownAppGuideHref`). */
export function vendorConsoleSentence(provider: string, appName: string): string {
  const c = VENDOR_CONSOLES[provider];
  const who = c?.name ?? appName;
  const where = c ? ` in ${c.where}` : " in its developer console";
  const redirect =
    c && !c.redirectBox
      ? " (it has no box for the redirect address below, so there is nothing to allow)"
      : ", allow the redirect address below";
  return (
    `${who} needs an app of your own before it lets Iron Jarvis sign in: make one${where}${redirect}, ` +
    "then paste the app’s ID and secret here. About 10 minutes, once."
  );
}
