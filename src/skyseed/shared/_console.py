"""Best-effort human feedback, separate from machine-readable output."""
import sys


def _console(message):
    try:
        print(message, file=sys.stderr, flush=True)
    except Exception:
        pass  # A closed console must not replace an operation's result or error.
