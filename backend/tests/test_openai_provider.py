import pytest

from app.config import Settings
from app.llm.client import LLMRequest, OpenAIProvider, RawResponse, estimate_cost


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _resp(content='{"ok": true}', finish="stop", refusal=None, prompt=1200, cached=1024, completion=50):
    usage = _Obj(prompt_tokens=prompt, completion_tokens=completion, prompt_tokens_details=_Obj(cached_tokens=cached))
    msg = _Obj(content=content, refusal=refusal)
    return _Obj(choices=[_Obj(message=msg, finish_reason=finish)], usage=usage, model="gpt-5-2025-08-07")


def _req(model="gpt-5", effort="medium", user=None):
    content = user or [{"type": "text", "text": "hello"}]
    return LLMRequest(
        task="t",
        model=model,
        system="Return JSON.",
        cached_context="PROFILE",
        messages=[{"role": "user", "content": content}],
        max_tokens=1000,
        effort=effort,
    )


def test_settings_auto_selects_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    s = Settings()
    assert s.llm_provider == "openai" and s.llm_configured
    assert s.llm_model_parse == "gpt-5-mini" and s.llm_model_default == "gpt-5"
    monkeypatch.setenv("LLM_MODEL_DEFAULT", "gpt-4.1")
    assert Settings().llm_model_default == "gpt-4.1"


def test_settings_anthropic_when_only_that_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    s = Settings()
    assert s.llm_provider == "anthropic" and s.llm_model_default == "claude-sonnet-5-5"


def test_request_shape_and_usage(monkeypatch):
    p = OpenAIProvider("sk-test", base_url=None, timeout=5)
    sent = {}
    monkeypatch.setattr(p.client.chat.completions, "create", lambda **kw: sent.update(kw) or _resp())
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}
    r = p.complete(_req(user=[img, {"type": "text", "text": "my answer"}]))
    assert sent["messages"][0] == {"role": "system", "content": "Return JSON.\n\nPROFILE"}
    parts = sent["messages"][1]["content"]
    assert parts[0] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}
    assert parts[1] == {"type": "text", "text": "my answer"}
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["reasoning_effort"] == "medium" and sent["max_completion_tokens"] == 1000
    assert "temperature" not in sent
    assert r.text == '{"ok": true}' and r.stop_reason == "end_turn"
    assert r.cache_read_tokens == 1024 and r.input_tokens == 176 and r.output_tokens == 50


def test_non_reasoning_model_gets_no_effort(monkeypatch):
    p = OpenAIProvider("sk-test", base_url=None, timeout=5)
    sent = {}
    monkeypatch.setattr(p.client.chat.completions, "create", lambda **kw: sent.update(kw) or _resp())
    p.complete(_req(model="gpt-4o-mini"))
    assert "reasoning_effort" not in sent
    assert sent["messages"][1]["content"] == "hello"  # text-only content collapses to a string


@pytest.mark.parametrize(
    "finish,refusal,expected", [("length", None, "max_tokens"), ("content_filter", None, "refusal"), ("stop", "I can't help", "refusal")]
)
def test_stop_reason_mapping(monkeypatch, finish, refusal, expected):
    p = OpenAIProvider("sk-test", base_url=None, timeout=5)
    monkeypatch.setattr(p.client.chat.completions, "create", lambda **kw: _resp(finish=finish, refusal=refusal))
    assert p.complete(_req()).stop_reason == expected


def test_self_heals_unsupported_reasoning_effort(monkeypatch):
    import httpx
    import openai

    p = OpenAIProvider("sk-test", base_url=None, timeout=5)
    calls = []

    def create(**kw):
        calls.append(kw)
        if "reasoning_effort" in kw:
            raise openai.BadRequestError(
                "Unsupported parameter: 'reasoning_effort'",
                response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
                body=None,
            )
        return _resp()

    monkeypatch.setattr(p.client.chat.completions, "create", create)
    p.complete(_req(model="gpt-5-custom"))
    assert len(calls) == 2 and "reasoning_effort" not in calls[1]
    p.complete(_req(model="gpt-5-custom"))
    assert len(calls) == 3  # remembered per model


def test_cost_estimate_uses_snapshot_prefix_and_override(monkeypatch):
    r = RawResponse(text="", stop_reason="end_turn", model="m", input_tokens=1_000_000, output_tokens=1_000_000)
    assert estimate_cost("gpt-5-mini-2025-08-07", r) == pytest.approx(0.25 + 2.0)
    monkeypatch.setenv("LLM_PRICE_INPUT_PER_MTOK", "1")
    monkeypatch.setenv("LLM_PRICE_OUTPUT_PER_MTOK", "3")
    from app.config import get_settings

    get_settings.cache_clear()
    assert estimate_cost("anything", r) == pytest.approx(4.0)
