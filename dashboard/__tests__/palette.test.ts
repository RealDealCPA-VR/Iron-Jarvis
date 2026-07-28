import { describe, expect, it } from "vitest";

import { scorePalette, type PaletteItem } from "../lib/palette";

/**
 * THE NEED: "I know the thing exists — I just don't know which page it's on."
 * Every test below is one sentence about what the user sees at the top of the
 * palette. Synthetic items only: the real nav list will churn constantly, and
 * these rules must not churn with it.
 */

/** Terse item builder — most tests care about two fields and nothing else. */
function item(p: Partial<PaletteItem> & { id: string }): PaletteItem {
  return { kind: "page", label: p.id, ...p };
}

const ids = (items: ReturnType<typeof scorePalette>) => items.map((i) => i.id);

describe("scorePalette — what the ranking chain puts first", () => {
  it("test_a_label_that_starts_with_the_query_beats_one_that_merely_contains_the_word", () => {
    const items = [
      item({ id: "local-fleet", label: "Local Fleet" }),
      item({ id: "fleet-monitor", label: "Fleet Monitor" }),
    ];
    // Typed "fleet" -> the page actually NAMED Fleet-something leads, even
    // though it was listed second.
    expect(ids(scorePalette("fleet", items))).toEqual([
      "fleet-monitor",
      "local-fleet",
    ]);
  });

  it("test_a_word_inside_the_label_beats_a_hit_that_only_exists_in_an_alias", () => {
    const items = [
      item({ id: "billing", label: "Billing", aliases: ["usage"] }),
      item({ id: "token-usage", label: "Token Usage" }),
    ];
    // What the thing is CALLED outranks what it also answers to.
    expect(ids(scorePalette("usage", items))).toEqual(["token-usage", "billing"]);
  });

  it("test_an_alias_hit_beats_the_query_being_buried_mid_word_somewhere", () => {
    const items = [
      item({ id: "prerename", label: "Prerename Widget" }),
      item({ id: "endpoints", label: "Endpoint Manager", aliases: ["rename"] }),
    ];
    expect(ids(scorePalette("rename", items))).toEqual(["endpoints", "prerename"]);
  });

  it("test_a_buried_substring_in_the_label_still_beats_a_hit_that_is_only_in_the_blurb", () => {
    const items = [
      item({ id: "blurb-only", label: "Endpoints", blurb: "rename an endpoint" }),
      item({ id: "prerename", label: "Prerename Widget" }),
    ];
    // The blurb is the weakest evidence we have: it is prose ABOUT the item.
    expect(ids(scorePalette("rename", items))).toEqual(["prerename", "blurb-only"]);
  });

  it("test_the_whole_chain_holds_at_once_from_label_prefix_down_to_blurb", () => {
    const items = [
      item({ id: "e-blurb", label: "Settings", blurb: "rename things here" }),
      item({ id: "d-substring", label: "Prerename Widget" }),
      item({ id: "c-alias", label: "Endpoint Manager", aliases: ["rename"] }),
      item({ id: "b-label-word", label: "Bulk Rename" }),
      item({ id: "a-label-prefix", label: "Rename Endpoint" }),
    ];
    expect(ids(scorePalette("rename", items))).toEqual([
      "a-label-prefix",
      "b-label-word",
      "c-alias",
      "d-substring",
      "e-blurb",
    ]);
  });

  it("test_the_scores_come_back_strictly_descending_so_the_ui_can_trust_the_order", () => {
    const items = [
      item({ id: "a", label: "Rename Endpoint" }),
      item({ id: "b", label: "Bulk Rename" }),
      item({ id: "c", label: "Endpoint Manager", aliases: ["rename"] }),
    ];
    const scores = scorePalette("rename", items).map((i) => i.score);
    expect(scores).toEqual([...scores].sort((x, y) => y - x));
    expect(new Set(scores).size).toBe(3);
  });
});

describe("scorePalette — aliases exist so the user can use their own word", () => {
  it("test_rename_finds_an_item_whose_label_and_blurb_never_say_rename", () => {
    const items = [
      item({
        id: "seeded-endpoint",
        kind: "action",
        label: "Edit Seeded Endpoint",
        blurb: "Change the address of a provider endpoint",
        aliases: ["rename", "retitle"],
      }),
    ];
    const hits = scorePalette("rename", items);
    expect(ids(hits)).toEqual(["seeded-endpoint"]);
    expect(hits[0].score).toBeGreaterThan(0);
  });

  it("test_an_alias_matches_on_a_word_boundary_not_just_at_its_start", () => {
    const items = [item({ id: "x", label: "Provider Setup", aliases: ["edit endpoint"] })];
    expect(ids(scorePalette("endpoint", items))).toEqual(["x"]);
  });

  it("test_a_hit_in_any_alias_counts_not_only_the_first_one", () => {
    const items = [item({ id: "x", label: "Provider Setup", aliases: ["abc", "rename"] })];
    expect(ids(scorePalette("rename", items))).toEqual(["x"]);
  });
});

describe("scorePalette — a word can open a word anywhere, not just at the front", () => {
  it("test_a_later_occurrence_that_opens_a_word_outranks_one_that_stays_buried", () => {
    // "Prechat chat": the FIRST "chat" is buried inside "Prechat", but the
    // second one is a word the user actually named. Stopping the scan at the
    // first occurrence would demote "Rename chat" to a buried substring and
    // bury half the palette's two-word labels with it.
    const items = [
      item({ id: "late", label: "Prechat chat" }),
      item({ id: "buried", label: "Prechat widget" }),
    ];
    const hits = scorePalette("chat", items);
    expect(ids(hits)).toEqual(["late", "buried"]);
    expect(hits[0].score).toBe(800);
    expect(hits[1].score).toBe(300);
  });

  it("test_the_same_full_scan_applies_inside_an_alias", () => {
    const items = [
      item({ id: "late", label: "Zed", aliases: ["prechat chat"] }),
      item({ id: "buried", label: "Zed", aliases: ["prechat"] }),
    ];
    const hits = scorePalette("chat", items);
    expect(ids(hits)).toEqual(["late", "buried"]);
    expect(hits[0].score).toBe(600);
    expect(hits[1].score).toBe(300);
  });
});

describe("scorePalette — hostile input from a box the user types into freely", () => {
  const items = [
    item({ id: "cpp", label: "C++ Tools" }),
    item({ id: "dot", label: "a.b config" }),
    item({ id: "usage", label: "Usage", blurb: "tokens" }),
  ];

  it("test_regex_metacharacters_in_the_query_are_searched_literally_and_never_throw", () => {
    // The query is matched with indexOf, NOT compiled into a RegExp: if it
    // were, every one of these would throw mid-keystroke and take the whole
    // search box down.
    for (const q of ["(", "\\", "[a-z]", "*", "$^", "|", "a)b", "+", "?"]) {
      expect(() => scorePalette(q, items)).not.toThrow();
    }
    expect(ids(scorePalette("c++", items))).toEqual(["cpp"]);
    expect(ids(scorePalette("a.b", items))).toEqual(["dot"]);
    expect(scorePalette("what?", items)).toEqual([]);
  });

  it("test_a_punctuation_only_query_matches_nothing_instead_of_everything", () => {
    expect(scorePalette("...", items)).toEqual([]);
  });

  it("test_a_single_character_and_an_absurdly_long_query_are_both_handled", () => {
    expect(ids(scorePalette("u", items))).toEqual(["usage"]);
    expect(scorePalette("u".repeat(5000), items)).toEqual([]);
  });

  it("test_an_item_with_an_empty_label_no_aliases_and_no_blurb_is_skipped_not_crashed_on", () => {
    const degenerate = [item({ id: "hollow", label: "", aliases: [], blurb: undefined })];
    expect(scorePalette("hollow", degenerate)).toEqual([]);
    expect(scorePalette("", degenerate)).toEqual([]);
  });
});

describe("scorePalette — every word must land (AND), or two-word queries flood", () => {
  const items = [
    item({ id: "endpoints-page", label: "Endpoints", blurb: "manage each endpoint" }),
    item({ id: "rename-file", kind: "action", label: "Rename File" }),
    item({
      id: "rename-endpoint",
      kind: "action",
      label: "Rename Endpoint",
      blurb: "give a seeded endpoint a new name",
    }),
  ];

  it("test_rename_endpoint_does_not_drag_in_a_page_that_only_knows_the_word_endpoint", () => {
    expect(ids(scorePalette("rename endpoint", items))).not.toContain("endpoints-page");
  });

  it("test_rename_endpoint_does_not_drag_in_an_action_that_only_knows_the_word_rename", () => {
    expect(ids(scorePalette("rename endpoint", items))).not.toContain("rename-file");
  });

  it("test_the_one_item_that_matches_both_words_is_the_only_and_therefore_top_result", () => {
    expect(ids(scorePalette("rename endpoint", items))).toEqual(["rename-endpoint"]);
  });

  it("test_each_word_matched_adds_to_the_score_so_matching_both_well_wins", () => {
    const two = [
      item({ id: "weak", label: "Endpoint Tools", blurb: "rename support" }),
      item({ id: "strong", label: "Rename Endpoint" }),
    ];
    expect(ids(scorePalette("rename endpoint", two))).toEqual(["strong", "weak"]);
  });

  it("test_extra_whitespace_between_words_is_not_treated_as_an_extra_word", () => {
    // Otherwise a stray double space would AND in an empty word and kill every
    // result while the user is mid-type.
    expect(ids(scorePalette("  rename   endpoint ", items))).toEqual(["rename-endpoint"]);
  });
});

describe("scorePalette — ties fall back to what the user most likely meant", () => {
  it("test_kind_breaks_a_tie_page_then_action_then_skill_then_project_then_thread", () => {
    const items: PaletteItem[] = [
      { id: "t", kind: "thread", label: "Alpha" },
      { id: "p", kind: "project", label: "Alpha" },
      { id: "s", kind: "skill", label: "Alpha" },
      { id: "a", kind: "action", label: "Alpha" },
      { id: "g", kind: "page", label: "Alpha" },
    ];
    expect(ids(scorePalette("alpha", items))).toEqual(["g", "a", "s", "p", "t"]);
  });

  it("test_two_identical_items_of_the_same_kind_keep_the_order_they_were_given_in", () => {
    const items = [
      item({ id: "first", label: "Alpha" }),
      item({ id: "second", label: "Alpha" }),
    ];
    expect(ids(scorePalette("alpha", items))).toEqual(["first", "second"]);
    expect(ids(scorePalette("alpha", [...items].reverse()))).toEqual(["second", "first"]);
  });

  it("test_score_outranks_kind_so_a_better_match_is_never_demoted_for_being_a_thread", () => {
    const items: PaletteItem[] = [
      { id: "page", kind: "page", label: "Client Notes", blurb: "alpha" },
      { id: "thread", kind: "thread", label: "Alpha Review" },
    ];
    expect(ids(scorePalette("alpha", items))).toEqual(["thread", "page"]);
  });
});

describe("scorePalette — what must never appear", () => {
  const items = [
    item({ id: "usage", label: "Usage" }),
    item({ id: "skills", label: "Skills", blurb: "browse installed skills" }),
  ];

  it("test_an_empty_query_shows_nothing_rather_than_the_entire_app", () => {
    expect(scorePalette("", items)).toEqual([]);
  });

  it("test_a_whitespace_only_query_also_shows_nothing", () => {
    expect(scorePalette("   \t ", items)).toEqual([]);
  });

  it("test_a_query_nothing_matches_returns_empty_instead_of_a_consolation_list", () => {
    expect(scorePalette("zzzznope", items)).toEqual([]);
  });

  it("test_non_matching_items_are_never_padded_in_to_fill_the_list", () => {
    expect(ids(scorePalette("usage", items))).toEqual(["usage"]);
  });
});

describe("scorePalette — case, limits and other plumbing users still feel", () => {
  it("test_typing_in_any_case_finds_the_item_in_any_case", () => {
    const items = [item({ id: "x", label: "LOCAL Fleet", aliases: ["GPU"] })];
    expect(ids(scorePalette("local", items))).toEqual(["x"]);
    expect(ids(scorePalette("LOCAL", items))).toEqual(["x"]);
    expect(ids(scorePalette("gpu", items))).toEqual(["x"]);
    expect(scorePalette("LoCaL", items)[0].score).toBe(scorePalette("local", items)[0].score);
  });

  it("test_the_dropdown_caps_at_twelve_rows_by_default_and_keeps_the_best_twelve", () => {
    const items = Array.from({ length: 20 }, (_, i) =>
      item({ id: `alpha-${i}`, label: `Alpha ${i}` }),
    );
    const hits = scorePalette("alpha", items);
    expect(hits).toHaveLength(12);
    expect(hits[0].id).toBe("alpha-0");
    expect(hits[11].id).toBe("alpha-11");
  });

  it("test_an_explicit_limit_is_honoured", () => {
    const items = Array.from({ length: 20 }, (_, i) =>
      item({ id: `alpha-${i}`, label: `Alpha ${i}` }),
    );
    expect(scorePalette("alpha", items, 3)).toHaveLength(3);
  });

  it("test_a_limit_of_zero_and_any_negative_limit_both_return_nothing", () => {
    // slice(0, -1) would hand back every match EXCEPT the last one, which is
    // the opposite of "show fewer" and impossible to spot in the UI.
    const items = Array.from({ length: 5 }, (_, i) =>
      item({ id: `alpha-${i}`, label: `Alpha ${i}` }),
    );
    expect(scorePalette("alpha", items, 0)).toEqual([]);
    expect(scorePalette("alpha", items, -1)).toEqual([]);
    expect(scorePalette("alpha", items, -99)).toEqual([]);
  });

  it("test_a_limit_larger_than_the_match_count_returns_only_the_matches", () => {
    const items = [item({ id: "usage", label: "Usage" })];
    expect(scorePalette("usage", items, 50)).toHaveLength(1);
  });

  it("test_an_empty_item_list_is_not_an_error", () => {
    expect(scorePalette("anything", [])).toEqual([]);
  });

  it("test_the_original_item_fields_survive_scoring_so_the_row_can_be_rendered", () => {
    const items = [
      item({ id: "usage", kind: "page", label: "Usage", blurb: "tokens", href: "/usage" }),
    ];
    expect(scorePalette("usage", items)[0]).toMatchObject({
      id: "usage",
      kind: "page",
      label: "Usage",
      blurb: "tokens",
      href: "/usage",
    });
  });

  it("test_scoring_does_not_mutate_or_reorder_the_caller_s_array", () => {
    const items = [
      item({ id: "b", label: "Bulk Rename" }),
      item({ id: "a", label: "Rename Endpoint" }),
    ];
    scorePalette("rename", items);
    expect(items.map((i) => i.id)).toEqual(["b", "a"]);
  });

  it("test_the_caller_s_own_item_objects_never_come_back_wearing_a_score", () => {
    // The nav list is a module-level constant; stamping `score` onto it would
    // leak the LAST query's ranking into every later render.
    const original = item({ id: "usage", label: "Usage" });
    const hits = scorePalette("usage", [original]);
    expect(hits[0]).not.toBe(original);
    expect(original).not.toHaveProperty("score");
  });

  it("test_an_accented_letter_does_not_count_as_the_start_of_a_word", () => {
    // With an ASCII-only word-character test, "ü" reads as a boundary and
    // "ller" would score against "Müller" as if it had been named outright.
    const items = [
      item({ id: "muller", label: "Müller Ledger" }),
      item({ id: "ledger", label: "Ledger Müller" }),
    ];
    expect(scorePalette("ller", items)[0].score).toBe(300);
    // ...while the accented character itself is still matched, case-folded.
    const uber = [item({ id: "uber", label: "Über Settings" })];
    expect(ids(scorePalette("Ü", uber))).toEqual(["uber"]);
    expect(ids(scorePalette("ü", uber))).toEqual(["uber"]);
    expect(scorePalette("ber", uber)[0].score).toBe(300);
  });
});
