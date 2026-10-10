from datetime import date
from types import SimpleNamespace

import httpx
import openai
import pytest

from jobradar.config import ScoringSettings
from jobradar.models import Job
from jobradar.scorer import ScoreResult, Scorer, SubScores, Usage

TODAY = date(2026, 10, 4)


def _result(score=82):
    return ScoreResult(
        track="C",
        score=score,
        sub_scores=SubScores(function=80, industry=80, seniority=80, location_language=100, bonus=50),
        cv_version="CV_StevenYang_AI_BDM",
        fit_points=["a", "b", "c"],
        gaps=["d"],
        red_flags=[],
    )


def _response(result=None, tier="flex", cached=14000, write=0):
    usage = SimpleNamespace(
        input_tokens=15000,
        input_tokens_details=SimpleNamespace(cached_tokens=cached, cache_write_tokens=write),
        output_tokens=500,
        output_tokens_details=SimpleNamespace(reasoning_tokens=100),
    )
    return SimpleNamespace(output_parsed=result or _result(), service_tier=tier, usage=usage)


class FakeClient:
    def __init__(self, *outcomes):
        self.calls = []
        self._outcomes = iter(outcomes)
        self.responses = self

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        outcome = next(self._outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _job():
    return Job(source="acme", ats="ashby", company="Acme", job_id="1", title="AI Partnerships Lead", url="https://x", description="Lead AI partnerships")


def _scorer(client, **settings):
    return Scorer("key", ScoringSettings(**settings), "PROFILE", {"CV_StevenYang_AI_BDM": "CV TEXT"}, client=client)


def test_profile_and_cvs_are_cached_prefix_and_job_comes_after():
    client = FakeClient(_response())
    outcome = _scorer(client).score(_job(), "new", TODAY)

    [call] = client.calls
    developer, user = call["input"]
    assert developer["role"] == "developer"
    assert developer["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
    assert "PROFILE" in developer["content"][0]["text"] and "CV TEXT" in developer["content"][0]["text"]
    assert "AI Partnerships Lead" in user["content"] and "近期新刊登" in user["content"]
    assert call["service_tier"] == "flex" and call["reasoning"] == {"effort": "low"}
    assert outcome.result.score == 82


def test_flex_falls_back_to_standard_once():
    error = openai.RateLimitError("busy", response=httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com")), body=None)
    client = FakeClient(error, _response(tier="default"))

    outcome = _scorer(client).score(_job(), "new", TODAY)

    assert [c["service_tier"] for c in client.calls] == ["flex", "auto"]
    assert outcome.usage.service_tier == "default"


def _rate_limit(message):
    return openai.RateLimitError(message, response=httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com")), body=None)


def test_account_rate_limit_waits_and_retries_same_tier(monkeypatch):
    waits = []
    monkeypatch.setattr("jobradar.scorer.time.sleep", waits.append)
    limit = _rate_limit("Rate limit reached for gpt-6-luna on tokens per min (TPM). Please try again in 3.711s.")
    client = FakeClient(limit, _response())

    _scorer(client).score(_job(), "new", TODAY)

    assert [c["service_tier"] for c in client.calls] == ["flex", "flex"]
    assert waits == [pytest.approx(4.711)]


def test_account_rate_limit_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr("jobradar.scorer.time.sleep", lambda _: None)
    limit = _rate_limit("Rate limit reached. Please try again in 250ms.")
    client = FakeClient(limit, limit, limit, limit)

    with pytest.raises(openai.RateLimitError):
        _scorer(client).score(_job(), "new", TODAY)
    assert len(client.calls) == 4


def test_score_is_clamped():
    client = FakeClient(_response(_result(score=130)))
    assert _scorer(client).score(_job(), None, TODAY).result.score == 100


def test_cost_uses_cached_rates_and_flex_discount():
    usage = Usage("gpt-6-luna", "flex", input_tokens=15000, cached_tokens=14000, cache_write_tokens=0, output_tokens=500, reasoning_tokens=100)
    # (1000×0.10 + 14000×0.01 + 500×0.50) / 1M × 0.5
    assert usage.cost_usd == pytest.approx(0.000245)
    write = Usage("gpt-6-luna", "default", input_tokens=15000, cached_tokens=0, cache_write_tokens=14000, output_tokens=500, reasoning_tokens=0)
    assert write.cost_usd == pytest.approx((1000 * 0.10 + 14000 * 0.125 + 500 * 0.50) / 1_000_000)


def test_unknown_model_rejected():
    with pytest.raises(ValueError, match="PRICES"):
        _scorer(FakeClient(), model="gpt-unknown")
