"""All development tests must use the configured, installed source root."""
import runpy
from pathlib import Path
import pytest

_verify = runpy.run_path(str(Path(__file__).parent / "scripts/check_active_source.py"))["verify_source"]


def pytest_sessionstart(session):
    try:
        _verify(report=True)
    except RuntimeError as error:
        raise pytest.UsageError(str(error)) from error


def pytest_sessionfinish(session, exitstatus):
    _verify()
