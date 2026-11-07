# Chat: work in a folder without a project (target v1.443.0)

The user: "i have a preference in chat module that isnt there, there is an
option to select a project, but what if i dont want it to be a specific project
- rather just work in a specific folder and that selection process should be
exactly as the folder is selected in build > code."

## What changed for the user

- A new chat shows Build › Code's folder chip beside "No project": "No folder ·
  Choose…". In the desktop app Choose… opens Windows' own folder picker; in a
  browser the app's folder tree in a dialog (the same component Build › Code
  uses). After a pick the chip names the folder (full path as its title) with
  an × back to "No folder".
- In a conversation the composer toolbar has a small folder chip beside the
  project chip: a folder icon, the folder's name, the same picker. In a tight
  toolbar (and on a phone) the name and × give way with the project's fit step,
  so the toolbar stays one row; "+" then has "No folder for this chat".
- "+ Choose a working folder" and the drawer's Change open the SAME picker (no
  more tree inside the drawer to change a folder; the drawer still shows the
  tree when there is no folder yet).
- A pick sets the chat's working folder (`workspace_dir` on every turn, saved
  with the chat, the sticky default for new chats), arms the file essentials
  over an empty tool set, and lets go of a conversation's own folder made for
  an earlier attachment (the next attachment goes to the picked folder).
- A folder OUTSIDE the active project takes the chat out of the project (the
  chip's title says so first). A folder INSIDE the project (its root or a
  folder under it) keeps the chat in it; the v1.329.0 "Working in <sub>" chip
  shows. (A deliberate reading of "not the project's root": the "@" folder
  pick and the old "+" flow kept a subfolder in the project, and that stays.)
- × (or "+ No folder for this chat") goes back to "No folder": the tools the
  pick armed go and the sticky default is forgotten. Inside a project there is
  no × (the project's folder is the folder).
- Right after a pick the daemon is asked whether the chat can work there
  (`POST /documents/folder-check`). A refusal is SAID: under the chip on a new
  chat, on the composer card (and the chip turns the danger tone) in a
  conversation. The pick stands either way.
- A reopened chat shows its saved folder on the chip.

## Code

- `dashboard/components/folders/FolderChip.tsx`: the picking half lifted into
  `useFolderPicker` (Windows' picker via `folderPickerBridge`, else the tree
  dialog), unchanged behaviour; FolderChip takes an optional `picker` (the
  caller renders the dialog), `clearTitle`, `chooseTitle`. The tree is now a
  `next/dynamic` import (it must stay off the /chat route:
  `chat-wave3-deferred-v1311`), so Build loads it lazily too.
- `dashboard/components/chat/ChatFolderChip.tsx`: the toolbar chip.
- `dashboard/lib/chatFolder.ts`: `insideFolder`, `pickLeavesProject`,
  `chatFolderTitle`, `checkChatFolder` (null on any failure: never a guess),
  the words.
- `dashboard/app/chat/page.tsx`: ONE `chatFolderPicker` for the page;
  `pickChatFolder` / `clearChatFolder`; `folderProblem` keyed by path and a
  sequence number (an older answer never shows; leaving a chat drops it);
  `pickingFolder` state removed.
- `src/iron_jarvis/daemon/routes/documents.py`: `POST /documents/folder-check
  {path}` → `{problem, checked}`; `fs_policy.root_problem` through
  `asyncio.to_thread`, capitalised; a network spelling (`\\host`, `//host`,
  device paths) is judged on its TEXT and never looked at (`checked: false`,
  nothing said; the chat lanes accept shares, so it is not a refusal); a check
  that raises is a sentence. `schemas.FolderCheckBody` (path ≤ 4096).

## Pins

- `dashboard/__tests__/chat-folder-chip-v1443.test.tsx` (18).
- `tests/test_chat_folder_check_v1443.py` (13).
- Moved on purpose: `drawer-folder-calm-v1329` "Change opens the shared calm
  picker and Cancel returns" (it opened the drawer's own tree).

## Mutations (each red, restored in a finally)

Daemon: M1 the check on the loop (no to_thread); M2 a network spelling looked
at; M3 the words not a sentence; M4 a raise is a 500; M5 the dashboard caller
renamed (also reddens `test_wave6_dead_code_v14390`).

Dashboard: D1 a pick never leaves the project; D2 a subfolder leaves too; D3 no
stale-answer guard; D4 clear keeps the sticky default; D5 a pick arms nothing;
D6 the conversation's own folder kept; D7 "+" opens the drawer instead; D8 the
picker ignores Windows' picker; D9 the toolbar name not fitted; D10 the hero
chip silent about a problem; D11 the drawer's Change opens nothing; D12 no
toolbar chip; D13 a problem outlives its folder. (D3 and D13 first survived —
the test covered both with one case — and each got its own case.)

## OPEN

- Not driven in the packaged app (Windows' picker is proven through the bridge
  stub only) and not checked in a real browser at 390 px; the one-row e2e
  (`e2e/composer-one-row-v1400.spec.ts`) was not run with the new chip.
- A restored chat's folder is not checked (only a pick is); its problem shows
  only at the next message, as before.
- A share folder is never checked here.
- The sticky default is still written by a pick (as before); choosing a folder
  in one chat makes it the next new chat's folder until cleared.

## Draft CLAUDE.md bullet

- **A chat can work in a FOLDER without a project, picked exactly as Build ›
  Code picks one** (v1.443.0, a USER REQUEST). `FolderChip`'s picking half is
  `useFolderPicker` (Windows' picker via `folderPickerBridge`, else the tree in
  a dialog; the tree is a `next/dynamic` import — it stays off /chat); the chat
  page holds ONE picker for the hero chip (beside "No project"), the toolbar's
  `ChatFolderChip` (name and × on the project's fit step), "+ Choose a working
  folder" and the drawer's Change. `pickChatFolder`: outside the active project
  leaves it (`lib/chatFolder.pickLeavesProject`; inside keeps it), arms
  `PROJECT_FILE_TOOLS` over an empty set, drops the conversation's own folder,
  then `POST /documents/folder-check` (root_problem off the loop; a network
  spelling never looked at) — the refusal shown only while about the folder on
  screen. Pins: `chat-folder-chip-v1443`, `tests/test_chat_folder_check_v1443.py`.

## Draft Handbook paragraph

**Work in a folder without a project.** On a new chat, beside "No project",
the folder chip reads "No folder · Choose…". Choose… opens Windows' own folder
picker (in a browser, the app's folder list). Jarvis then works on files in
that folder, saves what it makes there, and remembers it with the chat. In a
conversation the folder chip sits in the composer's bottom row next to the
project. Choosing a folder outside the chat's project takes the chat out of
the project; a folder inside the project keeps it. If Jarvis can't work in the
folder you chose (it can't save files there, for example), it says so under
the chip. × or "+ › No folder for this chat" goes back to no folder.
