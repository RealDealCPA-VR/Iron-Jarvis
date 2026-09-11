// Extends vitest's `expect` with @testing-library/jest-dom matchers
// (toBeInTheDocument, etc.) for the render tests.
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";

// v1.250.0 (S-04): useApi seeds from the last answer each path gave, which is
// module state. Clear it between tests so no suite inherits another's payload
// (a test asserting a first-visit placeholder must see one).
import { __resetApiCache } from "@/lib/apiCache";

afterEach(() => {
  __resetApiCache();
});
