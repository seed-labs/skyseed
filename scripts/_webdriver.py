"""Small Firefox WebDriver helpers shared by repository visual gates."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import shutil
import socket
import subprocess
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _request(
    endpoint: str,
    *,
    method: str = "GET",
    value: object | None = None,
    timeout: float = 30.0,
) -> Any:
    payload = None if value is None else json.dumps(value).encode("utf-8")
    request = Request(
        endpoint,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"WebDriver HTTP {error.code}: {body}"
        ) from error
    if isinstance(result, dict) and isinstance(result.get("value"), dict):
        driver_error = result["value"].get("error")
        if driver_error:
            raise RuntimeError(
                f"WebDriver {driver_error}: "
                f"{result['value'].get('message', '')}"
            )
    return result.get("value") if isinstance(result, dict) else result


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _default_geckodriver() -> str | None:
    return shutil.which("geckodriver")


def _stop_snap_driver_scope(port: int) -> bool:
    for process_directory in Path("/proc").iterdir():
        if not process_directory.name.isdigit():
            continue
        try:
            command = (process_directory / "cmdline").read_bytes().split(b"\0")
            cgroup = (process_directory / "cgroup").read_text(
                encoding="utf-8"
            )
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if (
            not any(b"geckodriver" in value for value in command)
            or str(port).encode("ascii") not in command
        ):
            continue
        scope = next(
            (
                line.rsplit("/", 1)[-1]
                for line in cgroup.splitlines()
                if line.endswith(".scope")
                and "snap.firefox.geckodriver-" in line
            ),
            None,
        )
        if scope is None:
            continue
        completed = subprocess.run(
            ("systemctl", "--user", "stop", scope),
            check=False,
            capture_output=True,
            text=True,
        )
        return completed.returncode == 0
    return False


def _stop_driver(process: subprocess.Popen[bytes], port: int) -> None:
    if process.poll() is None:
        try:
            process.terminate()
        except PermissionError:
            if not _stop_snap_driver_scope(port):
                raise
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except PermissionError:
            if not _stop_snap_driver_scope(port):
                raise
        process.wait(timeout=10)


def _wait_for_driver(base_url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status = _request(f"{base_url}/status", timeout=1.0)
        except (OSError, URLError):
            time.sleep(0.1)
            continue
        if isinstance(status, dict) and status.get("ready"):
            return
        time.sleep(0.1)
    raise TimeoutError("geckodriver did not become ready")


def _execute(base_url: str, session_id: str, script: str) -> Any:
    return _request(
        f"{base_url}/session/{session_id}/execute/sync",
        method="POST",
        value={"script": script, "args": []},
    )


def _capture_screenshot(
    base_url: str,
    session_id: str,
    directory: Path | None,
    filename: str,
) -> str | None:
    if directory is None:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    encoded = _request(
        f"{base_url}/session/{session_id}/screenshot", timeout=30.0
    )
    if not isinstance(encoded, str):
        raise RuntimeError(
            "WebDriver screenshot response is not base64 text"
        )
    path = directory / filename
    path.write_bytes(base64.b64decode(encoded))
    return path.name


__all__: list[str] = []
