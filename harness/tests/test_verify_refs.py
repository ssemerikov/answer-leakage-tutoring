"""Tests for the bibliography checker.

The last-field regression is the important one: `doi` is conventionally the
final field, so a parser that drops the last field reports every entry as
unverifiable -- and a checker that always says "no DOI" gets ignored, which is
worse than having no checker.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import verify_refs as V  # noqa: E402

BIB = """
@inproceedings{Key2025,
  author    = {Calderon, Nitay and Reichart, Roi},
  title     = {The Alternative Annotator Test for {LLM}-as-a-Judge},
  booktitle = {Proceedings of ACL},
  year      = {2025},
  doi       = {10.18653/v1/2025.acl-long.782}
}

@article{Other2024,
  author = {Smith, Jane},
  title  = {A Title},
  year   = 2024
}
"""


class TestParsing:
    def test_last_field_is_not_dropped(self):
        """The regression: doi is last, and a newline-terminated field pattern
        never matches it because the entry pattern ate the newline."""
        e = V.parse_bib(BIB)[0]
        assert e["fields"]["doi"] == "10.18653/v1/2025.acl-long.782"

    def test_nested_braces_preserved(self):
        e = V.parse_bib(BIB)[0]
        assert "{LLM}-as-a-Judge" in e["fields"]["title"]

    def test_bare_numeric_field(self):
        e = V.parse_bib(BIB)[1]
        assert e["fields"]["year"] == "2024"

    def test_all_entries_found(self):
        assert [e["key"] for e in V.parse_bib(BIB)] == ["Key2025", "Other2024"]

    def test_entry_kind(self):
        assert V.parse_bib(BIB)[0]["kind"] == "inproceedings"

    def test_multiline_value_collapsed(self):
        bib = "@misc{K,\n  title = {A very\n     long title},\n  year = {2020}\n}"
        assert V.parse_bib(bib)[0]["fields"]["title"] == "A very long title"


class TestNoDoiDetection:
    def test_entry_without_doi_but_with_url_is_not_flagged_unverifiable(self):
        bib = ('@inproceedings{K,\n  author = {A, B},\n  title = {T},\n'
               '  url = {https://example.org/x.pdf},\n  issn = {1613-0073}\n}')
        r = V.check(V.parse_bib(bib)[0])
        assert r["status"] == "NO_DOI"
        assert not r["notes"]          # URL+ISSN present, so it is citable

    def test_entry_with_nothing_is_flagged(self):
        bib = "@misc{K,\n  author = {A, B},\n  title = {T}\n}"
        r = V.check(V.parse_bib(bib)[0])
        assert r["status"] == "NO_DOI"
        assert r["notes"]


class TestNormalisation:
    def test_brace_and_case_folding(self):
        assert V._norm("The {LLM}-as-a-Judge Test") == "the llm as a judge test"
