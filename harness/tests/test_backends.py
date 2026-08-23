"""Tests for the backend clients. No network: transport is stubbed.

The behaviours under test are the ones whose failure would corrupt the study
silently rather than loudly -- dropped attrition, an unnoticed cache hit, a
misclassified permanent failure retried forever.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backends as B  # noqa: E402
import config as C  # noqa: E402


def _ok_body(content="hi", pt=4134, ct=3):
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct},
    }


class TestErrorClassification:
    @pytest.mark.parametrize("status,body,expected", [
        (403, "requires a subscription", "gated"),
        (410, "was retired at", "withdrawn"),
        (400, "Invalid model", "invalid_model"),
        (429, "slow down", "transient"),
        (503, "", "transient"),
        (418, "teapot", "http_418"),
        (None, "", "network"),
    ])
    def test_classification(self, status, body, expected):
        assert B.classify_error(status, body) == expected

    def test_permanent_failures_are_not_transient(self):
        """Retrying a 403 forever would hang the run and hide roster loss."""
        for s in (403, 410):
            assert B.classify_error(s, "") not in ("transient",)


class TestFailurePersistence:
    def test_http_failure_returns_result_not_exception(self, monkeypatch):
        """Every call must return a record. Raising here would tempt callers
        into try/except that drops the sample, understating attrition."""
        monkeypatch.setattr(B, "_post", lambda *a, **k: (403, {"error": "gated"}, {}))
        r = B.chat("kr/glm-5", [{"role": "user", "content": "x"}])
        assert r.ok is False
        assert r.error_kind == "gated"
        assert r.status == 403
        assert r.as_record()["model"] == "kr/glm-5"

    def test_network_exception_becomes_attrition_record(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("connection refused")
        monkeypatch.setattr(B, "_post", boom)
        r = B.chat("kr/glm-5", [{"role": "user", "content": "x"}])
        assert r.ok is False and r.error_kind == "network"
        assert r.latency_ms is not None

    def test_malformed_success_is_a_failure(self, monkeypatch):
        monkeypatch.setattr(B, "_post", lambda *a, **k: (200, {"choices": []}, {}))
        r = B.chat("kr/glm-5", [{"role": "user", "content": "x"}])
        assert r.ok is False and r.error_kind in ("malformed_response", "http_200")


class TestCacheGuard:
    def test_cache_hit_raises(self, monkeypatch):
        """A cached response returns identical text, fabricating perfect judge
        agreement and zero variance. It must stop the run, not warn."""
        monkeypatch.setattr(
            B, "_post",
            lambda *a, **k: (200, _ok_body(), {"x-omniroute-cache-hit": "true"}),
        )
        with pytest.raises(B.CacheHitError, match="variance"):
            B.chat("kr/glm-5", [{"role": "user", "content": "x"}])

    def test_no_cache_header_is_fine(self, monkeypatch):
        monkeypatch.setattr(B, "_post", lambda *a, **k: (200, _ok_body(), {}))
        assert B.chat("kr/glm-5", [{"role": "user", "content": "x"}]).ok


class TestTelemetry:
    def test_prompt_tokens_always_captured(self, monkeypatch):
        """The injected-context confound is only analysable if measured on
        every call, since it varies by model AND over time."""
        monkeypatch.setattr(B, "_post", lambda *a, **k: (200, _ok_body(pt=4604), {}))
        r = B.chat("kr/deepseek-3.2", [{"role": "user", "content": "x"}])
        assert r.prompt_tokens == 4604
        assert r.latency_ms is not None

    def test_temperature_recorded(self, monkeypatch):
        monkeypatch.setattr(B, "_post", lambda *a, **k: (200, _ok_body(), {}))
        r = B.chat("kr/glm-5", [{"role": "user", "content": "x"}], temperature=0.0)
        assert r.temperature == 0.0

    def test_default_generation_temperature_is_nonzero(self, monkeypatch):
        monkeypatch.setattr(B, "_post", lambda *a, **k: (200, _ok_body(), {}))
        assert B.chat("kr/glm-5", [{"role": "user", "content": "x"}]).temperature == C.GEN_TEMPERATURE
        assert C.GEN_TEMPERATURE > 0


class TestPathRouting:
    def test_controlled_path_strips_provider_prefix(self, monkeypatch):
        """The local runtime does not know about `kr/` prefixes."""
        seen = {}

        def spy(url, payload, timeout):
            seen["url"], seen["model"] = url, payload["model"]
            return 200, _ok_body(), {}

        monkeypatch.setattr(B, "_post", spy)
        B.chat("kr/glm-5", [{"role": "user", "content": "x"}], path=C.PATH_CONTROLLED)
        assert seen["model"] == "glm-5"
        assert C.OLLAMA_BASE in seen["url"]

    def test_hosted_path_keeps_full_id(self, monkeypatch):
        seen = {}

        def spy(url, payload, timeout):
            seen["url"], seen["model"] = url, payload["model"]
            return 200, _ok_body(), {}

        monkeypatch.setattr(B, "_post", spy)
        B.chat("kr/glm-5", [{"role": "user", "content": "x"}], path=C.PATH_HOSTED)
        assert seen["model"] == "kr/glm-5"
        assert C.OMNIROUTE_BASE in seen["url"]

    def test_unknown_path_rejected(self):
        with pytest.raises(ValueError, match="unknown path"):
            B.chat("kr/glm-5", [{"role": "user", "content": "x"}], path="magic")


class TestRetryPolicy:
    """`classify_error` documented transient retry for months while no retry
    existed; a 429 burst became permanent attrition and aborted stages against
    the 25% BackendUnavailable budget. These pin the policy."""

    @staticmethod
    def _failing(kind, status, n_fail, then_ok=True):
        """Fail `n_fail` times with `kind`, then succeed (or keep failing)."""
        calls = {"n": 0}

        def fake(url, payload, timeout):
            calls["n"] += 1
            if calls["n"] <= n_fail:
                if kind == "network":
                    raise ConnectionResetError("boom")
                return status, {"error": "rate limited"}, {}
            if not then_ok:
                return status, {"error": "rate limited"}, {}
            return 200, {"choices": [{"message": {"content": "ok"},
                                      "finish_reason": "stop"}],
                         "usage": {}}, {}
        return fake, calls

    def test_transient_is_retried_then_succeeds(self, monkeypatch):
        fake, calls = self._failing("transient", 429, n_fail=2)
        monkeypatch.setattr(B, "_post", fake)
        r = B.chat("ol/gemma4:cloud", [{"role": "user", "content": "x"}],
                   path=C.PATH_CONTROLLED)
        assert r.ok and r.content == "ok"
        assert calls["n"] == 3
        assert r.meta["attempts"] == 3

    def test_network_error_is_retried(self, monkeypatch):
        fake, calls = self._failing("network", None, n_fail=1)
        monkeypatch.setattr(B, "_post", fake)
        r = B.chat("ol/gemma4:cloud", [{"role": "user", "content": "x"}],
                   path=C.PATH_CONTROLLED)
        assert r.ok and calls["n"] == 2

    def test_retry_is_bounded_and_reports_failure(self, monkeypatch):
        fake, calls = self._failing("transient", 429, n_fail=99, then_ok=False)
        monkeypatch.setattr(B, "_post", fake)
        r = B.chat("ol/gemma4:cloud", [{"role": "user", "content": "x"}],
                   path=C.PATH_CONTROLLED)
        assert not r.ok
        assert calls["n"] == C.MAX_CALL_ATTEMPTS
        assert r.error_kind == "transient"

    def test_gated_is_never_retried(self, monkeypatch):
        """403 is permanent roster loss and must be reported, not hammered."""
        fake, calls = self._failing("gated", 403, n_fail=99, then_ok=False)
        monkeypatch.setattr(B, "_post", fake)
        r = B.chat("ol/gemma4:cloud", [{"role": "user", "content": "x"}],
                   path=C.PATH_CONTROLLED)
        assert not r.ok and r.error_kind == "gated"
        assert calls["n"] == 1

    def test_truncation_is_never_retried(self, monkeypatch):
        """Deterministic: the budget is too small. Retrying burns quota and
        hides the finding that the model needs a bigger allowance."""
        def fake(url, payload, timeout):
            return 200, {"choices": [{"message": {"content": ""},
                                      "finish_reason": "length"}],
                         "usage": {}}, {}
        seen = {"n": 0}
        def counting(*a, **kw):
            seen["n"] += 1
            return fake(*a, **kw)
        monkeypatch.setattr(B, "_post", counting)
        r = B.chat("ol/gpt-oss:120b-cloud", [{"role": "user", "content": "x"}],
                   path=C.PATH_CONTROLLED, max_tokens=16)
        assert not r.ok and r.error_kind == "truncated_before_content"
        assert seen["n"] == 1

    def test_success_first_time_records_no_attempts_key(self, monkeypatch):
        fake, _ = self._failing("transient", 429, n_fail=0)
        monkeypatch.setattr(B, "_post", fake)
        r = B.chat("ol/gemma4:cloud", [{"role": "user", "content": "x"}],
                   path=C.PATH_CONTROLLED)
        assert r.ok and "attempts" not in (r.meta or {})

    def test_the_retryable_set_is_exactly_as_declared(self):
        """Pinned as an exact set so widening it is a deliberate act.

        Widened once, on 2026-08-17, to add `auth`: a 401 on this backend is
        usually a token refresh, and leaving it unretryable let one blip abort a
        95-minute stage. Retry stays bounded by MAX_CALL_ATTEMPTS and sits
        beneath the 25% failure guard, so a genuinely dead credential still
        stops the run.
        """
        assert C.RETRYABLE_ERROR_KINDS == frozenset({"transient", "network", "auth"})

    def test_no_permanent_kind_is_ever_retryable(self):
        """The invariant the exact-set test exists to protect.

        `quota_exhausted` belongs here too: it is deterministic until the
        provider's window rolls over, and retrying it burns the remaining
        allowance proving the allowance is gone (spec 4.32).
        """
        for permanent in ("gated", "withdrawn", "invalid_model",
                          "truncated_before_content", "malformed_response",
                          "quota_exhausted"):
            assert permanent not in C.RETRYABLE_ERROR_KINDS


class TestQuota429IsNotRateLimit429:
    """429 does double duty and only one of its meanings is transient.

    Measured 2026-08-17: the Bank A re-screen hit A-L03-04 with an exhausted
    Ollama account. Every one of the 60 calls returned HTTP 429 with body
    "you (...) have reached your session usage limit", was classified
    `transient`, and was retried to the 4-attempt cap -- 240 requests into a
    wall that no retry could clear. The abort then reported `transient=60` and
    advised "fix the backend and re-run", the one remedy guaranteed not to work.

    Rate-limit 429s must keep their backoff: that is the failure the retry was
    built for (§ the 2026-08-16 A-L03-05 abort at 32% on a healthy backend).
    """

    #: Real bodies, copied from live responses rather than invented. CLAUDE.md
    #: documented only the weekly wording; the session wording is what actually
    #: stopped the re-screen, which is why matching enumerated messages would
    #: have failed exactly when it mattered.
    QUOTA_BODIES = [
        "you (serene_lovelace_581) have reached your session usage limit, "
        "upgrade for higher limits: https://ollama.com/upgrade",
        "you (serene_lovelace_581) have reached your weekly usage limit",
        "You have reached the limit of your plan",
        "insufficient_quota",
    ]

    @pytest.mark.parametrize("body", QUOTA_BODIES)
    def test_quota_bodies_classify_as_quota_exhausted(self, body):
        assert B.classify_error(429, body) == "quota_exhausted"

    @pytest.mark.parametrize("body", [
        "slow down", "rate limited", "too many requests", "",
    ])
    def test_rate_limit_bodies_stay_transient(self, body):
        """The retry exists for these. Reclassifying them would reintroduce the
        2026-08-16 failure: a healthy backend, a stage stopped by noise."""
        assert B.classify_error(429, body) == "transient"

    def test_quota_is_not_retryable(self):
        assert "quota_exhausted" not in C.RETRYABLE_ERROR_KINDS

    def test_a_quota_429_is_called_exactly_once(self, monkeypatch):
        """The point of the whole change: 1 request, not MAX_CALL_ATTEMPTS."""
        seen = {"n": 0}

        def fake(url, payload, timeout):
            seen["n"] += 1
            return 429, {"error": "you have reached your session usage limit"}, {}

        monkeypatch.setattr(B, "_post", fake)
        r = B.chat("ol/gemma4:cloud", [{"role": "user", "content": "x"}],
                   path=C.PATH_CONTROLLED)
        assert not r.ok and r.error_kind == "quota_exhausted"
        assert seen["n"] == 1, (
            f"a quota 429 was retried {seen['n']} times; retrying an exhausted "
            f"account cannot succeed and still reaches the provider")

    def test_a_rate_limit_429_is_still_retried_to_the_cap(self, monkeypatch):
        seen = {"n": 0}

        def fake(url, payload, timeout):
            seen["n"] += 1
            return 429, {"error": "too many requests, slow down"}, {}

        monkeypatch.setattr(B, "_post", fake)
        r = B.chat("ol/gemma4:cloud", [{"role": "user", "content": "x"}],
                   path=C.PATH_CONTROLLED)
        assert r.error_kind == "transient"
        assert seen["n"] == C.MAX_CALL_ATTEMPTS

    def test_the_abort_names_quota_instead_of_advising_a_re_run(self):
        """`_guard_failures` must not tell an operator to burn the remainder of
        an exhausted allowance proving it is exhausted."""
        import solvability as S
        with pytest.raises(S.BackendUnavailable) as e:
            S._guard_failures(60, 60, "baseline A-L03-04",
                              {"quota_exhausted": 60})
        msg = str(e.value)
        assert "QUOTA EXHAUSTED" in msg
        assert "re-running will not help" in msg
        assert "Fix the backend and re-run." not in msg

    def test_a_genuine_outage_still_advises_a_re_run(self):
        import solvability as S
        with pytest.raises(S.BackendUnavailable) as e:
            S._guard_failures(60, 60, "baseline A-L03-04", {"network": 60})
        assert "Fix the backend and re-run." in str(e.value)
        assert "QUOTA EXHAUSTED" not in str(e.value)


class TestAuth401IsNotGated403:
    """401 is credentials; 403 is entitlement. They must not share a policy.

    On 2026-08-17 a Bank B screen took `http_401` on 60/60 calls of one item and
    aborted a 95-minute stage. Every solver answered a manual probe seconds
    later with no intervention: it was a token refresh, not a dead credential.
    Because 401 fell through to the unrecognised-status branch it was retried
    zero times, so one blip cost the whole run.
    """

    def test_401_classifies_as_auth(self):
        assert B.classify_error(401, "Unauthorized") == "auth"

    def test_403_stays_gated(self):
        """Entitlement is permanent roster loss; retrying it hides attrition."""
        assert B.classify_error(403, "requires a subscription") == "gated"

    def test_auth_is_retryable_and_gated_is_not(self):
        assert "auth" in C.RETRYABLE_ERROR_KINDS
        assert "gated" not in C.RETRYABLE_ERROR_KINDS

    def test_a_transient_401_is_rescued_by_retry(self, monkeypatch):
        """The case that motivated the change."""
        calls = {"n": 0}

        def fake(url, payload, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                return 401, {"error": "Unauthorized"}, {}
            return 200, {"choices": [{"message": {"content": "ok"},
                                      "finish_reason": "stop"}], "usage": {}}, {}

        monkeypatch.setattr(B, "_post", fake)
        r = B.chat("ol/gemma4:cloud", [{"role": "user", "content": "x"}],
                   path=C.PATH_CONTROLLED)
        assert r.ok and calls["n"] == 2

    def test_a_permanent_401_still_stops_at_the_cap(self, monkeypatch):
        """Retry must stay bounded: a genuinely dead credential cannot loop."""
        calls = {"n": 0}

        def fake(url, payload, timeout):
            calls["n"] += 1
            return 401, {"error": "Unauthorized"}, {}

        monkeypatch.setattr(B, "_post", fake)
        r = B.chat("ol/gemma4:cloud", [{"role": "user", "content": "x"}],
                   path=C.PATH_CONTROLLED)
        assert not r.ok and r.error_kind == "auth"
        assert calls["n"] == C.MAX_CALL_ATTEMPTS

    def test_the_abort_message_says_re_authenticate_not_re_run(self):
        import solvability as S
        with pytest.raises(S.BackendUnavailable) as e:
            S._guard_failures(60, 60, "baseline B-question11_3_1", {"auth": 60})
        msg = str(e.value)
        assert "AUTHENTICATION FAILED" in msg
        assert "re-authenticate" in msg
        assert "Fix the backend and re-run." not in msg


class TestEveryProductionCallSiteStatesItsBudget:
    """spec 4.56. `backends.chat` has `max_tokens: int = 400`, and for the whole
    study `pipelines.run` omitted the argument and silently inherited it -- so
    every tutor reply was generated at a number nobody measured or chose, and
    6.2% of Bank A's calls were cut off mid-sentence while returning ok=True.

    The default cannot simply be removed: a dozen tests in this file call `chat`
    to exercise retry and error classification, where the budget is irrelevant
    and stating one would be noise. So the rule is enforced where it matters --
    at the production call sites -- rather than by the signature.

    THIS TEST IS THE ENFORCEMENT. A rule that lives only in a comment gets
    violated by the next call site (spec 4.51 made the same point about a grep
    filter, and 4.33 about budgets generally).
    """

    def test_no_module_calls_chat_without_max_tokens(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        offenders = []
        for f in sorted(root.glob("*.py")):
            src = f.read_text(encoding="utf-8")
            for m in re.finditer(r"\b(?:B|backends)\.chat\(", src):
                # Walk to the matching close paren so a multi-line call is read
                # as one call rather than as its first line.
                depth, i = 0, m.end() - 1
                while i < len(src):
                    if src[i] == "(":
                        depth += 1
                    elif src[i] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    i += 1
                call = src[m.start():i + 1]
                if "max_tokens" not in call:
                    line = src[:m.start()].count("\n") + 1
                    offenders.append(f"{f.name}:{line}")
        assert not offenders, (
            "these call sites inherit backends.chat's 400-token default instead "
            "of stating a measured budget: " + ", ".join(offenders)
            + ". Resolve one from C.MAX_TOKENS_SOLVER / C.MAX_TOKENS_GENERATOR "
              "via C.arm_budget, or pass it explicitly.")

    def test_the_default_is_still_the_one_the_completed_runs_used(self):
        """`C.GENERATOR_BUDGET_BEFORE_MEASUREMENT` is the only record of what
        Bank A's 2,016 cells were generated at, and it names this default as its
        source. If the default moves, that record stops being true -- and with no
        git repo there is nothing else to recover it from."""
        import inspect
        sig = inspect.signature(B.chat)
        assert sig.parameters["max_tokens"].default == 400
        assert C.GENERATOR_BUDGET_BEFORE_MEASUREMENT["value"] == 400
