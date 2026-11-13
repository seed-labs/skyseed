"""Join actual packet observations with Runner activation and UT attachment times."""
from datetime import datetime
import json
import os
from pathlib import Path


def read_records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def write_results(output, run_output):
    output, run_output = Path(output), Path(run_output)
    execution = read_records(run_output / "execution.jsonl")
    attachment = read_records(run_output / "attachment.jsonl")
    clock = next(event for event in execution if event["kind"] == "clock")
    origin = clock["origin"]
    epoch = datetime.fromisoformat(clock["start_time"])
    client_path = run_output / "tcp-client.jsonl"
    client = read_records(client_path) if client_path.exists() else []
    server = read_records(run_output / "tcp-server.jsonl")
    echoes = [{"time": event["monotonic"] - origin, "sequence": event["sequence"],
               "rtt_ms": event["rtt_s"] * 1000, "sent": event["sent"] - origin}
              for event in client if event["kind"] == "echo"]
    handovers = [{"time": event["monotonic"] - origin, "node": event["node"],
                  "previous": event["previous"], "attachment_lag_ms": 1000 * (event["monotonic"] - event["route_activated"])}
                 for event in attachment if event["kind"] == "attachment"]
    route_lag, link_lag = [], []
    for event in execution:
        if event["kind"] not in ("link", "route") or event["activated"] < origin:
            continue
        scheduled = [item["scheduled"] for item in event["adoptions"]] if "adoptions" in event else [event["scheduled"]]
        for timestamp in scheduled:
            due = origin + (datetime.fromisoformat(timestamp) - epoch).total_seconds()
            (route_lag if event["kind"] == "route" else link_lag).append(1000 * (event["activated"] - due))
    duration = next(event["completed"] for event in execution if event["kind"] in ("drained", "stopped")) - origin
    connected = next((event["monotonic"] - origin for event in client if event["kind"] == "connected"), None)
    reply_boundaries = [] if connected is None else [connected, *(echo["time"] for echo in echoes), duration]
    # Initialization and post-drain cleanup are not service changes. Keep real
    # detach/reconnect events inside the run, so coverage gaps stay visible.
    service_changes = []
    previously_attached = False
    for event in handovers:
        if event["time"] > duration:
            continue
        if previously_attached or event["previous"] is not None:
            if event["previous"] and event["node"]:
                kind = "handover"
            elif event["node"] is None:
                kind = "disconnect"
            else:
                kind = "reconnect"
            service_changes.append({**event, "kind": kind})
        if event["node"] is not None:
            previously_attached = True
    survived = []
    for event in service_changes:
        before = any(echo["time"] < event["time"] for echo in echoes)
        after = any(echo["sent"] > event["time"] for echo in echoes)
        survived.append({**event, "echo_before_and_after": before and after})
    summary = {"duration_s": duration,
               "tcp_connections": sum(event["kind"] == "connected" for event in client),
               "server_accepts": sum(event["kind"] == "accepted" for event in server),
               "tcp_echoes": len(echoes),
               "tcp_pending_at_end": [{"sequence": event["pending_sequence"],
                                       "sent": event["pending_sent"] - origin}
                                      for event in client if "pending_sequence" in event]
                                     if any(event["kind"] == "finished" for event in client) else None,
               "tcp_errors": [event for event in client if event["kind"] == "error"],
               "tcp_max_echo_rtt_ms": max((echo["rtt_ms"] for echo in echoes), default=None),
               "tcp_max_reply_gap_s": max((b["time"] - a["time"] for a, b in zip(echoes, echoes[1:])), default=None),
               "tcp_max_silence_s": max((b - a for a, b in zip(reply_boundaries, reply_boundaries[1:])), default=None),
               "service_changes": survived,
               "max_route_lag_ms": max(route_lag, default=None), "max_link_lag_ms": max(link_lag, default=None),
               "prepare_seconds": next(event["completed"] - event["started"] for event in execution if event["kind"] == "ready")}
    results = run_output / "views/results"
    record_names = ("execution.jsonl", "attachment.jsonl", "tcp-client.jsonl", "tcp-server.jsonl",
                    "ut.pcap", "gs.pcap", "store.json", "timings.json",
                    "packet-summary.json", "runtime-cost.txt", "resource-snapshot.json", "cleanup.json",
                    "outage-summary.json", "tcp-outage-socket.json", "validation.json")
    build_file = run_output / "inputs/build.json"
    if not build_file.exists():
        build_file = output / "build.json"  # Historical runs predate frozen build metadata.
    validation_file = run_output / "validation.json"
    validation = json.loads(validation_file.read_text()) if validation_file.exists() else None
    data = {"validation": validation, "record_files": ["summary.json", *(name for name in record_names if (run_output / name).exists())],
            "summary": summary, "tcp": echoes, "attachments": handovers,
            "run": run_output.name, "records_href": os.path.relpath(run_output, results),
            "overview_href": os.path.relpath(output / "index.html", results), "build": json.loads(build_file.read_text()),
            "additional_evidence": [name for name in ("packet-summary.json", "runtime-cost.txt", "resource-snapshot.json", "cleanup.json")
                                    if (run_output / name).exists()]}
    results.mkdir(parents=True, exist_ok=True)
    (run_output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    template = Path(__file__).with_name("results.html").read_text()
    (results / "index.html").write_text(template.replace("/*RESULT_DATA*/null", payload))
    (results / "results.json").write_text(payload + "\n")
    return data
