// Extends vitest's `expect` with @testing-library/jest-dom matchers
// (toBeInTheDocument, etc.) for the render tests.
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";

// v1.250.0 (S-04): useApi seeds from the last answer each path gave, which is
// module state. Clear it between tests so no suite inherits another's payload
// (a test asserting a first-visit placeholder must see one).
import { __resetApiCache } from "@/lib/apiCache";
// v1.384.0: Build's IJ harness keeps where the user was (the session, its
// running reply, the scroll) as module state too — the same rule.
import { resetTurnCache } from "@/lib/turnCache";
// v1.406.0 part 7: the Code view's split (two sessions side by side) is this
// window's module state too.
import { __resetSplitMemory } from "@/lib/codeSplit";
// v1.433.0: what this window knew of its device settings, and who listens.
// The import-free state module, never lib/deviceSettings itself: a module
// imported here is cached before a test file's vi.mock("@/lib/api") applies.
import { __resetDeviceSettings } from "@/lib/deviceSettingsState";

afterEach(() => {
  __resetApiCache();
  resetTurnCache();
  __resetSplitMemory();
  __resetDeviceSettings();
});
