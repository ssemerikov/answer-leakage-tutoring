"""Freeze the post-screen bank: retained items + provenance, hashed.

Runs only on a COMPLETE screen. The frozen file is what the study consumes and
what gets deposited; the pre-screen bank is released alongside it so the discard
rule can be audited rather than taken on trust.

The frozen file is a SECOND COPY of the retention decision: `run.screened_items`
derives retention live from the screen jsonl, and this derives it again into a
deposited artefact. That is the shape of every bug in §4.8-§4.17, so the two
copies are tied together explicitly -- `screen_sha256` records the bytes this
freeze actually read, and `verify_numbers` fails when the screen on disk has
moved away from it.

Usage:
    python3 freeze_bank.py --bank A
    python3 freeze_bank.py --bank A --roster ol --force   # supersede
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date

import config as C
import run as R
import solvability as S


def freeze(bank: str, rost: str = "kr", force: bool = False,
           prefix: bool = False) -> None:
    # Resolve through run._bank_path, never by rebuilding the name here: bank
    # id `bs` maps to bank_b_sample.json, and a second copy of that rule would
    # look for a bank_bs.json that does not exist. Two places deriving the same
    # filename is how `stage_solve` came to read the wrong roster's screen.
    src = R._bank_path(bank)
    data = json.loads(src.read_bytes())
    pre_hash = hashlib.sha256(src.read_bytes()).hexdigest()

    screen_path = R._jsonl_path("screen", bank, rost)
    rows = [json.loads(l) for l in screen_path.read_text().splitlines() if l.strip()]
    verdict = {r["item_id"]: r for r in rows}

    mcq = [i for i in data["items"] if i.get("type") == "mcq"]
    missing = [i["id"] for i in mcq if i["id"] not in verdict]
    if missing and not prefix:
        sys.exit(f"refusing to freeze: {len(missing)} MCQ items unscreened "
                 f"(pass --prefix to freeze the screened prefix instead)")
    if missing:
        # A PREFIX IS ONLY DEFENSIBLE IF IT IS ACTUALLY A PREFIX. The Bank B
        # sample is drawn once in a seed-locked order and every *prefix* of that
        # order is stratified -- which is the entire argument for screening part
        # of it. An arbitrary screened SUBSET carries no such guarantee, and the
        # difference is invisible once the verdicts are in a dict. The screen is
        # consumed in order, so this holds in practice; it is checked because the
        # claim the manuscript makes depends on it, not because it is doubted.
        order = [i["id"] for i in mcq]
        screened_ranks = [n for n, iid in enumerate(order) if iid in verdict]
        if screened_ranks != list(range(len(screened_ranks))):
            first_gap = next(n for n, r in enumerate(screened_ranks) if r != n)
            sys.exit(
                f"refusing to freeze: the screened items are not a prefix of the "
                f"drawn order -- item at position {first_gap} is unscreened while "
                f"later ones are screened. Every prefix of the drawn order is "
                f"stratified; an arbitrary subset is not, and the retention rate "
                f"could not be read as the sample's.")

    # The screen must have run against THIS bank, not an earlier edit of it --
    # but "the same bank" is a question about the MEASUREMENT, not about the
    # bytes. `author_forms` (§4.38) rewrote `answer_forms` on both banks after
    # they were screened: metadata that the screen never reads, that
    # `measurement_signature` deliberately excludes, and that changed every
    # bank file's sha256 all the same. A byte comparison alone therefore refuses
    # to freeze a bank whose measurements are perfectly valid, which is the same
    # inability §4.30 found in the other direction -- a hash that cannot tell a
    # metadata repair from a measurement change is wrong about one of them.
    #
    # So: bytes first, and if they differ, ask the signature. Both hashes are
    # recorded either way, so the deposited file shows exactly what was screened
    # and what was frozen.
    sig_now = C.measurement_signature(data["items"])
    ran_against = {r.get("bank_sha256") for r in rows if r.get("bank_sha256")}
    screened_sigs = {r.get("bank_measurement_sig") for r in rows}
    metadata_only = False
    if ran_against != {pre_hash}:
        if screened_sigs == {sig_now}:
            metadata_only = True
            print(f"note: bank bytes changed since the screen "
                  f"({sorted(ran_against)[0][:8]}... -> {pre_hash[:8]}...) but the "
                  f"measurement signature is unchanged ({sig_now[:8]}...), so the "
                  f"verdicts still describe these items. Freezing.", flush=True)
        else:
            sys.exit(
                f"refusing to freeze: screen ran against {ran_against}, bank is "
                f"now {pre_hash}, and the measurement signature does NOT match "
                f"(screen: {sorted(str(x) for x in screened_sigs)}, bank: "
                f"{sig_now}). The items themselves changed; re-screen.")

    # ...and under ONE study version. The bank guard above catches a changed
    # item; this catches a changed instrument. §4.28 changed a solver's token
    # budget without touching a single item, so every `bank_sha256` matched
    # while half the rows had been measured through a truncating budget and
    # half had not -- two populations in one file, invisible to the guard that
    # exists to prevent exactly that. Resumability makes this the DEFAULT
    # outcome of fixing a budget mid-screen, not an unlucky one.
    versions = {r.get("study_version") for r in rows}
    if len(versions) != 1:
        sys.exit(
            f"refusing to freeze: screen mixes study versions "
            f"{sorted(str(v) for v in versions)}. Rows measured under "
            f"different instruments are not one population. Quarantine the "
            f"file and re-screen; do not freeze across the boundary.")

    # ...and every retained verdict must be out of reach of its own failed
    # calls. A failed call is scored as a miss, so it can only have depressed a
    # baseline, which biases the screen toward wrongly RETAINING items near the
    # threshold -- the exact defect that made `A-L05-03` read PASS (§4.28) and
    # that recurred on the Bank B pilot when Bank A's token budgets were reused
    # on a bank they were never measured against. The bound is one-sided and
    # exact, so this is a fact about the file, not a heuristic.
    at_risk = [(i, S.truncation_slack(r)) for i, r in verdict.items()
               if S.truncation_slack(r) < 0]
    if at_risk:
        detail = ", ".join(f"{i} (short by {-s:.4f} = {-s * 60:.1f} calls)"
                           for i, s in sorted(at_risk, key=lambda x: x[1]))
        sys.exit(
            f"refusing to freeze: {len(at_risk)} retained verdict(s) could be "
            f"reversed by their own failed calls: {detail}. Re-measure these "
            f"items at a corrected budget (see measure_budgets.py); do not "
            f"freeze a partition that the measurement does not establish.")

    kept = [i for i in data["items"] if verdict.get(i["id"], {}).get("passes_headroom")]
    # Only over SCREENED items: `verdict[i["id"]]` raised a KeyError the moment a
    # prefix was allowed through, which is the honest failure but a poor message.
    rejected = [i["id"] for i in mcq
                if i["id"] in verdict and not verdict[i["id"]]["passes_headroom"]]
    unscreened = list(missing)

    # Short-answer items have no option set, so masked-stem identifiability does
    # not apply and the MCQ screen never saw them. They are carried in a SEPARATE
    # field rather than silently dropped: an earlier version of this script
    # filtered on the MCQ verdict alone and lost all 20 of them without a word,
    # which would have shrunk the bank by a third between the released
    # pre-screen file and the frozen one, with nothing in either file to explain
    # the discrepancy.
    short = [i for i in data["items"] if i.get("type") != "mcq"]
    if len(kept) + len(rejected) + len(short) + len(unscreened) != len(data["items"]):
        sys.exit("refusing to freeze: items unaccounted for after partition")

    base = sorted(verdict[i]["baseline"] for i in verdict)
    kept_b = sorted(verdict[i["id"]]["baseline"] for i in kept)
    rej_b = sorted(verdict[i]["baseline"] for i in rejected)

    out = {
        "bank_id": bank.upper(),
        "roster": rost,
        # Baselines are solver-dependent, so a frozen bank is only valid for the
        # roster that screened it: A-L01-01 measured 0.15 on the Kiro panel and
        # 0.47 on the Ollama panel. Naming the roster in both the record and the
        # filename prevents one roster's retention list being used with another's
        # measurements.
        "solvers": list(C.SOLVERS if rost == "kr" else C.OL_SOLVERS),
        "frozen": date.today().isoformat(),
        "source_bank_sha256": pre_hash,
        "source_bank_sha256_at_screen": sorted(x for x in ran_against if x),
        "bank_measurement_sig": sig_now,
        "metadata_changed_since_screen": metadata_only,
        "screen_file": screen_path.name,
        # A NAME IS NOT AN IDENTITY for this file. Run files are append-only and
        # every stage is resumable, so `screen_bankA_ol.jsonl` denotes whatever
        # the most recent run left there -- and a re-screen (§4.29) replaces its
        # contents wholesale while the name resolves exactly as before. The
        # frozen bank is the artefact that gets deposited and cited, so a
        # provenance pointer that silently retargets would put the divergence in
        # the published record rather than in a run. Hash what was actually read.
        "screen_sha256": hashlib.sha256(screen_path.read_bytes()).hexdigest(),
        "study_version": versions.pop(),
        "screen_k_per_solver": rows[0].get("k_per_solver"),
        "headroom_margin": C.HEADROOM_MARGIN,
        "n_candidates_mcq": len(mcq),
        # A prefix freeze says so IN THE ARTEFACT. Every consumer decides from
        # this field, so a partly screened bank cannot be used as though it were
        # complete without the deposited file recording it.
        "screened_prefix": bool(unscreened),
        "n_screened_mcq": len(mcq) - len(unscreened),
        "n_unscreened_mcq": len(unscreened),
        "unscreened_mcq_ids": unscreened,
        "n_retained_mcq": len(kept_b),
        "n_rejected_mcq": len(rejected),
        "rejected_ids": rejected,
        # The audit trail for "you tuned the discard rule": the widest interval
        # of thresholds producing these exact verdicts.
        "threshold_invariance_band": [max(kept_b), min(rej_b)] if rej_b else None,
        "operative_threshold": (1.0 / 3.0) + C.HEADROOM_MARGIN,
        "baseline_distribution": base,
        "n_short_answer_unscreened": len(short),
        "short_answer_status": (
            "Not screened by masked-stem identifiability: with no option set the "
            "measure is undefined. These items require their own instrument and "
            "are NEVER pooled with MCQ (item format is a pre-registered "
            "stratifying factor). Carried here unscreened, not retained."
        ),
        "items": kept,
        "items_short_answer_unscreened": short,
    }
    # One place derives this name, and it is the one the stages read from.
    dst = R.frozen_path(bank, rost)
    payload = json.dumps(out, indent=2, ensure_ascii=False).encode()

    # "Frozen" has to mean something. This path had no overwrite guard, so a
    # second freeze replaced the deposited artefact in place -- same filename,
    # different retention set, no record that it had ever held anything else.
    # CLAUDE.md and the spec both cite this file by sha256; a silent overwrite
    # turns those citations into references to a file that no longer exists.
    # Keep the superseded copy under the QUARANTINE_ convention used for run
    # files, which the project treats as never-deleted audit trail.
    if dst.exists() and dst.read_bytes() != payload:
        old = json.loads(dst.read_text(encoding="utf-8"))
        keep = C.BANKS_DIR / (
            f"QUARANTINE_{dst.stem}_superseded_{old.get('frozen', 'undated')}"
            f"_{hashlib.sha256(dst.read_bytes()).hexdigest()[:8]}.json")
        if not force:
            sys.exit(
                f"refusing to freeze: {dst.name} already exists and differs "
                f"(on disk: {old.get('n_retained_mcq')} retained, frozen "
                f"{old.get('frozen')}; new: {len(kept_b)} retained). Re-run "
                f"with --force to supersede it -- the existing file will be "
                f"kept as {keep.name}.")
        keep.write_bytes(dst.read_bytes())
        print(f"superseded copy kept -> {keep.name}")

    dst.write_bytes(payload)
    print(f"frozen -> {dst.name}")
    # Say what the denominator IS. On a prefix freeze "27/60" reads as a
    # retention rate over the drawn sample, which is not what was measured: 16 of
    # those 60 were never screened, and quoting them as the denominator would
    # understate retention by a third.
    if unscreened:
        print(f"  retained {len(kept_b)}/{len(mcq) - len(unscreened)} SCREENED mcq "
              f"({len(mcq)} drawn, {len(unscreened)} unscreened -- PREFIX freeze); "
              f"{len(short)} short-answer carried unscreened")
    else:
        print(f"  retained {len(kept_b)}/{len(mcq)} mcq; "
              f"{len(short)} short-answer carried unscreened")
    # Use the SAME comparison as the retention rule. A bare `<=` here reports
    # the band as violated whenever an item sits exactly on the threshold --
    # which is reachable, since the threshold is 29/60 and baselines are exact
    # sixtieths (see solvability.at_or_below).
    inside = (S.at_or_below(max(kept_b), out["operative_threshold"])
              and S.at_or_below(out["operative_threshold"], min(rej_b)))
    strict = max(kept_b) < out["operative_threshold"] < min(rej_b)
    note = "" if strict else (
        "  (NOT STRICT: an item sits ON the threshold, so the partition is "
        "decided by the tie rule, not by an empty band)")
    print(f"  invariance band [{max(kept_b):.2f}, {min(rej_b):.2f}] "
          f"contains threshold {out['operative_threshold']:.3f}: {inside}{note}")
    print(f"  sha256 {hashlib.sha256(payload).hexdigest()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", default="A")
    ap.add_argument("--roster", default="kr", choices=["kr", "ol"])
    ap.add_argument("--force", action="store_true",
                    help="supersede an existing frozen bank (the old one is "
                         "kept as banks/QUARANTINE_..., never deleted)")
    ap.add_argument("--prefix", action="store_true",
                    help="freeze the screened PREFIX of a partly screened bank. "
                         "Refused unless the screened items really are a prefix "
                         "of the drawn order; the unscreened ids are recorded in "
                         "the frozen file, and generation reads that record "
                         "rather than a flag")
    a = ap.parse_args()
    freeze(a.bank, a.roster, a.force, a.prefix)
