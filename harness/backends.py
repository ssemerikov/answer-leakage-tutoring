"""HTTP clients for the two generation paths, with mandatory telemetry.

Every call returns a CallResult, INCLUDING failures. Callers must persist
failures too: silently dropping them would understate attrition and inflate
apparent reliability -- a roster of "models that answered" is not the roster
that was sampled.

Two things are logged on every single call because the study's validity depends
on them:

1. `prompt_tokens`. The hosted router prepends vendor agent context we cannot
   disable. Measured 2026-08-15 on an identical 8-token message: 2,926
   (qwen3-coder-next) to 4,604 (deepseek-3.2) -- a 1,678-token spread perfectly
   aliased with the model factor, and unstable within a model (claude-haiku-4.5
   returned 4,134, then 6,134 minutes later on the same string). It cannot be
   treated as a constant, so it is measured per call and reported.

2. `cache_hit`. The router advertises a response cache. A cache hit returns
   byte-identical text, which would fabricate perfect judge agreement and zero
   within-cell variance -- results that look excellent and mean nothing. Any
   cache hit is a hard error, not a warning.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import config as C


class CacheHitError(RuntimeError):
    """A cached response would silently destroy within-cell variance."""


@dataclass
class CallResult:
    ok: bool
    model: str
    path: str
    content: str | None = None
    status: int | None = None
    error_kind: str | None = None
    error_detail: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cache_hit: bool | None = None
    latency_ms: float | None = None
    temperature: float | None = None
    system_prompt_sha256: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict:
        return asdict(self)


def classify_error(status: int | None, body: str) -> str:
    """Map a failure to an attrition category.

    The categories matter for the manuscript: `gated` and `withdrawn` are
    permanent roster loss to be reported, while `transient` is retried and
    consumes no sample slot.
    """
    if status is None:
        return "network"
    if status == 403:
        return "gated"
    # 401 IS NOT 403. A 403 is entitlement -- the account may not use this model,
    # which is permanent roster loss the manuscript must report. A 401 is
    # credentials, and on this backend it is usually momentary: on 2026-08-17 a
    # Bank B screen took `http_401` on 60/60 calls of one item and aborted, and
    # all three solvers answered a manual probe seconds later with no
    # intervention. It was a token-refresh blip.
    #
    # Falling through to `http_401` made it unretryable, so one blip killed a
    # 95-minute stage. Retried instead, because the risk is asymmetric and
    # bounded: a genuinely dead credential costs MAX_CALL_ATTEMPTS per call and
    # then trips the failure guard anyway, whereas the current behaviour turns
    # every transient blip into a manual restart. `_guard_failures` names auth
    # explicitly so a real credential failure is not reported as an outage.
    if status == 401:
        return "auth"
    if status == 410:
        return "withdrawn"
    if status == 400 and "invalid model" in body.lower():
        return "invalid_model"
    # 429 DOES DOUBLE DUTY and only one of its meanings is transient. A
    # rate-limit 429 ("slow down") is exactly what the backoff below is for. A
    # QUOTA 429 ("you have reached your session/weekly usage limit") is
    # deterministic for the rest of the quota window: no number of retries can
    # succeed, and each one still reaches the provider.
    #
    # Measured 2026-08-17, when the Bank A re-screen aborted on A-L03-04: 60
    # calls, every one a quota 429, each retried to the 4-attempt cap = 240
    # requests into a wall. Worse than the waste, the abort reported
    # `transient=60` and advised "fix the backend and re-run", which is the one
    # remedy that cannot work -- retrying a permanent condition "burns quota
    # while hiding the finding", exactly as the retry docstring below warns
    # about `truncated_before_content`.
    #
    # Matched on the body because the status cannot distinguish the two. Kept
    # deliberately broad: Ollama alone emits at least two wordings ("session
    # usage limit" and "weekly usage limit"), so match the shared substring
    # rather than enumerating messages a provider may reword at any time.
    if status == 429 and _is_quota_exhaustion(body):
        return "quota_exhausted"
    if status in C.TRANSIENT_STATUSES:
        return "transient"
    return f"http_{status}"


#: Substrings that mark a 429 as quota exhaustion rather than rate limiting.
#: A false positive costs a retry that would have failed anyway; a false
#: negative costs `MAX_CALL_ATTEMPTS` requests against an exhausted account.
_QUOTA_MARKERS = ("usage limit", "quota", "reached the limit", "insufficient_quota",
                  "upgrade for higher limits", "billing")


def _is_quota_exhaustion(body: str) -> bool:
    low = body.lower()
    return any(m in low for m in _QUOTA_MARKERS)


def _post(url: str, payload: dict, timeout: float) -> tuple[int, dict, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode(errors="replace")
            headers = {k.lower(): v for k, v in resp.headers.items()}
            try:
                return resp.status, json.loads(raw), headers
            except json.JSONDecodeError:
                return resp.status, {"_raw": raw}, headers
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        headers = {k.lower(): v for k, v in (e.headers or {}).items()}
        try:
            return e.code, json.loads(raw), headers
        except json.JSONDecodeError:
            return e.code, {"_raw": raw}, headers


def chat(
    model: str,
    messages: list[dict],
    *,
    path: str = C.PATH_HOSTED,
    temperature: float = C.GEN_TEMPERATURE,
    max_tokens: int = 400,
    timeout: float = 180.0,
    system_prompt_sha256: str | None = None,
) -> CallResult:
    """One chat completion, with bounded retry of RETRYABLE failures.

    WHY RETRY EXISTS. `classify_error` has always documented `transient` as
    "retried and consumes no sample slot", but no retry was ever implemented:
    a single 429 or dropped connection became permanent attrition and counted
    against the 25% `BackendUnavailable` budget. On 2026-08-16 the Ollama screen
    aborted on A-L03-05 at 32% failure while all three solvers answered a manual
    probe seconds later -- a healthy backend, a stage stopped by transient noise.

    WHY THIS DOES NOT WEAKEN THE GUARD. Retry sits BENEATH the failure guard, so
    the guard counts only calls that failed every attempt. A 25% threshold on
    post-retry failures is a stronger and more meaningful signal of a dead
    backend than the same threshold on raw first attempts.

    Only `transient` and `network` are retried. `gated`, `withdrawn` and
    `invalid_model` are permanent roster loss that the manuscript must report,
    and `truncated_before_content` is deterministic -- the token budget for that
    model is too small, and retrying only burns quota while hiding the finding.

    Never raises on model failure; returns a CallResult. Raises only
    CacheHitError, a study-integrity violation rather than a model failure.
    """
    attempts = 0
    delay = C.RETRY_BASE_DELAY_S
    while True:
        attempts += 1
        r = _chat_once(
            model, messages, path=path, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout,
            system_prompt_sha256=system_prompt_sha256,
        )
        if r.ok or r.error_kind not in C.RETRYABLE_ERROR_KINDS:
            break
        if attempts >= C.MAX_CALL_ATTEMPTS:
            break
        # Full jitter: retries from many concurrent callers must not re-collide
        # in lockstep, which is how a rate limit becomes self-sustaining.
        time.sleep(random.uniform(0, min(delay, C.RETRY_MAX_DELAY_S)))
        delay = min(delay * 2, C.RETRY_MAX_DELAY_S)

    if attempts > 1:
        # Attrition stays visible even when retry rescued the call.
        meta = dict(r.meta or {})
        meta["attempts"] = attempts
        r = replace(r, meta=meta)
    return r


def _chat_once(
    model: str,
    messages: list[dict],
    *,
    path: str = C.PATH_HOSTED,
    temperature: float = C.GEN_TEMPERATURE,
    max_tokens: int = 400,
    timeout: float = 180.0,
    system_prompt_sha256: str | None = None,
) -> CallResult:
    """A single attempt. See `chat` for the retry policy wrapped around this."""
    if path == C.PATH_HOSTED:
        url = f"{C.OMNIROUTE_BASE}/chat/completions"
    elif path == C.PATH_CONTROLLED:
        url = f"{C.OLLAMA_BASE}/v1/chat/completions"
    else:
        raise ValueError(f"unknown path {path!r}")

    payload = {
        "model": model.split("/", 1)[1] if path == C.PATH_CONTROLLED else model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    t0 = time.monotonic()
    try:
        status, body, headers = _post(url, payload, timeout)
    except Exception as e:  # noqa: BLE001 - network layer, any failure is attrition
        return CallResult(
            ok=False, model=model, path=path, status=None,
            error_kind="network", error_detail=repr(e)[:300],
            latency_ms=(time.monotonic() - t0) * 1000, temperature=temperature,
        )
    latency_ms = (time.monotonic() - t0) * 1000

    cache_hit = str(headers.get("x-omniroute-cache-hit", "")).lower() == "true"
    if cache_hit:
        raise CacheHitError(
            f"Cached response for {model} on {path}. A cache hit returns "
            f"byte-identical text and would fabricate zero within-cell "
            f"variance. Disable the router cache before running the study."
        )

    if status != 200 or "choices" not in body:
        detail = json.dumps(body)[:300]
        return CallResult(
            ok=False, model=model, path=path, status=status,
            error_kind=classify_error(status, detail), error_detail=detail,
            latency_ms=latency_ms, cache_hit=cache_hit, temperature=temperature,
        )

    usage = body.get("usage") or {}
    try:
        choice = body["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return CallResult(
            ok=False, model=model, path=path, status=status,
            error_kind="malformed_response", error_detail=json.dumps(body)[:300],
            latency_ms=latency_ms, cache_hit=cache_hit, temperature=temperature,
        )

    # A reasoning model that exhausts max_tokens inside its reasoning trace
    # returns HTTP 200 with finish_reason="length" and content="". Passing that
    # through would make `parse_choice` read it as a DECLINE -- i.e. "the
    # options alone were uninformative" -- when in fact the model never got to
    # speak. Measured on gpt-oss:120b at max_tokens=16: 16 completion tokens,
    # all reasoning, empty content. On a panel of reasoning models that artefact
    # would be indistinguishable from the study's own finding, so it is
    # attrition, not data.
    finish = choice.get("finish_reason")
    if finish == "length" and not (content or "").strip():
        return CallResult(
            ok=False, model=model, path=path, status=status,
            error_kind="truncated_before_content",
            error_detail=(f"finish_reason=length with empty content at "
                          f"max_tokens={max_tokens}; raise the budget for this model"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            latency_ms=latency_ms, cache_hit=cache_hit, temperature=temperature,
        )

    return CallResult(
        ok=True, model=model, path=path, content=content, status=status,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        cache_hit=cache_hit, latency_ms=latency_ms, temperature=temperature,
        system_prompt_sha256=system_prompt_sha256,
        meta={"tokens_in_header": headers.get("x-omniroute-tokens-in")},
    )


def measure_injected_context(model: str, path: str = C.PATH_HOSTED) -> CallResult:
    """Probe the path's hidden prompt overhead with a minimal message.

    Reported in the manuscript as a finding in its own right: commercial LLM
    routers silently prepend thousands of tokens of vendor agent context,
    varying by model and over time, invisible to the calling application.
    """
    return chat(
        model, [{"role": "user", "content": "Reply exactly: PONG"}],
        path=path, temperature=0.0, max_tokens=8,
    )
