"""Tests for key-position rebalancing.

Bank A as authored placed the key at option 1 in 10 items and option 2 in 30,
and NEVER at option 3 (chi2 = 35.0 over 40 three-option items). The stated
chance rate of 1/3 was therefore not the guessing rate: a solver that had merely
noticed the last option is never keyed guesses at 1/2, and a middle-preferring
one scores 0.75 -- while the headroom screen's threshold is calibrated on 1/3.

Found by comparison with Bank B, the independently authored anchor, whose human
textbook authors balance positions (chi2 = 0.1 / 8.2 / 4.2).
"""

import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C  # noqa: E402
import rebalance_bank as RB  # noqa: E402


def _items(n_items, n_opts, key_index):
    return [{"id": f"T-{i:03d}", "type": "mcq", "stem": f"stem {i}",
             "options": [f"opt{j}" for j in range(n_opts)],
             "answer_index": key_index, "answer": f"opt{key_index}"}
            for i in range(n_items)]


class TestBalancing:
    def test_degenerate_bank_becomes_uniform(self):
        items = _items(30, 3, 1)          # every key at option 2
        out = RB.rebalance(items)
        counts = collections.Counter(i["answer_index"] for i in out)
        assert sorted(counts.values()) == [10, 10, 10]

    def test_chi_square_drops(self):
        items = _items(40, 3, 1)
        before = RB.key_distribution(items)[3]
        after = RB.key_distribution(RB.rebalance(items))[3]
        assert RB.chi_square(before, 3) > 30
        assert RB.chi_square(after, 3) < 1

    def test_groups_balanced_independently_by_option_count(self):
        """A bank mixing 3- and 5-option items must be balanced within each
        group, not merely in aggregate -- chance differs per group."""
        items = _items(12, 3, 0) + [dict(i, id="F" + i["id"]) for i in _items(10, 5, 0)]
        out = RB.rebalance(items)
        for n_opt, counts in RB.key_distribution(out).items():
            assert RB.chi_square(counts, n_opt) < 2.0, (n_opt, counts)


class TestContentPreservation:
    """A rebalancer that altered an item would be worse than the imbalance."""

    def test_option_texts_are_preserved_as_a_set(self):
        items = _items(20, 4, 2)
        for old, new in zip(items, RB.rebalance(items)):
            assert sorted(new["options"]) == sorted(old["options"])

    def test_key_text_still_sits_at_the_recorded_index(self):
        items = _items(20, 4, 2)
        for old, new in zip(items, RB.rebalance(items)):
            assert new["options"][new["answer_index"]] == old["options"][old["answer_index"]]

    def test_answer_field_follows_the_key(self):
        items = _items(9, 3, 0)
        for new in RB.rebalance(items):
            assert new["answer"] == new["options"][new["answer_index"]]

    def test_stems_and_ids_untouched(self):
        items = _items(9, 3, 0)
        for old, new in zip(items, RB.rebalance(items)):
            assert new["stem"] == old["stem"] and new["id"] == old["id"]

    def test_non_mcq_items_pass_through_unchanged(self):
        items = _items(6, 3, 1) + [{"id": "S-1", "type": "short", "answer_forms": ["x"]}]
        out = RB.rebalance(items)
        assert out[-1] == {"id": "S-1", "type": "short", "answer_forms": ["x"]}

    def test_item_count_and_order_preserved(self):
        items = _items(9, 3, 1)
        out = RB.rebalance(items)
        assert [i["id"] for i in out] == [i["id"] for i in items]


class TestDeterminism:
    def test_same_seed_gives_same_permutation(self):
        items = _items(15, 4, 1)
        assert RB.rebalance(items, seed=7) == RB.rebalance(items, seed=7)

    def test_different_seed_gives_a_different_permutation(self):
        items = _items(15, 4, 1)
        assert RB.rebalance(items, seed=7) != RB.rebalance(items, seed=8)


class TestRealBanks:
    """Both banks must be balanced, or option position could masquerade as a
    bank effect in the very comparison Bank B exists to make."""

    def test_bank_a_is_balanced(self):
        import json
        d = json.loads((C.BANKS_DIR / "bank_a.json").read_text())
        for n_opt, counts in RB.key_distribution(d["items"]).items():
            assert RB.chi_square(counts, n_opt) < 1.0, (n_opt, counts)

    def test_bank_a_records_why_it_was_rebalanced(self):
        import json
        d = json.loads((C.BANKS_DIR / "bank_a.json").read_text())
        assert d["rebalanced"]["seed"] == C.SEED
        assert "chi2" in d["rebalanced"]["reason"]

    def test_pre_fix_bank_is_preserved_for_audit(self):
        assert (C.BANKS_DIR / "bank_a_v1_unbalanced.json").exists()

    def test_bank_b_is_balanced_too(self):
        import json
        p = C.BANKS_DIR / "bank_b.json"
        if not p.exists():
            return
        for n_opt, counts in RB.key_distribution(json.loads(p.read_text())["items"]).items():
            assert RB.chi_square(counts, n_opt) < 1.0, (n_opt, counts)

    def test_bank_b_locators_carry_both_hashes(self):
        """`as_published` proves we measured the published items; `as_measured`
        matches what we actually ran after reordering."""
        import json
        p = C.BANKS_DIR / "bank_b_locators.json"
        if not p.exists():
            return
        loc = json.loads(p.read_text())
        for e in loc["items"][:20]:
            assert e["sha256_as_published"] and e["sha256_as_measured"]
        assert "seed" in loc["option_order"] or str(C.SEED) in loc["option_order"]
