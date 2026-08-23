"""Verify every .bib entry against Crossref (and DataCite for arXiv DOIs).

Rationale, and it is not hypothetical for this project: the source thesis's
reference list contains four demonstrable mis-citations, entries whose author
lists are corrupted, and an ORCID attributed to the wrong person. Citations
assembled by any process -- human or model -- must be checked against the
registry rather than trusted.

Checks per entry: DOI resolves; first-author surname matches; title matches
after normalisation; year matches. Anything else is reported, never silently
corrected.

Usage:
    python3 verify_refs.py ../source/bibliography.bib
    python3 verify_refs.py --doi 10.18653/v1/2025.acl-long.782
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request

UA = "paper-els-15-refcheck/1.0 (mailto:semerikov@gmail.com)"


def _norm(s: str) -> str:
    s = re.sub(r"[{}\\]", "", s or "")
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def fetch_crossref(doi: str) -> dict | None:
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())["message"]
    except Exception:
        return None


def fetch_datacite(doi: str) -> dict | None:
    """arXiv DOIs (10.48550/*) are registered with DataCite, not Crossref."""
    url = f"https://api.datacite.org/dois/{urllib.parse.quote(doi)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())["data"]["attributes"]
    except Exception:
        return None


#: One bibtex field. The value alternative handles one level of nested braces
#: (``{LLM}-as-a-Judge``), and the terminator is comma-or-end so that the LAST
#: field of an entry is not skipped -- doi is conventionally last, so a
#: newline-terminated pattern silently reports every entry as having no DOI.
_FIELD = re.compile(
    r"(\w+)\s*=\s*(?:"
    r"\{((?:[^{}]|\{[^{}]*\})*)\}"      # {braced value}
    r"|\"([^\"]*)\""                     # "quoted value"
    r"|(\d+)"                            # bare number, e.g. year = 2025
    r")\s*(?:,|$)",
    re.S | re.M,
)


def parse_bib(text: str) -> list[dict]:
    entries = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,]+),(.*?)\n\}", text, re.S):
        kind, key, body = m.group(1), m.group(2).strip(), m.group(3)
        fields = {}
        for fm in _FIELD.finditer(body):
            value = next((g for g in fm.groups()[1:] if g is not None), "")
            fields[fm.group(1).lower()] = " ".join(value.split())
        entries.append({"kind": kind, "key": key, "fields": fields})
    return entries


def check(entry: dict) -> dict:
    f = entry["fields"]
    doi = f.get("doi", "").strip()
    out = {"key": entry["key"], "doi": doi, "status": "", "notes": []}

    if not doi:
        out["status"] = "NO_DOI"
        if not (f.get("url") or f.get("issn")):
            out["notes"].append("no DOI, URL or ISSN — unverifiable as cited")
        return out

    meta = fetch_datacite(doi) if doi.lower().startswith("10.48550") else fetch_crossref(doi)
    if meta is None:
        out["status"] = "UNRESOLVED"
        return out

    reg_title = ""
    if isinstance(meta.get("title"), list) and meta["title"]:
        reg_title = meta["title"][0]
    elif isinstance(meta.get("titles"), list) and meta["titles"]:
        reg_title = meta["titles"][0].get("title", "")
    if reg_title and f.get("title"):
        if _norm(reg_title)[:60] != _norm(f["title"])[:60]:
            out["notes"].append(f"title differs: registry={reg_title!r}")

    authors = meta.get("author") or meta.get("creators") or []
    if authors and f.get("author"):
        reg_first = (authors[0].get("family")
                     or authors[0].get("familyName")
                     or authors[0].get("name", "")).split(",")[0]
        bib_first = f["author"].split(" and ")[0]
        bib_surname = (bib_first.split(",")[0] if "," in bib_first
                       else bib_first.split()[-1])
        if _norm(reg_first) and _norm(reg_first) not in _norm(bib_surname):
            out["notes"].append(
                f"first author differs: registry={reg_first!r} bib={bib_surname!r}")
        if len(authors) != len(f["author"].split(" and ")):
            out["notes"].append(
                f"author count: registry={len(authors)} bib={len(f['author'].split(' and '))}")

    reg_year = None
    if "published" in meta:
        reg_year = meta["published"]["date-parts"][0][0]
    elif "issued" in meta:
        reg_year = meta["issued"]["date-parts"][0][0]
    elif "publicationYear" in meta:
        reg_year = meta["publicationYear"]
    if reg_year and f.get("year") and str(reg_year) != str(f["year"]):
        out["notes"].append(f"year: registry={reg_year} bib={f['year']}")

    out["status"] = "MISMATCH" if out["notes"] else "OK"
    out["registry_title"] = reg_title
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("bib", nargs="?")
    ap.add_argument("--doi", action="append", default=[])
    args = ap.parse_args()

    results = []
    if args.doi:
        for d in args.doi:
            # Same registry split as check(): arXiv DOIs are DataCite, not
            # Crossref. Without the fallback this path reports every arXiv
            # preprint as UNRESOLVED, which reads as "fabricated" rather than
            # "looked in the wrong registry".
            meta = (fetch_datacite(d) if d.lower().startswith("10.48550")
                    else fetch_crossref(d))
            if not meta:
                print(f"UNRESOLVED  {d}")
                continue
            title = ""
            if isinstance(meta.get("title"), list) and meta["title"]:
                title = meta["title"][0]
            elif isinstance(meta.get("titles"), list) and meta["titles"]:
                title = meta["titles"][0].get("title", "")
            auth = meta.get("author") or meta.get("creators") or []
            # NEVER truncate: this output is copied into .bib entries, and a
            # silently shortened author list is exactly the mis-citation this
            # tool exists to catch.
            names = " and ".join(
                (f"{a['family']}, {a.get('given','')}".strip(", ")
                 if a.get("family") else
                 f"{a.get('familyName','')}, {a.get('givenName','')}".strip(", ")
                 or a.get("name", "")) for a in auth)
            # Crossref gives {"date-parts": [[YYYY,...]]}; DataCite gives a bare
            # string for the same key, so this must not assume a dict.
            issued = meta.get("published") or meta.get("issued")
            if isinstance(issued, dict):
                year = issued["date-parts"][0][0]
            else:
                year = issued or meta.get("publicationYear", "")
            ctr = meta.get("container-title") or [""]
            print(f"OK  {d}\n    {title}\n    n_authors={len(auth)}\n    {names}\n"
                  f"    {year}  {ctr[0] if ctr else ''}")
            time.sleep(0.3)
        return

    if not args.bib:
        sys.exit("give a .bib path or --doi")
    entries = parse_bib(open(args.bib).read())
    print(f"{len(entries)} entries in {args.bib}\n")
    for e in entries:
        r = check(e)
        results.append(r)
        print(f"{r['status']:<11} {r['key']}")
        for n in r["notes"]:
            print(f"            - {n}")
        time.sleep(0.3)

    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("\nsummary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if counts.get("MISMATCH") or counts.get("UNRESOLVED"):
        sys.exit(1)


if __name__ == "__main__":
    main()
