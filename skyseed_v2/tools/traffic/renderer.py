"""Render the detached Traffic / Path Replay page."""

from __future__ import annotations

from html import escape
from typing import Any

from tools._presentation.html import standalone_page, json_for_script
from tools._presentation.shell_display import _SHELL_CONTROLS, _SHELL_SCRIPT
from ._spatial_renderer import _SPATIAL_BODY, _traffic_spatial_script


def render_traffic_replay_page(data: dict[str, Any]) -> str:
    """Render explicit planned reachability beside runtime observations."""

    inventory = data["inventory"]
    overview = data["overview"]
    coverage_rows = "".join(_coverage_row(c) for c in data.get("shell_coverage", ()))
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
.unreachable,.expected-unreachable,.expected-handover-disturbance {{ color:var(--cyan); }}
.unexplained-reachable-loss,.unexpected-reachable {{ color:var(--fail); }}
.activation-associated-loss {{ color:var(--warn); }}
.stat {{ min-width:0; }}
.stat .muted {{ overflow-wrap:anywhere; }}
code {{ overflow-wrap:anywhere; }}
</style>
<section class="card"><span class="status {escape(str(overview.get('qualification_status') or ('PASS' if overview['telemetry_complete'] else 'WARN')))}">{escape(str(overview.get('qualification_status') or ('COMPLETE' if overview['telemetry_complete'] else 'INCOMPLETE')))}</span>
  <h2>Physical evidence + planned intent + runtime telemetry</h2>
  <p>Formal PhysicalPositionSnapshot positions provide the physical presentation. Self-contained Plan replay provides explicit planned reachability. Compiled evidence provides activation and lineage. Packet/Runner records provide runtime facts.</p>
  <p>Telemetry evidence: {'complete' if overview['telemetry_complete'] else 'incomplete'}. Planned unreachability is informational and requires explicit planned evidence. Handover disturbance requires an actual relevant transition and bounded recovery; other reachable loss remains unexplained.</p>
  <pre>PhysicalWorld fingerprint: {escape(str(overview['physical_world_fingerprint']))}\nCompiler evidence: {escape(str(overview['compiler_evidence']))}\nclassification counts: {escape(str(overview['classification_counts']))}</pre>
</section>
<section class="card">{_SHELL_CONTROLS}
<p>Shell filters apply to observed hops and planned paths. Shared endpoints hidden by a filter remain explicitly marked in path text. These are overlapping coverage counts, not per-Shell executor packet totals.</p>
<div class="toolbar"><label>Shared boundary cursor <input id="traffic-time" type="range" min="0" max="{max(0,len(data['planned_evidence']['formal_transitions'])-1)}" value="0"></label><span id="traffic-time-label"></span><label><input id="traffic-all-times" type="checkbox" checked> Entire run</label></div></section>

{_SPATIAL_BODY}
<section class="card"><details><summary>Inventory and Shell test coverage</summary><section><h2>Inventory</h2><div class="grid stats">{''.join(_stat(key, value) for key, value in inventory.items())}</div></section><section class="card"><h2>Shell test coverage</h2><div class="scroll"><table><thead><tr><th>Shell</th><th>Coverage status</th><th>Planned episodes</th><th>Observed requests</th><th>Delivered replies</th><th>Directions observed</th><th>Stable / transition goals</th><th>Reason / limitation</th></tr></thead><tbody>{coverage_rows}</tbody></table></div><p>Coverage rows summarize the entire run under the selected Shell filter. Request/reply counts may overlap Shells on a path.</p></section></details></section>
<section class="card"><details><summary>Traffic transactions against planned Route outcomes · selected flow</summary>
<input id="traffic-query" placeholder="Episode, segment or ICMP identity">
<p id="traffic-visible-count" role="status">{'No ICMP transactions observed.' if not data['transactions'] else 'Loading paged transactions.'}</p><button id="traffic-prev">Previous page</button><button id="traffic-next">Next page</button>
  <p>Each row keeps packet direction, the matched explicit demand, planned path or unreachable reason, and the final taxonomy classification separate.</p>
  <div class="scroll"><table><thead><tr><th>Select transaction</th><th>Observed time</th><th>Formal boundary</th><th>Observed</th><th>Classification</th></tr></thead><tbody id="traffic-transactions"></tbody></table></div>
</details></section>
<section class="card"><h2>Formal planning and physical evidence coverage</h2>
  <p>{inventory['physical_frames']} PhysicalPositionSnapshots align exactly with {len(data['planned_evidence']['formal_transitions'])} planned boundaries.</p>
  <div class="scroll"><table><thead><tr><th>Timestamp</th><th>Plan</th><th>Operational connections</th><th>Zones served / unserved</th><th>Routes reachable / unreachable</th><th>State fingerprint</th></tr></thead><tbody>{boundary_rows}</tbody></table></div>
</section>
<section class="card"><h2>Probe qualification</h2>{qualification_body}</section>
<section class="card"><h2>Machine evidence</h2><p><code>traffic-replay.json</code> retains every formal ECEF position, canonical Plan transition and compact state summary, the exact final planned state once, matched transaction Route outcomes and reasons, Plan/realization lineage, packet records, classifications, workload records, probe results, and Tool accounting. Earlier full planned states are replayable offline and are not serialized once per frame.</p></section>
"""
    return standalone_page(
        title="Traffic / Path Replay",
        eyebrow="Explicit planned reachability and runtime evidence",
        body=body,
        script=_SHELL_SCRIPT + _traffic_spatial_script(data),
    )


def _stat(label: object, value: object) -> str:
    return (
        '<div class="card stat"><span class="muted">'
        f"{escape(str(label).replace('_', ' '))}</span><strong>{escape(str(value))}</strong></div>"
    )


def _coverage_row(value):
    goals = '<br>'.join(escape(f"{g['direction']} / {g['case']}: {g['eligible_candidate_observations']} retained eligible observations; selected {g['selected']}; observed requests {g['observed_requests']}") for g in value.get('goals', ())) or 'Eligibility was not recorded.'
    return f'<tr data-coverage-shell="{escape(value["shell_id"])}"><td>{escape(value["shell_id"])}</td><td>{escape(value["status"])}</td><td>{value["planned_episode_count"]}</td><td>{value["observed_requests"]}</td><td>{value["delivered_replies"]}</td><td>{escape(", ".join(value["observed_directions"]))}</td><td>{goals}</td><td>{escape(str(value["reason"] or ""))}</td></tr>'


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
        planned_detail = " → ".join(f'<span data-node-id="{escape(str(item["asn"])+"/"+item["entity_kind"]+"/"+item["entity_id"])}">{escape(_node_label(item))}</span>' for item in outcome["path"])
    else:
        planned_detail = escape(str(outcome["reason"]))
    return f"""<tr><td>{value['identifier']} / {value['sequence']}</td>
<td>{escape(direction)}</td><td>{escape(str(observed_time))}</td><td>{escape(str(value['formal_boundary_timestamp']))}</td>
<td>{'reply' if value['observed_reply'] else 'no reply'}</td>
<td class="{escape(str(value['planned_reachability']))}">{escape(str(value['planned_reachability']))}</td>
<td>{planned_detail}</td>
<td class="{escape(str(value['classification']))}">{escape(str(value['classification']))}</td>
<td>{escape(str(value['classification_reason']))}<br>{value["packet_observations"]} hop observations · {escape(str(value["pairing_status"]))} · {escape(str(value.get("reply_delivery", "unconfirmed")))}</td></tr>"""


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
<p>Raw executor totals: {summary['sent']} sent / {summary['received']} received / {summary['loss']} lost ({summary['loss_percent']:.3f}%). These totals do not equal timeout observations or packet-hop counts.</p>
<p>Global budget: {escape(str(summary.get('global_budget') or 'not recorded in historical plan'))}. Selected {summary['episodes']} episodes; estimated {summary['estimated_echo_requests']} echo requests. Planned concurrency bound: {escape(str(summary.get('planned_peak_concurrent_episodes', 'not recorded')))}; observed generator peak: {escape(str(summary.get('observed_peak_concurrent_generators', 'not recorded in this projection')))}.</p>
<p>Intended coverage: {escape(str(summary.get('coverage_status', 'NOT PROVEN')))}. Planned unreachability is informational. Normal handover disturbance requires an actual relevant transition, recovery, and a duration within {summary.get('qualification_policy', {}).get('maximum_transition_disruption_seconds', 2.0)} seconds (default 2; probe-plan qualification_policy, overridden by SKYSEED_TRAFFIC_MAX_TRANSITION_DISRUPTION_SECONDS). Missing request timestamps retain a conservative window from recorded ping outstanding reports, or generator launch when those clocks are unavailable; planned reachability must be constant throughout that window.</p>
<p>Traffic probe-plan construction: {escape(str(summary['probe_plan_construction_duration_seconds']))} seconds.</p>
<div class="scroll"><table><thead><tr><th>Episode</th><th>Classifications</th><th>Sent / received</th><th>Expected-unreachable request cases</th></tr></thead><tbody>{rows}</tbody></table></div>"""


__all__ = ["render_traffic_replay_page"]
