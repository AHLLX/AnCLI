"""Verify useful live feedback even when an installer is silent."""

import importlib.util
from pathlib import Path
import threading
import types

import pytest


spec = importlib.util.spec_from_file_location(
    "ancli_progress_core", Path(__file__).parents[1] / "src" / "ancli-core.py"
)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def test_silent_command_reports_while_running_and_stops(monkeypatch, capsys):
    started, release = threading.Event(), threading.Event()

    def silent_command(*args, **kwargs):
        started.set()
        assert release.wait(2), "test did not release silent command"
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(core, "COMMAND_STATUS_INTERVAL", 0.02)
    monkeypatch.setattr(core.subprocess, "run", silent_command)
    result = []
    worker = threading.Thread(target=lambda: result.append(core.run_cmd("npm install -g sample")))
    worker.start()
    assert started.wait(1)
    # This captures output while the fake child is still blocked, rather than
    # only proving that status text appears after the command has completed.
    deadline = core.time.monotonic() + 1
    output = ""
    try:
        while "Command still running" not in output and core.time.monotonic() < deadline:
            core.time.sleep(0.01)
            output += capsys.readouterr().out
        assert "> npm install -g sample" in output
        assert "Command still running" in output
    finally:
        release.set()
        worker.join(2)
    assert result == [True]
    capsys.readouterr()
    core.time.sleep(0.06)
    assert "Command still running" not in capsys.readouterr().out


def test_command_failure_is_preserved(monkeypatch):
    monkeypatch.setattr(core.subprocess, "run", lambda *a, **kw: types.SimpleNamespace(returncode=1))
    assert core.run_cmd("npm install -g sample") is False


def test_reporter_stops_when_subprocess_raises(monkeypatch):
    original_thread = threading.Thread
    reporters = []

    def make_reporter(*args, **kwargs):
        reporter = original_thread(*args, **kwargs)
        reporters.append(reporter)
        return reporter

    def fail(*args, **kwargs):
        raise OSError("cannot start subprocess")

    monkeypatch.setattr(core.threading, "Thread", make_reporter)
    monkeypatch.setattr(core.subprocess, "run", fail)
    with pytest.raises(OSError, match="cannot start"):
        core.run_cmd("npm install -g sample")
    assert len(reporters) == 1
    assert not reporters[0].is_alive()
