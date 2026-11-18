/**
 * v1.532.0 (fourth review, decision D2): the dashboard's second mask over a
 * saved step detail (`maskDetailText`, `DETAIL_MASK_RULES`) knows the same new
 * key shapes as the daemon's `detections/redact.mask` — Stripe, GitLab, npm,
 * Google OAuth, a JWT, a PEM private key block, an Entra client secret, and
 * the failure words' shapes (a bare Bearer value, AIza…, hf_…, gsk_…, xai-…).
 * The patterns are pinned identical to the daemon's by
 * tests/test_redact_linear_v1349.py; this file pins what they DO here.
 *
 * Every secret-shaped value is assembled at run time from pieces (the
 * repository is public: no literal here is shaped like a real key).
 */
import { describe, expect, it } from "vitest";
import { maskDetailText } from "@/lib/stepDetail";

const FILL = "Fake" + "Example" + "0123456789" + "NotReal" + "Value12";

const pem = (kind: string) =>
  "-----BEGIN " + kind + "PRIVATE KEY-----\n" +
  [0, 1, 2, 3].map((i) => "MII" + "Fake" + "Body" + String(i).repeat(40)).join("\n") +
  "\n-----END " + kind + "PRIVATE KEY-----";

const SHAPES: ReadonlyArray<readonly [string, string, string]> = [
  ["stripe secret live", "sk" + "_live_" + FILL.slice(0, 24), "sk_live_***"],
  ["stripe restricted live", "rk" + "_live_" + FILL.slice(0, 24), "rk_live_***"],
  ["stripe secret test", "sk" + "_test_" + FILL.slice(0, 24), "sk_test_***"],
  ["gitlab", "gl" + "pat-" + FILL.slice(0, 20), "glpat-***"],
  ["npm", "np" + "m_" + (FILL + "XYZ12").slice(0, 36), "npm_***"],
  ["google oauth", "GOC" + "SPX-" + FILL.slice(0, 28), "GOCSPX-***"],
  [
    "jwt",
    "ey" + "J" + "hbGciFake0Example" + "." + "ey" + "J" + "zdWIExample1Fake" + "." + "c2ln" + "NotReal2Sig_value",
    "eyJ***",
  ],
  ["pem rsa", pem("RSA "), "-----BEGIN RSA PRIVATE KEY-----***-----END RSA PRIVATE KEY-----"],
  ["pem plain", pem(""), "-----BEGIN PRIVATE KEY-----***-----END PRIVATE KEY-----"],
  ["entra", "Abc" + "8" + "Q" + "~" + FILL.slice(0, 34), "***"],
  ["bare bearer", "Bear" + "er " + "abc" + "123" + "Example.value", "Bearer ***"],
  ["google api key", "AI" + "za" + FILL.slice(0, 33), "AIza***"],
  ["hugging face", "h" + "f_" + FILL.slice(0, 30), "hf_***"],
  ["groq", "gs" + "k_" + FILL.slice(0, 30), "gsk_***"],
  ["xai", "xa" + "i-" + FILL.slice(0, 30), "xai-***"],
];

describe("the step detail's mask takes out the keys people paste", () => {
  for (const [name, value, masked] of SHAPES) {
    it(`masks ${name}, and masking it again changes nothing`, () => {
      const out = maskDetailText(`here it is: ${value} and that was all`);
      expect(out).toBe(`here it is: ${masked} and that was all`);
      for (const piece of ["FakeExample", "MIIFakeBody", "hbGciFake0", "abc123Example"]) {
        expect(out).not.toContain(piece);
      }
      expect(maskDetailText(out)).toBe(out);
    });
  }

  it("masks a PEM block cut before its END line", () => {
    const out = maskDetailText(pem("RSA ").slice(0, 90) + " ");
    expect(out.startsWith("-----BEGIN RSA PRIVATE KEY-----***")).toBe(true);
    expect(out).not.toContain("MIIFakeBody");
  });
});

describe("ordinary words close to a key are left alone", () => {
  for (const text of [
    "run npm_install first, then npm_ci",
    "set the mode to sk_live or sk_test in the settings",
    "the sk_live_ prefix is for live mode",
    "a JWT is three parts split by dots, the first starts with eyJ",
    "decode the JWT header eyJhbGci to read it",
    "GOCSPX is the prefix Google uses",
    "glpat- tokens expire",
    "-----BEGIN CERTIFICATE-----\nMIIBpublicpart\n-----END CERTIFICATE-----",
    "-----BEGIN PUBLIC KEY-----\nMIIBpublicpart\n-----END PUBLIC KEY-----",
    "a path like ~/notes/8Q~draft stays",
    "Bearer authentication failed",
    "the version is 1.2.3-rc.4 and the build is 20261118",
  ]) {
    it(JSON.stringify(text), () => {
      expect(maskDetailText(text)).toBe(text);
    });
  }

  it("an Entra shape one character too long or four too short stays", () => {
    const entra = "Abc" + "8" + "Q" + "~" + FILL.slice(0, 34);
    expect(maskDetailText(entra)).toBe("***");
    expect(maskDetailText(entra + "Q")).toBe(entra + "Q");
    expect(maskDetailText(entra.slice(0, -4))).toBe(entra.slice(0, -4));
  });
});
