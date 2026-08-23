"""Shared test fixtures.

Retry backoff sleeps for real seconds. Tests that exercise failure paths would
otherwise spend most of the suite asleep -- the suite went from 1.9s to 8.9s the
moment retry landed. Zero the delay by default; tests that care about the retry
POLICY assert on attempt counts, which is the behaviour that matters, not on
wall-clock backoff.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    import backends
    monkeypatch.setattr(backends.time, "sleep", lambda _s: None)


def fake_result(content, *, ok=None, error_kind=None, status=200, model="test/model",
                finish_reason="stop", **kw):
    """A CallResult-shaped double.

    Tests used to hand-roll `type("R", (), {"ok": ..., "content": ...})()`,
    which carries ONLY the two fields that test happened to need. When
    production code began reading `error_kind` -- to report which failures
    caused a BackendUnavailable abort -- 21 such stubs raised AttributeError.
    The stubs had been silently under-specifying the contract for as long as
    they existed; the code was right and the doubles were wrong.

    Building the real dataclass instead means a stub cannot omit a field that
    production code is entitled to read, so this class of breakage is now a
    construction error at the seam rather than a surprise later.
    """
    import backends
    if ok is None:
        ok = content is not None
    if not ok and error_kind is None:
        error_kind = "http_400"
    return backends.CallResult(
        ok=ok, model=model, path=kw.pop("path", "hosted"), content=content,
        status=status, error_kind=error_kind,
        error_detail=kw.pop("error_detail", None),
        prompt_tokens=kw.pop("prompt_tokens", 10),
        completion_tokens=kw.pop("completion_tokens", 5),
        cache_hit=kw.pop("cache_hit", False),
        latency_ms=kw.pop("latency_ms", 10),
        temperature=kw.pop("temperature", 0.0),
        system_prompt_sha256=kw.pop("system_prompt_sha256", None),
        meta=kw.pop("meta", {}),
    )
