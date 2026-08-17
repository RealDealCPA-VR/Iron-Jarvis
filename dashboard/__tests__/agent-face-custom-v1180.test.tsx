import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * v1.180.0 — the face becomes CHOOSABLE (shape / eyes / colour).
 *
 * The face has been derived from the agent's NAME since v1.171.0. That is a
 * good default and was a hard ceiling; the user asked to pick. What can fail
 * silently here, and is therefore pinned:
 *
 *  - PER-FIELD PRECEDENCE. A set field must win and an UNSET one must still
 *    derive. An implementation that fills the unset fields in with the seed at
 *    write time looks identical today and pins two fields the user never chose;
 *    one that ignores the override renders the old face and the picker lies.
 *    Every assertion here is guarded by a meta-check that the chosen value and
 *    the derived value really differ, so none of them can go vacuously green.
 *  - PORTRAITS STILL WIN. The v1.171.0 order (portrait > geometry) is
 *    unchanged; a chosen face must not start beating an uploaded picture, or an
 *    upload would look like it failed.
 *  - UNKNOWN VALUES DEGRADE, per field. An older dashboard against a newer
 *    daemon (or a hand-edited record) must draw the derived face for that one
 *    field, never crash and never invent geometry.
 *  - THE PICKER WRITES WHAT IT SHOWS. The preview is the real AgentFace, the
 *    Apply sends exactly the pinned fields with nulls for the rest, and Reset
 *    sends DELETE and returns the preview to the derived face.
 *  - AN OLDER DAEMON DEGRADES: no /agents/faces route means no picker (a
 *    control that could only fail) — but every face still renders, derived.
 */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    calls: [] as { method: string; path: string; body?: unknown }[],
    responses: {} as Record<string, unknown>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  get: (path: string) => {
    api.calls.push({ method: "GET", path });
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    }
    return Promise.resolve(r);
  },
  put: (path: string, body?: unknown) => {
    api.calls.push({ method: "PUT", path, body });
    return Promise.resolve({});
  },
  post: (path: string, body?: unknown) => {
    api.calls.push({ method: "POST", path, body });
    return Promise.resolve({});
  },
  patch: (path: string, body?: unknown) => {
    api.calls.push({ method: "PATCH", path, body });
    return Promise.resolve({});
  },
  del: (path: string) => {
    api.calls.push({ method: "DELETE", path });
    return Promise.resolve({});
  },
}));

import AgentFace, {
  EYE_STYLES,
  FACE_COLORS,
  FACE_SHAPES,
  faceColor,
  faceEyes,
  faceShape,
  resolveFace,
} from "@/components/agents/AgentFace";
import { SetupCard, faceFor, type DynamicAgentFull } from "@/components/agents/SetupCard";

afterEach(() => {
  cleanup();
  api.calls = [];
  api.responses = {};
  try {
    localStorage.clear();
  } catch {
    /* jsdom always has it */
  }
});

/* ------------------------------------------------------------------ helpers */

/** A member of `options` that is NOT `derived` — every "the override won"
 *  assertion picks its value this way, so it can never accidentally assert the
 *  seed's own value and pass while the override is ignored. */
function other<T extends string>(options: readonly T[], derived: T): T {
  const found = options.find((o) => o !== derived);
  expect(found).toBeDefined();
  return found as T;
}

const NAME = "remy";
const DERIVED_SHAPE = faceShape(NAME);
const DERIVED_COLOR = faceColor(NAME);
const DERIVED_EYES = faceEyes(NAME);
const PICK_SHAPE = other(FACE_SHAPES, DERIVED_SHAPE);
const PICK_COLOR = other(FACE_COLORS, DERIVED_COLOR);
const PICK_EYES = other(EYE_STYLES, DERIVED_EYES);

function faceEl(label = NAME): HTMLElement {
  return screen.getByLabelText(label);
}

/* ------------------------------------------------------- AgentFace: resolve */

describe("AgentFace — an override replaces the seed, field by field", () => {
  it("with no override at all the face is exactly the derived one (the v1.171.0 behaviour)", () => {
    render(<AgentFace name={NAME} />);
    const f = faceEl();
    expect(f.getAttribute("data-face-shape")).toBe(DERIVED_SHAPE);
    expect(f.getAttribute("data-face-color")).toBe(DERIVED_COLOR);
    expect(f.getAttribute("data-face-eyes")).toBe(DERIVED_EYES);
  });

  it("a full override changes the rendered shape, colour AND eyes", () => {
    render(
      <AgentFace
        name={NAME}
        face={{ shape: PICK_SHAPE, color: PICK_COLOR, eyes: PICK_EYES }}
      />,
    );
    const f = faceEl();
    expect(f.getAttribute("data-face-shape")).toBe(PICK_SHAPE);
    expect(f.getAttribute("data-face-color")).toBe(PICK_COLOR);
    expect(f.getAttribute("data-face-eyes")).toBe(PICK_EYES);
    // Not just attributes — the drawn body really carries the chosen colour.
    expect(f.querySelector("g")?.getAttribute("fill")).toBe(PICK_COLOR);
  });

  it("an UNSET field still derives from the name — one pinned field pins only itself", () => {
    render(<AgentFace name={NAME} face={{ shape: PICK_SHAPE }} />);
    const f = faceEl();
    expect(f.getAttribute("data-face-shape")).toBe(PICK_SHAPE);
    expect(f.getAttribute("data-face-color")).toBe(DERIVED_COLOR);
    expect(f.getAttribute("data-face-eyes")).toBe(DERIVED_EYES);
    // ...and the same holds for the other two, each on its own.
    cleanup();
    render(<AgentFace name={NAME} face={{ eyes: PICK_EYES }} />);
    expect(faceEl().getAttribute("data-face-eyes")).toBe(PICK_EYES);
    expect(faceEl().getAttribute("data-face-shape")).toBe(DERIVED_SHAPE);
    cleanup();
    render(<AgentFace name={NAME} face={{ color: PICK_COLOR, shape: null }} />);
    expect(faceEl().getAttribute("data-face-color")).toBe(PICK_COLOR);
    expect(faceEl().getAttribute("data-face-shape")).toBe(DERIVED_SHAPE);
  });

  it("a value this build cannot draw degrades to derived — that field only", () => {
    // The older-dashboard / newer-daemon case, and hand-edited records.
    render(
      <AgentFace name={NAME} face={{ shape: "octagon", eyes: PICK_EYES }} />,
    );
    expect(faceEl().getAttribute("data-face-shape")).toBe(DERIVED_SHAPE);
    expect(faceEl().getAttribute("data-face-eyes")).toBe(PICK_EYES);
  });

  it("every eye style really draws eyes, and error still shows X-X in ALL of them", () => {
    // A style that rendered nothing would be a face with no eyes; a style that
    // swallowed the error eyes would make a failed agent unreadable.
    for (const style of EYE_STYLES) {
      const { unmount } = render(<AgentFace name={NAME} face={{ eyes: style }} />);
      expect(screen.getAllByTestId("face-eye").length).toBeGreaterThan(0);
      unmount();
      const err = render(
        <AgentFace name={NAME} mood="error" face={{ eyes: style }} />,
      );
      expect(screen.getByTestId("face-eyes-error")).toBeInTheDocument();
      err.unmount();
    }
  });

  it("a stored PORTRAIT still wins over a chosen face", () => {
    render(
      <AgentFace
        name={NAME}
        avatarUrl="/agents/remy/avatar"
        face={{ shape: PICK_SHAPE, color: PICK_COLOR }}
      />,
    );
    expect(document.querySelector('img[src="/agents/remy/avatar"]')).not.toBeNull();
    expect(screen.queryByTestId("agent-face")).toBeNull();
  });

  it("resolveFace is the ONE precedence rule, and strips the participant prefix", () => {
    // Surfaces hold "dynamic:remy"; the face must resolve as "remy" so a seat
    // and a roster row wear the same face (the v1.171.0 cross-surface rule).
    expect(resolveFace("dynamic:remy")).toEqual({
      shape: DERIVED_SHAPE,
      color: DERIVED_COLOR,
      eyes: DERIVED_EYES,
    });
    expect(resolveFace("dynamic:remy", { color: PICK_COLOR }).color).toBe(PICK_COLOR);
  });
});

/* ----------------------------------------------------------------- faceFor */

describe("faceFor — a LOADED map is authoritative", () => {
  it("uses the row's own field only until the map lands", () => {
    // Before the fetch: the row's field from GET /agents.
    expect(faceFor(null, NAME, { shape: PICK_SHAPE })).toEqual({ shape: PICK_SHAPE });
    // After: the map wins, INCLUDING when it has no entry — that is what makes
    // a Reset visible immediately instead of showing a face no longer stored.
    expect(faceFor({}, NAME, { shape: PICK_SHAPE })).toBeNull();
    expect(faceFor({ [NAME]: { eyes: PICK_EYES } }, NAME)).toEqual({ eyes: PICK_EYES });
  });
});

/* --------------------------------------------------------------- SetupCard */

const AGENT: DynamicAgentFull = {
  name: NAME,
  description: "the accountant",
  provider: "",
  model: "",
  system_prompt: "You are Remy.",
  tools: [],
  effective_tools: ["read_file"],
  base_type: "builder",
  avatar: null,
  face: null,
};

function renderCard(over: Partial<React.ComponentProps<typeof SetupCard>> = {}) {
  return render(
    <SetupCard
      builtin={["builder"]}
      dynamic={[AGENT]}
      remotes={[]}
      models={[]}
      onAgentsChanged={() => {}}
      onRemotesChanged={() => {}}
      {...over}
    />,
  );
}

/** Open the collapsed card and wait for the faces fetch to settle. */
async function openCard(over?: Partial<React.ComponentProps<typeof SetupCard>>) {
  renderCard(over);
  fireEvent.click(screen.getByText("Set up agents"));
  await waitFor(() =>
    expect(api.calls.some((c) => c.path === "/agents/faces")).toBe(true),
  );
}

/** Open the persona editor on the dynamic row (the gear/pencil). */
function openRowEditor() {
  fireEvent.click(screen.getByTitle(`Edit the persona of "${NAME}"`));
}

describe("SetupCard — the picker", () => {
  it("draws each row's face from the stored override", async () => {
    api.responses["/agents/faces"] = {
      faces: { [NAME]: { shape: PICK_SHAPE, eyes: PICK_EYES } },
    };
    await openCard();
    await waitFor(() =>
      expect(faceEl().getAttribute("data-face-shape")).toBe(PICK_SHAPE),
    );
    // The unset colour still derives — on the real row, not just in isolation.
    expect(faceEl().getAttribute("data-face-color")).toBe(DERIVED_COLOR);
    expect(faceEl().getAttribute("data-face-eyes")).toBe(PICK_EYES);
  });

  it("choosing shape, eyes and colour previews live, then Apply PUTs exactly those fields", async () => {
    api.responses["/agents/faces"] = { faces: {} };
    await openCard();
    openRowEditor();
    const picker = screen.getByTestId(`face-picker-${NAME}`);
    const preview = () => screen.getByLabelText(`${NAME} — the face as chosen`);
    // Opens on the truth as stored: nothing pinned, everything derived.
    expect(preview().getAttribute("data-face-shape")).toBe(DERIVED_SHAPE);
    expect(within(picker).getByText("drawn from the name")).toBeInTheDocument();

    fireEvent.click(within(picker).getByLabelText(`Shape ${PICK_SHAPE}`));
    fireEvent.click(within(picker).getByLabelText(`Eyes ${PICK_EYES}`));
    fireEvent.click(within(picker).getByLabelText(`Colour ${PICK_COLOR}`));
    // LIVE, before anything is written — nothing has been sent yet.
    expect(preview().getAttribute("data-face-shape")).toBe(PICK_SHAPE);
    expect(preview().getAttribute("data-face-eyes")).toBe(PICK_EYES);
    expect(preview().getAttribute("data-face-color")).toBe(PICK_COLOR);
    expect(api.calls.some((c) => c.method === "PUT")).toBe(false);

    fireEvent.click(within(picker).getByText("Apply face"));
    // Wait on the note set at the END of the handler — never on the request
    // itself, which lands before the state it implies (v1.177.1 / v1.178.0).
    expect(await screen.findByText("Face saved.")).toBeInTheDocument();
    const write = api.calls.find((c) => c.method === "PUT");
    expect(write?.path).toBe(`/agents/${NAME}/face`);
    expect(write?.body).toEqual({
      shape: PICK_SHAPE,
      color: PICK_COLOR,
      eyes: PICK_EYES,
    });
    // And the card refetched the stored faces, so every row updates at once.
    expect(api.calls.filter((c) => c.path === "/agents/faces").length).toBe(2);
  });

  it("a field left on “from the name” is sent as null — it must keep deriving", async () => {
    api.responses["/agents/faces"] = { faces: {} };
    await openCard();
    openRowEditor();
    const picker = screen.getByTestId(`face-picker-${NAME}`);
    fireEvent.click(within(picker).getByLabelText(`Eyes ${PICK_EYES}`));
    fireEvent.click(within(picker).getByText("Apply face"));
    expect(await screen.findByText("Face saved.")).toBeInTheDocument();
    expect(api.calls.find((c) => c.method === "PUT")?.body).toEqual({
      shape: null,
      color: null,
      eyes: PICK_EYES,
    });
  });

  it("Reset DELETEs and the preview returns to the derived face", async () => {
    api.responses["/agents/faces"] = {
      faces: { [NAME]: { shape: PICK_SHAPE, color: PICK_COLOR, eyes: PICK_EYES } },
    };
    await openCard();
    openRowEditor();
    const picker = screen.getByTestId(`face-picker-${NAME}`);
    const preview = () => screen.getByLabelText(`${NAME} — the face as chosen`);
    await waitFor(() =>
      expect(preview().getAttribute("data-face-shape")).toBe(PICK_SHAPE),
    );
    // The stored choice is reflected as CHECKED, not merely drawn.
    expect(
      within(picker).getByLabelText(`Colour ${PICK_COLOR}`).getAttribute("aria-checked"),
    ).toBe("true");

    api.responses["/agents/faces"] = { faces: {} }; // what the refetch returns
    fireEvent.click(within(picker).getByText("Reset"));
    expect(await screen.findByText("Back to the face this name draws.")).toBeInTheDocument();
    expect(api.calls.some((c) => c.method === "DELETE" && c.path === `/agents/${NAME}/face`)).toBe(
      true,
    );
    expect(preview().getAttribute("data-face-shape")).toBe(DERIVED_SHAPE);
    expect(preview().getAttribute("data-face-color")).toBe(DERIVED_COLOR);
    expect(preview().getAttribute("data-face-eyes")).toBe(DERIVED_EYES);
  });

  it("un-pinning every field and pressing Apply resets instead of writing an empty override", async () => {
    api.responses["/agents/faces"] = { faces: { [NAME]: { shape: PICK_SHAPE } } };
    await openCard();
    openRowEditor();
    const picker = screen.getByTestId(`face-picker-${NAME}`);
    await waitFor(() =>
      expect(
        within(picker).getByLabelText(`Shape ${PICK_SHAPE}`).getAttribute("aria-checked"),
      ).toBe("true"),
    );
    fireEvent.click(within(picker).getByLabelText("Shape from the name"));
    fireEvent.click(within(picker).getByText("Apply face"));
    expect(await screen.findByText("Back to the face this name draws.")).toBeInTheDocument();
    expect(api.calls.some((c) => c.method === "PUT")).toBe(false);
    expect(api.calls.some((c) => c.method === "DELETE")).toBe(true);
  });

  it("a built-in agent's face is customizable too — same picker, same store", async () => {
    api.responses["/agents/faces"] = { faces: {} };
    await openCard();
    fireEvent.click(screen.getByTitle("Customize builder's face"));
    const picker = screen.getByTestId("face-picker-builder");
    const pick = other(FACE_SHAPES, faceShape("builder"));
    fireEvent.click(within(picker).getByLabelText(`Shape ${pick}`));
    fireEvent.click(within(picker).getByText("Apply face"));
    expect(await screen.findByText("Face saved.")).toBeInTheDocument();
    const write = api.calls.find((c) => c.method === "PUT");
    expect(write?.path).toBe("/agents/builder/face");
    expect((write?.body as { shape?: string })?.shape).toBe(pick);
  });

  it("a daemon with no face routes DEGRADES: no picker, but every face still draws", async () => {
    // /agents/faces is left unmocked → the api mock rejects it as a 404.
    await openCard();
    await waitFor(() =>
      expect(screen.queryByTitle("Customize builder's face")).toBeNull(),
    );
    openRowEditor();
    expect(screen.queryByTestId(`face-picker-${NAME}`)).toBeNull();
    // The identity survives: the row still wears its derived face.
    const row = screen.getAllByLabelText(NAME)[0];
    expect(row.getAttribute("data-face-shape")).toBe(DERIVED_SHAPE);
  });

  it("switching built-in chips does NOT hand the next agent the previous draft", async () => {
    // The built-in strip reuses ONE picker for whichever chip is open. Two
    // agents with no override both arrive as `face: null`, so a re-sync keyed
    // only on `face` never fires and the second picker opened PRE-PINNED with
    // the first agent's unapplied choice — one Apply from writing builder's
    // shape onto planner. (Reviewer defect, v1.180.0.)
    api.responses["/agents/faces"] = { faces: {} };
    await openCard({ builtin: ["builder", "planner"] });
    fireEvent.click(screen.getByTitle("Customize builder's face"));
    const first = screen.getByTestId("face-picker-builder");
    const pick = other(FACE_SHAPES, faceShape("builder"));
    fireEvent.click(within(first).getByLabelText(`Shape ${pick}`));
    expect(within(first).getByText("chosen")).toBeInTheDocument();

    fireEvent.click(screen.getByTitle("Customize planner's face"));
    const second = screen.getByTestId("face-picker-planner");
    expect(within(second).getByText("drawn from the name")).toBeInTheDocument();
    expect(
      within(second).getByLabelText("Shape from the name").getAttribute("aria-checked"),
    ).toBe("true");
    expect(
      screen.getByLabelText("planner — the face as chosen").getAttribute("data-face-shape"),
    ).toBe(faceShape("planner"));
  });

  it("a FAILED faces refetch keeps the last confirmed truth — it never claims 'no overrides'", async () => {
    // A LOADED map is authoritative, so writing `{}` on a timeout would redraw
    // every customized agent as derived and reset the open picker to "drawn
    // from the name" directly under "Face saved." A 500 confirms nothing.
    // (Reviewer defect, v1.180.0.)
    api.responses["/agents/faces"] = { faces: { [NAME]: { shape: PICK_SHAPE } } };
    await openCard();
    await waitFor(() =>
      expect(faceEl().getAttribute("data-face-shape")).toBe(PICK_SHAPE),
    );
    const api_ = await import("@/lib/api");
    const spy = vi
      .spyOn(api_, "get")
      .mockRejectedValueOnce(new api.FakeApiError("boom", 500));
    openRowEditor();
    const picker = screen.getByTestId(`face-picker-${NAME}`);
    fireEvent.click(within(picker).getByText("Apply face"));
    expect(await screen.findByText("Face saved.")).toBeInTheDocument();
    // The refetch rejected — the stored face is still drawn, and the picker
    // still says it is chosen. (The open editor renders a second face for the
    // portrait row, hence getAll.)
    expect(
      screen.getAllByLabelText(NAME)[0].getAttribute("data-face-shape"),
    ).toBe(PICK_SHAPE);
    expect(within(picker).getByText("chosen")).toBeInTheDocument();
    spy.mockRestore();
  });

  it("an error from the daemon is shown verbatim, and nothing is claimed saved", async () => {
    api.responses["/agents/faces"] = { faces: {} };
    await openCard();
    openRowEditor();
    const picker = screen.getByTestId(`face-picker-${NAME}`);
    const api_ = await import("@/lib/api");
    vi.spyOn(api_, "put").mockRejectedValueOnce(
      new api.FakeApiError("shape must be one of: circle, … — got 'octagon'", 400),
    );
    fireEvent.click(within(picker).getByLabelText(`Shape ${PICK_SHAPE}`));
    fireEvent.click(within(picker).getByText("Apply face"));
    expect(
      await screen.findByText(/shape must be one of/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Face saved.")).toBeNull();
  });
});
