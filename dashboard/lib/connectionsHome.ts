/**
 * Where the ONE connections front door is (v1.455.0, readiness Wave H — the
 * owner's decision on item 35): the Settings › Connections group, whose top
 * holds the Discover / Connected / Import landing
 * (`components/settings/ConnectionsHome`). Every other connection page — the
 * Directory, Tools, Model servers, Notifications — links here.
 *
 * The address goes through the OLD `/connections` alias (lib/relocated), the
 * v1.353.0 rule: no link points at `/settings?section=` (the App Router keeps
 * Settings mounted across a search-param change). `tab` names the landing's
 * tab, and SettingsHome reads it as "the Connections group", not the accounts
 * section the alias opens on its own — so `/connections` and
 * `/connections?focus=endpoints` land exactly where they always did.
 *
 * Import-free on purpose (several pages and the tests read it).
 */

/** The landing's address (`tab=connected` / `tab=import` opens that tab). */
export const CONNECTIONS_HOME_HREF = "/connections?tab=discover";
/** The words of a link to it. */
export const CONNECTIONS_HOME_LABEL = "All your connections";
