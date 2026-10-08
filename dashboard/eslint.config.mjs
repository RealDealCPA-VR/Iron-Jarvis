// Calm UI redesign (AUDIT Q12): Next's default lint rules. Enforced on the
// files the redesign touches (scripts/lint_redesign.py at the repo root);
// the rest of the dashboard predates any linter and is not gated yet.
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

export default [
  { ignores: ["node_modules/**", ".next/**", ".next-e2e/**", "__audit_*/**", "__review_*/**", "e2e/.home/**"] },
  ...compat.extends("next/core-web-vitals"),
];
