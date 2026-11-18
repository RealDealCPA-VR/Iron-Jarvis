// v1.458.0: the redaction rules, in the page, for what a recording sends out.
//
// A recording describes the elements the user acted on with the page's own
// words (role, accessible name, nearby text). A page can print a token in a
// button's name or a link's text; before those words leave the tab they go
// through the SAME rules the daemon masks with (`detections/redact.py`). The
// rule block below is a byte-for-byte copy of the dashboard's
// `DETAIL_MASK_RULES` (dashboard/lib/stepDetail.ts), which
// tests/test_redact_linear_v1349.py keeps equal to the daemon's;
// tests/test_browser_recording_v1458.py keeps this copy equal to that one.
// (The add-on cannot import from the dashboard: it is bundled on its own.)

const MASK = "***";

export const DETAIL_MASK_RULES: ReadonlyArray<readonly [RegExp, string]> = [
  // v1.532.0 (D2): a PEM private key block, header through END line (first, so
  // no later rule cuts its body short; a cut block is still masked).
  [
    /(-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY-----)(?![*]{3})[A-Za-z0-9+\/=\s]*(-----END [A-Z0-9 ]{0,40}PRIVATE KEY-----)?/g,
    `$1${MASK}$2`,
  ],
  // Authorization: Bearer <v> / Basic <v> / Token <v> / <v>
  [/(\bauthorization["']?\s*[:=]\s*["']?(?:(?:bearer|basic|token)\s+)?)[^\s"',;]+/gi, `$1${MASK}`],
  // key = value / "key": "value" for secret-named keys (refresh_token=…, pwd:…)
  [
    /((?:api[_-]?key|access[_-]?key|client[_-]?secret|token|password|passwd|secret|pwd)["']?\s*[=:]\s*["']?)(?![*]{3})[^\s"'&,;}]+/gi,
    `$1${MASK}`,
  ],
  // curl -u user:pass / --user user:pass
  [/((?:^|\s)(?:-u|--user)[\s=]+["']?[^\s:"']+:)[^\s"']+/g, `$1${MASK}`],
  // scheme://user:pass@host
  [/((?<![a-z0-9+.-])(?=[a-z0-9+.-]*?\b[a-z])[a-z0-9+.-]*:\/\/[^/\s:@]+:)[^@\s/]+(@)/gi, `$1${MASK}$2`],
  // known token shapes
  [/\b(ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|sk-ant-|sk-|xox[abprs]-)[A-Za-z0-9_-]{6,}/g, `$1${MASK}`],
  [/\b(AKIA|ASIA)[0-9A-Z]{16}\b/g, `$1${MASK}`],
  // v1.532.0 (D2): a bare Bearer value, Google AIza…, xAI / Groq / Hugging Face
  [/(\bbearer:?\s+)(?![*]{3})(?=[A-Za-z0-9._~+\/=-]*\d)[A-Za-z0-9._~+\/=-]{12,}/gi, `$1${MASK}`],
  [/\b(AIza)[0-9A-Za-z_-]{30,}/g, `$1${MASK}`],
  [/\b(xai-|gsk_|hf_)[A-Za-z0-9]{20,}/g, `$1${MASK}`],
  // Stripe secret / restricted keys (live and test)
  [/\b((?:sk|rk)_(?:live|test)_)[A-Za-z0-9]{16,}/g, `$1${MASK}`],
  // GitLab personal access token, Google OAuth client secret
  [/\b(glpat-|GOCSPX-)[A-Za-z0-9_-]{20,}/g, `$1${MASK}`],
  // npm access token
  [/\b(npm_)[A-Za-z0-9]{30,}/g, `$1${MASK}`],
  // a JWT (three base64url parts, the first starting eyJ)
  [/(?<![A-Za-z0-9_-])(eyJ)[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/g, `$1${MASK}`],
  // an Entra (Azure AD) client secret pasted bare: 3 characters, a digit, Q~, 31-34 more
  [/(?<![A-Za-z0-9_~.-])[A-Za-z0-9_~.]{3}\dQ~[A-Za-z0-9_~.-]{31,34}(?![A-Za-z0-9_~-])/g, MASK],
];

/** `text` with every secret-looking VALUE replaced by "***". */
export function maskText(text: string): string {
  let out = String(text ?? "");
  for (const [rx, repl] of DETAIL_MASK_RULES) out = out.replace(rx, repl);
  return out;
}
