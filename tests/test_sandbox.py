"""Tests for agentkit.sandbox — containment (no network, deterministic).

Security is the design point, so the tests are adversarial: a command that
tries to escape the cwd jail is blocked, a runaway is killed by the wall-clock
timeout, oversized output is capped, and shell metacharacters cannot inject a
second command because there is no shell.
"""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agentkit.sandbox import (
    DockerSandbox,
    ExecResult,
    Sandbox,
    SubprocessSandbox,
)
from agentkit.sandbox.net_guard import (
    EgressBlocked,
    allowed_hosts,
    assert_allowed,
    host_of,
    is_allowed,
)
from agentkit.sandbox.core import MAX_OUTPUT_BYTES


# ---------------------------------------------------------------------------
# ExecResult value type
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_exec_result_is_frozen_value_type():
    r = ExecResult(stdout="out", stderr="err", exit_code=0, duration=0.1)
    assert r.stdout == "out"
    assert r.exit_code == 0
    with pytest.raises(FrozenInstanceError):
        r.exit_code = 1  # type: ignore[misc]


@pytest.mark.unit
def test_subprocess_sandbox_satisfies_protocol():
    assert isinstance(SubprocessSandbox(), Sandbox)


# ---------------------------------------------------------------------------
# Command execution (argv, not shell)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_runs_argv_command_and_captures_stdout(tmp_path: Path):
    sb = SubprocessSandbox()
    r = sb.run([sys.executable, "-c", "print('hello')"], timeout=10, cwd=tmp_path)
    assert r.exit_code == 0
    assert "hello" in r.stdout
    assert r.duration >= 0.0


@pytest.mark.unit
def test_runs_python_code_string(tmp_path: Path):
    sb = SubprocessSandbox()
    r = sb.run("print(2 + 2)", timeout=10, cwd=tmp_path)
    assert r.exit_code == 0
    assert "4" in r.stdout


@pytest.mark.unit
def test_nonzero_exit_is_captured_not_raised(tmp_path: Path):
    sb = SubprocessSandbox()
    r = sb.run([sys.executable, "-c", "import sys; sys.exit(3)"], timeout=10, cwd=tmp_path)
    assert r.exit_code == 3


# ---------------------------------------------------------------------------
# argv-not-shell: injection is impossible
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_shell_metacharacters_are_inert(tmp_path: Path):
    """`; rm -rf` etc. are passed as literal argv, never interpreted by a shell."""
    sb = SubprocessSandbox()
    canary = tmp_path / "canary.txt"
    canary.write_text("alive")
    # If a shell ran this, the `; rm` would delete the canary. As argv it's a
    # literal string argument to echo-via-python.
    payload = "ok; rm -rf " + str(canary)
    r = sb.run([sys.executable, "-c", "import sys; print(sys.argv[1])", payload],
               timeout=10, cwd=tmp_path)
    assert r.exit_code == 0
    assert payload in r.stdout
    assert canary.exists(), "no shell ran, so the canary must survive"


# ---------------------------------------------------------------------------
# cwd jail
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_cwd_must_exist(tmp_path: Path):
    sb = SubprocessSandbox()
    with pytest.raises(ValueError):
        sb.run("print(1)", timeout=10, cwd=tmp_path / "does-not-exist")


@pytest.mark.unit
def test_write_outside_cwd_is_blocked(tmp_path: Path):
    """A code string that names a path escaping cwd is rejected before execution."""
    sb = SubprocessSandbox()
    escape = str(tmp_path.parent / "escape.txt")
    code = f"open({escape!r}, 'w').write('x')"
    with pytest.raises(ValueError):
        sb.run(code, timeout=10, cwd=tmp_path)


@pytest.mark.unit
def test_writing_inside_cwd_is_allowed(tmp_path: Path):
    sb = SubprocessSandbox()
    target = tmp_path / "inside.txt"
    code = f"open({str(target)!r}, 'w').write('x')"
    r = sb.run(code, timeout=10, cwd=tmp_path)
    assert r.exit_code == 0
    assert target.read_text() == "x"


# ---------------------------------------------------------------------------
# wall-clock timeout actually kills
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_timeout_kills_runaway(tmp_path: Path):
    sb = SubprocessSandbox()
    r = sb.run([sys.executable, "-c", "import time; time.sleep(30)"],
               timeout=1, cwd=tmp_path)
    assert r.exit_code != 0
    assert r.duration < 10, "the runaway must have been killed near the timeout"
    assert "timeout" in r.stderr.lower()


# ---------------------------------------------------------------------------
# output cap
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_output_is_capped(tmp_path: Path):
    sb = SubprocessSandbox()
    code = f"print('A' * ({MAX_OUTPUT_BYTES} * 4))"
    r = sb.run(code, timeout=15, cwd=tmp_path)
    assert len(r.stdout.encode()) <= MAX_OUTPUT_BYTES + 256  # cap + truncation marker


# ---------------------------------------------------------------------------
# net_guard: default-deny egress allowlist
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_loopback_is_allowed_by_default():
    assert is_allowed("http://localhost:8000/v1")
    assert is_allowed("http://127.0.0.1:8317")
    assert "localhost" in allowed_hosts()


@pytest.mark.unit
def test_external_host_is_blocked_by_default():
    assert not is_allowed("http://attacker.example/v1")
    with pytest.raises(EgressBlocked):
        assert_allowed("https://evil.test/exfil")


@pytest.mark.unit
def test_allowlist_extends_via_env(monkeypatch):
    monkeypatch.setenv("ALLOWED_EGRESS_HOSTS", "api.openai.com, example.test")
    assert is_allowed("https://api.openai.com/v1")
    assert "example.test" in allowed_hosts()


@pytest.mark.unit
def test_host_of_handles_bare_host_and_url():
    assert host_of("http://Example.COM/path") == "example.com"
    assert host_of("localhost") == "localhost"


# ---------------------------------------------------------------------------
# DockerSandbox seam
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_docker_sandbox_is_an_unimplemented_seam(tmp_path: Path):
    sb = DockerSandbox()
    assert isinstance(sb, Sandbox)
    with pytest.raises(NotImplementedError):
        sb.run("print(1)", timeout=10, cwd=tmp_path)
