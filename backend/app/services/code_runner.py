"""Run candidate code against a problem's tests in an isolated subprocess.

Isolation (local single-user app): separate interpreter in isolated mode (-I), fresh temp dir,
wall-clock timeout, CPU/memory/file-size rlimits, scrubbed environment (no API keys, no proxy).
Never expose this endpoint beyond localhost.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from ..config import get_settings

HARNESS = r"""
import json, sys, traceback, io, contextlib
sys.path.insert(0, sys.argv[1])
results = []
out = io.StringIO()
try:
    with contextlib.redirect_stdout(out):
        import test_solution as T
except Exception as e:
    print(json.dumps({"fatal": "".join(traceback.format_exception_only(type(e), e)).strip(),
                      "trace": traceback.format_exc()[-4000:], "stdout": out.getvalue()[-4000:]}))
    sys.exit(0)
names = [n for n in dir(T) if n.startswith("test_") and callable(getattr(T, n))]
names.sort(key=lambda n: getattr(getattr(T, n), "__code__", None).co_firstlineno if hasattr(getattr(T, n), "__code__") else 0)
for n in names:
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            getattr(T, n)()
        results.append({"name": n, "passed": True, "stdout": buf.getvalue()[-2000:]})
    except AssertionError as e:
        tb = traceback.extract_tb(e.__traceback__)
        line = tb[-1].line if tb else ""
        results.append({"name": n, "passed": False, "error": f"AssertionError: {e} " + (f"[{line}]" if line else ""), "stdout": buf.getvalue()[-2000:]})
    except Exception as e:
        results.append({"name": n, "passed": False, "error": "".join(traceback.format_exception_only(type(e), e)).strip(), "stdout": buf.getvalue()[-2000:]})
print(json.dumps({"results": results, "stdout": out.getvalue()[-4000:]}))
"""


def _limits(mem_mb: int, cpu_s: int):  # pragma: no cover - runs in child
    def apply() -> None:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 1024 * 1024, mem_mb * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        except (ValueError, OSError):
            pass
        os.setsid()

    return apply


def run_tests(code: str, tests: str, *, timeout: float | None = None) -> dict[str, Any]:
    s = get_settings()
    timeout = timeout or s.code_run_timeout_seconds
    with tempfile.TemporaryDirectory(prefix="jp-run-") as d:
        p = Path(d)
        (p / "solution.py").write_text(code, encoding="utf-8")
        (p / "test_solution.py").write_text(tests, encoding="utf-8")
        (p / "harness.py").write_text(HARNESS, encoding="utf-8")
        env = {"PATH": "/usr/bin:/bin", "HOME": d, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
        preexec = _limits(s.code_run_memory_mb, int(timeout) + 1) if os.name == "posix" else None
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(p / "harness.py"), d],
                cwd=d,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                preexec_fn=preexec,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "passed": 0,
                "total": 0,
                "results": [],
                "error": f"Timed out after {timeout:.0f}s (infinite loop?)",
                "duration_ms": int(timeout * 1000),
            }
        duration = int((time.monotonic() - t0) * 1000)
        line = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        try:
            payload = json.loads(line[0])
        except json.JSONDecodeError:
            err = (proc.stderr or proc.stdout or "no output").strip()[-3000:]
            if proc.returncode and proc.returncode < 0:
                err = f"Process killed (signal {-proc.returncode}) — memory or CPU limit exceeded?\n{err}"
            return {"ok": False, "passed": 0, "total": 0, "results": [], "error": err, "duration_ms": duration}
        if "fatal" in payload:
            return {
                "ok": False,
                "passed": 0,
                "total": 0,
                "results": [],
                "error": payload["fatal"],
                "trace": payload.get("trace"),
                "stdout": payload.get("stdout", ""),
                "duration_ms": duration,
            }
        results = payload["results"]
        passed = sum(1 for r in results if r["passed"])
        return {
            "ok": passed == len(results) and bool(results),
            "passed": passed,
            "total": len(results),
            "results": results,
            "stdout": payload.get("stdout", ""),
            "error": None,
            "duration_ms": duration,
        }
