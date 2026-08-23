#!/usr/bin/env python3
"""Report HARD failures in a stage log, ignoring routine quota stops.

    failure_since.py <byte-offset> <logfile>

Prints one line per failure EVENT found after the offset; prints nothing when
the only failures are quota exhaustion. Self-tests with no arguments.

WHY THIS IS A FILE AND NOT A GREP. The obvious test --

    grep -E 'Traceback|BackendUnavailable' log | grep -v quota_exhausted

-- excludes LINES when the thing being judged is an EVENT. A quota abort prints
a traceback whose reason appears only on its LAST line, so "Traceback (most
recent call last):", the frames, and "raise BackendUnavailable(" all survive the
exclusion, and a routine 20-minute wait gets reported as a hard failure. That
filter was fixed once inside a watcher, documented in a comment there, and then
retyped in its broken form into a fresh monitor two hours later (spec 4.51) --
the comment was documentation of a fix with no home. This is the home.

A traceback is one event spanning many lines, so the log is split on the
supervisor's own launch banner (each launch dies at most once, so one part holds
at most one traceback) and a part is a failure only if it contains a failure
signature and NO quota reason anywhere in it.
"""
from __future__ import annotations

import re
import sys

#: Lines that mean the stage stopped. Not necessarily a hard failure.
FAIL = re.compile(r"Traceback \(most recent call last\)|BackendUnavailable|"
                  r"^\s*raise \w+|Killed|MemoryError|SystemExit: [1-9]")
#: Any of these ANYWHERE in the part means the stop was quota, which is routine:
#: the supervisor absorbs it by waiting. `429` alone is deliberately included --
#: a rate-limited call that exhausted its retries reads the same to a watcher.
QUOTA = re.compile(r"quota_exhausted|usage limit|rate.?limit|429|"
                   r"reached the limit|insufficient_quota")
#: An auth failure is a HARD failure -- a 401 is credentials, not entitlement --
#: but on THIS backend it has twice been transient. Local Ollama proxying
#: `:cloud` models returned `auth=6` on 2026-08-22 at 07:09 and again at 23:28,
#: and both cleared with no human action (~43 min the first time). The a-priori
#: rule "re-authenticate, looping cannot help" is right in general and MEASURED
#: WRONG here, so the supervisor waits it out under a bound rather than either
#: exiting at once (which cost a 9-hour idle night) or looping forever (which is
#: what the rule exists to prevent).
AUTH = re.compile(r"\bauth=\d|AUTHENTICATION FAILED|\b401\b|Unauthorized|"
                  r"No credentials for provider")
#: The supervisor writes this on every (re)launch; it delimits events.
BANNER = re.compile(r"=== supervisor launch |supervisor start:")


def failures(text: str) -> list[str]:
    parts, cur = [], []
    for line in text.splitlines():
        if BANNER.search(line) and cur:
            parts.append("\n".join(cur)); cur = []
        cur.append(line)
    if cur:
        parts.append("\n".join(cur))
    out = []
    for part in parts:
        if FAIL.search(part) and not QUOTA.search(part):
            hit = [l for l in part.splitlines() if FAIL.search(l)]
            out.append(hit[-1].strip() if hit else part.splitlines()[-1].strip())
    return out


def auth_only(text: str) -> bool:
    """Is EVERY hard failure in this slice an auth failure?

    The bound is what keeps 4.64 intact. A slice carrying an auth failure AND a
    real traceback must still stop, because waiting cannot fix the traceback --
    so this asks whether auth explains ALL of them, never whether auth appears
    somewhere. "Is there an auth failure?" is 4.40's forbidden question in the
    shape that would swallow a genuine crash.
    """
    hard = failures(text)
    return bool(hard) and all(AUTH.search(h) for h in hard)


def quota_in(text: str) -> bool:
    """Did the slice stop for quota? The counterpart to `failures`.

    A stage can add zero rows for two reasons that need OPPOSITE responses: the
    next unit of work fails deterministically (stop -- looping cannot help), or
    the quota window is still closed (keep waiting). `failures` answers the first
    by staying silent on a quota stop; nothing answered the second, so a
    supervisor that relaunched into a still-closed window saw zero progress and
    called it a reproducible failure. Observed 2026-08-21: solve bs aborted at
    120/3888 two seconds into a relaunch, on `quota_exhausted=6`.
    """
    return bool(QUOTA.search(text))


def _selftest() -> int:
    quota = ("=== supervisor launch 1 ===\n"
             "Traceback (most recent call last):\n"
             "  File \"run.py\", line 1, in <module>\n"
             "    raise BackendUnavailable(\n"
             "solvability.BackendUnavailable: quota_exhausted on 3 solvers\n")
    hard = ("=== supervisor launch 1 ===\n"
            "Traceback (most recent call last):\n"
            "  File \"run.py\", line 1, in <module>\n"
            "KeyError: 'answer_index'\n")
    assert failures(quota) == [], "a routine quota stop was reported as hard"
    assert failures(hard), "a real traceback was not reported"
    assert failures("") == []
    assert failures("all fine\nrows=10\n") == []
    # A quota part and a hard part in ONE log: the hard one must still surface,
    # which line-wise exclusion over the whole file would also get right, but
    # part-wise exclusion could get wrong if the parts were merged.
    both = quota + "=== supervisor launch 2 ===\n" + hard
    assert len(failures(both)) == 1, failures(both)
    assert quota_in(quota) and not quota_in(hard), "quota detection is wrong"
    assert not quota_in(""), "empty text is not a quota stop"

    auth = ("=== supervisor launch 3 ===\n"
            "Traceback (most recent call last):\n"
            "  File \"run.py\", line 1, in <module>\n"
            "solvability.BackendUnavailable: leak X: 6/6 calls FAILED. "
            "Error kinds: auth=6. AUTHENTICATION FAILED on every attempt.\n")
    assert failures(auth), "an auth stop is still a HARD failure"
    assert auth_only(auth), "an auth-only slice was not recognised"
    assert not auth_only(hard), "a plain traceback must not read as auth"
    assert not auth_only(""), "no failure at all is not an auth failure"
    # The bound that keeps 4.64 intact: auth PLUS a real crash must stop.
    assert not auth_only(auth + "=== supervisor launch 4 ===\n" + hard), (
        "a slice mixing auth with a genuine traceback must not read as auth-only")
    print("failure_since.py: self-tests pass (both directions, both questions)")
    return 0


USAGE = ("usage: failure_since.py [--quota|--auth] <byte-offset> <logfile>\n"
         "       failure_since.py            (self-test, no arguments)")


def main(argv: list[str]) -> int:
    want_quota = "--quota" in argv
    want_auth = "--auth" in argv
    argv = [a for a in argv if a not in ("--quota", "--auth")]

    # NO ARGUMENTS is the documented self-test.  ANY OTHER wrong invocation is a
    # usage error, loudly.  It used to fall through to the self-test as well, and
    # on 2026-08-22 `watch_stage.sh` was rebuilt calling this with one argument:
    # it ran the self-test, printed "self-tests pass", exited 0, and so the
    # watcher's hard-failure check could never fire.  When `solve bs` died on an
    # auth failure the watcher reported "supervisor gone" -- true, but not the
    # reason, which was sitting in the log it had not read.  A misuse that
    # answers "fine" is worse than a crash (spec 4.63: an empty measurement
    # clears every ceiling vacuously).
    if len(argv) == 1:
        return _selftest()
    if len(argv) != 3:
        print(USAGE, file=sys.stderr)
        return 64
    try:
        off = int(argv[1])
    except ValueError:
        print(f"first argument must be a byte offset, got {argv[1]!r}\n{USAGE}",
              file=sys.stderr)
        return 64
    path = argv[2]
    try:
        with open(path, "rb") as fh:
            fh.seek(off)
            text = fh.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return 0
    if want_auth:
        # exit 0 == "every hard failure here is auth"; the shell branches on the
        # status, never on prose.
        return 0 if auth_only(text) else 1
    if want_quota:
        # Exit status IS the answer, so a shell can branch on it without parsing.
        return 0 if quota_in(text) else 1
    hits = list(failures(text))
    for line in hits:
        print(line)
    # The exit status agrees with the output, so a caller that branches on either
    # protocol gets the same answer.  `supervise.sh` reads stdout; the watcher
    # reads both.  Two protocols for one question is how the wrong one gets used.
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
