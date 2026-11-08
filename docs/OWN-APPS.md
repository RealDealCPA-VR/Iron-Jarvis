# Your own sign-in apps

Google Drive, Gmail, OneDrive and Dropbox only let a program sign in to your
account through an app that you register with them first, in your own browser,
signed in as yourself. Iron Jarvis cannot make that app for you: it belongs to
your account. You do it once per service, and it takes a few minutes.

Each section below is the steps for one service. The names of buttons and
pages are the vendor's own and they change from time to time; when a step
here does not match what you see, follow the vendor's own page, linked at the
end of each section.

What every service asks for:

- **A name for the app.** Anything you like, for example "Iron Jarvis".
- **Who may use it.** Only you. You never publish the app.
- **The redirect address.** Where the vendor sends you back after you sign
  in. Iron Jarvis shows it on the card with a Copy button; it is an address on
  this PC, `http://localhost:8787/oauth/<service>/callback`.
- **The app's ID and secret.** You paste them into the card's two boxes and
  press **Save app details**, then Connect.

## Google Drive

You make the app in the Google Cloud console, at
<https://console.cloud.google.com>, signed in with the Google account whose
Drive you want Iron Jarvis to read.

1. **Make a project.** At the top of the console, open the project list and
   create a new project (any name). Everything below happens inside it.
2. **Turn on the Google Drive API.** Search the console for "Google Drive
   API", open it and press **Enable**.
3. **Set up the consent screen.** Open **Google Auth platform** (search for
   it if it is not in the menu). If it says it is not configured yet, press
   **Get started**: give the app a name and your email as the support email,
   pick **External** as the audience, give your email as the contact, agree to
   Google's data policy and press **Create**.
4. **Add yourself as a test user.** In **Google Auth platform**, open
   **Audience**, find **Test users**, press **Add users**, enter your own
   email and press **Save**. Only the accounts listed here can sign in.
5. **Make the client.** In **Google Auth platform**, open **Clients**, press
   **Create client**, choose the application type **Desktop app**, give it a
   name and press **Create**. Google then shows the **Client ID** and the
   **Client secret**.
6. **Paste them into Iron Jarvis.** On the Google Drive card (Settings ›
   Connections), paste the Client ID into **App ID** and the Client secret into
   **App secret**, press **Save app details**, then press Connect and sign in.

A **Desktop app** client has no box for a redirect address: Google lets it
return to an address on this PC by itself, so there is nothing to paste for
step 5.

While the app's publishing status is **Testing** (the default), Google ends
the sign-in after seven days and you press Connect again. Google's warning
that it "hasn't verified this app" is expected for an app only you use: it is
your own app.

Google's own pages: [the consent screen](https://developers.google.com/workspace/guides/configure-oauth-consent),
[a Desktop app client](https://developers.google.com/identity/protocols/oauth2/native-app).

## Gmail

Gmail is connected through a separate program (the Gmail server on the card's
GitHub page), which reads its Google app from a file of its own. The Google
side is the same as Google Drive's, with two differences.

1. Do steps 1 to 4 of [Google Drive](#google-drive), but in step 2 turn on the
   **Gmail API** instead of (or as well as) the Google Drive API.
2. In step 5 make a **Desktop app** client, and then press **Download JSON**
   (the download button beside the client) instead of copying the ID and
   secret.
3. Rename the downloaded file to `gcp-oauth.keys.json`.
4. Put it in a folder named `.gmail-mcp` inside your user folder (for example
   `C:\Users\<you>\.gmail-mcp\gcp-oauth.keys.json`). Make the folder if it is
   not there.
5. Sign in once: open a Build terminal and run
   `npx @gongrzhe/server-gmail-autoauth-mcp auth`. It opens your browser for
   the Google sign-in and saves the result in the same folder.
6. Press Connect on the Gmail card (Settings › Connections › Directory).

The Gmail program's own page has the same steps:
<https://github.com/GongRzhe/Gmail-MCP-Server>.

## OneDrive

You make the app in the Microsoft Entra admin center, at
<https://entra.microsoft.com>. Microsoft registers apps inside a directory:
a work or school account has one already. With only a personal Microsoft
account (Outlook.com, Hotmail), make a free Azure account first
(<https://azure.microsoft.com/free>); it comes with a **Default Directory**,
and you register the app there. A personal account on its own is refused.

1. **Register the app.** Open **Entra ID › App registrations** and press
   **New registration**. Give it a name. Under **Supported account types**
   choose the option that includes **personal Microsoft accounts** (for
   example "Any Entra ID Tenant + Personal Microsoft accounts"): Iron Jarvis
   signs in through Microsoft's common sign-in page, which a single-tenant app
   refuses. Press **Register**.
2. **Copy the ID.** The app's **Overview** page shows the
   **Application (client) ID**.
3. **Add the redirect address.** Open **Authentication**, press **Add
   Redirect URI** (or **Add a platform**), choose **Mobile and desktop
   applications**, and add the address the OneDrive card shows as a **custom
   redirect URI**. Press **Configure**.
4. **Paste it into Iron Jarvis.** On the OneDrive card, paste the
   Application (client) ID into **App ID**. Leave **App secret** empty: an app
   on the Mobile and desktop platform has no secret, and Microsoft refuses a
   sign-in that sends one. Press **Save app details**, then Connect.

Microsoft's own pages: [register an app](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app),
[add a redirect address](https://learn.microsoft.com/en-us/entra/identity-platform/how-to-add-redirect-uri).

## Dropbox

You make the app in the Dropbox App Console, at
<https://www.dropbox.com/developers/apps>.

1. **Create the app.** Press **Create app**, choose **Scoped access**, then
   choose how much it may see: **App folder** (only a folder of its own) or
   **Full Dropbox** (everything). Give it a name and create it.
2. **Allow what Iron Jarvis does.** Open the **Permissions** tab, tick
   `files.metadata.read`, `files.content.read` and `files.content.write`, and
   press **Submit**.
3. **Add the redirect address.** Open the **Settings** tab and add the
   address the Dropbox card shows under **Redirect URIs** in the OAuth 2
   section.
4. **Copy the key and secret.** The same Settings tab shows the **App key**
   and the **App secret**.
5. **Paste them into Iron Jarvis.** On the Dropbox card, paste the App key
   into **App ID** and the App secret into **App secret**, press **Save app
   details**, then Connect.

Dropbox's own page: [the OAuth guide](https://docs.dropboxapi.com/dropbox-api/docs/oauth).

## If the sign-in fails

- **"Redirect address mismatch" or similar.** The address in the vendor's
  console must be exactly the one the card shows, character for character.
  Copy it again with the card's Copy button.
- **"Access blocked" or "not a test user" (Google).** Add the account you
  sign in with under **Audience › Test users**.
- **It worked last week and stopped (Google).** An app in Testing is signed
  out after seven days. Press Connect again.
- **OneDrive says the app is public and refuses a secret.** A secret was
  saved for it. In Settings › Connections, open **Secrets**, delete
  `onedrive_oauth_client_secret`, and press Connect again.
