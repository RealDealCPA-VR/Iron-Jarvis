// GENERATED FILE — DO NOT EDIT.
//
// Written by `uv run python -m iron_jarvis.browser.gen_protocol` from
// src/iron_jarvis/browser/protocol.py, which is the single source of truth for
// the Browser Bridge wire protocol.
//
// A hand edit here is reverted by the next generation and fails
// tests/test_browser_protocol_v1235.py, which regenerates this file into a
// buffer and compares it byte for byte. Change protocol.py and regenerate.

// --- Constants, from protocol.py ---

export const ALL_DIRECTIVES = ["request_host_permissions", "disconnect"] as const;
export const ALL_EVENTS = ["tab_activated", "navigation_completed", "download_completed"] as const;
export const ALL_FRAME_TYPES = ["browser.command", "browser.directive", "browser.paired", "browser.pairing_required", "browser.ready", "browser.connection_replaced", "browser.hello", "browser.response", "browser.event", "browser.pairing_ack"] as const;
export const ALL_METHODS = ["status", "list_tabs", "active_tab", "read_page", "get_elements", "screenshot", "activate_tab", "scroll", "create_tab", "close_tab", "click", "type_text", "press_key", "navigate"] as const;
export const COMMAND_TIMEOUTS_S = { "navigate": 30.0, "read_page": 20.0 };
export const DAEMON_TO_EXTENSION = ["browser.command", "browser.directive", "browser.paired", "browser.pairing_required", "browser.ready", "browser.connection_replaced"] as const;
export const DEFAULT_COMMAND_TIMEOUT_S = 15.0;
export const DEFAULT_SNAPSHOT_MODE = "interactive";
export const DIRECTIVE_DISCONNECT = "disconnect";
export const DIRECTIVE_REQUEST_HOST_PERMISSIONS = "request_host_permissions";
export const EVENT_DOWNLOAD_COMPLETED = "download_completed";
export const EVENT_ID_PREFIX = "evt_";
export const EVENT_NAVIGATION_COMPLETED = "navigation_completed";
export const EVENT_TAB_ACTIVATED = "tab_activated";
export const EXTENSION_TO_DAEMON = ["browser.hello", "browser.response", "browser.event", "browser.pairing_ack"] as const;
export const FRAME_COMMAND = "browser.command";
export const FRAME_CONNECTION_REPLACED = "browser.connection_replaced";
export const FRAME_DIRECTIVE = "browser.directive";
export const FRAME_EVENT = "browser.event";
export const FRAME_HELLO = "browser.hello";
export const FRAME_PAIRED = "browser.paired";
export const FRAME_PAIRING_ACK = "browser.pairing_ack";
export const FRAME_PAIRING_REQUIRED = "browser.pairing_required";
export const FRAME_READY = "browser.ready";
export const FRAME_RESPONSE = "browser.response";
export const FULL_TEXT_CHARS = 60000;
export const LOCAL_UI_METHODS = ["activate_tab", "scroll", "create_tab", "close_tab"] as const;
export const MAX_AX_DEPTH = 24;
export const MAX_AX_NODES = 5000;
export const MAX_ELEMENTS = 250;
export const MAX_FRAME_BYTES = 524288;
export const MAX_HEADINGS = 100;
export const MAX_LINKS = 200;
export const MAX_NAME_CHARS = 200;
export const MAX_TEXT_CHARS = 20000;
export const METHOD_ACTIVATE_TAB = "activate_tab";
export const METHOD_ACTIVE_TAB = "active_tab";
export const METHOD_CLICK = "click";
export const METHOD_CLOSE_TAB = "close_tab";
export const METHOD_CREATE_TAB = "create_tab";
export const METHOD_GET_ELEMENTS = "get_elements";
export const METHOD_LIST_TABS = "list_tabs";
export const METHOD_NAVIGATE = "navigate";
export const METHOD_PRESS_KEY = "press_key";
export const METHOD_READ_PAGE = "read_page";
export const METHOD_SCREENSHOT = "screenshot";
export const METHOD_SCROLL = "scroll";
export const METHOD_STATUS = "status";
export const METHOD_TYPE_TEXT = "type_text";
export const MODE_FULL = "full";
export const MODE_INTERACTIVE = "interactive";
export const MODE_SUMMARY = "summary";
export const PAGE_ACTION_METHODS = ["click", "type_text", "press_key", "navigate"] as const;
export const PAIRING_DEADLINE_S = 300.0;
export const PAIRING_ID_PREFIX = "pair_";
export const PASSWORD_AUTOCOMPLETE = ["current-password", "new-password"] as const;
export const PAYMENT_AUTOCOMPLETE = ["cc-csc", "cc-exp", "cc-exp-month", "cc-exp-year", "cc-number"] as const;
export const PROTOCOL_VERSION = 1;
export const READ_METHODS = ["status", "list_tabs", "active_tab", "read_page", "get_elements", "screenshot"] as const;
export const REQUEST_ID_PREFIX = "req_";
export const RESTRICTED_INBOUND_FRAMES = ["browser.pairing_ack"] as const;
export const SENSITIVE_AUTOCOMPLETE = ["cc-csc", "cc-exp", "cc-exp-month", "cc-exp-year", "cc-number", "current-password", "new-password"] as const;
export const SNAPSHOT_ID_PREFIX = "snap_";
export const SNAPSHOT_MODES = ["summary", "interactive", "full"] as const;
export const SUMMARY_TEXT_CHARS = 2000;
export const UNSUPPORTED_HOSTS = ["chromewebstore.google.com", "chrome.google.com"] as const;
export const UNSUPPORTED_SCHEMES = ["chrome:", "edge:", "about:", "devtools:", "view-source:", "chrome-extension:"] as const;

// --- Error codes and their model-actionable remedies, from errors.py ---

export type BrowserErrorCode =
  | "BROWSER_NOT_CONNECTED"
  | "BROWSER_ACCESS_OFF"
  | "READ_ONLY_MODE"
  | "TAB_NOT_FOUND"
  | "PAGE_NOT_READY"
  | "ELEMENT_NOT_FOUND"
  | "STALE_ELEMENT"
  | "STALE_SNAPSHOT"
  | "PERMISSION_DENIED"
  | "ACTION_TIMEOUT"
  | "NAVIGATION_FAILED"
  | "DOWNLOAD_FAILED"
  | "UNSUPPORTED_PAGE"
  | "EXTENSION_ERROR"
  | "PAIRING_REQUIRED"
  | "AUTHENTICATION_FAILED"
  | "CONNECTION_REPLACED";

export const BROWSER_ERROR_CODES = ["BROWSER_NOT_CONNECTED", "BROWSER_ACCESS_OFF", "READ_ONLY_MODE", "TAB_NOT_FOUND", "PAGE_NOT_READY", "ELEMENT_NOT_FOUND", "STALE_ELEMENT", "STALE_SNAPSHOT", "PERMISSION_DENIED", "ACTION_TIMEOUT", "NAVIGATION_FAILED", "DOWNLOAD_FAILED", "UNSUPPORTED_PAGE", "EXTENSION_ERROR", "PAIRING_REQUIRED", "AUTHENTICATION_FAILED", "CONNECTION_REPLACED"] as const;

export const REMEDIES: Record<BrowserErrorCode, string> = {
  "BROWSER_NOT_CONNECTED": "Your browser is not connected to Iron Jarvis. Ask the user to open the Browser page in Iron Jarvis and pair their browser, then retry.",
  "BROWSER_ACCESS_OFF": "Browser access is off. Ask the user to set Browser access to Read only or Interactive on the Browser page in Iron Jarvis, then retry.",
  "READ_ONLY_MODE": "Browser access is Read only, so this tool cannot change the page. The reading tools still work; ask the user to switch Browser access to Interactive if you should act on the page.",
  "TAB_NOT_FOUND": "No tab {tab_id} is open. Call browser_list_tabs and retry with an id from that list.",
  "PAGE_NOT_READY": "Tab {tab_id} has not finished loading. Call browser_read_page again before acting on it.",
  "ELEMENT_NOT_FOUND": "No element {element_id} in snapshot {snapshot_id}. Call browser_read_page to list what is on the page now.",
  "STALE_ELEMENT": "The page changed after the previous snapshot. Call browser_read_page and retry using the new element ID.",
  "STALE_SNAPSHOT": "No current snapshot for this tab. Call browser_read_page and retry with the new element ID.",
  "PERMISSION_DENIED": "Site access has not been granted to the Iron Jarvis browser add-on. Open the Browser page in Iron Jarvis and press Grant site access.",
  "ACTION_TIMEOUT": "Your browser did not answer in time. Call browser_get_status to check the connection, then retry the call once.",
  "NAVIGATION_FAILED": "Navigation to {url} failed. Check the address, then retry once or ask the user to open the page themselves.",
  "DOWNLOAD_FAILED": "The download did not complete. Ask the user to check their browser's downloads, then retry.",
  "UNSUPPORTED_PAGE": "{scheme} pages are closed to add-ons by Chrome. Ask the user to switch to a normal tab.",
  "EXTENSION_ERROR": "Your browser reported an error: {detail}. Call browser_get_status; if it persists, ask the user to reload the add-on from chrome://extensions.",
  "PAIRING_REQUIRED": "This browser is not paired with Iron Jarvis. Ask the user to open the Browser page in Iron Jarvis and press Pair.",
  "AUTHENTICATION_FAILED": "Your browser's pairing was refused. Ask the user to press Forget on the Browser page in Iron Jarvis and pair the browser again.",
  "CONNECTION_REPLACED": "Another browser connected and replaced this one, so this call was abandoned rather than left hanging. Call browser_get_status and retry against the connected browser.",
};

// --- Frame and payload shapes, from the TypedDicts in protocol.py ---

/** The two-key failure envelope, built only by browser_error(). */
export interface ErrorEnvelope {
  code: string;
  message: string;
}

/** Daemon -> extension: run one method. ``id`` keys the pending future. */
export interface CommandFrame {
  id: string;
  type: string;
  method: string;
  params: Record<string, unknown>;
}

/** Extension -> daemon: the answer to one command. */
export interface ResponseFrame {
  id: string;
  type: string;
  success: boolean;
  result?: Record<string, unknown>;
  error?: ErrorEnvelope;
}

/** Daemon -> extension: do something to the browser session itself. */
export interface DirectiveFrame {
  id: string;
  type: string;
  action: string;
  params?: Record<string, unknown>;
}

/** Daemon -> extension: this socket is unpaired; here is its request id. */
export interface PairingRequiredFrame {
  type: string;
  request_id: string;
}

/** Extension -> daemon: the one frame a restricted socket may send. */
export interface PairingAckFrame {
  type: string;
  request_id: string;
}

/** Daemon -> extension: the pairing token, delivered exactly once, ever. */
export interface PairedFrame {
  type: string;
  token: string;
}

/** Daemon -> extension: you are the authoritative connection. */
export interface ReadyFrame {
  type: string;
  active: boolean;
  access?: string;
}

/** Daemon -> outgoing extension: a newer socket took over (D08). */
export interface ConnectionReplacedFrame {
  type: string;
  error: ErrorEnvelope;
}

/** Extension -> daemon: who I am and whether I hold the host grant. */
export interface HelloFrame {
  type: string;
  extension_id: string;
  extension_version: string;
  host_permission: boolean;
}

/** Extension -> daemon: something happened in the browser, unprompted. */
export interface EventFrame {
  id: string;
  type: string;
  event: string;
  payload: Record<string, unknown>;
}

/** ``read_page`` params. Every limit is sent, none is assumed. */
export interface ReadPageParams {
  mode: string;
  tab_id?: number;
  max_chars?: number;
  max_elements?: number;
  max_headings?: number;
  max_links?: number;
  max_name_chars?: number;
  max_ax_depth?: number;
  max_ax_nodes?: number;
}

/** ``get_elements`` params — a filtered view of one fresh snapshot. */
export interface GetElementsParams {
  tab_id?: number;
  query?: string;
  role?: string;
  limit?: number;
}

/** ``screenshot`` params. ``full_page`` is always sent, never inferred. */
export interface ScreenshotParams {
  full_page: boolean;
  tab_id?: number;
}

/** One entry in the interactive registry (D13), verbatim. */
export interface ElementRow {
  id: string;
  role: string;
  name: string;
  text: string;
  visible: boolean;
  enabled: boolean;
  type?: string;
  autocomplete?: string;
  sensitive?: boolean;
  value?: null;
}

/** One heading, as ``{level, text}``. */
export interface HeadingRow {
  level: number;
  text: string;
}

/** One link. ``element_id`` ties it back to the registry. */
export interface LinkRow {
  element_id: string;
  text: string;
  href: string;
}

/** One form. ``fields`` holds element ids, never values. */
export interface FormRow {
  name: string;
  action: string;
  fields: string[];
}

/** The Q03 injection warning that rides on a snapshot. */
export interface SecurityNote {
  warning: boolean;
  category: string;
  reason: string;
}

/** One limit that bit, named, with how much was dropped (D13A). */
export interface TruncationRow {
  limit: string;
  kept: number;
  total: number;
}

/** The ``read_page`` result — the snapshot of plan section 9.1. */
export interface SnapshotResult {
  snapshot_id: string;
  page_version: number;
  tab_id: number;
  title: string;
  url: string;
  mode: string;
  truncated: boolean;
  text: string;
  headings: HeadingRow[];
  elements: ElementRow[];
  forms: FormRow[];
  links: LinkRow[];
  security?: SecurityNote | null;
  timestamp?: string;
  truncation?: TruncationRow[];
  counts?: Record<string, number>;
  omitted?: string[];
}

/** The ``get_elements`` result — a filtered slice of a fresh snapshot. */
export interface ElementsResult {
  snapshot_id: string;
  page_version: number;
  elements: ElementRow[];
  count: number;
  truncated: boolean;
  truncation?: TruncationRow[];
}

/** The ``screenshot`` result. Base64 PNG on the response frame (D14). */
export interface ScreenshotResult {
  tab_id: number;
  media_type: string;
  data_b64: string;
}

// --- Frame type -> shape, mirroring protocol.FRAME_SHAPES ---

export type BrowserFrame =
  | CommandFrame
  | ResponseFrame
  | DirectiveFrame
  | PairingRequiredFrame
  | PairingAckFrame
  | PairedFrame
  | ReadyFrame
  | ConnectionReplacedFrame
  | HelloFrame
  | EventFrame;

