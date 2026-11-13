"""Tests for subprocess sandbox: isolation, timeouts, and env scrubbing."""

import os
from tierlab.sandbox import execute_in_sandbox, get_safe_subprocess_env


def test_sandbox_normal_execution():
    code = "print('sandbox_ok')"
    res = execute_in_sandbox(code=code, timeout_s=5.0)
    assert res.returncode == 0
    assert "sandbox_ok" in res.stdout
    assert not res.timed_out
    assert res.wall_clock_s > 0.0


def test_sandbox_timeout_handling():
    code = "import time; time.sleep(5.0)"
    res = execute_in_sandbox(code=code, timeout_s=0.5)
    assert res.timed_out
    assert res.returncode == -1
    assert "timed out" in (res.error or "").lower()


def test_sandbox_env_scrubbing():
    os.environ["SECRET_API_TOKEN_SHOULD_BE_HIDDEN"] = "super_secret_value"
    code = """
import os
val = os.environ.get("SECRET_API_TOKEN_SHOULD_BE_HIDDEN", "NOT_FOUND")
print("FOUND_SECRET:" + val)
"""
    res = execute_in_sandbox(code=code, timeout_s=5.0)
    assert "FOUND_SECRET:NOT_FOUND" in res.stdout


def test_sandbox_syntax_error():
    code = "def broken_code(:"
    res = execute_in_sandbox(code=code, timeout_s=5.0)
    assert res.returncode != 0
    assert "SyntaxError" in res.stderr
