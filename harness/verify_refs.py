"""Verify every .bib entry against Crossref (and DataCite for arXiv DOIs).

Rationale, and it is not hypothetical for this project: the source thesis's
reference list contains four demonstrable mis-citations, entries whose author
lists are corrupted, and an ORCID attributed to the wrong person. Citations
assembled by any process -- human or model -- must be checked against the
registry rather than trusted.

Checks per entry with a DOI: the DOI resolves; the first-author surname
matches; the author count matches; the WHOLE title matches the registry title,
or title + ": " + subtitle (Crossref keeps subtitles in their own field); the
year is one of the registry's publication years (print, online, issued, journal
issue -- never its metadata dates). Names and titles are compared after folding
accents and TeX escapes on both sides, so D{\\'\\i}az, Diaz and the registry's
UTF-8 spelling are one name. Anything else is reported, never silently
corrected, and nothing is silently skipped: a DOI entry whose title, author or
year the parser cannot read fails, an entry the parser cannot close fails, a
repeated field or a repeated key fails (BibTeX prints the FIRST copy, so a later
correct copy must not vouch for it), and a check the REGISTRY cannot support is
printed as "not checked" and counted in the summary line. A bib year that passes
only because it is far (> 1 year) from every print year is also listed there.

Exit status 1 if any entry is MISMATCH, UNRESOLVED or UNPARSED.

Usage:
    python3 verify_refs.py ../source/bibliography.bib
    python3 verify_refs.py --doi 10.18653/v1/2025.acl-long.782
"""

from __future__ import annotations

import argparse
import html
import os
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

#: Crossref asks clients for a contact address. The deposit carries none: set
#: VERIFY_REFS_MAILTO to your own address to be routed to the "polite" pool.
UA = "refcheck/1.0" + (f" (mailto:{os.environ['VERIFY_REFS_MAILTO']})"
                       if os.environ.get("VERIFY_REFS_MAILTO") else "")

#: Lookups per DOI. Only transient failures are retried (HTTP 429 or 5xx, a
#: timeout, a dropped connection); a 404 means "not registered here" and is
#: final. Without retries a rate-limited run reports real works as UNRESOLVED,
#: which reads as "fabricated".
_ATTEMPTS = 3
_BACKOFF_S = 2.0

#: Statuses that fail the run.
FAILING = ("MISMATCH", "UNRESOLVED", "UNPARSED")


# --------------------------------------------------------------------------
# Registry access
# --------------------------------------------------------------------------

def _get_json(url: str) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:      # before URLError: it is a subclass
            if e.code != 429 and e.code < 500:
                return None
        except (urllib.error.URLError, OSError):  # timeouts and dropped connections
            pass
        except Exception:                        # malformed payload: not transient
            return None
        if attempt + 1 < _ATTEMPTS:
            time.sleep(_BACKOFF_S * (attempt + 1))
    return None


def fetch_crossref(doi: str) -> dict | None:
    data = _get_json(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}")
    try:
        return data["message"] if data else None
    except (KeyError, TypeError):
        return None


def fetch_datacite(doi: str) -> dict | None:
    """arXiv DOIs (10.48550/*) are registered with DataCite, not Crossref."""
    data = _get_json(f"https://api.datacite.org/dois/{urllib.parse.quote(doi)}")
    try:
        return data["data"]["attributes"] if data else None
    except (KeyError, TypeError):
        return None


def is_datacite_doi(doi: str) -> bool:
    return doi.strip().lower().startswith("10.48550/")


def fetch(doi: str) -> dict | None:
    """The one place that decides which registry a DOI belongs to."""
    return fetch_datacite(doi) if is_datacite_doi(doi) else fetch_crossref(doi)


# --------------------------------------------------------------------------
# Normalisation: one rule for both sides of every comparison
# --------------------------------------------------------------------------

_TAG = re.compile(r"</?[A-Za-z][\w:.-]*(?:\s[^<>]*)?/?>")          # <i>, <sub>, <mml:math ...>
_TEX_ACCENT = re.compile(r"\\[`'^\"~=.]|\\[uvHcdbrkt](?![A-Za-z])\s*")  # \' \" \v s \c{c} ...
_TEX_LETTERS = {"ss": "ss", "ae": "ae", "AE": "AE", "oe": "oe", "OE": "OE", "aa": "a",
                "AA": "A", "o": "o", "O": "O", "l": "l", "L": "L", "i": "i", "j": "j"}
_TEX_LETTER = re.compile(r"\\(ss|ae|AE|oe|OE|aa|AA|o|O|l|L|i|j)(?![A-Za-z])\s*")
_TEX_SYMBOL = re.compile(r"\\([&%$#_{}])")
_TEX_INVISIBLE = re.compile(r"\\[-/]")                              # \- and \/
_TEX_LOGO = re.compile(r"\\(BibTeX|LaTeX|TeX)(?![A-Za-z])")          # words, not markup
_TEX_COMMAND = re.compile(r"\\[A-Za-z]+\s*")                         # \emph, \textit, ...
#: Letters NFKD does not decompose, mapped as their TeX spellings fold above.
_UNDECOMPOSABLE = str.maketrans({"ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "ß": "ss",
                                 "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "đ": "d",
                                 "Đ": "D", "ı": "i", "ð": "d", "Ð": "D", "þ": "th",
                                 "Þ": "Th"})


def _registry_text(s: str) -> str:
    """Registry-side markup only: Crossref titles carry HTML entities and
    JATS/MathML tags (<i>, <sub>, <mml:math>). On the bib side "<...>" is text
    that renders, so stripping it there would hide inserted words. Tags are
    inline, so they are removed without a space: "H<sub>2</sub>O" is "H2O"."""
    return _TAG.sub("", html.unescape(s or ""))


def _norm(s: str) -> str:
    """Fold a bib or registry string to a comparison key (both sides; registry
    strings go through _registry_text first).

    TeX escapes and accents are folded BEFORE braces are dropped, so
    ``D{\\'\\i}az`` becomes "diaz" (not "d iaz"); the registry's ``Díaz`` loses
    its combining mark (not the whole letter). Letters of any script are kept:
    erasing non-ASCII text would make two different Cyrillic titles equal.
    \\LaTeX, \\TeX and \\BibTeX are words and are kept; other commands are dropped.
    """
    s = s or ""
    s = _TEX_ACCENT.sub("", s)
    s = _TEX_LETTER.sub(lambda m: _TEX_LETTERS[m.group(1)], s)
    s = _TEX_SYMBOL.sub(r"\1", s)
    s = _TEX_INVISIBLE.sub("", s)
    s = _TEX_LOGO.sub(r"\1", s)
    s = _TEX_COMMAND.sub("", s)
    s = re.sub(r"[{}\\]", "", s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.translate(_UNDECOMPOSABLE).casefold()
    s = re.sub(r"[\W_]+", " ", s)
    return s.strip()


# --------------------------------------------------------------------------
# What the registry says (shared by check() and --doi)
# --------------------------------------------------------------------------

def registry_titles(meta: dict) -> list[str]:
    """Every form of the registry title a correct citation may carry: the
    title, and title + ": " + subtitle. Crossref: ``title`` and ``subtitle``
    lists. DataCite: ``titles`` items, no titleType = title, "Subtitle" =
    subtitle; translated and alternative titles are NOT accepted."""
    mains: list[str] = []
    subs: list[str] = []
    if isinstance(meta.get("title"), list):
        mains = [t for t in meta["title"] if isinstance(t, str) and t.strip()]
        subs = [t for t in meta.get("subtitle") or [] if isinstance(t, str) and t.strip()]
    elif isinstance(meta.get("titles"), list):
        for t in meta["titles"]:
            if not isinstance(t, dict) or not str(t.get("title") or "").strip():
                continue
            if t.get("titleType") is None:
                mains.append(t["title"])
            elif t.get("titleType") == "Subtitle":
                subs.append(t["title"])
    forms = []
    for m in mains:
        forms.append(m)
        forms.extend(f"{m}: {s}" for s in subs)
    return forms


#: The ONLY fields a year is read from. Crossref also carries created,
#: deposited and indexed: metadata dates, usually this year, which would wave
#: through any entry dated this year. DataCite's ``dates`` list is not read.
_YEAR_FIELDS = ("published-print", "published-online", "issued", "published",
                "publicationYear")
_ISSUE_YEAR_FIELDS = ("published-print", "published-online")


def _year_of(value) -> int | None:
    """A year from either registry shape: Crossref {"date-parts": [[Y, ...]]},
    DataCite "2026" / "2026-08-01T..." / 2026."""
    if isinstance(value, dict):
        try:
            return _year_of(value["date-parts"][0][0])
        except (KeyError, IndexError, TypeError):
            return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        m = re.match(r"\s*(\d{4})", value)
        return int(m.group(1)) if m else None
    return None


def registry_years(meta: dict) -> set[int]:
    values = [meta.get(k) for k in _YEAR_FIELDS]
    issue = meta.get("journal-issue")
    if isinstance(issue, dict):
        values += [issue.get(k) for k in _ISSUE_YEAR_FIELDS]
    return {y for y in map(_year_of, values) if y}


def registry_print_years(meta: dict) -> set[int]:
    values = [meta.get("published-print")]
    issue = meta.get("journal-issue")
    if isinstance(issue, dict):
        values.append(issue.get("published-print"))
    return {y for y in map(_year_of, values) if y}


#: A bib year more than this far from every print year is listed as "not
#: checked" (it passed only on an online/issued date). Not a failure:
#: OnlineFirst lags of 2+ years are real.
_ONLINE_LAG_NOTE = 1


def registry_authors(meta: dict) -> list[dict]:
    return meta.get("author") or meta.get("creators") or []


def _registry_surname(a: dict) -> str:
    return _registry_text(
        (a.get("family") or a.get("familyName") or a.get("name") or "").split(",")[0])


# --------------------------------------------------------------------------
# Bib side
# --------------------------------------------------------------------------

def _split_depth0(s: str, sep: re.Pattern) -> list[str]:
    """Split ``s`` on ``sep`` only where it occurs outside braces."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(s):
        c = s[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif depth == 0:
            m = sep.match(s, i)
            if m and m.end() > i:
                parts.append(s[start:i])
                start = i = m.end()
                continue
        i += 1
    parts.append(s[start:])
    return [p.strip() for p in parts]


_AND = re.compile(r"\s+and\s+", re.I)


def bib_authors(field: str) -> list[str]:
    return [a for a in _split_depth0(field, _AND) if a]


def bib_surname(name: str) -> str:
    """"Last, First" -> Last; "First Last" -> the last brace-aware token."""
    head = _split_depth0(name, re.compile(","))
    if len(head) > 1:
        return head[0]
    tokens = _split_depth0(name, re.compile(r"\s+"))
    return tokens[-1] if tokens else ""


#: Name particles that may precede the registry surname in the bib surname
#: ("van Braak", "de la Rosa"). Any other extra word is a different name.
_PARTICLES = frozenset({"van", "von", "der", "den", "de", "del", "della", "dei", "degli",
                        "di", "da", "das", "dos", "du", "des", "le", "la", "les", "ter",
                        "ten", "zu", "zur", "bin", "ibn", "al", "el", "st", "y"})


def _surname_matches(registry: str, bib: str) -> bool:
    """The registry surname's words must END the bib surname, and any bib words
    before them must be particles: "Braak" matches "van Braak"; "Roediger" does
    not match "Roediger-Smith", "Smith Roediger" or "Roediger Smith"; "Li" does
    not match "Lin". Stricter than the old substring test, which let "Fan" pass
    "Fang"."""
    r, b = _norm(registry).split(), _norm(bib).split()
    if not r or not b or len(r) > len(b):
        return False
    lead = b[:len(b) - len(r)]
    return b[len(b) - len(r):] == r and all(t in _PARTICLES for t in lead)


# --------------------------------------------------------------------------
# Parsing: a brace-depth scanner, any nesting level
# --------------------------------------------------------------------------

_ENTRY = re.compile(r"@\s*(\w+)\s*([{(])")
_FIELD_NAME = re.compile(r"\s*([A-Za-z_][\w:.+-]*)\s*=\s*")
_BARE = re.compile(r"[\w.:+/-]+")
_CONCAT = re.compile(r"\s*#\s*")
_AFTER_VALUE = re.compile(r"\s*(,|$)")
_NOT_ENTRIES = {"comment", "string", "preamble"}
#: A field-shaped line OUTSIDE any entry: what an extra '}' leaves behind when
#: it closes an entry early (the DOI after it would otherwise vanish unseen).
_STRAY_FIELD = re.compile(r"^[ \t]*[A-Za-z_][\w:.+-]*[ \t]*=[ \t]*[{\"\d]", re.M)


def _close_brace(s: str, i: int) -> int:
    """Index of the brace closing the one opened at s[i], or -1."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _close_paren_entry(s: str, i: int) -> int:
    depth = 0
    for j in range(i + 1, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
        elif s[j] == ")" and depth == 0:
            return j
    return -1


def _close_quote(s: str, i: int) -> int:
    """Index of the '"' ending the value opened at s[i]; braces must balance
    inside, so a '"' within braces does not end it."""
    depth = 0
    for j in range(i + 1, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
        elif s[j] == '"' and depth == 0:
            return j
    return -1


def _parse_fields(body: str) -> tuple[dict, str | None]:
    """Fields of one entry body (the text after ``key,``). Returns the fields
    read and an error message if the body is not well formed."""
    fields: dict[str, str] = {}
    i, n = 0, len(body)
    while True:
        rest = body[i:]
        if not rest.strip(" \t\r\n,"):
            return fields, None
        m = _FIELD_NAME.match(body, i)
        if not m:
            return fields, f"cannot read a field at {rest.strip()[:40]!r}"
        name, i = m.group(1).lower(), m.end()
        if name in fields:
            # BibTeX keeps the FIRST value ("I'm ignoring K's extra field"):
            # a later, correct copy must not vouch for the one that renders.
            return fields, f"field {name} repeated"
        parts = []
        while True:
            if i < n and body[i] == "{":
                j = _close_brace(body, i)
                if j < 0:
                    return fields, f"field {name}: unbalanced braces"
                parts.append(body[i + 1:j])
                i = j + 1
            elif i < n and body[i] == '"':
                j = _close_quote(body, i)
                if j < 0:
                    return fields, f"field {name}: unterminated quoted value"
                parts.append(body[i + 1:j])
                i = j + 1
            else:
                v = _BARE.match(body, i)
                if not v:
                    return fields, f"field {name}: no value"
                parts.append(v.group(0))
                i = v.end()
            c = _CONCAT.match(body, i)
            if not c:
                break
            i = c.end()
        fields[name] = " ".join("".join(parts).split())
        a = _AFTER_VALUE.match(body, i)
        if not a:
            return fields, f"field {name}: expected ',' after the value, got {body[i:i + 20]!r}"
        i = a.end()


def parse_bib(text: str) -> list[dict]:
    """Entries in file order: {"kind", "key", "fields"}, plus "error" when an
    entry cannot be read. A broken entry is reported, never dropped, and the
    scan resumes at the next "@" so one bad entry does not hide the rest."""
    entries = []
    seen: set[str] = set()           # keys, casefolded: BibTeX's "Repeated entry"
    pos = 0
    prev = None                      # the entry the text at `pos` follows
    while True:
        m = _ENTRY.search(text, pos)
        gap = text[pos:m.start() if m else len(text)]
        if prev is not None and not prev.get("error") and _STRAY_FIELD.search(gap):
            prev["error"] = "a field follows the entry's closing brace (an extra '}'?)"
        if not m:
            return entries
        kind, opener = m.group(1), m.end() - 1
        end = (_close_brace(text, opener) if m.group(2) == "{"
               else _close_paren_entry(text, opener))
        if kind.lower() in _NOT_ENTRIES:
            pos = end + 1 if end >= 0 else m.end()
            prev = None
            continue
        if end < 0:
            key = text[m.end():].split(",", 1)[0].strip()[:80]
            prev = {"kind": kind, "key": key, "fields": {},
                    "error": "entry never closes (unbalanced braces)"}
            entries.append(prev)
            pos = m.end()
            continue
        body = text[m.end():end]
        key, comma, rest = body.partition(",")
        fields, err = _parse_fields(rest) if comma else ({}, None)
        prev = {"kind": kind, "key": key.strip(), "fields": fields}
        if err:
            prev["error"] = err
        elif prev["key"].casefold() in seen:
            prev["error"] = (f"key {prev['key']!r} repeated: BibTeX prints the first "
                             "entry with this key and ignores this one")
        seen.add(prev["key"].casefold())
        entries.append(prev)
        pos = end + 1


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------

def check(entry: dict) -> dict:
    f = entry["fields"]
    doi = f.get("doi", "").strip()
    out = {"key": entry["key"], "doi": doi, "status": "", "notes": [], "unchecked": []}

    if entry.get("error"):
        out["status"] = "UNPARSED"
        out["notes"].append(entry["error"])
        return out

    if not doi:
        out["status"] = "NO_DOI"
        if not (f.get("url") or f.get("issn")):
            out["notes"].append("no DOI, URL or ISSN — unverifiable as cited")
        return out

    meta = fetch(doi)
    if meta is None:
        out["status"] = "UNRESOLVED"
        return out

    # A DOI entry the parser could not fully read must not pass by default.
    for field in ("title", "author", "year"):
        if not f.get(field, "").strip():
            out["notes"].append(f"bib entry has no readable {field} field")

    titles = [_registry_text(t) for t in registry_titles(meta)]
    if not titles:
        out["unchecked"].append("title (registry has none)")
    elif f.get("title", "").strip():
        if not _norm(f["title"]) or _norm(f["title"]) not in {_norm(t) for t in titles}:
            out["notes"].append(f"title differs: registry={max(titles, key=len)!r}")

    authors = registry_authors(meta)
    if not authors:
        out["unchecked"].append("authors (registry has none)")
    elif f.get("author", "").strip():
        bib = bib_authors(f["author"])
        reg_first = _registry_surname(authors[0])
        bib_first = bib_surname(bib[0]) if bib else ""
        if not _norm(reg_first):
            out["unchecked"].append("first author (registry has no surname)")
        elif not _surname_matches(reg_first, bib_first):
            out["notes"].append(
                f"first author differs: registry={reg_first!r} bib={bib_first!r}")
        if len(authors) != len(bib):
            out["notes"].append(f"author count: registry={len(authors)} bib={len(bib)}")

    years = registry_years(meta)
    bib_year = f.get("year", "").strip()
    if not years:
        out["unchecked"].append("year (registry has no publication date)")
    elif bib_year and (not bib_year.isdigit() or int(bib_year) not in years):
        out["notes"].append(
            f"year: registry={'/'.join(map(str, sorted(years)))} bib={bib_year}")
    elif bib_year:
        prints = registry_print_years(meta)
        lag = min((abs(int(bib_year) - p) for p in prints), default=0)
        if lag > _ONLINE_LAG_NOTE:
            out["unchecked"].append(
                f"year {bib_year} matches an online/issued date only, {lag} years from print "
                f"({'/'.join(map(str, sorted(prints)))})")

    out["status"] = "MISMATCH" if out["notes"] else "OK"
    out["registry_title"] = max(titles, key=len) if titles else ""
    return out


def _format_names(authors: list[dict]) -> str:
    # NEVER truncate: this output is copied into .bib entries, and a silently
    # shortened author list is exactly the mis-citation this tool exists to catch.
    return " and ".join(
        (f"{a['family']}, {a.get('given', '')}".strip(", ")
         if a.get("family") else
         f"{a.get('familyName', '')}, {a.get('givenName', '')}".strip(", ")
         or a.get("name", "")) for a in authors)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("bib", nargs="?")
    ap.add_argument("--doi", action="append", default=[])
    args = ap.parse_args()

    results = []
    if args.doi:
        for d in args.doi:
            meta = fetch(d)
            if not meta:
                print(f"UNRESOLVED  {d}")
                continue
            titles = [_registry_text(t) for t in registry_titles(meta)]
            auth = registry_authors(meta)
            years = "/".join(map(str, sorted(registry_years(meta))))
            ctr = meta.get("container-title") or [""]
            print(f"OK  {d}\n    {max(titles, key=len) if titles else ''}\n"
                  f"    n_authors={len(auth)}\n    {_format_names(auth)}\n"
                  f"    {years}  {ctr[0] if ctr else ''}")
            time.sleep(0.3)
        return

    if not args.bib:
        sys.exit("give a .bib path or --doi")
    with open(args.bib, encoding="utf-8") as fh:
        entries = parse_bib(fh.read())
    print(f"{len(entries)} entries in {args.bib}\n")
    for e in entries:
        r = check(e)
        results.append(r)
        print(f"{r['status']:<11} {r['key']}")
        for n in r["notes"]:
            print(f"            - {n}")
        for u in r["unchecked"]:
            print(f"            (not checked: {u})")
        if r["doi"]:
            time.sleep(0.3)

    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    unchecked = sum(len(r["unchecked"]) for r in results)
    print("\nsummary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
          + f"; not checked={unchecked}")
    if any(counts.get(s) for s in FAILING):
        sys.exit(1)


if __name__ == "__main__":
    main()
