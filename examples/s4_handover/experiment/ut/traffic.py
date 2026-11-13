"""One TCP echo connection; no reconnect after connection establishment."""
import sys
import json
from pathlib import Path
import socket
import signal
import time


def record(output, kind, **values):
    output.write(json.dumps({"kind": kind, "monotonic": time.monotonic(), **values}) + "\n")
    output.flush()


def serve(address, seconds, output):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((address, 2323))
        listener.listen(1)
        listener.settimeout(seconds)
        record(output, "listening", address=address)
        connection, peer = listener.accept()
        with connection:
            connection.settimeout(seconds)
            record(output, "accepted", peer=peer)
            while True:
                data = connection.recv(4096)
                if not data:
                    break
                connection.sendall(data)
            record(output, "closed")


def client(address, seconds, output):
    end = float("inf") if seconds is None else time.monotonic() + seconds
    with socket.socket() as connection:
        connection.settimeout(seconds)
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        record(output, "connecting", address=address)
        connection.connect((address, 2323))
        record(output, "connected", local=connection.getsockname(), peer=connection.getpeername())
        sequence = 0
        while time.monotonic() < end:
            begun = time.monotonic()
            connection.settimeout(None if seconds is None else max(.001, end - begun))
            payload = f"skyseed {sequence}\n".encode()
            received = b""
            try:
                connection.sendall(payload)
                while len(received) < len(payload):
                    part = connection.recv(len(payload) - len(received))
                    if not part:
                        raise ConnectionError("server closed the established connection")
                    received += part
            except TimeoutError:
                if time.monotonic() < end:
                    raise
                # The socket deadline is the measurement window's end. Keep
                # the unanswered probe visible without calling it a crash.
                record(output, "finished", echoes=sequence, pending_sequence=sequence,
                       pending_sent=begun, reason="measurement window ended")
                return
            if received != payload:
                raise ValueError("TCP echo payload changed")
            record(output, "echo", sequence=sequence, sent=begun, rtt_s=time.monotonic() - begun)
            sequence += 1
            time.sleep(.2)
        record(output, "finished", echoes=sequence)


if __name__ == "__main__":
    mode, address, seconds, path = sys.argv[1:]
    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    with Path(path).open("x") as output:
        try:
            function = serve if mode == "server" else client
            function(address, None if seconds == "None" else float(seconds), output)
        except KeyboardInterrupt:
            record(output, "stopped", reason="experiment interrupted")
        except (OSError, ValueError) as error:
            record(output, "error", message=str(error), type=type(error).__name__)
            raise
