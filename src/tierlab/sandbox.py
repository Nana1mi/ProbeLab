"""Subprocess sandbox runner for candidate code execution.

Ensures candidate code is executed in an isolated temporary directory, with
strict timeouts, environment variable scrubbing, and zero execution in the main process.
"""

from __future__ import annotations

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
) -> SandboxExecutionResult:
    """Execute code in an isolated temporary directory in a separate subprocess."""
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
        # Add current working dir / pythonpath to allow standard libraries
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
            return SandboxExecutionResult(
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                wall_clock_s=duration,
                timed_out=False,
                temp_dir=temp_dir,
            )
        except subprocess.TimeoutExpired as e:
            duration = time.perf_counter() - start_time
            return SandboxExecutionResult(
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
            return SandboxExecutionResult(
                returncode=-1,
                stdout="",
                stderr=str(e),
                wall_clock_s=duration,
                timed_out=False,
                temp_dir=temp_dir,
                error=str(e),
            )
    finally:
        # Clean up temporary directory
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
