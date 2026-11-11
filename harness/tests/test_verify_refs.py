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


# ---------------------------------------------------------------------------
# P1E (2026-11-03): registry-record tests. Every record below is a TRIMMED copy
# of what the registry actually returned on 2026-11-03 (Crossref /works/{doi}
# "message", DataCite /dois/{doi} "data.attributes"); only fields the checker
# could read are kept, plus the metadata dates (created/deposited/indexed) that
# it must NOT read. No test touches the network: the autouse fixture below makes
# any real request fail the test.
# ---------------------------------------------------------------------------

import json  # noqa: E402
import urllib.error  # noqa: E402

import pytest  # noqa: E402

ROEDIGER = json.loads(r'''{"DOI": "10.1111/j.1467-9280.2006.01693.x", "type": "journal-article",
 "title": ["Test-Enhanced Learning"],
 "subtitle": ["Taking Memory Tests Improves Long-Term Retention"],
 "author": [{"given": "Henry L.", "family": "Roediger", "sequence": "first"},
            {"given": "Jeffrey D.", "family": "Karpicke", "sequence": "additional"}],
 "published": {"date-parts": [[2006, 3]]}, "published-print": {"date-parts": [[2006, 3]]},
 "published-online": {"date-parts": [[2006, 3, 1]]}, "issued": {"date-parts": [[2006, 3]]},
 "journal-issue": {"issue": "3", "published-print": {"date-parts": [[2006, 3]]}},
 "created": {"date-parts": [[2006, 2, 23]]}, "deposited": {"date-parts": [[2026, 5, 1]]},
 "indexed": {"date-parts": [[2026, 11, 2]]}}''')

DAWSON_BOOK = json.loads(r'''{"DOI": "10.4324/9780429324178", "type": "book",
 "title": ["Defending Assessment Security in a Digital World"],
 "subtitle": ["Preventing E-Cheating and Supporting Academic Integrity in Higher Education"],
 "author": [{"given": "Phillip", "family": "Dawson", "sequence": "first"}],
 "published": {"date-parts": [[2020, 10, 26]]}, "published-online": {"date-parts": [[2020, 10, 26]]},
 "issued": {"date-parts": [[2020, 10, 26]]}, "created": {"date-parts": [[2020, 9, 3]]},
 "deposited": {"date-parts": [[2021, 4, 2]]}, "indexed": {"date-parts": [[2026, 10, 30]]}}''')

BAKER = json.loads(r'''{"DOI": "10.1145/985692.985741", "type": "proceedings-article",
 "title": ["Off-task behavior in the cognitive tutor classroom"],
 "subtitle": ["when students \"game the system\""],
 "author": [{"given": "Ryan Shaun", "family": "Baker", "sequence": "first"},
            {"given": "Albert T.", "family": "Corbett", "sequence": "additional"},
            {"given": "Kenneth R.", "family": "Koedinger", "sequence": "additional"},
            {"given": "Angela Z.", "family": "Wagner", "sequence": "additional"}],
 "published": {"date-parts": [[2004, 4, 25]]}, "published-print": {"date-parts": [[2004, 4, 25]]},
 "published-online": {"date-parts": [[2004, 4, 25]]}, "issued": {"date-parts": [[2004, 4, 25]]},
 "created": {"date-parts": [[2004, 7, 20]]}, "deposited": {"date-parts": [[2025, 6, 18]]},
 "indexed": {"date-parts": [[2026, 10, 29]]}}''')

FAN_BJET = json.loads(r'''{"DOI": "10.1111/bjet.13544", "type": "journal-article",
 "title": ["Beware of metacognitive laziness: Effects of generative artificial intelligence on learning motivation, processes, and performance"],
 "subtitle": [],
 "author": [{"given": "Yizhou", "family": "Fan", "sequence": "first"},
            {"given": "Luzhen", "family": "Tang", "sequence": "additional"},
            {"given": "Huixiao", "family": "Le", "sequence": "additional"},
            {"given": "Kejie", "family": "Shen", "sequence": "additional"},
            {"given": "Shufang", "family": "Tan", "sequence": "additional"},
            {"given": "Yueying", "family": "Zhao", "sequence": "additional"},
            {"given": "Yuan", "family": "Shen", "sequence": "additional"},
            {"given": "Xinyu", "family": "Li", "sequence": "additional"},
            {"given": "Dragan", "family": "Gašević", "sequence": "additional"}],
 "published": {"date-parts": [[2024, 12, 10]]}, "published-print": {"date-parts": [[2025, 3]]},
 "published-online": {"date-parts": [[2024, 12, 10]]}, "issued": {"date-parts": [[2024, 12, 10]]},
 "journal-issue": {"issue": "2", "published-print": {"date-parts": [[2025, 3]]}},
 "created": {"date-parts": [[2024, 12, 10]]}, "deposited": {"date-parts": [[2026, 9, 1]]},
 "indexed": {"date-parts": [[2026, 11, 3]]}}''')

DIAZ = json.loads(r'''{"DOI": "10.1016/j.compedu.2024.105071", "type": "journal-article",
 "title": ["Artificial intelligence for teaching and learning in schools: The need for pedagogical intelligence"],
 "subtitle": [],
 "author": [{"given": "Brayan", "family": "Díaz", "sequence": "first"},
            {"given": "Miguel", "family": "Nussbaum", "sequence": "additional"}],
 "published": {"date-parts": [[2024, 8]]}, "published-print": {"date-parts": [[2024, 8]]},
 "issued": {"date-parts": [[2024, 8]]}, "created": {"date-parts": [[2024, 5, 8]]},
 "deposited": {"date-parts": [[2024, 11, 18]]}, "indexed": {"date-parts": [[2026, 11, 1]]}}''')

RIVERS = json.loads(r'''{"DOI": "10.1007/s40593-015-0070-z", "type": "journal-article",
 "title": ["Data-Driven Hint Generation in Vast Solution Spaces: a Self-Improving Python Programming Tutor"],
 "subtitle": [],
 "author": [{"given": "Kelly", "family": "Rivers", "sequence": "first"},
            {"given": "Kenneth R.", "family": "Koedinger", "sequence": "additional"}],
 "published": {"date-parts": [[2017, 3]]}, "published-print": {"date-parts": [[2017, 3]]},
 "issued": {"date-parts": [[2017, 3]]},
 "journal-issue": {"issue": "1", "published-print": {"date-parts": [[2017, 3]]}},
 "created": {"date-parts": [[2015, 10, 30]]}, "deposited": {"date-parts": [[2026, 5, 18]]},
 "indexed": {"date-parts": [[2026, 10, 7]]}}''')

#: DataCite (arXiv). `published` is a bare STRING here, not Crossref's
#: {"date-parts": ...}: indexing it as a dict is the TypeError of P1B.
KADIR_DATACITE = json.loads(r'''{"doi": "10.48550/arxiv.2608.00515",
 "titles": [{"title": "Auditable Release Control for Pedagogical Leakage in LLM Tutors"}],
 "creators": [{"name": "Kadir, Nizam", "nameType": "Personal", "givenName": "Nizam", "familyName": "Kadir"}],
 "published": "2026", "publicationYear": 2026,
 "dates": [{"date": "2026-08-01T08:24:30Z", "dateType": "Submitted", "dateInformation": "v1"},
           {"date": "2026-08-04T00:26:44Z", "dateType": "Updated", "dateInformation": "v1"},
           {"date": "2026-08", "dateType": "Available", "dateInformation": "v1"}]}''')

KADIR_BIB = r"""@misc{Kadir2026ReleaseControl,
	author       = {Kadir, Nizam},
	title        = {Auditable Release Control for Pedagogical Leakage in {LLM} Tutors},
	howpublished = {arXiv preprint arXiv:2608.00515},
	year         = {2026},
	doi          = {10.48550/arXiv.2608.00515}
}"""


def _bib(key="K", author="Roediger, Henry L. and Karpicke, Jeffrey D.",
         title="Test-enhanced learning: Taking memory tests improves long-term retention",
         year="2006", doi="10.1111/j.1467-9280.2006.01693.x", kind="article"):
    return (f"@{kind}{{{key},\n\tauthor = {{{author}}},\n\ttitle = {{{title}}},\n"
            f"\tyear = {{{year}}},\n\tdoi = {{{doi}}}\n}}\n")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No test may reach a registry, and none may wait. The fetchers swallow
    exceptions (a lookup failure is a status, not a crash), so raising here
    would be invisible: record the attempt and fail at teardown instead."""
    attempts = []

    def _no_network(req, *a, **k):
        attempts.append(getattr(req, "full_url", req))
        raise OSError("network disabled in tests")

    monkeypatch.setattr(V.urllib.request, "urlopen", _no_network)
    monkeypatch.setattr(V.time, "sleep", lambda s: None)
    yield
    assert not attempts, f"a test tried to reach the network: {attempts}"


@pytest.fixture
def registry(monkeypatch):
    """Serve recorded records by DOI; any other DOI is unregistered (None).
    Records which registry each DOI was looked up in."""
    served, calls = {}, []

    def crossref(doi):
        calls.append(("crossref", doi))
        return served.get(("crossref", doi.lower()))

    def datacite(doi):
        calls.append(("datacite", doi))
        return served.get(("datacite", doi.lower()))

    monkeypatch.setattr(V, "fetch_crossref", crossref)
    monkeypatch.setattr(V, "fetch_datacite", datacite)

    class R:
        def crossref(self, record, doi=None):
            served[("crossref", (doi or record["DOI"]).lower())] = record

        def datacite(self, record, doi=None):
            served[("datacite", (doi or record["doi"]).lower())] = record

        def run(self, bib_text):
            return V.check(V.parse_bib(bib_text)[0])

    r = R()
    r.calls = calls
    return r


class TestDataCiteRecords:
    """Requirement 1: a DataCite date that is a plain string must not crash."""

    def test_arxiv_record_with_string_published_is_checked_not_crashed(self, registry):
        registry.datacite(KADIR_DATACITE, doi="10.48550/arXiv.2608.00515")
        r = registry.run(KADIR_BIB)
        assert r["status"] == "OK", r["notes"]
        assert registry.calls == [("datacite", "10.48550/arXiv.2608.00515")]

    def test_arxiv_doi_never_goes_to_crossref(self, registry):
        registry.crossref(dict(KADIR_DATACITE, DOI="10.48550/arXiv.2608.00515",
                               title=["x"]))
        r = registry.run(KADIR_BIB)          # not served by DataCite -> UNRESOLVED
        assert r["status"] == "UNRESOLVED"
        assert [c[0] for c in registry.calls] == ["datacite"]

    def test_wrong_year_on_a_datacite_record_still_fires(self, registry):
        registry.datacite(KADIR_DATACITE, doi="10.48550/arXiv.2608.00515")
        r = registry.run(KADIR_BIB.replace("{2026}", "{2025}"))
        assert r["status"] == "MISMATCH"
        assert any(n.startswith("year") for n in r["notes"])

    @pytest.mark.parametrize("value, year", [
        ({"date-parts": [[2025, 3]]}, 2025),
        ("2026", 2026),
        ("2026-08-01T08:24:30Z", 2026),
        (2024, 2024),
        ({"date-parts": [[None]]}, None),     # Crossref's "date unknown"
        ({"date-parts": []}, None),
        ("", None),
        (None, None),
        (True, None),
    ])
    def test_year_parsed_from_either_shape(self, value, year):
        assert V._year_of(value) == year

    def test_datacite_years_come_from_both_fields(self):
        assert V.registry_years(KADIR_DATACITE) == {2026}
        only_string = {k: v for k, v in KADIR_DATACITE.items() if k != "publicationYear"}
        assert V.registry_years(only_string) == {2026}

    def test_doi_mode_prints_a_datacite_record(self, registry, monkeypatch, capsys):
        registry.datacite(KADIR_DATACITE, doi="10.48550/arXiv.2608.00515")
        monkeypatch.setattr(sys, "argv", ["verify_refs.py", "--doi", "10.48550/arXiv.2608.00515"])
        V.main()
        out = capsys.readouterr().out
        assert "Auditable Release Control" in out and "2026" in out and "Kadir, Nizam" in out


class TestSubtitles:
    """Requirement 2: Crossref keeps a subtitle in its own field."""

    def test_title_plus_subtitle_matches(self, registry):
        registry.crossref(ROEDIGER)
        assert registry.run(_bib())["status"] == "OK"

    def test_title_alone_matches(self, registry):
        registry.crossref(ROEDIGER)
        assert registry.run(_bib(title="Test-enhanced learning"))["status"] == "OK"

    def test_subtitle_alone_does_not_match(self, registry):
        registry.crossref(ROEDIGER)
        r = registry.run(_bib(title="Taking memory tests improves long-term retention"))
        assert r["status"] == "MISMATCH" and any(n.startswith("title") for n in r["notes"])

    def test_title_with_a_wrong_subtitle_does_not_match(self, registry):
        registry.crossref(ROEDIGER)
        r = registry.run(_bib(title="Test-enhanced learning: Taking memory tests improves recall"))
        assert r["status"] == "MISMATCH"

    def test_quoted_subtitle_and_tex_quotes(self, registry):
        registry.crossref(BAKER)
        r = registry.run(_bib(
            author="Baker, Ryan Shaun and Corbett, Albert T. and Koedinger, Kenneth R. and Wagner, Angela Z.",
            title="Off-task behavior in the cognitive tutor classroom: When students ``game the system''",
            year="2004", doi="10.1145/985692.985741", kind="inproceedings"))
        assert r["status"] == "OK", r["notes"]

    def test_book_subtitle_over_a_line_break(self, registry):
        registry.crossref(DAWSON_BOOK)
        bib = ("@book{D,\n\tauthor = {Dawson, Phillip},\n"
               "\ttitle = {Defending assessment security in a digital world:\n"
               "\t{Preventing} e-cheating and supporting academic integrity in\n\thigher education},\n"
               "\tyear = {2020},\n\tdoi = {10.4324/9780429324178}\n}")
        assert registry.run(bib)["status"] == "OK"

    def test_datacite_subtitle_title_type(self, registry):
        rec = dict(KADIR_DATACITE, titles=[
            {"title": "Auditable Release Control"},
            {"title": "Pedagogical Leakage in LLM Tutors", "titleType": "Subtitle"},
            {"title": "Something Else Entirely", "titleType": "TranslatedTitle"}])
        registry.datacite(rec, doi="10.48550/arXiv.2608.00515")
        ok = KADIR_BIB.replace("Auditable Release Control for Pedagogical Leakage in {LLM} Tutors",
                               "Auditable release control: Pedagogical leakage in {LLM} tutors")
        assert registry.run(ok)["status"] == "OK"
        for not_a_title in ("Something else entirely",
                            "Auditable release control: Something else entirely"):
            translated = KADIR_BIB.replace(
                "Auditable Release Control for Pedagogical Leakage in {LLM} Tutors", not_a_title)
            assert registry.run(translated)["status"] == "MISMATCH", not_a_title


class TestYearFields:
    """Requirement 3: any publication date the registry records, and only those."""

    def _fan(self, year):
        return _bib(author="Fan, Yizhou and Tang, Luzhen and Le, Huixiao and Shen, Kejie and "
                           "Tan, Shufang and Zhao, Yueying and Shen, Yuan and Li, Xinyu and "
                           "Ga{\\v s}evi{\\'c}, Dragan",
                    title="Beware of metacognitive laziness: Effects of generative artificial "
                          "intelligence on learning motivation, processes, and performance",
                    year=year, doi="10.1111/bjet.13544")

    def test_issue_year_accepted(self, registry):
        registry.crossref(FAN_BJET)
        assert registry.run(self._fan("2025"))["status"] == "OK"

    def test_online_year_accepted(self, registry):
        registry.crossref(FAN_BJET)
        assert registry.run(self._fan("2024"))["status"] == "OK"

    def test_year_in_no_publication_field_fires(self, registry):
        registry.crossref(FAN_BJET)
        r = registry.run(self._fan("2023"))
        assert r["status"] == "MISMATCH" and any(n.startswith("year") for n in r["notes"])

    def test_metadata_dates_are_not_publication_dates(self, registry):
        """indexed/deposited are the current year for most records: accepting
        them would wave through any entry dated this year."""
        registry.crossref(ROEDIGER)
        r = registry.run(_bib(year="2026"))
        assert r["status"] == "MISMATCH" and any(n.startswith("year") for n in r["notes"])

    def test_created_date_is_not_a_publication_date(self, registry):
        registry.crossref(RIVERS)
        r = registry.run(_bib(author="Rivers, Kelly and Koedinger, Kenneth R.",
                              title="Data-driven hint generation in vast solution spaces: "
                                    "A self-improving {Python} programming tutor",
                              year="2015", doi="10.1007/s40593-015-0070-z"))
        assert r["status"] == "MISMATCH"

    def test_journal_issue_year_alone_is_enough(self):
        rec = {"issued": {"date-parts": [[2024, 12]]},
               "journal-issue": {"published-online": {"date-parts": [[2025, 1]]}}}
        assert V.registry_years(rec) == {2024, 2025}

    def test_exactly_the_allowed_fields(self):
        assert V.registry_years(ROEDIGER) == {2006}
        assert V.registry_years(FAN_BJET) == {2024, 2025}
        assert V.registry_years(RIVERS) == {2017}


class TestAccentsAndEscapes:
    """Requirement 4: fold accents and TeX escapes on BOTH sides."""

    @pytest.mark.parametrize("a, b", [
        ("D{\\'\\i}az", "Díaz"),
        ("Díaz", "Diaz"),
        ("D{\\'\\i}az", "Diaz"),
        ("D\\'{\\i}az", "Díaz"),
        ("Ga{\\v s}evi{\\'c}", "Gašević"),
        ("Ga\\v{s}evi\\'{c}", "Gasevic"),
        ("P{\\^\\i}slar", "Pîslar"),
        ("K{\\\"a}ser", "Käser"),
        ("Kabakc{\\i}", "Kabakcı"),
        ("Kujanp{\\\"a}{\\\"a}", "Kujanpää"),
        ("Rakovi{\\'c}", "Raković"),
        ("P{\\u a}durean", "Pădurean"),
        ("{\\O}stergaard", "Østergaard"),
        ("Wa{\\l}\\k{e}sa", "Wałęsa"),
        ("Stra{\\ss}e", "Straße"),
        ("Computers \\& Education", "Computers & Education"),
        ("A \\LaTeX\\ guide", "A LaTeX guide"),
        ("\\BibTeX{} and \\TeX", "BibTeX and TeX"),
    ])
    def test_equal_after_folding(self, a, b):
        assert V._norm(a) == V._norm(b)

    @pytest.mark.parametrize("a, b", [
        ("Díaz", "Dias"),
        ("Gašević", "Gasevich"),
        ("Тест", "Текст"),        # non-Latin titles are compared, not erased
    ])
    def test_still_different_after_folding(self, a, b):
        assert V._norm(a) != V._norm(b)

    def test_non_latin_text_is_kept(self):
        assert V._norm("Тест") == "тест"

    @pytest.mark.parametrize("surname", ["D{\\'\\i}az", "Díaz", "Diaz"])
    def test_first_author_with_any_spelling_of_diaz(self, registry, surname):
        registry.crossref(DIAZ)
        r = registry.run(_bib(author=f"{surname}, Brayan and Nussbaum, Miguel",
                              title="Artificial intelligence for teaching and learning in "
                                    "schools: The need for pedagogical intelligence",
                              year="2024", doi="10.1016/j.compedu.2024.105071"))
        assert r["status"] == "OK", r["notes"]


class TestNestedBraces:
    """Requirement 5: two (or more) brace levels are parsed, never skipped."""

    def test_two_levels(self):
        bib = "@article{K,\n  title = {{Outer {Inner} text}},\n  year = {2020}\n}"
        assert V.parse_bib(bib)[0]["fields"]["title"] == "{Outer {Inner} text}"

    def test_three_levels(self):
        bib = "@article{K,\n  title = {{A {B {C}} d}},\n  doi = {10.1/x}\n}"
        f = V.parse_bib(bib)[0]["fields"]
        assert f["title"] == "{A {B {C}} d}" and f["doi"] == "10.1/x"

    def test_rivers_as_it_was_written_is_checked(self, registry):
        """The pre-P1E Rivers entry: its title was never parsed, so its title
        check was silently skipped and the entry read OK."""
        registry.crossref(RIVERS)
        bib = ("@article{Rivers2017DataDrivenHints,\n"
               "\tauthor  = {Rivers, Kelly and Koedinger, Kenneth R.},\n"
               "\ttitle   = {{Data-Driven Hint Generation in Vast Solution Spaces: a\n"
               "\tSelf-Improving {Python} Programming Tutor}},\n"
               "\tjournal = {International Journal of Artificial Intelligence in Education},\n"
               "\tyear    = {2017},\n\tdoi     = {10.1007/s40593-015-0070-z}\n}")
        e = V.parse_bib(bib)[0]
        assert "{Python}" in e["fields"]["title"]
        assert V.check(e)["status"] == "OK"
        wrong = bib.replace("Vast Solution Spaces", "Small Solution Spaces")
        assert registry.run(wrong)["status"] == "MISMATCH"

    def test_quoted_value_with_braces(self):
        bib = '@inproceedings{K,\ntitle = "{The {LLM} Test: {A} {B}}",\nyear = "2025"\n}'
        assert V.parse_bib(bib)[0]["fields"]["title"] == "{The {LLM} Test: {A} {B}}"

    def test_macro_and_concatenation(self):
        bib = '@misc{K,\n  month = jul,\n  note = "a" # {b} # "c",\n  year = 2021\n}'
        f = V.parse_bib(bib)[0]["fields"]
        assert f["month"] == "jul" and f["note"] == "abc" and f["year"] == "2021"

    def test_indented_closing_brace_does_not_swallow_the_next_entry(self):
        bib = ("@article{A,\n  title = {One},\n  doi = {10.1/a}\n  }\n\n"
               "@article{B,\n  title = {Two},\n  doi = {10.1/b}\n}\n")
        es = V.parse_bib(bib)
        assert [e["key"] for e in es] == ["A", "B"]
        assert [e["fields"]["doi"] for e in es] == ["10.1/a", "10.1/b"]

    def test_at_sign_inside_a_value_is_not_an_entry(self):
        bib = ("@inproceedings{A,\n  booktitle = {CS{\\&}SE@SW 2023 @misc{x}},\n  year = {2023}\n}\n")
        es = V.parse_bib(bib)
        assert [e["key"] for e in es] == ["A"]

    def test_string_and_comment_blocks_are_not_entries(self):
        bib = '@string{acl = "ACL"}\n@comment{ignore me}\n@misc{K,\n  year = {2020}\n}\n'
        assert [e["key"] for e in V.parse_bib(bib)] == ["K"]

    def test_unbalanced_entry_is_reported_and_the_next_one_still_parsed(self):
        bib = ("@article{Broken,\n  title = {Never {closed},\n  doi = {10.1/a}\n}\n\n"
               "@article{Fine,\n  title = {Two},\n  doi = {10.1/b}\n}\n")
        es = V.parse_bib(bib)
        assert [e["key"] for e in es] == ["Broken", "Fine"]
        assert es[0].get("error") and not es[1].get("error")
        assert V.check(es[0])["status"] == "UNPARSED"

    def test_extra_closing_brace_is_reported_not_truncated(self):
        """An extra '}' closes the entry early; the doi after it must not
        quietly turn the entry into NO_DOI."""
        bib = ("@article{A,\n  title = {One}},\n  doi = {10.1/a}\n}\n\n"
               "@article{B,\n  title = {Two},\n  doi = {10.1/b}\n}\n")
        es = V.parse_bib(bib)
        assert [e["key"] for e in es] == ["A", "B"]
        assert es[0].get("error") and not es[1].get("error")
        assert V.check(es[0])["status"] == "UNPARSED"

    def test_comment_lines_between_entries_are_not_stray_fields(self):
        bib = ("% job: stakes — §1 Intro\n@article{A,\n  title = {One}\n}\n"
               "%% note = {x}\n%% another note\n@article{B,\n  title = {Two}\n}\n")
        assert not any(e.get("error") for e in V.parse_bib(bib))

    def test_doi_entry_without_a_parsed_title_is_not_ok(self, registry):
        registry.crossref(ROEDIGER)
        bib = ("@article{K,\n  author = {Roediger, Henry L. and Karpicke, Jeffrey D.},\n"
               "  year = {2006},\n  doi = {10.1111/j.1467-9280.2006.01693.x}\n}")
        r = registry.run(bib)
        assert r["status"] == "MISMATCH" and any("title" in n for n in r["notes"])

    @pytest.mark.parametrize("field", ["author", "year"])
    def test_doi_entry_without_author_or_year_is_not_ok(self, registry, field):
        registry.crossref(ROEDIGER)
        bib = "\n".join(l for l in _bib().splitlines() if not l.strip().startswith(field))
        r = registry.run(bib)
        assert r["status"] == "MISMATCH" and any(field in n for n in r["notes"])


class TestMismatchesStillFire:
    """Requirement 8: every mismatch class still fails after the fixes."""

    def test_wrong_title(self, registry):
        registry.crossref(ROEDIGER)
        r = registry.run(_bib(title="Retrieval practice and long-term retention"))
        assert r["status"] == "MISMATCH" and any(n.startswith("title") for n in r["notes"])

    def test_title_wrong_only_after_sixty_characters(self, registry):
        """The old check compared the first 60 normalised characters only."""
        registry.crossref(FAN_BJET)
        bib = TestYearFields()._fan("2025").replace("and performance", "and well-being")
        r = registry.run(bib)
        assert r["status"] == "MISMATCH" and any(n.startswith("title") for n in r["notes"])

    def test_truncated_title(self, registry):
        registry.crossref(FAN_BJET)
        bib = TestYearFields()._fan("2025").replace(
            ": Effects of generative artificial intelligence on learning motivation, "
            "processes, and performance", ": Effects of generative artificial intelligence")
        assert registry.run(bib)["status"] == "MISMATCH"

    def test_wrong_year(self, registry):
        registry.crossref(ROEDIGER)
        r = registry.run(_bib(year="2007"))
        assert r["status"] == "MISMATCH" and any(n.startswith("year") for n in r["notes"])

    def test_wrong_first_author(self, registry):
        registry.crossref(ROEDIGER)
        r = registry.run(_bib(author="Karpicke, Jeffrey D. and Roediger, Henry L."))
        assert r["status"] == "MISMATCH" and any(n.startswith("first author") for n in r["notes"])

    @pytest.mark.parametrize("surname", ["Fang", "Fa", "Fanning"])
    def test_first_author_prefix_or_superstring_fires(self, registry, surname):
        """The old check was substring containment: registry 'Fan' passed bib 'Fang'."""
        registry.crossref(FAN_BJET)
        bib = TestYearFields()._fan("2025").replace("Fan, Yizhou", f"{surname}, Yizhou")
        r = registry.run(bib)
        assert r["status"] == "MISMATCH" and any(n.startswith("first author") for n in r["notes"])

    def test_surname_particle_still_matches(self):
        assert V._surname_matches("Braak", "van Braak")
        assert V._surname_matches("van Braak", "van Braak")
        assert not V._surname_matches("van Braak", "Braak")
        assert not V._surname_matches("Li", "Lin")

    def test_given_first_bib_name(self, registry):
        registry.crossref(ROEDIGER)
        assert registry.run(_bib(author="Henry L. Roediger and Jeffrey D. Karpicke"))["status"] == "OK"

    def test_wrong_author_count(self, registry):
        registry.crossref(ROEDIGER)
        r = registry.run(_bib(author="Roediger, Henry L."))
        assert r["status"] == "MISMATCH" and any(n.startswith("author count") for n in r["notes"])

    def test_and_inside_braces_is_one_author(self, registry):
        registry.crossref(DAWSON_BOOK)
        bib = _bib(author="{Dawson and Sons}", title="Defending assessment security in a digital world",
                   year="2020", doi="10.4324/9780429324178", kind="book")
        r = registry.run(bib)
        assert not any(n.startswith("author count") for n in r["notes"])

    def test_unregistered_doi_is_unresolved(self, registry):
        r = registry.run(_bib(doi="10.1111/j.1467-9280.2006.99999.x"))
        assert r["status"] == "UNRESOLVED"

    def test_doi_of_another_work(self, registry):
        """A DOI that resolves, but to a different paper."""
        registry.crossref(ROEDIGER, doi="10.4324/9780429324178")
        bib = _bib(author="Dawson, Phillip", title="Defending assessment security in a digital world",
                   year="2020", doi="10.4324/9780429324178", kind="book")
        r = registry.run(bib)
        assert r["status"] == "MISMATCH"
        assert {n.split(":")[0] for n in r["notes"]} >= {"title differs", "first author differs",
                                                        "year"}

    @pytest.mark.parametrize("record, status, rc", [
        (ROEDIGER, "OK", 0),
        (dict(ROEDIGER, title=["Something else"], subtitle=[]), "MISMATCH", 1),
        (None, "UNRESOLVED", 1),
    ])
    def test_exit_code_is_the_gate(self, registry, monkeypatch, capsys, tmp_path,
                                   record, status, rc):
        if record is not None:
            registry.crossref(record)
        p = tmp_path / "x.bib"
        p.write_text(_bib() + "\n@misc{NoDoi,\n  title = {T},\n  url = {https://e.org}\n}\n",
                     encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["verify_refs.py", str(p)])
        try:
            V.main()
            code = 0
        except SystemExit as e:
            code = e.code
        out = capsys.readouterr().out
        assert code == rc
        assert f"{status:<11} K" in out and "NO_DOI" in out

    def test_unparsed_entry_fails_the_run(self, registry, monkeypatch, tmp_path):
        p = tmp_path / "x.bib"
        p.write_text("@article{Broken,\n  title = {Never {closed},\n  year = {2020}\n}\n",
                     encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["verify_refs.py", str(p)])
        with pytest.raises(SystemExit) as e:
            V.main()
        assert e.value.code == 1


class TestRequests:
    """Requirement 9 and retry behaviour. Requests carry the existing
    User-Agent and nothing else: one header, no query string."""

    class _Resp:
        def __init__(self, payload):
            self._b = json.dumps(payload).encode()

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _serve(self, monkeypatch, outcomes):
        seen = []

        def fake(req, timeout=None):
            seen.append(req)
            o = outcomes.pop(0)
            if isinstance(o, Exception):
                raise o
            return self._Resp(o)

        monkeypatch.setattr(V.urllib.request, "urlopen", fake)
        return seen

    def _http(self, code):
        return urllib.error.HTTPError("https://api.crossref.org/works/x", code, "x", {}, None)

    def test_request_carries_only_the_existing_user_agent(self, monkeypatch):
        seen = self._serve(monkeypatch, [{"message": ROEDIGER}])
        assert V.fetch_crossref("10.1111/j.1467-9280.2006.01693.x") == ROEDIGER
        (req,) = seen
        assert req.header_items() == [("User-agent", V.UA)]
        assert "?" not in req.full_url and "mailto" not in req.full_url
        assert req.full_url.startswith("https://api.crossref.org/works/")

    def test_datacite_request(self, monkeypatch):
        seen = self._serve(monkeypatch, [{"data": {"attributes": KADIR_DATACITE}}])
        assert V.fetch_datacite("10.48550/arXiv.2608.00515") == KADIR_DATACITE
        assert seen[0].full_url.startswith("https://api.datacite.org/dois/")
        assert seen[0].header_items() == [("User-agent", V.UA)]

    def test_404_is_not_retried(self, monkeypatch):
        seen = self._serve(monkeypatch, [self._http(404)])
        assert V.fetch_crossref("10.1/none") is None
        assert len(seen) == 1

    @pytest.mark.parametrize("transient", [429, 503])
    def test_transient_http_error_is_retried(self, monkeypatch, transient):
        seen = self._serve(monkeypatch, [self._http(transient), {"message": ROEDIGER}])
        assert V.fetch_crossref("10.1111/j.1467-9280.2006.01693.x") == ROEDIGER
        assert len(seen) == 2

    def test_network_error_retried_then_given_up(self, monkeypatch):
        errs = [urllib.error.URLError("down") for _ in range(V._ATTEMPTS)]
        seen = self._serve(monkeypatch, errs)
        assert V.fetch_crossref("10.1/x") is None
        assert len(seen) == V._ATTEMPTS



# ---------------------------------------------------------------------------
# P1E fix round 1 (review docs/revision-2026-11/reviews/P1E-task-review.md)
# ---------------------------------------------------------------------------

MILLMAN = json.loads(r"""{"DOI": "10.1177/001316446502500304", "type": "journal-article",
 "title": ["An Analysis of Test-Wiseness"], "subtitle": [],
 "author": [{"given": "Jason", "family": "Millman", "sequence": "first"},
            {"given": "Carol H.", "family": "Bishop", "sequence": "additional"},
            {"given": "Robert", "family": "Ebel", "sequence": "additional"}],
 "published": {"date-parts": [[1965, 10]]}, "published-print": {"date-parts": [[1965, 10]]},
 "published-online": {"date-parts": [[1965, 10, 1]]}, "issued": {"date-parts": [[1965, 10]]},
 "journal-issue": {"issue": "3", "published-print": {"date-parts": [[1965, 10]]}},
 "created": {"date-parts": [[2007, 3, 11]]}, "deposited": {"date-parts": [[2026, 4, 28]]},
 "indexed": {"date-parts": [[2026, 10, 15]]}}""")

MILLMAN_BIB = _bib(key="Millman1965TestWiseness",
                   author="Millman, Jason and Bishop, Carol H. and Ebel, Robert",
                   title="An analysis of test-wiseness", year="1965",
                   doi="10.1177/001316446502500304")


def _run_main(monkeypatch, capsys, tmp_path, bib_text):
    p = tmp_path / "x.bib"
    p.write_text(bib_text, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["verify_refs.py", str(p)])
    try:
        V.main()
        code = 0
    except SystemExit as e:
        code = e.code
    return code, capsys.readouterr().out


class TestRepeatedFieldsAndKeys:
    """F1: BibTeX keeps the FIRST of a repeated field and the FIRST of a
    repeated entry, so a later correct copy must not vouch for the printed one."""

    @pytest.mark.parametrize("field, first, second", [
        ("title", "Completely wrong title",
         "Test-enhanced learning: Taking memory tests improves long-term retention"),
        ("year", "1999", "2006"),
        ("doi", "10.9999/wrong", "10.1111/j.1467-9280.2006.01693.x"),
    ])
    def test_repeated_field_is_unparsed(self, registry, field, first, second):
        registry.crossref(ROEDIGER)
        bib = _bib().replace(f"\t{field} = {{{second}}}",
                             f"\t{field} = {{{first}}},\n\t{field} = {{{second}}}")
        assert bib.count(f"\t{field} = ") == 2
        e = V.parse_bib(bib)[0]
        assert e.get("error") == f"field {field} repeated"
        assert V.check(e)["status"] == "UNPARSED"

    def test_repeated_field_name_is_case_insensitive(self):
        bib = "@article{K,\n  title = {One},\n  TITLE = {Two}\n}"
        assert V.parse_bib(bib)[0].get("error") == "field title repeated"

    def test_repeated_key_later_copy_is_unparsed(self, registry):
        registry.crossref(ROEDIGER)
        wrong_first = ("@article{K,\n\tauthor = {Roediger, Henry L. and Karpicke, Jeffrey D.},\n"
                       "\ttitle = {Completely wrong title},\n\tyear = {2006}\n}\n\n")
        es = V.parse_bib(wrong_first + _bib())
        assert [e["key"] for e in es] == ["K", "K"]
        assert not es[0].get("error")
        assert "repeated" in es[1].get("error", "")
        assert V.check(es[1])["status"] == "UNPARSED"

    def test_repeated_key_is_case_insensitive(self):
        bib = "@article{Key,\n  title = {One}\n}\n@misc{key,\n  title = {Two}\n}\n"
        es = V.parse_bib(bib)
        assert not es[0].get("error") and "repeated" in es[1].get("error", "")

    def test_distinct_keys_are_not_flagged(self):
        bib = "@article{K1,\n  title = {One}\n}\n@article{K2,\n  title = {One}\n}\n"
        assert not any(e.get("error") for e in V.parse_bib(bib))

    def test_string_block_does_not_count_as_a_key(self):
        bib = '@string{K = "x"}\n@article{K,\n  title = {One}\n}\n'
        assert not any(e.get("error") for e in V.parse_bib(bib))

    def test_repeated_key_fails_the_run(self, registry, monkeypatch, capsys, tmp_path):
        registry.crossref(ROEDIGER)
        code, out = _run_main(monkeypatch, capsys, tmp_path,
                              "@misc{K,\n  title = {Wrong},\n  url = {https://e.org}\n}\n\n" + _bib())
        assert code == 1 and "UNPARSED" in out and "NO_DOI" in out


class TestNotCheckedIsReported:
    """F2: a check the REGISTRY cannot support is printed and counted."""

    @pytest.mark.parametrize("drop, line", [
        (("title", "subtitle"), "(not checked: title (registry has none))"),
        (("author",), "(not checked: authors (registry has none))"),
        (("published", "published-print", "published-online", "issued", "journal-issue"),
         "(not checked: year (registry has no publication date))"),
    ])
    def test_line_and_count(self, registry, monkeypatch, capsys, tmp_path, drop, line):
        registry.crossref({k: v for k, v in ROEDIGER.items() if k not in drop})
        code, out = _run_main(monkeypatch, capsys, tmp_path, _bib())
        assert line in out
        assert out.rstrip().endswith("summary: OK=1; not checked=1")
        assert code == 0          # a registry gap is reported, not failed

    def test_first_author_without_surname(self, registry, monkeypatch, capsys, tmp_path):
        rec = dict(ROEDIGER, author=[{"given": "Henry L.", "sequence": "first"},
                                     ROEDIGER["author"][1]])
        registry.crossref(rec)
        code, out = _run_main(monkeypatch, capsys, tmp_path, _bib())
        assert "(not checked: first author (registry has no surname))" in out
        assert "not checked=1" in out

    def test_count_is_zero_when_everything_was_checked(self, registry, monkeypatch, capsys,
                                                       tmp_path):
        registry.crossref(ROEDIGER)
        code, out = _run_main(monkeypatch, capsys, tmp_path, _bib())
        assert "(not checked" not in out
        assert out.rstrip().endswith("summary: OK=1; not checked=0")


class TestOnlineOnlyYear:
    """F3: a bib year far from every print year passes (OnlineFirst lags are
    real) but is reported, not silently accepted."""

    def test_online_year_far_from_print_is_reported(self, registry):
        rec = dict(MILLMAN, **{"published-online": {"date-parts": [[2006, 5]]}})
        registry.crossref(rec)
        r = registry.run(MILLMAN_BIB.replace("{1965}", "{2006}"))
        assert r["status"] == "OK"                           # not failed ...
        assert any("online" in u and "41 years from print" in u for u in r["unchecked"]), r
        r = registry.run(MILLMAN_BIB)                        # ... and silent for the print year
        assert r["status"] == "OK" and not r["unchecked"]

    def test_one_year_lag_is_not_reported(self, registry):
        registry.crossref(FAN_BJET)
        r = registry.run(TestYearFields()._fan("2024"))      # online 2024, print 2025
        assert r["status"] == "OK" and not r["unchecked"]

    def test_two_year_lag_is_reported(self, registry):
        rec = dict(RIVERS, **{"published-online": {"date-parts": [[2015, 10, 30]]}})
        registry.crossref(rec)
        r = registry.run(_bib(author="Rivers, Kelly and Koedinger, Kenneth R.",
                              title="Data-driven hint generation in vast solution spaces: "
                                    "A self-improving {Python} programming tutor",
                              year="2015", doi="10.1007/s40593-015-0070-z"))
        assert r["status"] == "OK"
        assert any("2 years from print" in u for u in r["unchecked"]), r

    def test_no_print_date_means_no_note(self, registry):
        registry.crossref(DAWSON_BOOK)                       # online only
        bib = _bib(author="Dawson, Phillip", title="Defending assessment security in a digital world",
                   year="2020", doi="10.4324/9780429324178", kind="book")
        assert not registry.run(bib)["unchecked"]

    def test_year_outside_the_set_still_fails(self, registry):
        rec = dict(MILLMAN, **{"published-online": {"date-parts": [[2006, 5]]}})
        registry.crossref(rec)
        assert registry.run(MILLMAN_BIB.replace("{1965}", "{1990}"))["status"] == "MISMATCH"


class TestRegistryOnlyMarkup:
    """F4: HTML tags and entities are registry-side markup; on the bib side
    "<...>" is text that renders, so it must be compared, not deleted."""

    @pytest.mark.parametrize("registry_s, bib_s", [
        ("<i>In vivo</i> tutoring", "In vivo tutoring"),
        ("Computers &amp; Education", "Computers \\& Education"),
        ("H<sub>2</sub>O and <mml:math><mml:mi>x</mml:mi></mml:math>", "H2O and x"),
    ])
    def test_registry_markup_is_removed(self, registry_s, bib_s):
        assert V._norm(V._registry_text(registry_s)) == V._norm(bib_s)

    def test_bib_side_angle_brackets_are_text(self):
        assert V._norm("memory <b and wrong words> tests") != V._norm("memory tests")

    def test_bib_angle_bracket_insertion_fails(self, registry):
        registry.crossref(ROEDIGER)
        r = registry.run(_bib(title="Test-enhanced learning: Taking memory <b and wrong words> "
                                    "tests improves long-term retention"))
        assert r["status"] == "MISMATCH"

    def test_registry_title_with_tags_matches(self, registry):
        registry.crossref(dict(ROEDIGER, title=["Test-Enhanced <i>Learning</i>"]))
        assert registry.run(_bib())["status"] == "OK"

    def test_registry_surname_with_entity_matches(self, registry):
        rec = dict(ROEDIGER, author=[{"given": "Henry L.", "family": "Roediger &amp; Co",
                                      "sequence": "first"}, ROEDIGER["author"][1]])
        registry.crossref(rec)
        assert registry.run(_bib(author="{Roediger \\& Co}, Henry L. and Karpicke, Jeffrey D."))[
            "status"] == "OK"

    def test_tex_logo_is_a_word_not_deleted(self, registry):
        assert V._norm("\\LaTeX") == "latex"
        assert V._norm("\\TeX") == "tex"
        assert V._norm("\\BibTeX") == "bibtex"
        registry.crossref(ROEDIGER)
        r = registry.run(_bib(title="Test-enhanced learning: Taking memory tests \\LaTeX\\ "
                                    "improves long-term retention"))
        assert r["status"] == "MISMATCH"

    def test_other_commands_still_dropped(self):
        assert V._norm("\\emph{In vivo} tutoring") == V._norm("In vivo tutoring")


class TestSurnameRunEndsTheName:
    """F5: the registry surname must END the bib surname; anything before it
    must be a particle (van, de, ...)."""

    @pytest.mark.parametrize("registry_s, bib_s", [
        ("Braak", "van Braak"),
        ("Braak", "van der Braak"),
        ("Rosa", "de la Rosa"),
        ("Dinucu-Jianu", "Dinucu-Jianu"),
        ("Roediger", "Roediger"),
    ])
    def test_matches(self, registry_s, bib_s):
        assert V._surname_matches(registry_s, bib_s)

    @pytest.mark.parametrize("registry_s, bib_s", [
        ("Roediger", "Roediger-Smith"),
        ("Roediger", "Roediger Smith"),
        ("Roediger", "Smith Roediger"),        # a leading token that is not a particle
        ("Roediger", "Smith-Roediger"),
        ("Braak", "van Braaks"),
    ])
    def test_does_not_match(self, registry_s, bib_s):
        assert not V._surname_matches(registry_s, bib_s)

    @pytest.mark.parametrize("surname", ["Roediger-Smith", "Smith Roediger", "{Roediger Smith}"])
    def test_genuinely_different_first_author_fails(self, registry, surname):
        registry.crossref(ROEDIGER)
        r = registry.run(_bib(author=f"{surname}, Henry L. and Karpicke, Jeffrey D."))
        assert r["status"] == "MISMATCH" and any(n.startswith("first author") for n in r["notes"])
