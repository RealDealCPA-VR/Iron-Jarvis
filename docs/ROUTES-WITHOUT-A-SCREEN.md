# Routes without a screen

Wave 6 of the chat-first plan (`docs/CHAT-FIRST-PLAN-2026-11-04.md`, Part C and
decision D6, approved as recommended): nothing is orphaned without a reason
written down. This page is that list. Written for v1.439.0, 2026-11-06.

## How the list was made

1. Every route the daemon registers (`create_app(...).routes`: 629 with the
   framework's own before this wave, 620 after it).
2. Every place that could call one: the dashboard (`dashboard/app`,
   `components`, `lib`), the desktop app (`desktop/*.js`), the browser add-on
   (`extensions/chrome/src` and its build file), and the CLI
   (`daemon/cli.py`, `daemon/client.py`). String literals were read with their
   constants, small path helpers (`${base(id)}/commit`), the daemon's address
   (`${API_BASE}/...`) and a run-time last segment (`/goals/${id}/${verb}`)
   expanded; comments were left out.
3. Each route with no caller found was then checked BY HAND (method included:
   a `GET` and a `DELETE` of the same path are two routes), and given one
   decision:
   - **REMOVE**: a leftover of the removed round table, or an older copy of a
     current route. Its handler, and anything only it used, is gone.
   - **KEEP**: it has a caller outside these clients (Slack, an OAuth
     provider, a remote agent, an MCP client), it is a documented API or a
     probe, or it is used headlessly. The reason is in the row.
   - **KEEP (group D)**: a feature Wave 6 group D is reconnecting to a screen.
   - **KEEP (unsure)**: nothing calls it and it is not clearly dead; the row
     says why it was not removed. These are the owner's to decide.

`tests/test_wave6_dead_code_v14390.py` keeps this honest: every registered
route must have a caller by the rule in step 2 OR a row here; every KEEP row
must still be registered; every REMOVE row must be gone (404/405). A new route
nobody calls fails that test until it has a row.

**What the check cannot see.** It reads paths, not methods, so where two
methods share a path (`GET` and `DELETE /goals/{id}`), or a run-time last
segment could be anything (`/workflows/runs/${id}`), it counts the route as
called. It can miss an orphan; it never invents one. Rows marked "(by hand)"
are routes the check counts as called that have no caller.

## The table

| Method | Route | Decision | Reason |
|---|---|---|---|
| GET | `/agents/threads` | REMOVE | Round-table leftover: the Agents page's room list (ThreadRail), gone from the dashboard since v1.308.0. |
| POST | `/agents/threads` | REMOVE | Round-table leftover: made a room from the Agents page. Chat @-mentions make their rooms through `POST /chat/panel` (`AgentThreads.for_chat`). |
| PUT | `/agents/threads/{thread_id}/participants` | REMOVE | Round-table leftover: re-seat a room by hand. A project room still follows its team through `PUT /projects/{id}/team` (`world.sync_room`). |
| DELETE | `/agents/threads/{thread_id}` | REMOVE | Round-table leftover (D6: "remove those routes"). Deleting a project still hands its rooms back to General. |
| POST | `/agents/threads/{thread_id}/say` | REMOVE | Round-table leftover: one speaking round from the Agents page. Rounds still run from chat (`POST /chat/panel`), the phone and remote inbound. |
| POST | `/agents/threads/{thread_id}/remember` | REMOVE | Round-table leftover (D6). A chat's own Remember (`POST /chat/threads/{id}/remember`) is unchanged. |
| POST | `/projects/{project_id}/world/room` | REMOVE | Round-table leftover: opened a project's round table (the removed RoundTable). `world.ensure_room` and `NoTeamError` went with it. |
| GET | `/providers` | REMOVE | Older copy of `GET /health`'s `providers` (the same `_visible_providers()` list). |
| POST | `/providers/endpoint-models` | REMOVE | Older copy of `POST /connections/endpoints/models` (the Connections form's "Fetch available models"). `discovery.list_endpoint_models`, which only it used, went with it. |
| GET | `/openapi.json` | KEEP | FastAPI's own API description; the auth layer exempts it as a documented developer door. |
| GET | `/docs` | KEEP | FastAPI's own API browser (developer use). |
| GET | `/docs/oauth2-redirect` | KEEP | Part of FastAPI's API browser. |
| GET | `/redoc` | KEEP | FastAPI's own API reference (developer use). |
| GET | `/mcp` | KEEP | Iron Jarvis's MCP server: the CLIs in Build terminals and the Code view's tools call it (by hand: the check counts a placeholder address as a caller). |
| POST | `/mcp` | KEEP | The same MCP server (by hand, as above). |
| DELETE | `/mcp` | KEEP | The same MCP server: ends an MCP session (by hand, as above). |
| POST | `/comm/slack/events/{name}` | KEEP | Slack calls it (Events API). |
| POST | `/agents/remote/{name}/inbound` | KEEP | A remote agent calls it to message back (token-checked). |
| GET | `/doctor` | KEEP | The headless doctor (the readiness and Settings views read the same checks in-process); probes and support use it. |
| GET | `/metrics` | KEEP | Headless twin of `ironjarvis metrics`. Its last screen was the Overview's HealthCard, removed in this wave. |
| GET | `/ui/visits` | KEEP | The redesign's measurement read of page opens (`docs/redesign/REPORT.md`, S0). |
| GET | `/guide/search` | KEEP | The Guide's inspection surface (searches the bundled docs the Guide answers from). |
| GET | `/guide/ground` | KEEP | The Guide's inspection surface (the grounding a Guide answer would get). |
| GET | `/assignments/{assignment_id}` | KEEP | The single read of the assignments API (the list, create, cancel, unblock and retry have screens). |
| GET | `/coach/proposals` | KEEP | The coach's cross-agent list; each agent's card reads its own through `GET /agents/{name}/coach`. |
| GET | `/media/jobs` | KEEP | Headless read of the media job queue; the chat gets a finished job's line from the daemon itself. |
| POST | `/browser/recordings` | KEEP | v1.458.0: save a task recorded in the browser as a skill, for a script. The add-on saves the same way through its panel socket (`record_save`), which ends in the same function (`browser/recording.save_and_publish`). |
| GET | `/media/jobs/{job_id}` | KEEP | Headless read of one media job (as above). |
| GET | `/agents/{name}/face` | KEEP | One agent's face override; the pages read all of them from `GET /agents/faces`, and `PUT`/`DELETE` of this path have screens (by hand). |
| DELETE | `/goals/{goal_id}` | KEEP (group D) | Deleting a goal from Chat: group D is giving it a button on the Autonomy page (by hand: `GET` of the path is called). |
| POST | `/iron-proxy/accounts/{account_id}/signout` | KEEP (group D) | Signing an Iron-Proxy account out: group D is reconnecting it on the Connections card. |
| GET | `/build/sessions/{thread_id}/ci` | KEEP (group D) | A coding session's pull-request watch: group D is showing it in the session's views. |
| DELETE | `/triggers/calendar/url` | KEEP (group D) | Removing a saved calendar link: group D is reconnecting it on the Reflex page. |
| GET | `/routing` | KEEP (unsure) | Auto routing's view (suggested model, tiers). Its only screen was the title-bar ModelSwitcher, removed in this wave; Auto is still set through Settings rows (`default_provider` "auto", `routing_model`) and from chat. The owner decides whether Auto needs its own press again or these three go. Checked in review: no screen calls any of the three; Auto stays choosable as the default (Settings › Models, whose Default provider list starts with "auto", and the chat's settings tools), but not for one chat (the composer's model menu has no Auto row). `/routing/enable`'s only extra over the Settings rows is picking the cheapest connected model as the classifier. |
| POST | `/routing/enable` | KEEP (unsure) | Turns Auto on with the cheapest connected model (as above). |
| POST | `/routing/disable` | KEEP (unsure) | Turns Auto off, back to a connected model (as above). |
| GET | `/fleet/code-route` | KEEP (unsure) | Coding work pointed at a local node. The Settings rows `fleet_code_target`/`fleet_code_route_enabled` are the current way; this route also checks the target is a routable node. Checked in review: the settings writer does NOT (it saves any `fleet_code_target` text), so this route is the only door that refuses a target that is not a routable fleet node. Removing it would lose that check. |
| PUT | `/fleet/code-route` | KEEP (unsure) | As above. |
| GET | `/fleet/nodes/{node_id}/history` | KEEP (unsure) | A node's telemetry series, written for sparklines the Fleet page never drew. |
| POST | `/fleet/probe` | KEEP (unsure) | What answers at an address before it is saved. Its only screen was the Model servers page's own add form, retired in v1.455.0 (readiness Wave H item 31: ONE add form, on Settings › Connections, which reads a server's models through `/connections/endpoints/models` instead). Saves nothing; kept for scripts and a later preview on the one form. |
| GET | `/build/library` | KEEP (unsure) | A folder's whole harness library; the session views read `GET /build/sessions/{id}/library`. |
| POST | `/build/sessions/{thread_id}/reflect` | KEEP (unsure) | Asks for the library reflection now; the screens wait for the idle stretch. |
| GET | `/vault` | KEEP (unsure) | Which providers the browser-session vault holds a login for; nothing reads it. Checked in review: only test mocks name it (`overview-wave2-v1310` pins that the Status view no longer reads it); it is neither a round-table leftover nor a copy of another route, so it stays the owner's call. |
| POST | `/agents/remote/{name}/run` | KEEP (unsure) | Runs a remote agent once with a task (headless); chat and agents reach remotes through `delegate_remote` and rooms. |
| GET | `/artifacts/{name}` | KEEP (unsure) | One agent artifact's metadata and text; the Artifacts page lists code artifacts, and the creative library serves files through `/creative/file/{name}`. |
| GET | `/fs/home` | KEEP (unsure) | The home folder's path; the directory tree starts from `/fs/drives` instead. |
| POST | `/code-artifacts` | KEEP (unsure) | Save a script by hand; the Artifacts page has no Save form (agents save through the code tool). By hand: the list's `GET` is called. |
| POST | `/workflows/runs/prune` | KEEP (unsure) | Trims finished workflow-run history (the newest `keep` stay); nothing calls it. By hand: `/workflows/runs/${id}` reads as a caller. |
| GET | `/skills/proposals` | KEEP (unsure) | The skill-proposal list; the Skills page reads proposals inside `GET /skills/learning`. By hand: `/skills/${name}` reads as a caller. |

## What this wave removed from the dashboard

The seven modules imported only by tests: `components/EventStream.tsx`,
`components/FirstRunWizard.tsx`, `components/ModelSwitcher.tsx`,
`components/overview/AppGrid.tsx`, `components/overview/HealthCard.tsx`,
`components/PowerTips.tsx` and `lib/tileDrag.ts`, plus the parts of
`lib/appTiles.ts` only AppGrid read (the tile catalogue, arrangement and
groups). `recordOpen` and `recordDoorOpen` stay: the nav and the chat's doors
call them.
