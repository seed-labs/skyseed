"""Local request transport for existing experiment owners; no domain state."""
from contextlib import contextmanager
from hashlib import sha256
import argparse
import json
from pathlib import Path
import socket
import socketserver
from threading import Thread


class _ControlUnavailable(RuntimeError):
    """No endpoint accepted a command, or a read-only status reply is unavailable."""


def _address(output, owner):
    # Abstract Unix sockets avoid path-length limits and disappear with their
    # process. No stale file can masquerade as a retained computation session.
    digest = sha256(str(Path(output).resolve()).encode()).hexdigest()[:32]
    return f"\0skyseed-{digest}-{owner}"


def _request(output, owner, command, **arguments):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        try:
            connection.connect(_address(output, owner))
        except (ConnectionRefusedError, FileNotFoundError) as error:
            raise _ControlUnavailable(f"{owner} session is unavailable; continuation requires its original live process") from error
        try:
            with connection.makefile("rwb") as stream:
                stream.write(json.dumps({"command": command, **arguments}).encode() + b"\n")
                stream.flush()
                reply = stream.readline()
                if not reply:
                    raise EOFError("control endpoint closed before replying")
        except (ConnectionError, EOFError) as error:
            if command == "status":
                raise _ControlUnavailable(f"{owner} status is unavailable: endpoint closed before replying") from error
            # An accepted mutation may already have taken effect. Do not treat
            # its missing response as an absent owner or retry the command.
            raise RuntimeError(f"{owner} {command} response was lost; operation completion is unknown") from error
        response = json.loads(reply)
    if "error" in response:
        raise RuntimeError(response["error"])
    return response["result"]


@contextmanager
def _serve_control(output, owner, dispatch):
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                result = {"result": dispatch(json.loads(self.rfile.readline()))}
            except Exception as error:
                result = {"error": f"{type(error).__name__}: {error}"}
            self.wfile.write(json.dumps(result, default=str).encode() + b"\n")

    with socketserver.ThreadingUnixStreamServer(_address(output, owner), Handler) as server:
        thread = Thread(target=server.serve_forever, name=f"skyseed-{owner}-control")
        thread.start()
        try:
            yield
        finally:
            server.shutdown()
            thread.join()


def _write_control_client(output):
    path = Path(output) / "experiment_control.py"
    path.write_text('''#!/usr/bin/env python3
"""Optional controls for this output's live experiment owners.

Continue preserves the original scene/Models and requires its computation
process. Pause holds the fully active network; container clocks/apps still run.
Reset cleans one runtime invocation, then replays retained materials.
"""
from pathlib import Path
from skyseed_v2.experiment._control import control_main

# Editable defaults. Explicit CLI arguments override these values.
ADDITIONAL_SECONDS = 300.0   # Used by continue unless TARGET_END is set.
TARGET_END = None           # Absolute ISO UTC endpoint, e.g. 2026-01-01T00:15:00+00:00.
BATCH_SECONDS = 300.0       # Delivery duration; None inherits the session setting.

if __name__ == "__main__":
    raise SystemExit(control_main(Path(__file__).resolve().parent,
        additional_seconds=ADDITIONAL_SECONDS, target_end=TARGET_END,
        batch_seconds=BATCH_SECONDS))
''', encoding="utf-8")
    path.chmod(0o755)


def control_main(output, *, additional_seconds=300.0, target_end=None, batch_seconds=None, argv=None):
    parser = argparse.ArgumentParser(description="Control retained computation and Runner execution separately")
    commands = parser.add_subparsers(dest="command", required=True)
    extend = commands.add_parser("continue", help="extend the original live calculation")
    target = extend.add_mutually_exclusive_group()
    target.add_argument("--seconds", type=float)
    target.add_argument("--until", help="absolute ISO timestamp with timezone")
    extend.add_argument("--batch-seconds", type=float, default=batch_seconds)
    for command in ("status", "pause", "resume", "reset", "cancel-computation", "cancel-runtime", "shutdown"):
        commands.add_parser(command)
    args = parser.parse_args(argv)
    try:
        if args.command == "continue":
            until = args.until if args.until is not None else target_end if args.seconds is None else None
            result = _request(output, "computation", "continue",
                seconds=(args.seconds if args.seconds is not None else additional_seconds) if until is None else None,
                until=until, batch_seconds=args.batch_seconds)
        elif args.command == "status":
            result = {}
            for owner in ("computation", "runtime"):
                try:
                    result[owner] = _request(output, owner, "status")
                except _ControlUnavailable as error:
                    result[owner] = {"available": False, "reason": str(error)}
        elif args.command == "shutdown":
            result = {}
            try:
                result["computation"] = _request(output, "computation", "shutdown")
            except _ControlUnavailable as error:
                result["computation"] = {"available": False, "reason": str(error)}
            # serve(run=True) retains the request through deployment and joins
            # runtime ownership. Independent sealed execution has its own endpoint.
            if not result["computation"].get("runtime_managed", False):
                try:
                    result["runtime"] = _request(output, "runtime", "cancel-runtime")
                except _ControlUnavailable as error:
                    result["runtime"] = {"available": False, "reason": str(error)}
            result["shutdown_requested"] = bool(
                result["computation"].get("shutdown_requested")
                or result.get("runtime", {}).get("cancel_requested"))
            # Unavailable endpoints cannot attest that their cleanup completed.
            result["shutdown_complete"] = (
                result["computation"].get("shutdown_complete")
                if result["computation"].get("runtime_managed") else None)
        elif args.command == "cancel-computation":
            result = _request(output, "computation", args.command)
        else:
            result = _request(output, "runtime", args.command)
        print(json.dumps(result, indent=2, default=str))
        return 0
    except RuntimeError as error:
        parser.exit(1, f"{error}\n")
