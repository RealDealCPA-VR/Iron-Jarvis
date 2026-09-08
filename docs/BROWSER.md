# Iron Jarvis — Your browser

*How Iron Jarvis works in the browser you already use: what it can do, what it
asks you first, what it will never do, and what this first version cannot do
yet. Current as of v1.239.0 (2026-09-07).*

---

## Two browsers, and which one this is about

The **Browser** page holds two different things. They share no code, no profile
and no cookies, and mixing them up is the fastest way to misunderstand both.

**Your browser** is the Chrome or Edge *you* already use, already signed in to
your bank, your practice software, the IRS, your email. Iron Jarvis reaches it
through a small **browser add-on** that you load yourself and then **pair** from
inside Jarvis. That is what this guide is about.

**Jarvis browser** is a browser the app would own itself, with its own profile,
signed in to nothing. **It does not exist yet.** The app is built so it can be
added later, and nothing in the interface pretends it is already there.

Below both sits **computer use**, the older machinery that drives a headless
Chromium of its own behind a domain allowlist. It is a separate feature with
separate settings, and `docs/COMPUTER-USE.md` covers it.

One sentence to keep them apart: **your browser is the one you are already
logged into, and Jarvis acts in it only with your approval; the computer-use
browser is one Jarvis owns, and it knows nobody.**

## Supported browsers

**Chrome or Edge, version 120 or newer.** The add-on is built for Manifest V3
and its `manifest.json` names 120 as the floor, so an older Chrome refuses to
load it and says so. Any Chromium browser at 120 or above will most likely work,
but those two are what this is tested against. Firefox and Safari are not
supported: the add-on is written to Chrome's own add-on interfaces.

## Loading the add-on

The installer ships the add-on **inside the app**, so there is nothing to
download and no source checkout to keep. The **Browser** page names the add-on
folder and gives you a button to copy what it names.

Chrome's **Load unpacked** asks for a *directory*, so you need the folder's place
on this machine as well as its name. On a default Windows install the add-on is
the `browser-addon` folder here:

```
%LOCALAPPDATA%\Programs\Iron Jarvis\resources\browser-addon
```

— that is `AppData\Local\Programs\Iron Jarvis\resources\browser-addon` under
your own user folder. The installer lets you choose a different location, and if
you did, the add-on is in `resources\browser-addon` underneath the folder you
chose.

1. Open Iron Jarvis → **Browser**, and note the add-on folder it names.
2. In Chrome, open `chrome://extensions`.
3. Turn on **Developer mode**, top right.
4. Press **Load unpacked** and choose that folder — the one holding
   `manifest.json`.
5. Come back to Jarvis. The card will say **Waiting to pair**.

Chrome calls this *loading unpacked*, its wording for an add-on that did not come
from its Web Store. Chrome may ask you about it again each time it starts, and
may show a warning about developer-mode add-ons; that is Chrome being careful
about software it did not distribute, and there is no way to switch it off from
this side. Web Store distribution is planned and is not in this version.

*Working from a source checkout instead of the installed app?* The add-on has to
be built first — `pnpm install && pnpm run check` inside `extensions/chrome` —
because the built files are deliberately not committed. The Browser page's steps
say so, and `extensions/chrome/README.md` has the detail.

## The guided setup (v1.240.0)

The Browser card has a **Set up my browser** button. It opens a window that walks
the five steps below in order and moves itself along as each one lands, so you are
never looking at a step you have already done or one you cannot do yet. The last
step is the sidebar itself — where its toolbar icon is, and how to pin it — because
everything before it makes the add-on *work* and only that one makes it *visible*. It names
the add-on folder for *this* machine and gives you a button to copy it, and while
it is open Jarvis opens the site-access page for you rather than making you find
it.

The window does not change any setting on its own. Browser access is a control you
can see inside it, and it is written only when you pick a level.

Two steps in that list are yours, and the guide says so plainly instead of leaving
you to wonder why the automation stopped:

* **Developer mode** and **Load unpacked** are Chrome's. No application can install
  an add-on into your browser; only the Web Store can, and there is no listing yet.
* **Pair** is yours on purpose. See below.

## Pairing

Loading the add-on does not connect it to anything. It knocks; you answer.

**Why Jarvis will not press Pair for you.** This was built to be automatic and then
deliberately taken out. The connection an add-on opens is an ordinary local one,
and the identity it presents is a header that any program running on your computer
could write. Jarvis cannot tell a real browser from a program claiming to be one —
and no secret fixes that, because anything the add-on can read, a program running
as you can read too. If Jarvis paired on its own, a program on your machine could
take the credential meant for your browser, lock your real add-on out, and leave
this card reading **Connected**. So a person looks once and confirms. That press is
the security boundary of the whole feature, not friction waiting to be removed.

1. The add-on connects to the daemon on your own machine with no credential and
   is held in a restricted state where it can do nothing but ask to be paired.
2. The Browser card shows **Waiting to pair**. Press **Pair**.
3. Jarvis mints a credential for that browser and hands it over once. The
   browser stores it; Jarvis stores only a fingerprint of it and could not
   recover the original if it wanted to.
4. The card becomes **Connected**.

An unanswered pairing request expires after five minutes and the row disappears.
**Disconnect** ends the live session and keeps the pairing, so the browser
reconnects on its own. **Forget browser** revokes it, and the next connection
starts over from step 1.

### Site access

A freshly loaded add-on can reach **no site at all**. Chrome will not let Jarvis
ask for that on your behalf — the request has to come from a button inside the
add-on itself — so the Browser card sends you to a single-purpose page with one
button on it. Press it, accept Chrome's prompt, and normal use stops prompting.

You can narrow that access afterwards in Chrome's own controls
(`chrome://extensions` → the add-on → **Site access**), down to particular sites
if you prefer. Jarvis cannot grant or narrow it per site from its own screens.
When a page is out of reach, Jarvis says so rather than reporting an empty page.

## The three access modes

Browser access is one setting on the Browser page, and it ships **off**.

| Setting | What Jarvis may do |
|---|---|
| **Off** | Nothing. No browser tool exists at all — not for chat, not for an agent, not for a coding harness. |
| **Read only** | Look, never touch: list your open tabs, name the tab you are looking at, read the text and structure of a page, and take a screenshot of the visible part of it. |
| **Interactive** | Everything **Read only** does, plus acting on the page: click, type, press a key, scroll, navigate, and activate, open or close a tab. Every one of those asks you first. |

Off is the default because reading a browser you are signed in to is a real
permission, not a convenience, and this app does not grant itself those.

### What gets asked before an action

Two layers, and the second is the one that matters once you get comfortable.

- **Everything that acts asks, by default.** All eight acting abilities ship set
  to *ask*, so the first time Jarvis wants to click, type, press a key, scroll,
  navigate, or open, switch or close a tab, it stops and shows you a card naming
  the tab, the control, and what it is about to do. Nothing reaches your browser
  until you say yes.
- **The four that change a page cannot be talked out of asking.** Clicking,
  typing, pressing a key and navigating sit on the **deny floor**: an agent
  definition that sets one of them to *allow*, or a capability an agent proposes
  for itself, is dropped rather than obeyed, so nothing Jarvis reads can raise
  them behind your back. Lowering the bar is a decision you make yourself, in
  Settings, and the second half of this rule still applies afterwards. Even after
  you have
  said "allow for this conversation", Jarvis asks again — every time — when the
  target reads as **destructive or transactional** (*Delete account*, *Submit
  payment*, *Confirm transfer*), when the page marks the field as a password or
  a payment detail, or when the control **cannot be identified well enough to
  judge**. If it cannot read what it is about to act on, it stops rather than
  guessing.

Whatever Jarvis types is replaced with `***REDACTED***` in the record before the
record is written — always, not only in password boxes — so it is not in the
Activity ledger, in an export, or in a backup.

### Downloads

If something Jarvis clicks starts a download, Chrome handles it exactly as it
always does, and the file lands wherever your own Chrome settings put it,
normally your **Downloads** folder. Jarvis is told when it finishes and learns
the file's real path, so you can then say "read that statement" or "put it in
this project": it can read a completed download and copy it into a project.

What it **cannot** do is change where Chrome saves things, or quietly redirect a
download somewhere else. Moving the file is a copy you asked for, not a setting
Jarvis touched.

## The sidebar

**Where the icon is, and why you probably cannot see it yet.** Chrome does not
put a newly loaded unpacked add-on on the toolbar — it files it behind the
**puzzle-piece** button at the top right. Open that menu, find **Iron Jarvis**,
and press the **pin** beside it; the icon then stays on the toolbar and one
click opens the sidebar. Until you pin it, the sidebar is reachable only through
that menu, which is the difference between a feature existing and a feature
being usable. The guided setup makes this its last step, and the **Browser** card
repeats it for anyone who set their browser up long ago.

**If the icon opens a small popup instead of a sidebar**, your browser is still
running an older copy of the add-on. Chrome keeps the copy it loaded until you
reload it, so updating Iron Jarvis does not update what Chrome is running: open
`chrome://extensions` and press **Reload** on Iron Jarvis. The add-on carries the
same version number as the app it shipped with, the sidebar prints that version
in its own header, and the Browser card prints the app's — an older number in the
sidebar is the copy to reload.

Clicking the Iron Jarvis icon opens a chat docked beside the page you are
reading. **It is a window onto Jarvis, not a second Jarvis.** The panel holds no
model, no agent loop, no settings and no tool list. Your question travels down
the connection the browser already has, the daemon runs the turn with the same
engine, the same persona and the same permission gates as the chat on the Jarvis
page, and the answer is streamed back into the panel as it is written.

**What it may do in your browser follows the Browser access setting exactly.**
**Read only** gets the inspection tools. **Interactive** gets the full set with
every page action still stopping at the approval card. With Browser access
**off** the daemon runs nothing at all for the panel and says so — the sidebar
never sits there looking busy over a turn that was refused. An approval a turn
raises is shown in the panel and answered there, and a refusal is recorded as
your decision, exactly as it is anywhere else.

**Stop, and what it cannot do.** Stop ends the answer being written and prevents
the next step. It does **not** kill a step that is already running: that step
finishes on its own thread and its write lands. Stop is not an undo, and the
panel says so on the press rather than implying an abort it cannot perform.

**Steer, and what it cannot do.** A steer note joins the conversation at the
next step boundary — the one place the turn is between things — so it cannot
interrupt a half-written sentence. The panel marks the note **pending** until
the turn actually takes it, and if the turn ends before a boundary comes round,
the panel says the note was not taken. It never shows a correction as landed
before it has.

## Letting a coding harness use it

A coding CLI you launch inside a **Build** pane — Claude Code, Codex or Pi — can
drive this same browser, through Jarvis, behind the same gates. None of it is
automatic:

1. In **Build**, open the pane's **Capabilities** list and tick **Browser**. A
   pane starts with nothing ticked, and a pane that grants nothing is handed no
   credential at all.
2. Launch the CLI from that pane's **Launch** menu. Jarvis reads what the
   installed build of that CLI actually advertises, writes the configuration
   pointing it at Jarvis, and hands the pane a credential of its own. The menu
   says which method it configured — and says plainly when a build cannot be
   pointed at Jarvis at all, in which case the pane launches exactly as it does
   today, with no Jarvis capabilities.
3. Ask the harness about your browser. It sees the same tools your chat sees,
   and every call it makes is asked about, gated and recorded the same way,
   tagged with the pane it came from.

**That credential belongs to the pane and to nothing else.** It is not the app's
access token, it cannot be used for anything else, and it lives in memory only:
closing the pane, or restarting Iron Jarvis, ends it. If a harness starts
reporting that Jarvis refuses it, relaunch it from the Build pane rather than
hunting for a setting. Untick **Browser** and the harness's very next call is
refused, with nothing to restart. The configuration file Jarvis writes into the
pane's folder holds **no** credential — only a reference to one — so it is safe
to commit; if you already had one of your own there, your entries are kept and
Jarvis adds itself alongside them.

## The security model

**Five credentials, and none of them substitutes for another.** The app's own
access token opens the app's routes. The pairing credential opens the browser
socket and nothing else. A pane's credential opens the harness door and nothing
else. Provider secrets stay in the encrypted vault. And the credentials your own
MCP servers use stay in their own configuration — Jarvis starts those servers and
never borrows what they authenticate with. Each door refuses every credential
except its own, and that refusal is tested rather than assumed.

**Only one browser at a time.** Exactly one paired browser is connected. If a
second connects it takes over, the first is told why, and anything in flight on
it fails with a reason instead of hanging.

**Only your own machine can reach it.** The daemon listens on this computer's
loopback address, so nothing off this computer can open the browser socket at
all. On top of that, the socket admits only the add-on's own fixed identity — the
same on every install — and turns every other browser origin away. That
second check tells one add-on from another; it is not a wall against other
programs already running as you, which can read this app's files anyway.

**Passwords are never read.** No field's value is ever collected, password or
not, and the stripping happens inside the page before anything is sent, so a
password never reaches Iron Jarvis at all. Your browser's own password manager
keeps working, because Jarvis never sees what it fills.

**Page content is untrusted data, always.** A web page can contain text that
tries to give an assistant orders. It does not get to: page text is handed over
as data, a page that looks like it is trying is flagged in the result, and the
next state-changing action on that page is held to a stricter standard. A
suspicious page does not silently end what you asked for.

**Everything is recorded.** Every browser call is on the Activity ledger with
what it did, in which tab, and whether it was approved — with typed text redacted
and no credential ever written down.

## Checking it

**Test**, on the Browser card, does a harmless read-only round trip and reports
what came back: connection, active tab, how long it took. It changes nothing on
the page. It is the fastest way to tell a browser that is not running from an
add-on that was never loaded.

The app's own check-up reports on the browser too — whether the service started,
whether the add-on shipped with the app, whether a browser is paired and
connected. Those rows are **advisory**. A machine where nobody has loaded the
add-on is not a broken machine, and the check-up says so rather than reporting a
fault.

## Known limits of this first version

Stated here so you meet them in a document rather than in the middle of
something.

1. **One browser, one connection.** A newer connection replaces the older one.
   Two browsers cannot be driven at once.
2. **Chrome and Edge only**, version **120 or newer** — the floor the add-on's
   own manifest declares. An older Chrome refuses to load it.
3. **Loaded unpacked.** There is no Web Store listing yet, so the add-on is
   loaded in developer mode and Chrome may prompt you about it each time it
   starts.
4. **Site access is all or nothing when you grant it.** You grant all sites once,
   from the add-on's own page, and narrow it afterwards in Chrome's controls if
   you wish. Jarvis cannot grant per-site access from its own screens.
5. **Downloads land where Chrome puts them.** Jarvis learns the path and can copy
   the file into a project. It cannot redirect the download itself.
6. **Writing files stays inside the workspace**, so moving a download into a
   project happens through the app's save-a-copy route rather than by renaming a
   file that lives outside it.
7. **No clicking by coordinates.** Jarvis aims at what a control *is* — its role
   and its name, then its place in the page's structure. A control drawn on a
   canvas with no readable name cannot be clicked.
8. **No file upload, no special handling for drop-down `select` menus, no hover,
   no drag and drop.** They are on the list for the next phase.
9. **Nothing inside an embedded frame is read.** Only the top document of the
   tab is. An `<iframe>` is skipped whether it comes from this site or another
   one — an embedded viewer, a payment box, a chat widget — and its contents are
   simply absent from the reading rather than reported as empty. **Shadow DOM**
   is different: an open shadow root *is* walked, and a closed one reads as
   nothing at all, so it cannot even be counted.
10. **The ids Jarvis gives page elements belong to one reading.** They are not
    durable, and reusing one after the page has moved on is refused on purpose
    rather than acted on blindly.
11. **Screenshots capture the visible part** of the active tab. Stitching a whole
    long page into one image is not implemented.
12. **Page content is always untrusted.** A flagged page marks the result and
    constrains what may happen next; it does not end your turn for you.
13. **A pane's credential dies with the daemon.** After Iron Jarvis restarts,
    relaunch the harness from its Build pane.
14. **Only the Browser capability is enforced per pane.** Files, Shell,
    Extensions and Memory are recorded and shown so you can see what a pane is
    for, but nothing gates on them yet — a harness in that pane still has
    whatever file and shell access its own CLI came with. The interface says so.
15. **Keeping a harness away from its own web tools is best effort.** Some builds
    cannot be told to switch their own browsing off, and where Jarvis cannot
    verify that, it says so instead of implying an isolation it did not get.
16. **There is no Jarvis browser yet.** A browser the app owns, with its own
    isolated profile, is the next phase.
17. **Chrome unloads an idle add-on.** Manifest V3 stops a browser add-on that
    has been quiet for a while, so a bridge nobody has used for a long stretch
    shows as **Paired — not running** with Chrome plainly open in front of you.
    It is not broken and does not need re-pairing: it reconnects by itself as
    soon as anything wakes the add-on — opening its side panel, switching tabs, or
    restarting the browser. Keeping it awake with a timer would need a Chrome
    permission the add-on deliberately does not ask for.

## What comes later

**Phase 2** is the Jarvis-owned browser and its per-project profiles, file
upload, drop-down `select` handling, hover, drag and drop, reading inside frames,
shadow-DOM hardening, richer structured extraction, network and console
inspection, and watching a page so a change can wake a rule. Web Store
distribution is on that list too, which is what would end the developer-mode
prompt. You can also ask the Guide what is planned for the browser. (The
maintainers' full list is `docs/TODO.md` in the source repository, which is not
part of an installed copy.)

Everything in **Phase 3** — remembering the pages you visited, summarising them,
searching them later — is off by default and will ask for its own separate
consent when it exists. This machine holds client material, and an index of what
you read would record which client you were working on and when. That is your
decision to make, not a default to inherit.
