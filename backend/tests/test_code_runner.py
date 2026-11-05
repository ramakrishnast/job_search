import pytest

from app.services.code_runner import run_tests
from app.services.questions import load_seed

REFERENCE = {
    "code-retry-backoff": """
import time
class RetryableError(Exception):
    pass
def call_with_retries(fn, max_attempts=4, base_delay=0.5, sleep=time.sleep):
    for attempt in range(max_attempts):
        try:
            return fn()
        except RetryableError:
            if attempt == max_attempts - 1:
                raise
            sleep(base_delay * 2 ** attempt)
""",
    "code-chunk-text": """
def chunk_words(text, chunk_size, overlap):
    if chunk_size < 1 or overlap >= chunk_size or overlap < 0:
        raise ValueError("bad args")
    words = text.split()
    out, i, step = [], 0, chunk_size - overlap
    while i < len(words):
        out.append(" ".join(words[i:i + chunk_size]))
        if i + chunk_size >= len(words):
            break
        i += step
    return out
""",
    "code-sse-parse": """
import json
def collect_stream_text(lines):
    out, data = [], []
    def flush():
        if not data:
            return False
        ev = json.loads("\\n".join(data)); data.clear()
        t = ev.get("type")
        if t == "error":
            raise RuntimeError(ev.get("error", {}).get("message", "error"))
        if t == "message_stop":
            return True
        if t == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
            out.append(ev["delta"]["text"])
        return False
    for line in list(lines) + [""]:
        if line.startswith(":"):
            continue
        if line == "":
            if flush():
                break
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    return "".join(out)
""",
    "code-token-bucket": """
class TokenBucket:
    def __init__(self, capacity, refill_per_second, clock):
        self.capacity, self.rate, self.clock = capacity, refill_per_second, clock
        self.tokens, self.last = capacity, clock()
    def try_acquire(self, tokens=1):
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if tokens > self.capacity or tokens > self.tokens:
            return False
        self.tokens -= tokens
        return True
""",
    "code-cosine-topk": """
import math
def top_k_cosine(query, docs, k):
    qn = math.sqrt(sum(x * x for x in query))
    def sim(d):
        dn = math.sqrt(sum(x * x for x in d))
        return 0.0 if qn == 0 or dn == 0 else sum(a * b for a, b in zip(query, d)) / (qn * dn)
    order = sorted(range(len(docs)), key=lambda i: (-sim(docs[i]), i))
    return order[:k]
""",
    "code-json-repair": """
import json, re
def generate_validated(call_model, required_keys, max_attempts=3):
    messages = [{"role": "user", "content": "Return JSON"}]
    for _ in range(max_attempts):
        reply = call_model(messages)
        text = reply.strip()
        m = re.search(r"```(?:json)?\\s*(.*?)```", text, re.S)
        if m:
            text = m.group(1)
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            missing = [k for k in required_keys if k not in data]
            if missing:
                raise ValueError(f"missing keys {missing}")
            return data
        except ValueError as e:
            messages = messages + [{"role": "assistant", "content": reply}, {"role": "user", "content": f"Invalid: {e}"}]
    raise ValueError("no valid output")
""",
    "code-rrf": """
def reciprocal_rank_fusion(rankings, k=60, top_n=None):
    scores, first = {}, {}
    order = 0
    for lst in rankings:
        seen, rank = set(), 0
        for d in lst:
            if d in seen:
                continue
            seen.add(d); rank += 1
            scores[d] = scores.get(d, 0.0) + 1.0 / (k + rank)
            if d not in first:
                first[d] = order; order += 1
    out = sorted(scores, key=lambda d: (-scores[d], first[d]))
    return out[:top_n] if top_n else out
""",
}

CODING = [q for q in load_seed()["questions"] if q["category"] == "coding"]


@pytest.mark.parametrize("q", CODING, ids=[q["key"] for q in CODING])
def test_reference_solution_passes_seed_tests(q):
    res = run_tests(REFERENCE[q["key"]], q["tests"])
    assert res["ok"], res
    assert res["passed"] == res["total"] >= 3


@pytest.mark.parametrize("q", CODING, ids=[q["key"] for q in CODING])
def test_starter_code_fails(q):
    res = run_tests(q["starter_code"], q["tests"])
    assert not res["ok"] and res["passed"] < res["total"]


def test_syntax_error_reported():
    res = run_tests("def f(:\n  pass", "from solution import f\ndef test_x():\n    assert f()")
    assert not res["ok"] and "SyntaxError" in res["error"]


def test_timeout():
    res = run_tests("def f():\n    while True: pass", "from solution import f\ndef test_x():\n    f()", timeout=2)
    assert not res["ok"] and "Timed out" in res["error"]


def test_env_is_scrubbed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    code = "import os\ndef key():\n    return os.environ.get('ANTHROPIC_API_KEY')"
    res = run_tests(code, "from solution import key\ndef test_k():\n    assert key() is None")
    assert res["ok"], res
