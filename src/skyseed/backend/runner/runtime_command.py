"""Persistent command entry; select one runtime adapter without loading the other."""
from pathlib import Path
import sys
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import json
import traceback
from time import monotonic_ns
from skyseed.backend.runner.process_incarnation import _capture_process_incarnations


def main():
    if len(sys.argv) != 2:
        return 2
    command = sys.argv[1]
    if command == "capture_incarnations":
        try:
            pids = json.load(sys.stdin)["pids"]
            if not isinstance(pids, list) or not pids:
                raise ValueError("incarnation capture requires a PID list")
            values = _capture_process_incarnations(tuple(pids))
            result = {"incarnations": values, "requested_pid_count": len(pids),
                      "captured_pid_count": len(values), "capture_mode": "single-privileged-batch"}
            print(json.dumps({"status": "ok", "result": result}), flush=True)
            return 0
        except BaseException as error:
            print(json.dumps({"status": "error", "error": str(error)}), flush=True)
            return 1
    if command == "serve-bpf":
        from skyseed.backend.bpf.dataplane.runtime_command import _respond
    elif command == "serve-linux":
        from skyseed.backend.linux.runtime_command import _dispatch_action
        def _respond(action, request, *, request_timing):
            started = monotonic_ns()
            try:
                response = {"status": "ok", "result": _dispatch_action(action, request)}
            except BaseException as error:
                response = {"status": "error", "error": str(error), "error_type": type(error).__name__,
                            "evidence": getattr(error, "evidence", None), "traceback": traceback.format_exc()}
            response["helper_timing"] = {**request_timing, "dispatch_start_monotonic_ns": started,
                                        "dispatch_end_monotonic_ns": monotonic_ns()}
            return response
    else:
        return 2
    for line in sys.stdin:
        received = monotonic_ns()
        envelope = json.loads(line)
        response = _respond(envelope["action"], envelope["request"], request_timing={
            "line_received_monotonic_ns": received, "request_decoded_monotonic_ns": monotonic_ns()})
        print(json.dumps(response, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
