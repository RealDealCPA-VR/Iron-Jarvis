/**
 * THE DEMO, IN ONE SET OF WORDS (v1.357.0, product readiness G76).
 *
 * The built-in stand-in that answers when no AI model is chosen (provider id
 * "mock") had six names before this file: "Mock answer", "the offline mock",
 * "Simulated mode", "the offline demo", "the built-in offline model", "demo
 * mode (output simulated)". A first-time user reading two of them could not
 * tell they were the same thing — and this is the one place where knowing it
 * matters most, because nothing it says is real.
 *
 * VOCABULARY.md's row: the thing is the **demo model**; what it says is "a
 * scripted demo, not real answers"; under one reply it wrote, "Demo answer.
 * No AI model ran." Every surface that says any of that takes the words from
 * here. The provider id "mock" stays on the wire (posted values, titles that
 * record what ran); `lib/onboarding.providerDisplay("mock")` is the label.
 *
 * Dependency-free on purpose: the receipt, the bell, the banners and the
 * mission cards all import it, and ~70 tests mock `lib/api` wholesale.
 * Plain sentences, no dash asides (the calm chat copy rule).
 */

/** The thing's name, mid-sentence ("Running on the demo model"). */
export const DEMO_MODEL = "demo model";

/** The thing's name as a label: the model menu, a settings option, a card. */
export const DEMO_MODEL_LABEL = "Demo model (scripted)";

/** What its replies ARE, mid-sentence ("replies are a scripted demo, not
 *  real answers"). */
export const DEMO_REPLIES = "a scripted demo, not real answers";

/** The state's short label (the chip, the strip under the title bar). */
export const DEMO_STATE = "Demo replies";

/** Under ONE reply the demo model wrote: the chat receipt, a mission's
 *  receipt and a teammate card that answered. */
export const DEMO_ANSWER = "Demo answer. No AI model ran.";

/** A run on the demo model that has not answered (a teammate still working,
 *  a mission that failed on it). */
export const DEMO_RUNNING = "On the demo model. No AI model ran.";

/** The chip's hover: what "demo model" on a row means. */
export const DEMO_CHIP_TITLE = `Ran on the built-in ${DEMO_MODEL}: ${DEMO_REPLIES}.`;
