"""Subprocess sandbox runner for candidate code execution.

Ensures candidate code is executed in an isolated temporary directory, with
strict timeouts, environment variable scrubbing, and zero execution in the main process.
Implements sandbox execution caching by candidate hash per Item #14.
"""

from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field

from tierlab.exceptions import SandboxExecutionError, SandboxTimeoutError


class SandboxExecutionResult(BaseModel):
    returncode: int
    stdout: str
    stderr: str
    wall_clock_s: float
    timed_out: bool = False
    temp_dir: str = ""
    error: str | None = None


_SANDBOX_EXECUTION_CACHE: dict[str, SandboxExecutionResult] = {}
_RUN_CACHE: ContextVar[dict[str, SandboxExecutionResult] | None] = ContextVar('sandbox_run_cache', default=None)


def get_sandbox_cache() -> dict[str, SandboxExecutionResult]:
    cache = _RUN_CACHE.get()
    return cache if cache is not None else _SANDBOX_EXECUTION_CACHE


def clear_sandbox_cache() -> None:
    get_sandbox_cache().clear()


def start_sandbox_run() -> None:
    """Fresh cache for this async run, including concurrently served experiments."""
    _RUN_CACHE.set({})


def get_safe_subprocess_env() -> dict[str, str]:
    """Construct a clean, minimal environment without sensitive API keys."""
    safe_keys = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "PYTHONPATH",
        "PYTHONHOME",
        "LC_ALL",
        "LANG",
    }
    clean_env: dict[str, str] = {}
    for k, v in os.environ.items():
        if k.upper() in safe_keys:
            clean_env[k] = v

    clean_env["PYTHONUNBUFFERED"] = "1"
    clean_env["PYTHONDONTWRITEBYTECODE"] = "1"
    return clean_env


def execute_in_sandbox(
    code: str,
    entry_filename: str = "solution.py",
    runner_script: str | None = None,
    extra_files: dict[str, str | bytes] | None = None,
    timeout_s: float = 10.0,
    args: list[str] | None = None,
    use_cache: bool = True,
) -> SandboxExecutionResult:
    """Execute code in an isolated temporary directory in a separate subprocess.

    Per Item #14: Execution results are cached by content hash so each candidate
    is only executed once in the sandbox.
    """
    cache_key = ""
    if use_cache:
        raw_key = json.dumps([code, entry_filename, runner_script, timeout_s, args,
                              sorted((k, hashlib.sha256(v.encode('utf-8') if isinstance(v, str) else v).hexdigest())
                                     for k, v in (extra_files or {}).items())])
        cache_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        if cache_key in get_sandbox_cache():
            return get_sandbox_cache()[cache_key]

    temp_dir = tempfile.mkdtemp(prefix="tierlab_sandbox_")
    temp_path = Path(temp_dir)
    try:
        # Write candidate file
        solution_file = temp_path / entry_filename
        solution_file.write_text(code, encoding="utf-8")

        # Write runner script if provided
        target_script = solution_file
        if runner_script:
            runner_file = temp_path / "runner.py"
            runner_file.write_text(runner_script, encoding="utf-8")
            target_script = runner_file

        # Write extra files
        if extra_files:
            for fname, content in extra_files.items():
                target_file = temp_path / fname
                target_file.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, str):
                    target_file.write_text(content, encoding="utf-8")
                else:
                    target_file.write_bytes(content)

        cmd = [sys.executable, str(target_script)]
        if args:
            cmd.extend(args)

        env = get_safe_subprocess_env()
        env["PYTHONPATH"] = f"{temp_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}"

        start_time = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                cwd=temp_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            duration = time.perf_counter() - start_time
            res = SandboxExecutionResult(
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                wall_clock_s=duration,
                timed_out=False,
                temp_dir=temp_dir,
            )
        except subprocess.TimeoutExpired as e:
            duration = time.perf_counter() - start_time
            res = SandboxExecutionResult(
                returncode=-1,
                stdout=e.stdout or "" if isinstance(e.stdout, str) else "",
                stderr=e.stderr or "" if isinstance(e.stderr, str) else "",
                wall_clock_s=duration,
                timed_out=True,
                temp_dir=temp_dir,
                error=f"Execution timed out after {timeout_s}s",
            )
        except Exception as e:
            duration = time.perf_counter() - start_time
            res = SandboxExecutionResult(
                returncode=-1,
                stdout="",
                stderr=str(e),
                wall_clock_s=duration,
                timed_out=False,
                temp_dir=temp_dir,
                error=str(e),
            )

        if use_cache and cache_key:
            get_sandbox_cache()[cache_key] = res
        return res
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
