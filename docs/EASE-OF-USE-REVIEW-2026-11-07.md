# Ease-of-use review — 2026-11-07 (v1.442.1)

**Goal of this review:** can someone who is not an expert reach everything in
Iron Jarvis easily, and is the first-time experience great?

**How it was done:**

- Eight reviewers read the code area by area: first run, Chat, Settings and
  connections, Build and coding, automations and agents, memory/files/help,
  the desktop app, and the browser add-on.
- One of them ran a full wiring sweep with scripts. It checked 773 calls from
  the screens to the daemon, every link, every page, every event, every
  setting, and the bridge between the desktop and the dashboard.
- One ran a throwaway fresh install on scratch ports. It clicked through all
  41 pages at desktop and phone size and sent a chat message. Your running
  app was never touched.
- I re-checked the most important findings in the code myself. Those are
  marked **(checked)**. The rest were checked in the code by a reviewer and
  quoted with file and line.

**Nothing was changed.** This is the list only.

**The good news first:**

- No screen calls a route that doesn't exist.
- No sidebar or Everything link is broken.
- Every page loads on a fresh install.
- A demo chat message works end to end.
- Error handling in chat (Retry, Sign in press, your message put back) is
  strong.

The problems are almost all of one kind: **the feature exists and works, but
a beginner can't find it, can't understand it, or gets stuck one step before
it works.**

---

## Part 1 — Fix these first (they decide the first impression)

### 1. "Use ChatGPT for answers" picks a model ChatGPT won't run **(checked)**
- **Before:** A ChatGPT Plus user presses the one button. Iron Jarvis saves
  `gpt-4o-mini` as the model and runs it through Codex. Codex only runs its
  own models, so the very first question very likely fails with "Codex doesn't
  offer gpt-4o-mini on this ChatGPT plan". The Claude button has the same
  shape: it pins an older fixed model instead of the plan's own default.
- **Fix:** When the answer comes through a sign-in (Claude Code or Codex),
  save the plan's own default ("subscription"), never a fixed API model name.
- **After:** The first question after pressing the button is answered by the
  plan's normal model.
- *Where:* `daemon/routes/settings.py:933-934`, `daemon/app.py:2846-2848`

### 2. The demo model is a maze with no exit
- **Before:**
  - On a fresh install the strip at the top says "a model is connected but
    not chosen yet". That is false: the demo model itself counts as
    "connected".
  - Its "Choose a model" button opens a menu where every real choice is
    greyed out. The reason is only in a hover tooltip, and there is no
    "Connect" link.
  - The connect choices on the empty chat disappear as soon as you send your
    first message, which most people do right away. After that the demo reply
    says "Choose a model above", and above is that same dead menu.
  - After one message the screen says "this is the demo" four times, with two
    different ways out.
  - If you do pick a model from the strip, only that one chat changes. New
    chats, agents and schedules stay on the demo.
- **Fix:**
  - Don't count the demo model as connected.
  - Put the connect choices under the demo reply, and keep them above the
    box for as long as the demo is the default.
  - End the model menu with a "Connect a model…" row, and show the reason on
    each greyed row as visible text.
  - Say "this is the demo" once.
  - Make the strip's button the same one-press "Use Claude for answers" that
    sets the default everywhere.
- **After:** Someone with nothing connected is always one press from
  connecting, whatever chat they are in. Someone who connects gets real
  answers everywhere.
- *Where:* `components/SimulatedBanner.tsx:55` **(checked)**,
  `app/chat/page.tsx:11797`, `components/chat/ModelMenu.tsx:820-827`,
  `daemon/chat_turn.py:7695`, `components/ProviderDowngradeBanner.tsx:39`

### 3. A wrong API key becomes your default before it is tested
- **Before:** Paste a key and press Connect. The key is saved AND made the
  default immediately, even if it's wrong. A common slip is an OpenAI key in
  the Anthropic tab, which is selected by default. The panel then
  disappears, and its error message disappears with it. Your next question
  fails with a key error and nothing explains why. A good key gets no
  "done" message either.
- **Fix:** Test the key first, and switch to it only if the test passes.
  Recognise the key's shape (`sk-ant-` vs `sk-`) and pick the right tab.
  Keep the result on screen ("Done — Anthropic answers you now", or why
  not).
- **After:** A wrong key changes nothing and says why. A good key says it
  worked.
- *Where:* `daemon/routes/connections.py:1381-1390`,
  `components/onboarding/ConnectDoors.tsx:345-372`

### 4. After signing in to Claude Code or Codex, it still says "not signed in" for up to 2 minutes
- **Before:**
  - You finish signing in in the terminal and come back to Chat. The panel
    still says "not signed in" and offers "Sign in" again, which opens a
    second terminal.
  - The "Use Claude for answers" button only appears after an automatic
    re-check that runs every 2 minutes.
  - The text says it "re-checks every few seconds". That isn't true.
  - Signing in also means working inside Claude Code's own text screen,
    which on its first run asks extra questions (theme, "trust this
    folder?").
- **Fix:**
  - Add an "I've signed in — check now" button.
  - Have the terminal spot "Login successful" and turn the panel green by
    itself.
  - Open the sign-in terminal in a folder Iron Jarvis owns, so it isn't
    asked to trust your home folder.
  - Fix the wording.
- **After:** The panel turns green seconds after you sign in, and one more
  press gets real answers.
- *Where:* `providers/cli_auth.py:264`, `ConnectDoors.tsx:554-620`,
  `terminals/manager.py:465`

### 5. Creative Studio's "Autopilot" starts switched ON **(checked)** — DECIDED 2026-11-07: keep it on (v1.175.0's reason: the Studio is built to run hands-off)
- **Before:** Starting a Creative session launches Claude Code or Codex with
  every safety prompt turned off: no approvals, no sandbox. Iron Jarvis even
  clicks through Claude's own warning screen. The text beside the box says
  "Leave it off…", but the box starts on. That is risky on a PC with client
  data.
- **Fix:** Start it off. Make turning it on a deliberate choice.
- **After:** Nothing runs unattended unless you choose it.
- *Where:* `app/creative/page.tsx:1847`, `daemon/routes/creative.py:825-893`

### 6. File Search looks in the app's own data folder, not in your files **(checked)**
- **Before:**
  - Unless you change a hidden "advanced" setting, File Search, "By
    meaning", and the AI's own file search look only in Iron Jarvis's own
    folder (logs, config, backups).
  - A newcomer searching for their own document gets "No matches".
  - "By meaning" also returns an empty list without saying it needs a local
    embedding model.
- **Fix:**
  - Default to your Documents folder and your projects' folders.
  - Show "Folders to search" on the File Search page itself.
  - Leave the app's own folder out.
  - Have "By meaning" say what it needs.
- **After:** The first search finds your documents.
- *Where:* `platform.py:1320`, `app/filesearch/page.tsx:39`,
  `filesearch/service.py:585`

### 7. Restoring a backup can't be undone
- **Before:** "Restore and restart" replaces your database and settings
  without first saving today's state, so restoring the wrong backup loses
  everything since. The "something is still running" check also misses
  chat replies, coding sessions, terminals and night tasks. The refusal text
  is technical.
- **Fix:** Save a "before restore" backup first and name it in the success
  message. Use the same busy check the updater uses, and plain words.
- **After:** A restore can always be reversed.
- *Where:* `daemon/routes/settings.py:677-748`, `maintenance.py:572`

### 8. The installer is unsigned, so Windows warns "Windows protected your PC" (known, still open)
- **Before:** The very first thing a new user sees is a scary warning they
  have to click past ("More info → Run anyway"), then a multi-page
  installer.
- **Fix:** Sign the installer (the code-signing plan, under the LLC).
  Consider a one-click per-user install.
- **After:** It installs like any normal app.
- *Where:* `desktop/package.json:58-65`

### 9. The browser add-on: out-of-date setup page, stale copies, and hard install
- **Before:**
  - The add-on's setup page says it "sees only your tab titles" and that
    acting on pages comes "in later versions". It already reads, clicks and
    fills forms, so the consent text is wrong.
  - It says "Chrome" and `chrome://` to Edge users. Help's tile also says
    "Pair your own Chrome".
  - After each app update, the browser keeps running the OLD add-on copy,
    and nothing says so (the version is reported but never compared).
  - Installing still needs Developer mode and "Load unpacked" (known).
- **Fix:**
  - Rewrite the setup page to say what access really allows, in words that
    don't name one browser.
  - Compare the add-on's version with the app's, and show "Your browser is
    running an older copy — press Reload".
  - Longer term, publish the add-on to the Edge and Chrome stores.
- **After:** You agree to what it really does, and a stale copy is caught
  automatically.
- *Where:* `extensions/chrome/src/setup/setup.html:140-155`,
  `components/browser/YourBrowserCard.tsx:470-529`, `app/help/page.tsx:157`

### 10. Old chats disappear after 100 **(checked)**
- **Before:** The chat list only ever loads the newest 100 chats, and
  "Search chats" only searches those 100 titles. Older chats vanish from the
  list, the Archived view and Ctrl K. Searching for something said inside a
  chat finds nothing. It looks like lost work.
- **Fix:** Add "Show older chats" (load more), and make the search box search
  the words inside every chat. The daemon already has a route that reads
  message text.
- **After:** Every chat can be found.
- *Where:* `daemon/routes/chat.py:1806`, `app/chat/page.tsx:4314-4340`

### 11. The two "Code" views look the same, and Build › Code dead-ends
- **Before:**
  - Chat › Code (Jarvis writes the code) and Build › Code (your own Claude
    Code or Codex) are both just called "Code". Neither points to the other.
  - In Build › Code with no coding tool installed, the box says "Start a
    session with Claude Code, Codex…", Send is grey, and nothing says why or
    where to get one.
  - A signed-out tool says "Open a terminal, run it, and sign in there". The
    one-press Sign in button the app already has is not shown.
- **Fix:**
  - Under each start box, add one line with a link: "Jarvis writes the code
    here — want your own Claude Code instead? Open Build › Code", and the
    reverse in Build › Code.
  - When no tool is installed, show "Get Claude Code / Get Codex" links plus
    "Or let Jarvis do it in Chat › Code".
  - Show the Sign in button beside a signed-out tool.
- **After:** You always know which one you are in, and every dead end has a
  one-press way forward.
- *Where:* `components/buildCode/StartComposer.tsx:105-110, 435-440, 779-791`,
  `lib/buildApp.ts:475`, `components/chat/ChatCodeSurface.tsx:68-71`

### 12. Chat › Code's "Work on a new branch" is on by default and explained in git words (known, still open)
- **Before:** Pick a folder that happens to be a git repository, and Jarvis
  edits a hidden copy beside it. You open your folder and see nothing
  changed. Getting the work back means Changes → Branch → Commit → "Merge
  into main".
- **Fix:**
  - Use plain words: "Work on a separate copy, so your folder stays as it is
    until you keep the changes."
  - Add one "Keep these changes" button that commits and merges.
  - Consider starting the switch off.
- **After:** A non-programmer finds the work in their own folder, or gets it
  with one press.
- *Where:* `components/harness/HarnessStartComposer.tsx:257, 776-781`,
  `HarnessBranch.tsx:354-368`

### 13. Editing a notification destination quietly changes it **(checked)**
- **Before:**
  - Edit any destination (for example to change a token) and its alert list
    resets to "everything", including approval asks you had turned off.
  - Edit an email destination that was made two-way from chat, and two-way
    email is silently switched off. The page has no fields for it at all,
    yet chat sends you to that page to set it.
- **Fix:**
  - Pre-fill the saved alert choice when editing.
  - Have the server keep any saved setting the form doesn't show.
  - Add "Reading mail server", "Listen for incoming mail" and "Allowed
    senders" to the email form.
- **After:** An edit only changes what you changed.
- *Where:* `components/settings/pages/NotificationsPage.tsx:514-546`,
  `daemon/routes/comm.py:376-443`, `daemon/app.py:3572-3579`

### 14. One click removes an extension and all its saved keys **(checked)**
- **Before:** On the Tools page, a catalog card's "Remove" deletes the
  extension, every saved token and its records in one press, with no Undo.
  The same action on the row just below asks twice. Its "busy" state also
  never shows.
- **Fix:** Use the same two-press "Remove — press again" button.
- **After:** Removing is two presses everywhere.
- *Where:* `components/settings/pages/ToolsPage.tsx:1317-1325`

### 15. Settings shows about 50 expert-only rows to everyone
- **Before:**
  - The settings list marks some rows "advanced", but the Settings page
    ignores that.
  - Models alone shows about 35 rows, including raw JSON boxes ("Auto-routing
    tiers", "Model roles", "Context windows") and a second endpoint address
    field.
  - Connections shows "Account groups" as JSON of internal account ids.
- **Fix:** Fold advanced rows under a closed "More options" in each group.
  Search and direct links still open them. Hide "Account groups" here,
  because the Iron-Proxy card already has a Group picker.
- **After:** Each settings group opens on the handful of things people
  actually change.
- *Where:* `components/settings/SettingsHome.tsx:388-390`, `settings/schema.py`

### 16. Google Drive, OneDrive, Dropbox and Gemini sign-in make you type exact secret names
- **Before:** The steps say: create an "OAuth app", copy a "redirect
  address", then go to another page and save it under the exact name
  `google_drive_oauth_client_id`. One typo and sign-in keeps failing.
- **Fix:** Put "App ID" and "App secret" boxes on the card itself, save them
  under the right names automatically, and give the redirect address a Copy
  button.
- **After:** No hand-typed internal names.
- *Where:* `components/settings/pages/ConnectionsPage.tsx:1080-1099, 1180-1195`

### 17. The Microsoft 365 card says "the Handbook has the steps" but gives no link
- **Before:** Your firm's admin has to find the Microsoft (Entra) setup steps
  alone. The card's only "Docs" link goes to the add-on's GitHub page. Chat
  links the steps; the card doesn't.
- **Fix:** Add "How your admin sets this up" linking to the Handbook section,
  and label the GitHub link as the program's own page.
- **After:** The admin steps are one press from the card.
- *Where:* `connectors/catalog.py:420, 453-459`,
  `components/settings/pages/DirectoryPage.tsx:852`

### 18. Ctrl K's "Settings" results do nothing when Settings is already open **(checked)**
- **Before:** While on Settings, Ctrl K → "Default model" (or "Appearance")
  changes the address bar and nothing else. Settings reads the address only
  once, when it first opens.
  - Links between Settings sections (Directory → Connections, Connections →
    Secrets, "How to set it up") may have the same problem. A 2-minute
    browser check will confirm it.
- **Fix:** Make Settings react when the address changes, or have the palette
  and those links call Settings' own "go to section".
- **After:** The same press works from any page.
- *Where:* `components/CommandPalette.tsx:343, 410`,
  `components/settings/SettingsHome.tsx:275-285`

### 19. A screen reader never announces a finished reply
- **Before:** The only "announce this" area exists only while the reply is
  being written, and is removed when the reply ends. A blind user hears
  nothing and has to go looking for the answer.
- **Fix:** Give the conversation a "log" role that announces new replies, or
  keep one always-present announcer that says "Reply ready".
- **After:** Screen-reader users hear when the answer arrives.
- *Where:* `app/chat/page.tsx:12477-12493, 12658-12686`

---

## Part 2 — Automations: things run, but you can't see, pause or edit them

### 20. A rule ("reflex") that fails to start tells nobody
- **Before:** If a rule's workflow was deleted, the remote agent is down, or
  Emergency stop is on, the failure is written only on the rule's own row.
  The bell, phone and tray say nothing, so you think your automation works.
- **Fix:** Ring the bell (and the phone, by default) when a rule can't start:
  "Your rule X couldn't start: why".
- **After:** A broken rule is noticed the first time it should have run.
- *Where:* `reflex/router.py:160-184, 271-287`, `components/NotificationBell.tsx:220-262`

### 21. Folder watchers' suggestions arrive silently
- **Before:** The Watchers page promises suggestions "wait in Autonomy →
  Proposals". When a folder changes, the suggestion is stored and nothing
  tells you.
- **Fix:** Raise a notice when a watcher makes a new suggestion: "Your
  watcher X noticed changes · Review".
- **After:** A watcher actually tells you when it has something.
- *Where:* `sentinels/service.py:262-288`, `motivation/engine.py:205-260`

### 22. A workflow with a failed step says "Done" **(checked)**
- **Before:** A step set to "skip if it fails" can fail and the run still
  finishes "completed". Run history shows Done and the bell stays quiet.
  Scheduled runs already say "Part failed"; manual and rule-started runs
  don't.
- **Fix:** Finish such runs as "Part failed" (amber), and ring the bell.
- **After:** Every surface tells the truth about a partly failed run.
- *Where:* `workflows/engine.py:515, 656-661`, `app/workflows/page.tsx:766-775`

### 23. Emergency stop is invisible outside the Autonomy page, and is called two names
- **Before:**
  - Once Emergency stop is on, schedules, goals, rules and phone tasks are
    all skipped. But the Schedules page still shows "Next run: tomorrow
    8:00 AM", and no banner anywhere says everything automatic is paused.
    Forget you turned it on and you get a silent week.
  - Error messages say "turn off Emergency stop on the Autonomy page", but
    the button there is called "Kill switch".
- **Fix:** Show one app-wide strip while it is on: "Emergency stop is on —
  nothing starts on its own · Turn off". Call it "Emergency stop"
  everywhere.
- **After:** It can't be forgotten, and the words in an error match the
  button that fixes it.
- *Where:* `app/autonomy/page.tsx:453-459`, `agents/orchestrator.py:422-426`

### 24. Schedules can't be paused, only deleted **(checked)**
- **Before:** The row shows a dot for on/off, but nothing turns it off.
  "Pause my morning briefing while I'm on vacation" means deleting it and
  re-entering everything later. The daemon already supports pausing.
- **Fix:** Add a Pause/Resume switch on each row.
- **After:** Taking a break is one press.
- *Where:* `app/schedules/page.tsx:971`, `daemon/routes/system.py:789-830`

### 25. Editing a schedule loses most choices and needs cron
- **Before:** The edit window can't change who runs it, the project, where
  the result goes, or the workflow. Changing the time needs a cron
  expression like `0 9 * * 1-5`. The add form had friendly presets; the edit
  form drops them.
- **Fix:** Reuse the add form's friendly controls in the edit window.
- **After:** Change a schedule without knowing cron, and without deleting it.
- *Where:* `app/schedules/page.tsx:282-337, 1408-1468`

### 26. Creating a schedule: grey button with no reason, and "Every minute" listed first
- **Before:** You fill in the task and the time, and "Add schedule" stays
  grey because a name is missing. Nothing says so, and the placeholder looks
  like a code id. "Every minute" is the FIRST repeat choice: one mis-click
  runs an agent 1,440 times a day. "Daily at 7:30" needs cron.
- **Fix:**
  - Fill the name from the task's first words, and say "Give it a name" if
    it's empty.
  - Move "Every minute" to the end, with a warning.
  - Add a plain "every day / weekdays / on <day> at <time>" picker.
- **After:** The first schedule works on the first try.
- *Where:* `app/schedules/page.tsx:829-860, 902`, `lib/schedules.ts:7-19`

### 27. Rules and watchers can't be edited
- **Before:** A typo in a rule's keyword, or a watcher's folder, means delete
  and re-create. A single watcher can't be paused on its own.
- **Fix:** Add Edit for rules and watchers, and a per-watcher on/off switch.
- **After:** Fix things where they are.
- *Where:* `daemon/schemas.py:1681-1690`, `daemon/routes/autonomy.py:164-197`

### 28. A rule for email/Slack/phone messages never fires while that source is off
- **Before:** You add "When an email mentions invoice → …". It saves green
  and switched on, and never fires, because email listening is off on
  another page. Nothing on the rule says so.
- **Fix:** Show "Email listening is off — turn it on in Settings ›
  Notifications" on the form and on the rule.
- **After:** A rule that can't fire says so before you rely on it.
- *Where:* `app/reflex/page.tsx:189, 600-675`

### 29. The Rules form starts on its two most technical choices
- **Before:** "Add your first rule" opens on "Webhook fires → Run a
  workflow", both of which need setup on other pages. "Start a session" is
  engine wording.
- **Fix:**
  - Start on "An email/message arrives → An agent does a task".
  - Rename "Start a session" to "Have an agent do a task".
  - Add two "Try this" examples, like the Schedules page has.
- **After:** The first rule needs no other page.
- *Where:* `app/reflex/page.tsx:325-327, 692`

### 30. A running workflow can only be stopped from the canvas that started it
- **Before:** A workflow started by a schedule or a rule, or before a page
  reload, shows "running" in Run history with no Stop button.
- **Fix:** Put Stop on every running row in Run history.
- **After:** Any runaway run can be stopped from the list.
- *Where:* `app/workflows/page.tsx:705-800`

### 31. A workflow step can silently lose what you typed
- **Before:** Type step arguments with a missing quote and click away. The
  box still shows your text, but the step keeps the old arguments. A
  misspelled tool name saves fine and fails mid-run.
- **Fix:** Show an error under the box when the text is invalid. Pick tools
  from a list. Refuse an unknown tool at Save.
- **After:** What you see is what runs.
- *Where:* `components/workflow/NodeInspector.tsx:174-203`,
  `daemon/routes/workflows.py:459-463`

### 32. Night shift needs a typed folder path in a git repository, and the morning has no "keep it"
- **Before:** No folder picker, just a text box. A folder that isn't a git
  repository is refused in git words. In the morning nothing brings the work
  into your own folder.
- **Fix:**
  - Use the same folder picker as everywhere else.
  - Offer "Make this folder a repository" when one is refused.
  - Add "Keep these changes" to each morning row.
- **After:** Queueing and keeping night work needs no git knowledge.
- *Where:* `components/harness/TonightList.tsx:139-153`,
  `components/harness/LastNightList.tsx:176-217`

### 33. "Try on several models" is offered where it can never work
- **Before:** The chip shows in a plain folder too. Pressing it says it needs
  "a folder in a git repository", with nothing you can press to fix that.
- **Fix:** Hide it where it can't work, or offer "Make this folder a
  repository".
- *Where:* `components/harness/HarnessStartComposer.tsx:164-173, 643-652`

---

## Part 3 — Chat polish that matters

### 34. While an approval card waits you can't type, and can't say "do this instead"
- **Before:** The message box is hidden while a card waits, which looks like
  a freeze. The only answers are Allow or Deny. Build › Code's card already
  has "No, tell it what to do instead".
- **Fix:** Add "No, tell it what to do instead" to the chat card, and say
  that the box comes back after you answer.
- *Where:* `app/chat/page.tsx:13313-13315`, `components/chat/ApprovalCard.tsx`

### 35. "Local only" in one chat quietly applies to every new chat **(checked)**
- **Before:** Lock one sensitive chat to this PC, and every new chat starts
  locked too. With a cloud default model, your next question then stops on
  a "send to cloud?" card you never asked for. The button says "this chat".
- **Fix:** Keep it per chat. Leave "new chats start local only" to its own
  setting, or ask once ("Start new chats local only too?").
- *Where:* `app/chat/page.tsx:5797-5808`, `lib/localOnlyChat.ts:30`

### 36. "Setting changed" cards show internal values
- **Before:** Cards read "approve_for_me → auto" or "mark1 → daylight", and
  the Settings link opens the top of Settings.
- **Fix:** Show the choice's display name ("Ask before commands → Auto"), and
  link straight to that setting.
- *Where:* `components/chat/ConfigCards.tsx:85-101`, `lib/configCards.ts:112-118`

### 37. Typing during a reply: Enter sends a "steer note", and queueing needs Ctrl+Enter
- **Before:** A beginner types their next question during a long answer and
  presses Enter. It becomes a "note" that may go unread. "Steer" means
  nothing to them, and on a phone there is no way to queue a message for
  after the reply.
- **Fix:** Say "Add a note for Jarvis while it works…". Put a "Send after
  this reply" button beside Stop. Rename "Steer sent" to "Note sent".
- *Where:* `app/chat/page.tsx:2188-2242, 12677, 14377-14393`

### 38. Composer menus can't be closed with Escape
- **Before:** The "+", Project and Tools menus close only on a mouse click
  outside. Escape stops the running reply instead.
- **Fix:** Escape closes the menu and returns focus, like the model menu
  already does.
- *Where:* `app/chat/page.tsx:7271-7298, 2183-2186`

### 39. "Waiting for your OK" vs "unread" in the chat list differ by colour only
- **Before:** A colour-blind user can't tell which chat needs approval. This
  breaks the project's own "never by colour alone" rule.
- **Fix:** Add a small icon or a "Needs you" word.
- *Where:* `components/chat/ThreadGroups.tsx:188-195`

### 40. Two "Project" controls, and the hint points at the one that can't create a project
- **Before:** "Add to project" says "Make one with the Project button above".
  That button's picker has no "New project".
- **Fix:** Add "New project…" to that picker, or fix the hint.
- *Where:* `app/chat/page.tsx:10778-10803, 10959, 14558-14572`

### 41. If the chat list fails to load, it just looks empty
- **Before:** On a slow start or a server hiccup the list shows nothing and
  no message. It reads as "my chats are gone".
- **Fix:** "Your chats couldn't load. Try again", with Retry.
- *Where:* `app/chat/page.tsx` (`showThreadsFor`, empty catch)

### 42. Copy on code blocks never appears on touch screens
- **Before:** The copy button shows only on mouse hover, so phones and tablets
  can't copy code.
- **Fix:** Always show it on touch devices, like the other reply buttons.
- *Where:* `components/chat/CodeBlock.tsx:113`

### 43. On a phone, "New chat" is two taps deep behind a button labelled "Chats"
- **Fix:** A "+ New chat" button in the chat's top bar on small screens.
- *Where:* `app/chat/page.tsx:10683-10695`

### 44. Greyed buttons explain themselves only on hover
- **Before:** Attach (at 4 files), Share and Map just go grey. On touch and
  keyboard there's no reason given.
- **Fix:** A short visible note, e.g. "4 files is the most per message".
- *Where:* `app/chat/page.tsx:13737, 10813-10822, 14430`

### 45. A long pasted message fills the screen forever
- **Fix:** Fold a long bubble of your own behind "Show all" after about 15
  lines.
- *Where:* `app/chat/page.tsx:1803-1812`

### 46. Small errors (upload failed, remember failed) look like a broken reply and can't be dismissed
- **Fix:** Their own small notice with a ×.
- *Where:* `app/chat/page.tsx` `addFiles` and `rememberThread` → `chat-turn-error`

---

## Part 4 — Settings, connections and setup details

### 47. Removing the default model's server, or disconnecting the default, leaves chat pointing at nothing
- **Fix:** The confirm says "This is your default; Jarvis will use X instead",
  and picks a working default.
- *Where:* `daemon/routes/fleet.py:450-485`, `daemon/routes/connections.py:1457-1463`

### 48. Scanned-document reading (OCR) has no setting anywhere, yet its message says "settings"
- **Before:** You can't turn OCR on or off, set the page limit, or choose
  "read on this PC first" (which matters for client scans). Neither the
  Settings page nor chat can change it. The refusal points at a config file.
  The memory-search embedder has the same gap.
- **Fix:** Add these as settings rows, and reword the messages.
- *Where:* `core/config.py:597-601, 907-908`, `documents/ocr.py:86`,
  `documents/attachment_rag.py:111`

### 49. Ollama running with no model: "it's answering", then the button fails with a command to type
- **Fix:**
  - Say "Ollama is running but has no model yet", and point at the "Download
    a starter model" button.
  - Grey out step 2 until Ollama is installed: today "Download" before
    install claims it's downloading while the terminal shows an error.
  - Make step 3 the one "connect and use" press.
  - Reset the slow re-check when you press it.
- *Where:* `ConnectDoors.tsx:646-705`, `daemon/routes/settings.py:937-948`,
  `terminals/ai_clis.py:153-166`, `fleet/sampler.py:86`

### 50. Claude Code/Codex not installed: the only path is a developer web page with a PowerShell command (known)
- **Fix:** An "Install Claude Code for me" one-press job (like the Ollama
  starter), then Sign in.
- *Where:* `ConnectDoors.tsx:527-538`, `lib/onboarding.ts:714`

### 51. Adding a destination with a name already in use silently replaces the old one
- **Fix:** Refuse: "You already have “phone”. Pick another name, or press Edit
  on it."
- *Where:* `daemon/routes/comm.py:365-367, 450-452`

### 52. A destination name with a space fails with "invalid channel name"
- **Fix:** Say the rule beside the box, or turn spaces into dashes.
- *Where:* `daemon/routes/comm.py:354-355`

### 53. Slack's two-way switches still say "(true/false)" and "Set to true"
- **Fix:** Plain words, like Telegram's ("Listen for messages", "Allowed
  senders").
- *Where:* `daemon/app.py:3533-3540`

### 54. One setting with two controls that disagree until a reload
- **Before:** Browser access, Iron-Proxy, backup copy folder and the custom
  endpoint address are each changed both on a card AND on a plain Settings
  row. Changing one leaves the other showing the old value.
- **Fix:** One control per setting.
- *Where:* `components/settings/SettingsHome.tsx:201-211`

### 55. "Obsidian vault" / "Notion database" settings do nothing until a restart, and Notion needs a hidden secret
- **Fix:** Mark them "needs restart" and name the token, or retire them in
  favour of Memory › "Add a memory base".
- *Where:* `platform.py:1362-1370`, `settings/schema.py:798-801`

### 56. "Needs Node.js" gives its download link only on mouse hover
- **Fix:** A real "Get Node.js (free)" link beside the badge.
- *Where:* `components/settings/pages/DirectoryPage.tsx:676-684`

### 57. Adding a memory base says "Source added" without checking it works
- **Fix:** Read it back and say "Memory base X added and answering", or why
  not.
- *Where:* `components/memory/LongTerm.tsx:479-480`

### 58. Two pages scroll sideways on a phone (found by the live test)
- A long file path in a Tools chip ("Uses c:\users\…\node_repl") makes the
  whole Tools page 597 px wide on a 390 px screen.
- The Microsoft 365 card is 10 px wider than a phone screen, cutting off
  Connect.
- **Fix:** Show a short program name, let chips wrap, and give the card grid
  `minmax(0,1fr)`.
- *Where:* `lib/mcpImport.ts:~168`, `components/settings/pages/DirectoryPage.tsx:1554, 1600`

### 59. The home's "This PC: 5 of 6 helpers ready" makes a normal PC look broken
- **Before:** A PC without Microsoft Word shows a missing helper ("old .doc
  files"). Its Details show developer text such as `uv sync`.
- **Fix:** Count only required helpers on the home, and fold developer
  details away.
- *Where:* `components/chat/HomeReadiness.tsx:79-90`, `onboarding/doctor.py:619-707`

### 60. The key panel has no "where do I get a key" link for Anthropic or OpenAI
- **Fix:** Add console.anthropic.com and platform.openai.com links, with "billed
  per use".
- *Where:* `ConnectDoors.tsx:117-133`

### 61. Nothing on the home says what to try next after the first real answer
- **Before:** The checklist ("Work with a document", "Teach it your style")
  and "Import your tools" live on Everything › Status, which newcomers don't
  open.
- **Fix:** One quiet "Next: drop a PDF here and ask for a summary" line on the
  chat home.
- *Where:* `components/overview/StatusOverview.tsx:223, 409`

---

## Part 5 — Help, desktop app and wording

### 62. The Handbook (shown in Help and read by the Guide) says things that are no longer true
- **Before:**
  - The Handbook mentions an "Overview tips card" that was deleted.
  - It says "four places", but the sidebar has five.
  - It talks about "Simple mode", which was retired.
  - It says "Build is the terminals", while Build now has a Code view.
  - After the user guide it becomes thousands of lines of release notes in
    no time order.
- **Fix:** Move the release notes to a separate CHANGELOG, and keep the
  Handbook to how things work now.
- *Where:* `docs/HANDBOOK.md:31, 89, 128, 573, 4456`

### 63. The Guide also reads the developer manual (CLAUDE.md)
- **Before:** Ask "how do updates install?" and you may get the developer
  release steps ("bump the version, push to master").
- **Fix:** Leave developer files out of the Guide, or rank them very low and
  label them "developer notes".
- *Where:* `guide/corpus.py:54-55, 91-92`

### 64. "Recommended Settings" in Help is out of date and written in config keys
- **Fix:** Rewrite it with Settings' own labels, or remove it from Help.
- *Where:* `docs/RECOMMENDED-SETTINGS.md`

### 65. "What's new and updates" shows nothing about what's new
- **Fix:** Show the installed (and pending) release notes on the Updates card.
- *Where:* `components/settings/pages/UpdatesPage.tsx`

### 66. Quick task (Spotlight) says "Task done" when it needs you or partly failed, and hides its files
- **Fix:**
  - Use the same "Needs you / Part failed" words as everywhere else.
  - Save work to the Documents chat folder.
  - Add "Open folder" to the notice.
- *Where:* `desktop/main.js:2987-3014`

### 67. The "update is not starting" dialog says "reinstall the previous version from Releases"
- **Before:** There are hundreds of releases, and it doesn't say which one.
- **Fix:** Link straight to the last working version's installer.
- *Where:* `desktop/main.js:5994-6006`

### 68. Desktop messages show raw technical text
- **Before:**
  - The start-up failure dialog shows the raw error message.
  - The window-recovered notice names internal reasons like "oom" and claims
    "Nothing was lost", which isn't always true.
  - Spotlight shows "connect ECONNREFUSED 127.0.0.1:8787".
- **Fix:** Plain sentences, with the raw text kept in the logs.
- *Where:* `desktop/main.js:2899-2903, 6027, 6242`

### 69. The start-up splash says "a few seconds" for up to 3 minutes
- **Fix:** After about 15 seconds, change to "The first start after installing
  can take a minute…".
- *Where:* `desktop/loading.html:425-426`

### 70. The Activity page shows raw event names as its headlines
- **Before:** About 90 kinds of events show as "webhook.rejected",
  "trust.lowered", "workflow.completed" and so on. A webhook refused for a
  wrong secret is only a bare "webhook.rejected" row, with no name or
  reason.
- **Fix:** Add words for every event, plus the detail ("A webhook was refused
  · github-push · bad signature").
- *Where:* `dashboard/lib/eventWords.ts:23`, `eval/observability.py:440`

### 71. Agent history lists some Claude Code sessions twice (found live)
- **Before:** A resumed session file carries its parent's id, so two sessions
  show as the same one. The second can never be opened, and React warns
  about duplicate keys.
- **Fix:** Use the file name as the session id.
- *Where:* `history/claude_code.py:179-180`, `components/AgentHistory.tsx:385`

### 72. The Sessions page sometimes throws a React error on load (found live, about 1 load in 4)
- **Before:** The "Agent type" list's order differs between server and
  browser.
- **Fix:** Use the same order as the daemon, or render the list only after it
  loads.
- *Where:* `components/NewSessionForm.tsx:30, 79`

### 73. Wording to clean up (small, one line each)

| Where | Today | Plain words |
|---|---|---|
| `app/self-dev/page.tsx:37` | "Open Settings → Advanced" goes nowhere | link to `self_dev_enabled` in System |
| `app/autonomy/page.tsx:394` | "The trust surface for the Motivation Layer" | "Goals Jarvis works toward on its own, and the switch that stops it all" |
| `lib/nav.ts:286` | Watchers "always-on" (they start off) | "Watch a folder and get a suggestion when files change" |
| `app/sentinels/page.tsx:366-383` | a "Watcher kind" picker with one choice | remove it |
| `app/sessions/page.tsx:312-318` | the main button wipes all finished runs | move it into a ⋯ menu |
| `app/schedules/page.tsx:822-825` | "Notifications page" is not a link | make it a link |
| `components/TitleBar.tsx:203` | "Iron Jarvis — Overview" | "Iron Jarvis — Home" |
| `app/help/page.tsx:597` | "Settings → Maintenance" opens the top of Settings | link to System › Maintenance |
| `app/documents/page.tsx:1098` | "Redact PII" | "Hide personal details (SSNs, phone numbers…)" |
| `lib/buildApp.ts:2244` | "Iron-Proxy has these…" on a coding screen | "These accounts are marked…" |
| `components/buildCode/CodePanes.tsx:804` | "Its own MCP servers" | "Apps and tools it connected to" |
| `lib/sessionRail.ts:302-360` | "Claude Code's tool names" in Chat › Code | "Instructions written for Claude models" |
| `components/terminal/TerminalPane.tsx:138, 1729` | "no Jarvis capabilities", "isn't on your PATH" | "Starts without Iron Jarvis's memory and browser", "Not on this PC — get it" |
| `components/chat/HomeReadiness.tsx:105` | "Ask anything below" (the box is above) | "Type your question in the box" |
| `app/chat/page.tsx:7053, 12428, 13029` | "server-owned", "grounded", raw provider ids | plain words |
| `lib/chatControlWords.ts:108` | "All 6 tool slots are in use" | "You can turn on up to 6 tools per chat. Turn one off first." |
| `schema.py:463-465` | "the calendar link is set as a secret" | "Add your calendar link on the Rules page" (and link it) |

### 74. Smaller stuck states in Build
- **"Detecting…" forever:** the Launch menu can say this forever after one
  failed check. Add "Couldn't check — Try again".
  *Where:* `app/terminals/page.tsx:937-943`
- **Failed terminal list:** shows the error AND "No terminals yet", which
  invites duplicates. Show "Couldn't read your terminals — Retry" instead.
  *Where:* `app/terminals/page.tsx:911-917`
- **"Reading <tool>'s model list…":** can stay up for good. Keep checking for
  about 30 s, then say so.
  *Where:* `components/buildCode/BuildCodeView.tsx:205-206`
- **Build › Code start menus:** open inside a scroll box on short windows.
  Place them like the model menu.
  *Where:* `components/buildCode/StartComposer.tsx:148`
- **"Find my chat ID" on Telegram:** can stop a later search early. Keep and
  clear the timer.
  *Where:* `NotificationsPage.tsx:353`
- **Running now:** leaves out workflow runs.
  *Where:* `lib/runningNow.ts`

---

## Suggested order

1. **One release, "the first ten minutes":** items 1, 2, 3, 4, 5, 6, 11, 14,
   18, 58.
   - These are the items that decide whether a newcomer gets a real answer in
     their first session.
   - Most are small changes.
2. **"Nothing silent":** items 13, 20, 21, 22, 23, 24, 7, 10.
3. **"Edit, don't delete":** items 25–31, 47, 51.
4. **Wording and help:** items 62–73.
5. **Needs your decision:** 8 (signing), 9 (store listing), 12 (branch
   default), 35 (Local only default).
