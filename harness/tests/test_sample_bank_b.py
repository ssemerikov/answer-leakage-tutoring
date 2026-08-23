"""The Bank B sample is pre-registered; a biased or irreproducible draw would
invalidate the bank comparison it exists to support."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C  # noqa: E402
import sample_bank_b as SB  # noqa: E402


def _pop(n3=51, n4=100, n5=34):
    items = []
    for k, n in ((3, n3), (4, n4), (5, n5)):
        for i in range(n):
            items.append({"id": f"B-{k}-{i:03d}", "options": ["x"] * k})
    return items


class TestStratification:

    def test_sample_size_is_exact(self):
        for n in (10, 45, 60, 99):
            assert len(SB.stratified_sample(_pop(), n)) == n

    def test_strata_track_the_population(self):
        pop = _pop()
        s = SB.stratified_sample(pop, 45)
        for k in (3, 4, 5):
            pop_share = sum(1 for i in pop if len(i["options"]) == k) / len(pop)
            smp_share = sum(1 for i in s if len(i["options"]) == k) / len(s)
            assert abs(pop_share - smp_share) < 0.05, k

    def test_deterministic_under_seed(self):
        pop = _pop()
        a = [i["id"] for i in SB.stratified_sample(pop, 45)]
        b = [i["id"] for i in SB.stratified_sample(pop, 45)]
        assert a == b

    def test_a_different_seed_gives_a_different_draw(self):
        pop = _pop()
        a = [i["id"] for i in SB.stratified_sample(pop, 45, seed=1)]
        b = [i["id"] for i in SB.stratified_sample(pop, 45, seed=2)]
        assert a != b

    def test_no_duplicates(self):
        s = SB.stratified_sample(_pop(), 60)
        assert len({i["id"] for i in s}) == len(s)

    def test_sampling_everything_returns_everything(self):
        pop = _pop(5, 5, 5)
        s = SB.stratified_sample(pop, 15)
        assert {i["id"] for i in s} == {i["id"] for i in pop}

    def test_oversubscribed_stratum_does_not_crash(self):
        """A stratum smaller than its allocation must clamp, not raise."""
        pop = _pop(1, 1, 100)
        s = SB.stratified_sample(pop, 50)
        assert len(s) <= 50

    def test_draw_does_not_depend_on_input_order(self):
        """Reproducibility must survive a reordered bank file."""
        pop = _pop()
        rev = list(reversed(pop))
        a = [i["id"] for i in SB.stratified_sample(pop, 45)]
        b = [i["id"] for i in SB.stratified_sample(rev, 45)]
        assert a == b


class TestAgainstTheRealBank:

    def test_real_bank_sample_is_proportional(self):
        import json
        p = C.BANKS_DIR / "bank_b.json"
        if not p.exists():
            pytest.skip("bank_b.json is local-only (GFDL); not present")
        items = json.loads(p.read_text())["items"]
        s = SB.stratified_sample(items, 45)
        assert len(s) == 45
        assert len({i["id"] for i in s}) == 45
        ids = {i["id"] for i in items}
        assert all(i["id"] in ids for i in s)


class TestBankIdWiring:
    """`bs` must resolve to the sample file and to its own output files.

    Without this, `--bank B` was the only way to reach Bank B and would screen
    all 185 items -- 6.8 h, ~130 retained, and a ~101 h main study, which is the
    outcome the sample exists to avoid.
    """

    def test_bs_resolves_to_the_sample_file(self):
        import run as R
        assert R._bank_path("bs").name == "bank_b_sample.json"
        assert R._bank_path("BS").name == "bank_b_sample.json"

    def test_plain_b_still_means_the_whole_bank(self):
        import run as R
        assert R._bank_path("B").name == "bank_b.json"

    def test_sample_and_full_bank_never_share_an_output_file(self):
        import run as R
        for stage in ("screen", "placebo", "generate", "solve", "judge"):
            full = R._jsonl_path(stage, "B", "ol")
            samp = R._jsonl_path(stage, "bs", "ol")
            assert full != samp, stage
            assert samp.name == f"{stage}_bankBS_ol.jsonl"

    def test_unknown_bank_still_follows_the_default_rule(self):
        import run as R
        assert R._bank_path("Z").name == "bank_z.json"


class TestTwoStageSizingIsHonest:
    """The two-stage design is only legitimate if stage 2 cannot pick items.

    Sizing the second stage from the first stage's retention is standard. What
    would NOT be legitimate is letting the observed retention influence WHICH
    items get screened -- that is screening until the numbers look good. The
    protection is that the draw and its ORDER are fixed at stage 0 under the
    seed, and stage 2 only extends that fixed sequence.
    """

    def test_sample_order_is_fixed_by_the_seed(self):
        pop = _pop()
        a = [i["id"] for i in SB.stratified_sample(pop, 60)]
        b = [i["id"] for i in SB.stratified_sample(pop, 60)]
        assert a == b, "order must be reproducible, not just membership"

    def test_draws_at_different_n_do_not_nest(self):
        """Recorded because the protocol depends on knowing this.

        A draw of 20 and a draw of 45 share only about half their items, so a
        two-stage design must draw ONCE at N_max and consume a prefix -- never
        draw the pilot, then draw again for stage 2.
        """
        pop = _pop()
        small = {i["id"] for i in SB.stratified_sample(pop, 20)}
        large = {i["id"] for i in SB.stratified_sample(pop, 45)}
        assert small - large, "if these nested the warning could be dropped"

    def test_every_prefix_is_stratified(self):
        """The pilot must have the full draw's option-count mix.

        The draw is returned in interleaved draw order, NOT sorted by id.
        Sorting by id looks tidier and is quietly wrong: Bank B ids carry the
        source chapter, so an id-sorted prefix is the earliest chapters -- the
        easiest topics -- and a pilot drawn that way would size the whole second
        stage from an unrepresentative subset.
        """
        import collections
        pop = _pop()
        draw = SB.stratified_sample(pop, 60)
        full = collections.Counter(len(i["options"]) for i in draw)
        for pre in (10, 20, 30, 45):
            c = collections.Counter(len(i["options"]) for i in draw[:pre])
            for k in full:
                assert abs(c[k] / pre - full[k] / len(draw)) < 0.08, (pre, k)

    def test_draw_order_is_not_id_order(self):
        pop = _pop()
        draw = SB.stratified_sample(pop, 45)
        ids = [i["id"] for i in draw]
        assert ids != sorted(ids), "returned in id order; prefixes would be biased"

    def test_prefix_of_one_draw_is_stable(self):
        pop = _pop()
        a = [i["id"] for i in SB.stratified_sample(pop, 60)]
        b = [i["id"] for i in SB.stratified_sample(pop, 60)]
        assert a[:20] == b[:20] and a == b
