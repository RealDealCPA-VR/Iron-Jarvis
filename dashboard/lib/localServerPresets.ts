/**
 * LM Studio and llama.cpp are one press (v1.355.0, G72).
 *
 * Neither was detected and no card named them: a user with LM Studio running
 * had to know to add a custom endpoint, type `localhost:1234` and pick a
 * server type. These are the two presets the endpoint forms offer as chips —
 * Settings › Connections' "Add an endpoint" form and the first-run key door's
 * "Model servers on your network" — each filling the address and server type and then
 * pressing the form's EXISTING "Fetch available models"
 * (components/connections/EndpointModelPicker), so the server's own models are
 * listed to pick. Nothing is probed until the chip is pressed, and nothing is
 * saved until the user saves, exactly like typing the address by hand.
 *
 * The addresses are each server's documented default: LM Studio's local
 * server listens on 1234, llama.cpp's `llama-server` on 8080; both speak the
 * OpenAI-compatible API under `/v1` (`/v1/models` lists what is loaded).
 *
 * Dependency-free (no lib/api import — ~70 test files mock it wholesale).
 */

export interface LocalServerPreset {
  id: "lmstudio" | "llamacpp";
  /** The chip's words. */
  label: string;
  /** The endpoint's name in pickers when the form has a name box left empty. */
  name: string;
  /** The server's documented default address. */
  baseUrl: string;
  /** Both speak the OpenAI-compatible API. */
  protocol: "openai";
}

export const LOCAL_SERVER_PRESETS: readonly LocalServerPreset[] = [
  {
    id: "lmstudio",
    label: "LM Studio on this PC",
    name: "LM Studio",
    baseUrl: "http://localhost:1234/v1",
    protocol: "openai",
  },
  {
    id: "llamacpp",
    label: "llama.cpp",
    name: "llama.cpp",
    baseUrl: "http://localhost:8080/v1",
    protocol: "openai",
  },
];

/** The picker's own "Fetch available models" button (EndpointModelPicker's
 *  testid) — pressed, never re-implemented, after a chip fills the form. */
export const FETCH_MODELS_SELECTOR = '[data-testid="endpoint-fetch-models"]';
