import pytest

from app.llm.client import (
    LLMBudgetExceeded,
    LLMClient,
    LLMNotConfigured,
    LLMOutputInvalid,
    LLMRefusal,
    extract_json,
)
from app.llm.schemas import FitScoreOut
from app.models import LLMCall

from .test_llm_schemas import GOOD_FIT


def gen(llm, **kw):
    return llm.generate(task="fit_score", schema=FitScoreOut, system="s", user="u", model="claude-sonnet-5-5", **kw)


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('Sure! {"a": {"b": "}"}} trailing') == {"a": {"b": "}"}}
    with pytest.raises(ValueError):
        extract_json("no json")


def test_retries_on_invalid_then_succeeds(fake_llm, db):
    bad = {**GOOD_FIT, "verdict": "Skip"}  # inconsistent with score 82
    p = fake_llm({"fit_score": ["not json at all", bad, GOOD_FIT]})
    from app.llm.client import get_llm

    out = gen(get_llm(), db=db)
    assert out.verdict == "Strong"
    assert p.count("fit_score") == 3
    # the retry includes the validation error as feedback
    last = p.calls[-1].messages[-1]["content"]
    assert "failed validation" in last and "inconsistent" in last
    assert db.query(LLMCall).count() == 3


def test_gives_up_after_retries(fake_llm, db):
    fake_llm({"fit_score": ["{}"]})
    from app.llm.client import get_llm

    with pytest.raises(LLMOutputInvalid):
        gen(get_llm(), db=db)


def test_cache_prevents_second_call(fake_llm, db):
    p = fake_llm({"fit_score": [GOOD_FIT]})
    from app.llm.client import get_llm

    a = gen(get_llm(), db=db, cache_key_parts=["job-hash", "profile-hash"])
    b = gen(get_llm(), db=db, cache_key_parts=["job-hash", "profile-hash"])
    assert a == b and p.count("fit_score") == 1
    gen(get_llm(), db=db, cache_key_parts=["other-job", "profile-hash"])
    assert p.count("fit_score") == 2


def test_refusal_surfaces(fake_llm, db):
    fake_llm({"fit_score": [("", "refusal")]})
    from app.llm.client import get_llm

    with pytest.raises(LLMRefusal):
        gen(get_llm(), db=db)


def test_not_configured(db):
    with pytest.raises(LLMNotConfigured):
        gen(LLMClient(None), db=db)


def test_budget_guard_blocks_background_only(fake_llm, db, monkeypatch):
    fake_llm({"fit_score": [GOOD_FIT]})
    from app.llm.client import get_llm

    db.add(LLMCall(task="x", model="m", cost_usd=10.0))
    db.commit()
    with pytest.raises(LLMBudgetExceeded):
        gen(get_llm(), db=db, interactive=False)
    assert gen(get_llm(), db=db, interactive=True).score == 82


def test_anthropic_provider_request_shape(monkeypatch):
    """Verify the request we send to the SDK: cached system block, effort, JSON schema, fallback beta."""
    from app.llm.client import AnthropicProvider, LLMRequest

    sent = {}

    class Msg:
        content = [type("B", (), {"type": "text", "text": "{}"})()]
        stop_reason = "end_turn"
        model = "claude-sonnet-5-5"
        usage = type("U", (), {"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 4})()

    prov = AnthropicProvider("sk-test", refusal_fallback=True, structured_outputs=True, timeout=5)
    monkeypatch.setattr(prov.client.beta.messages, "create", lambda **kw: sent.setdefault("beta", kw) and Msg())
    monkeypatch.setattr(prov.client.messages, "create", lambda **kw: sent.setdefault("std", kw) and Msg())
    req = LLMRequest(
        task="t",
        model="claude-sonnet-5-5",
        system="sys",
        cached_context="resume",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=100,
        effort="low",
        json_schema={"type": "object"},
    )
    r = prov.complete(req)
    kw = sent["beta"]
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert kw["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert "temperature" not in kw and "thinking" not in kw
    assert r.cache_read_tokens == 3
    # Haiku has no server-side fallback → standard endpoint
    prov.complete(LLMRequest(task="t", model="claude-haiku-5-5", system="s", cached_context=None, messages=[], max_tokens=10, effort="low"))
    assert "fallbacks" not in sent["std"]
