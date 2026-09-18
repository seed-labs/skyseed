"""Optional numeric performance receiver shared by computation owners."""
from typing import Protocol
import sys

class PerformanceRecorder(Protocol):
    def record(self, **facts: object) -> None: ...

def record_performance(receiver: PerformanceRecorder, **facts: object) -> None:
    """A measurement failure cannot change the observed operation's semantics."""
    try:
        receiver.record(**facts)
    except Exception as error:
        try:
            print(f"performance recording degraded: {type(error).__name__}: {error}", file=sys.stderr)
        except Exception:
            pass
