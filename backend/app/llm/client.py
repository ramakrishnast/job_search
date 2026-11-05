"""Claude client: structured JSON generation with Pydantic validation, retries,
content-addressed caching, usage logging and a daily budget guard."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal, utcnow
from ..models import LLMCache, LLMCall

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# $ per 1M tokens: (input, output). Cache reads ~= 10% of input, writes 1.25x.
PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-fable-5-1": (10.00, 50.00),
    # OpenAI (list prices; override with LLM_PRICE_INPUT/OUTPUT_PER_MTOK for other models)
    "gpt-5": (1.25, 10.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}
_UNKNOWN_MODEL_PRICE = (2.00, 10.00)  # conservative default so the budget guard still works
# Models that accept the server-side refusal fallback (`fallbacks: "default"`) on the Claude API.
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}


class LLMError(Exception):
    """Base error for LLM failures surfaced to the API layer."""


class LLMNotConfigured(LLMError):
    pass


class LLMRefusal(LLMError):
    pass


class LLMBudgetExceeded(LLMError):
    pass


class LLMOutputInvalid(LLMError):
    def __init__(self, message: str, last_errors: str = "") -> None:
        super().__init__(message)
        self.last_errors = last_errors


@dataclass
class RawResponse:
    text: str
    stop_reason: str | None
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    request_id: str | None = None


@dataclass
class LLMRequest:
    task: str
    model: str
    system: str
    cached_context: str | None  # large stable block (e.g. resume) placed in system for prompt caching
    messages: list[dict[str, Any]]
    max_tokens: int
    effort: str
    json_schema: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Provider(Protocol):
    def complete(self, req: LLMRequest) -> RawResponse: ...


def model_price(model: str) -> tuple[float, float]:
    s = get_settings()
    if s.llm_price_input_per_mtok is not None and s.llm_price_output_per_mtok is not None:
        return s.llm_price_input_per_mtok, s.llm_price_output_per_mtok
    if model in PRICING:
        return PRICING[model]
    # dated snapshots, e.g. "gpt-5-mini-2025-08-07"
    for name in sorted(PRICING, key=len, reverse=True):
        if model.startswith(name + "-"):
            return PRICING[name]
    return _UNKNOWN_MODEL_PRICE


def estimate_cost(model: str, r: RawResponse) -> float:
    inp, out = model_price(model)
    return (r.input_tokens * inp + r.output_tokens * out + r.cache_read_tokens * inp * 0.1 + r.cache_write_tokens * inp * 1.25) / 1_000_000


class AnthropicProvider:
    """Thin adapter over the official `anthropic` SDK."""

    def __init__(self, api_key: str, *, refusal_fallback: bool, structured_outputs: bool, timeout: float) -> None:
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=timeout)
        self.refusal_fallback = refusal_fallback
        self.structured_outputs = structured_outputs
        self._lock = threading.Lock()

    def _build(self, req: LLMRequest) -> dict[str, Any]:
        system_blocks: list[dict[str, Any]] = [{"type": "text", "text": req.system}]
        if req.cached_context:
            system_blocks.append({"type": "text", "text": req.cached_context, "cache_control": {"type": "ephemeral"}})
        output_config: dict[str, Any] = {"effort": req.effort}
        if req.json_schema is not None and self.structured_outputs:
            output_config["format"] = {"type": "json_schema", "schema": req.json_schema}
        return {
            "model": req.model,
            "max_tokens": req.max_tokens,
            "system": system_blocks,
            "messages": req.messages,
            "output_config": output_config,
        }

    def complete(self, req: LLMRequest) -> RawResponse:
        a = self._anthropic
        kwargs = self._build(req)
        use_fallback = self.refusal_fallback and req.model in FALLBACK_MODELS
        try:
            msg = self._send(kwargs, use_fallback)
        except a.BadRequestError as e:
            text = str(e).lower()
            # Self-heal if an optional feature is rejected for this account/model.
            if use_fallback and "fallback" in text:
                log.warning("Server-side fallback rejected; disabling. %s", e)
                with self._lock:
                    self.refusal_fallback = False
                msg = self._send(kwargs, False)
            elif "format" in kwargs["output_config"] and ("schema" in text or "output_config" in text or "format" in text):
                log.warning("Structured output schema rejected; retrying with prompt-only JSON. %s", e)
                kwargs["output_config"].pop("format", None)
                msg = self._send(kwargs, use_fallback)
            else:
                raise
        text_parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
        usage = msg.usage
        return RawResponse(
            text="".join(text_parts),
            stop_reason=msg.stop_reason,
            model=getattr(msg, "model", req.model),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            request_id=getattr(msg, "_request_id", None),
        )

    def _send(self, kwargs: dict[str, Any], use_fallback: bool) -> Any:
        if use_fallback:
            return self.client.beta.messages.create(**kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        return self.client.messages.create(**kwargs)


class OpenAIProvider:
    """Adapter over the official `openai` SDK (Chat Completions, JSON mode).

    Request shapes are normalized from the internal (Anthropic-style) content blocks:
    text blocks → text parts, base64 image blocks → data-URL image parts.
    """

    REASONING_PREFIXES = ("gpt-5", "gpt-6", "o1", "o3", "o4")

    def __init__(self, api_key: str, *, base_url: str | None, timeout: float) -> None:
        import openai

        self._openai = openai
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url or None, max_retries=3, timeout=timeout)
        self._no_reasoning_effort: set[str] = set()
        self._no_json_mode: set[str] = set()

    @staticmethod
    def _convert_content(content: Any) -> Any:
        if isinstance(content, str):
            return content
        parts: list[dict[str, Any]] = []
        for b in content:
            if b.get("type") == "text":
                parts.append({"type": "text", "text": b["text"]})
            elif b.get("type") == "image":
                src = b.get("source", {})
                url = f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
        if all(p["type"] == "text" for p in parts):
            return "\n\n".join(p["text"] for p in parts)
        return parts

    def build(self, req: LLMRequest) -> dict[str, Any]:
        # Stable content first (instructions + profile) so OpenAI's automatic prefix caching applies.
        system = req.system + (f"\n\n{req.cached_context}" if req.cached_context else "")
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in req.messages:
            messages.append({"role": m["role"], "content": self._convert_content(m["content"])})
        kwargs: dict[str, Any] = {"model": req.model, "messages": messages, "max_completion_tokens": req.max_tokens}
        if req.model not in self._no_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if req.model.startswith(self.REASONING_PREFIXES) and req.model not in self._no_reasoning_effort:
            kwargs["reasoning_effort"] = {"low": "low", "medium": "medium"}.get(req.effort, "high")
        return kwargs

    def complete(self, req: LLMRequest) -> RawResponse:
        o = self._openai
        for _ in range(3):
            kwargs = self.build(req)
            try:
                resp = self.client.chat.completions.create(**kwargs)
                break
            except o.BadRequestError as e:
                text = str(e).lower()
                # Self-heal per model if an optional parameter is unsupported.
                if "reasoning_effort" in kwargs and "reasoning" in text:
                    self._no_reasoning_effort.add(req.model)
                elif "response_format" in kwargs and ("response_format" in text or "json" in text):
                    self._no_json_mode.add(req.model)
                else:
                    raise
        else:  # pragma: no cover
            raise LLMError("OpenAI request kept failing with unsupported parameters")
        choice = resp.choices[0]
        msg = choice.message
        stop = {"stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}.get(
            choice.finish_reason or "", choice.finish_reason
        )
        if getattr(msg, "refusal", None):
            stop = "refusal"
        usage = resp.usage
        cached = 0
        if usage is not None and getattr(usage, "prompt_tokens_details", None) is not None:
            cached = getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0
        prompt = (getattr(usage, "prompt_tokens", 0) or 0) if usage is not None else 0
        return RawResponse(
            text=msg.content or "",
            stop_reason=stop,
            model=getattr(resp, "model", req.model),
            input_tokens=max(0, prompt - cached),
            output_tokens=(getattr(usage, "completion_tokens", 0) or 0) if usage is not None else 0,
            cache_read_tokens=cached,
            request_id=getattr(resp, "_request_id", None),
        )


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Parse the first JSON object in a model response (tolerates code fences / prose)."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in response")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("unterminated JSON object in response")


def _transform_schema(model: type[BaseModel]) -> dict[str, Any]:
    try:
        from anthropic.lib._parse._transform import transform_schema

        return transform_schema(model)
    except Exception:  # pragma: no cover - SDK internals moved
        return model.model_json_schema()


class LLMClient:
    def __init__(self, provider: Provider | None) -> None:
        self.provider = provider

    @property
    def configured(self) -> bool:
        return self.provider is not None

    # ---------------------------------------------------------------- budget
    @staticmethod
    def spent_last_24h(db: Session) -> float:
        since = utcnow() - timedelta(hours=24)
        val = db.scalar(select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0)).where(LLMCall.created_at >= since))
        return float(val or 0.0)

    def _check_budget(self, db: Session, interactive: bool) -> None:
        budget = get_settings().llm_daily_budget_usd
        if interactive or budget <= 0:
            return
        spent = self.spent_last_24h(db)
        if spent >= budget:
            raise LLMBudgetExceeded(
                f"Background LLM budget reached (${spent:.2f} of ${budget:.2f} in last 24h). "
                "Raise LLM_DAILY_BUDGET_USD or trigger the action manually."
            )

    # ---------------------------------------------------------------- core
    def generate(
        self,
        *,
        task: str,
        schema: type[T],
        system: str,
        user: str | list[dict[str, Any]],
        model: str,
        cached_context: str | None = None,
        max_tokens: int = 8000,
        effort: str = "medium",
        validation_context: dict[str, Any] | None = None,
        cache_key_parts: list[str] | None = None,
        interactive: bool = True,
        history: list[dict[str, Any]] | None = None,
        db: Session | None = None,
    ) -> T:
        """Generate a validated `schema` instance.

        cache_key_parts: when given, the validated output is cached under a hash of
        (task, model, schema, parts) and reused forever — each job is parsed/scored once.
        """
        own_db = db is None
        db = db or SessionLocal()
        try:
            cache_key = None
            if cache_key_parts is not None:
                h = hashlib.sha256()
                for p in [task, model, json.dumps(schema.model_json_schema(), sort_keys=True), *cache_key_parts]:
                    h.update(p.encode("utf-8"))
                    h.update(b"\x00")
                cache_key = h.hexdigest()
                hit = db.scalar(select(LLMCache).where(LLMCache.key == cache_key))
                if hit is not None:
                    try:
                        return schema.model_validate(hit.data, context=validation_context)
                    except ValidationError:
                        db.delete(hit)
                        db.commit()

            if self.provider is None:
                raise LLMNotConfigured("No LLM API key configured: set OPENAI_API_KEY (or ANTHROPIC_API_KEY) in .env and restart.")
            self._check_budget(db, interactive)

            json_schema = _transform_schema(schema)
            schema_text = json.dumps(schema.model_json_schema(), indent=None)
            full_system = (
                f"{system}\n\nRespond with a single JSON object that conforms to this JSON schema "
                f"(no prose, no code fences):\n{schema_text}"
            )
            user_content: list[dict[str, Any]] = [{"type": "text", "text": user}] if isinstance(user, str) else list(user)
            messages: list[dict[str, Any]] = [*(history or []), {"role": "user", "content": user_content}]

            attempts = 1 + max(0, get_settings().llm_validation_retries)
            last_err = ""
            for attempt in range(attempts):
                req = LLMRequest(
                    task=task,
                    model=model,
                    system=full_system,
                    cached_context=cached_context,
                    messages=messages,
                    max_tokens=max_tokens,
                    effort=effort,
                    json_schema=json_schema,
                )
                t0 = time.monotonic()
                try:
                    raw = self.provider.complete(req)
                except Exception as e:
                    self._log_call(db, task, model, None, int((time.monotonic() - t0) * 1000), str(e))
                    raise LLMError(f"Claude API request failed: {e}") from e
                self._log_call(db, task, model, raw, int((time.monotonic() - t0) * 1000), None)

                if raw.stop_reason == "refusal":
                    raise LLMRefusal("The model declined this request.")
                if raw.stop_reason == "max_tokens":
                    last_err = "response was truncated (max_tokens); be more concise"
                    max_tokens = int(max_tokens * 1.5)
                else:
                    try:
                        data = extract_json(raw.text)
                        result = schema.model_validate(data, context=validation_context)
                        if cache_key is not None:
                            db.add(LLMCache(key=cache_key, task=task, model=model, data=result.model_dump(mode="json")))
                            db.commit()
                        return result
                    except (ValueError, ValidationError) as e:
                        last_err = str(e)
                log.info("LLM task %s attempt %d invalid: %s", task, attempt + 1, last_err[:500])
                messages = [
                    *messages,
                    {"role": "assistant", "content": raw.text or "{}"},
                    {
                        "role": "user",
                        "content": (
                            "Your previous response failed validation:\n"
                            f"{last_err[:3000]}\n\n"
                            "Return a corrected JSON object only. Do not invent facts to satisfy the rules."
                        ),
                    },
                ]
            raise LLMOutputInvalid(f"Model output failed validation after {attempts} attempts", last_err)
        finally:
            if own_db:
                db.close()

    @staticmethod
    def _log_call(db: Session, task: str, model: str, raw: RawResponse | None, latency: int, error: str | None) -> None:
        try:
            rec = LLMCall(task=task, model=model, latency_ms=latency, ok=error is None, error=error)
            if raw is not None:
                rec.input_tokens = raw.input_tokens
                rec.output_tokens = raw.output_tokens
                rec.cache_read_tokens = raw.cache_read_tokens
                rec.cache_write_tokens = raw.cache_write_tokens
                rec.cost_usd = estimate_cost(model, raw)
                rec.request_id = raw.request_id
            db.add(rec)
            db.commit()
        except Exception:  # pragma: no cover - logging must never break the request
            db.rollback()
            log.exception("failed to log LLM call")


_client: LLMClient | None = None
_client_lock = threading.Lock()


def get_llm() -> LLMClient:
    global _client
    with _client_lock:
        if _client is None:
            s = get_settings()
            provider: Provider | None = None
            key = s.llm_api_key
            if key and s.llm_provider == "openai":
                provider = OpenAIProvider(key, base_url=s.openai_base_url, timeout=s.llm_timeout_seconds)
            elif key and s.llm_provider == "anthropic":
                provider = AnthropicProvider(
                    key,
                    refusal_fallback=s.llm_refusal_fallback,
                    structured_outputs=s.llm_structured_outputs,
                    timeout=s.llm_timeout_seconds,
                )
            _client = LLMClient(provider)
        return _client


def set_llm(client: LLMClient | None) -> None:
    """Override (tests) or reset the global client."""
    global _client
    with _client_lock:
        _client = client
