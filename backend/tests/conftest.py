"""Test fixtures: isolated data dir + SQLite DB per test, fake Claude provider."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["LLM_PROVIDER"] = "auto"
os.environ["LLM_MODEL_PARSE"] = ""
os.environ["LLM_MODEL_DEFAULT"] = ""
os.environ["DISCOVERY_SCHEDULE_ENABLED"] = "false"
os.environ["APP_ENV"] = "test"
os.environ["HTTP_MIN_INTERVAL_SECONDS"] = "0"
os.environ["LLM_DAILY_BUDGET_USD"] = "5"

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app.config import get_settings

    get_settings.cache_clear()
    from app import db as dbmod
    from app.llm import client as llmmod
    from app.main import migrate
    from app.services.questions import load_seed

    load_seed.cache_clear()
    dbmod.init_engine(get_settings().db_url)
    migrate()
    from app.db import session_scope
    from app.services.questions import seed_questions

    with session_scope() as s:
        seed_questions(s)
    llmmod.set_llm(None)
    yield tmp_path
    llmmod.set_llm(None)
    dbmod.get_engine().dispose()
    get_settings.cache_clear()


@pytest.fixture
def db() -> Iterator[Any]:
    from app.db import SessionLocal

    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


class FakeProvider:
    """Scripted stand-in for the Claude API. `script[task]` is a list of responses
    (str JSON, dict, or callable(req) -> str) consumed in order; the last one repeats."""

    def __init__(self, script: dict[str, list[Any]]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[Any] = []

    def complete(self, req: Any) -> Any:
        from app.llm.client import RawResponse

        self.calls.append(req)
        queue = self.script.get(req.task)
        if not queue:
            raise AssertionError(f"unexpected LLM task {req.task}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        stop = "end_turn"
        if callable(item):
            item = item(req)
        if isinstance(item, tuple):
            item, stop = item
        text = item if isinstance(item, str) else json.dumps(item)
        return RawResponse(text=text, stop_reason=stop, model=req.model, input_tokens=1000, output_tokens=200)

    def count(self, task: str) -> int:
        return sum(1 for c in self.calls if c.task == task)


@pytest.fixture
def fake_llm() -> Callable[[dict[str, list[Any]]], FakeProvider]:
    from app.llm.client import LLMClient, set_llm

    def make(script: dict[str, list[Any]]) -> FakeProvider:
        p = FakeProvider(script)
        set_llm(LLMClient(p))
        return p

    return make


@pytest.fixture
def client() -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c
