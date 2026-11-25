"""Manual-run progress and shell queries, separate from the experiment API."""
import json
from pathlib import Path
import signal
from threading import Event, Thread
import time


def status_line(state):
    elapsed = state.get("elapsed", 0) if state["origin"] is None else max(0, time.monotonic() - state["origin"])
    period = state["period_seconds"]
    cycle, seconds = divmod(elapsed, period)
    outage = state["outage"]
    interrupted = outage["start_seconds"] <= seconds < outage["end_seconds"]
    service = state.get("service") or "none"
    return (f"{state['phase']} | elapsed {elapsed:.1f}s | cycle {int(cycle) + 1} "
            f"{int(seconds // 60):02d}:{seconds % 60:04.1f} | "
            f"GS outage {'ON' if interrupted else 'off'} | UT service {service}")


class RunConsole:
    def __init__(self, output, run_output, build):
        self.output, self.run_output = Path(output), Path(run_output)
        self.state = {"phase": "starting", "origin": None,
                      "period_seconds": build["simulated_seconds"],
                      "outage": build["perturbation"], "run_output": str(self.run_output.resolve())}
        self.runner = None
        self.done = Event()

    def phase(self, name):
        self.state["phase"] = name
        print(f"\n{name}", flush=True)
        self._write()

    def _write(self):
        # Atomic replacement lets skytime read while this process updates it.
        temporary = self.output / "runtime.tmp"
        temporary.write_text(json.dumps(self.state))
        temporary.replace(self.output / "runtime.json")

    def watch(self, runner):
        self.runner = runner
        self.previous_handler = signal.signal(signal.SIGINT, lambda *_: runner.stop())
        self.phase("running · Ctrl+C to stop")
        self.thread = Thread(target=self._display, name="s4-console")
        self.thread.start()

    def _display(self):
        while not self.done.wait(1):
            self.state["origin"] = self.runner.origin
            attachment = self.run_output / "attachment-state.json"
            if attachment.exists():
                self.state["service"] = json.loads(attachment.read_text())["node"]
            self._write()
            print("\r" + status_line(self.state) + "\033[K", end="", flush=True)

    def finish(self):
        self.done.set()
        if self.runner is not None:
            self.thread.join()
            if self.runner.origin is not None:
                self.state["elapsed"] = time.monotonic() - self.runner.origin
        self.state["origin"] = None

    def close(self):
        if not self.done.is_set():
            self.finish()
        if self.runner is not None:
            signal.signal(signal.SIGINT, self.previous_handler)
        self.phase("stopped")


if __name__ == "__main__":
    import sys
    output = Path(__file__).resolve().parents[1] / "output"
    state = json.loads((output / "runtime.json").read_text())
    print(status_line(state))
    terminal_path = Path(state["run_output"]) / "terminal.json"
    if len(sys.argv) > 1 and sys.argv[1] == "nodes":
        terminal = json.loads(terminal_path.read_text())
        print(f"UT\t{terminal['ut_address']}\t{terminal['ut_container']}")
        for index, (node, address) in enumerate(terminal["ground_ips"].items()):
            print(f"GS {index + 1}\t{address}\t{terminal['containers'][node]}\t{node}")
        service = state.get("service")
        if service:
            print(f"service\t—\t{terminal['containers'][service]}\t{service}")
