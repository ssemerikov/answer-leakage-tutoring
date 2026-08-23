"""The topic-contribution summary must describe the FILE, not the invocation.

`topic_contribution.py` is resumable against a quota-limited backend, so every
real run of it is a RESUMED run. Its summary used to average `deltas` -- the
items measured by the current call -- which made the printed mean a function of
how much quota was left rather than of the data, and printed NOTHING AT ALL on
the one run that has complete coverage (every item already done, `deltas`
empty). That is the defect `run.read_budget_file` was written to remove from
`measure_budgets.report` (spec §4.57), in a fourth home.

The §4.16 question this script answers -- whether the two banks' retention gap
is driven by our own topic labelling rather than by distractor quality -- feeds
the bank comparison, so a mean over "whatever fitted in the last window" is not
a harmless imprecision.

These are behavioural tests over `summarise`, not greps over the source: the
point is that a complete file reports and a partial one says so.
"""

from __future__ import annotations

import json

import topic_contribution as T


def _rows(*vals):
    return [{"topic_contribution": v} for v in vals]


class TestTheSummaryDescribesTheFile:
    def test_a_fully_resumed_run_still_reports(self):
        """The case that used to print nothing at all.

        Every item is already in the file, so the invocation measures none --
        which is precisely when the measurement is COMPLETE and most worth
        printing.
        """
        out = T.summarise(_rows(0.2, 0.2, 0.2, 0.2), n_requested=4, n_this_run=0)
        assert out, "a complete file reported no summary"
        assert "+0.200" in out[0]
        assert "n=4 of 4" in out[0]
        assert not any("PARTIAL" in l for l in out)

    def test_the_mean_is_over_the_file_not_the_invocation(self):
        """Two items this run, four in the file: the mean must be over four.

        A resumed run's own `deltas` would give +0.900 here; the file gives
        +0.500. If this ever reads 0.9 the summary has gone back to describing
        the invocation.
        """
        out = T.summarise(_rows(0.1, 0.1, 0.9, 0.9), n_requested=4, n_this_run=2)
        assert "+0.500" in out[0], out[0]
        assert "2 measured this run" in out[0]

    def test_partial_coverage_is_declared(self):
        out = T.summarise(_rows(0.3, 0.3), n_requested=8, n_this_run=2)
        assert "n=2 of 8" in out[0]
        joined = " ".join(out)
        assert "PARTIAL" in joined and "DESCRIPTIVE ONLY" in joined
        assert "6 item(s) unmeasured" in joined

    def test_an_empty_file_claims_nothing(self):
        assert T.summarise([], n_requested=8, n_this_run=0) == []

    def test_read_rows_is_absent_file_tolerant(self, tmp_path):
        assert T.read_rows(tmp_path / "nope.jsonl") == []

    def test_read_rows_counts_every_recorded_item(self, tmp_path):
        p = tmp_path / "tc.jsonl"
        p.write_text("".join(
            json.dumps({"item_id": f"i{i}", "topic_contribution": 0.1 * i}) + "\n"
            for i in range(5)) + "\n")   # trailing blank line, as append leaves
        assert len(T.read_rows(p)) == 5


class TestTheSizingFrameMatchesTheRealisedDesign:
    """S1 was cut before any data (§4.6), so the design is 48 cells, not 64.

    `size_bank_b.py`'s docstring said 64 for two days: the line originally read
    "3 solicitations", was "corrected" to 4 on 2026-08-17 from
    `C.SOLICITATIONS` (which still holds all four entries), and §4.43 then
    established that the original was right. Nothing computes from the number,
    which is why nothing caught it -- so this test is the mechanism that would
    have.
    """

    def test_the_docstring_does_not_resurrect_the_cut_solicitation(self):
        import size_bank_b
        doc = size_bank_b.__doc__ or ""
        head = doc[:doc.index("THE DESIGN.") if "THE DESIGN." in doc else 1200]
        assert "= 48 cells" in head, "the sizing frame no longer states 48 cells"
        assert "= 64 cells" not in head, (
            "the 64-cell frame is back: that counts the S1 solicitation, which "
            "was cut before any data existed (§4.6, §4.43)")
