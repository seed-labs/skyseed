"""Prevent accidental collection of the retired implementation's test suite."""
import pytest
raise pytest.UsageError(
    "Legacy tests/ is inactive during the paper-aligned redesign; use new_tests/. "
    "Historical execution requires a separate, explicit checkout."
)
