"""Render the detached Traffic / Path Replay page."""

from __future__ import annotations

from html import escape
from typing import Any

from tools._html import standalone_page


def render_traffic_replay_page(data: dict[str, Any]) -> str:
    """Render explicit planned reachability beside runtime observations."""

    inventory = data["inventory"]
    overview = data["overview"]
    transaction_rows = "".join(
        _transaction_row(value) for value in data["transactions"]
    )
    boundary_rows = "".join(
        _boundary_row(value)
        for value in data["planned_evidence"]["formal_transitions"]
    )
    qualification = data["qualification"]
    qualification_body = (
        '<p class="muted">No probe qualification evidence supplied.</p>'
        if qualification is None
        else _qualification(qualification)
    )
    body = f"""
<style>
.scroll {{ overflow:auto;max-height:560px; }}
.numeric {{ text-align:right;font-variant-numeric:tabular-nums; }}
.reachable,.observed-reachable {{ color:var(--pass); }}
.unreachable,.expected-unreachable {{ color:var(--warn); }}
.unexplained-reachable-loss,.unexpected-reachable {{ color:var(--fail); }}
.activation-associated-transient {{ color:var(--cyan); }}
code {{ overflow-wrap:anywhere; }}
</style>
<section class="card"><span class="status {'PASS' if overview['telemetry_complete'] else 'WARN'}">{'COMPLETE' if overview['telemetry_complete'] else 'INCOMPLETE'}</span>
  <h2>Physical evidence + planned intent + runtime telemetry</h2>
  <p>Formal PhysicalFrame positions provide the physical presentation. Self-contained Plan replay provides explicit planned reachability. Compiled evidence provides activation and lineage. Packet/Runner records provide runtime facts.</p>
  <p class="warning">A planned-unreachable result is never guessed from missing forwarding. Reachable loss is separated into activation-associated transient loss and unexplained reachable loss.</p>
  <pre>PhysicalWorld fingerprint: {escape(str(overview['physical_world_fingerprint']))}\nCompiler evidence: {escape(str(overview['compiler_evidence']))}\nclassification counts: {escape(str(overview['classification_counts']))}</pre>
</section>
<section><h2>Inventory</h2><div class="grid stats">{''.join(_stat(key, value) for key, value in inventory.items())}</div></section>
<section class="card"><h2>Traffic transactions against planned Route outcomes</h2>
  <p>Each row keeps packet direction, the matched explicit demand, planned path or unreachable reason, and the final taxonomy classification separate.</p>
  <div class="scroll"><table><thead><tr><th>ICMP</th><th>Direction</th><th>Observed time</th><th>Formal boundary</th><th>Observed</th><th>Planned</th><th>Planned path / reason</th><th>Classification</th><th>Evidence reason</th></tr></thead><tbody>{transaction_rows or '<tr><td colspan="9">No ICMP transactions observed.</td></tr>'}</tbody></table></div>
</section>
<section class="card"><h2>Formal planning and physical evidence coverage</h2>
  <p>{inventory['physical_frames']} PhysicalFrames align exactly with {len(data['planned_evidence']['formal_transitions'])} planned boundaries.</p>
  <div class="scroll"><table><thead><tr><th>Timestamp</th><th>Plan</th><th>Operational connections</th><th>Zones served / unserved</th><th>Routes reachable / unreachable</th><th>State fingerprint</th></tr></thead><tbody>{boundary_rows}</tbody></table></div>
</section>
<section class="card"><h2>Probe qualification</h2>{qualification_body}</section>
<section class="card"><h2>Machine evidence</h2><p><code>traffic-replay.json</code> retains every formal ECEF position, canonical Plan transition and compact state summary, the exact final planned state once, matched transaction Route outcomes and reasons, Plan/realization lineage, packet records, classifications, workload records, probe results, and Tool accounting. Earlier full planned states are replayable offline and are not serialized once per frame.</p></section>
"""
    return standalone_page(
        title="Traffic / Path Replay",
        eyebrow="Explicit planned reachability and runtime evidence",
        body=body,
    )


def _stat(label: object, value: object) -> str:
    return (
        '<div class="card stat"><span class="muted">'
        f"{escape(str(label))}</span><strong>{escape(str(value))}</strong></div>"
    )


def _transaction_row(value: dict[str, Any]) -> str:
    request = value["request"]
    observed_time = None if request is None else request["simulation_timestamp"]
    direction = (
        "unknown"
        if request is None
        else f"{request['source_ipv4']} → {request['destination_ipv4']}"
    )
    outcome = value["route_outcome"]
    if outcome is None:
        planned_detail = "no matching explicit Route demand"
    elif outcome["kind"] == "reachable":
        planned_detail = " → ".join(_node_label(item) for item in outcome["path"])
    else:
        planned_detail = str(outcome["reason"])
    return f"""<tr><td>{value['identifier']} / {value['sequence']}</td>
<td>{escape(direction)}</td><td>{escape(str(observed_time))}</td><td>{escape(str(value['formal_boundary_timestamp']))}</td>
<td>{'reply' if value['observed_reply'] else 'no reply'}</td>
<td class="{escape(str(value['planned_reachability']))}">{escape(str(value['planned_reachability']))}</td>
<td>{escape(planned_detail)}</td>
<td class="{escape(str(value['classification']))}">{escape(str(value['classification']))}</td>
<td>{escape(str(value['classification_reason']))}</td></tr>"""


def _node_label(value: dict[str, Any]) -> str:
    return f"AS{value['asn']}:{value['entity_kind']}:{value['entity_id']}"


def _boundary_row(value: dict[str, Any]) -> str:
    state = value["state_summary"]
    return f"""<tr><td>{escape(str(value['timestamp']))}</td><td><code>{escape(str(value['plan_id']))}</code></td>
<td class="numeric">{state['operational_connection_count']}</td>
<td class="numeric">{state['zones_served']} / {state['zones_unserved']}</td>
<td class="numeric"><span class="reachable">{state['reachable_route_count']}</span> / <span class="unreachable">{state['unreachable_route_count']}</span></td>
<td><code>{escape(str(value['state_fingerprint']))}</code></td></tr>"""


def _qualification(value: dict[str, Any]) -> str:
    summary = value["summary"]
    rows = "".join(
        f"<tr><td>{escape(str(item['episode_id']))}</td>"
        f"<td>{escape(', '.join(item['classifications']))}</td>"
        f"<td>{item['sent']} / {item['received']}</td>"
        f"<td>{item['expected_unreachable_observations']}</td></tr>"
        for item in value["episodes"]
    )
    return f"""<p><span class="status {escape(str(summary['status']))}">{escape(str(summary['status']))}</span> active frontier {escape(str(summary['active_frontier']))}; evidence complete {escape(str(summary['global_evidence_complete']))}</p>
<p>Traffic probe-plan construction: {escape(str(summary['probe_plan_construction_duration_seconds']))} seconds.</p>
<div class="scroll"><table><thead><tr><th>Episode</th><th>Classifications</th><th>Sent / received</th><th>Expected-unreachable observations</th></tr></thead><tbody>{rows}</tbody></table></div>"""


__all__ = ["render_traffic_replay_page"]
