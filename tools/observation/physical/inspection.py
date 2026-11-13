"""Export real Physical queries to a standalone interactive inspection page."""
from pathlib import Path

from tools.observation._presentation.inspection_page import write_page
from tools.observation._presentation.scene_display import scene_inputs


def inspection_data(physical, timestamps):
    frames = []
    for timestamp in timestamps:
        snapshot = physical.snapshot_at(timestamp)
        reports = physical.candidates(snapshot)
        frames.append({
            "timestamp": snapshot.timestamp.isoformat(),
            "states": {identity: {"position_m": state.position_m,
                                    "velocity_m_s": state.velocity_m_s, "data": dict(state.data)}
                       for identity, state in snapshot.states.items()},
            "candidates": {identity: [{"peer_id": candidate.peer_id,
                                       "distance_m": candidate.distance_m,
                                       "propagation_delay_s": candidate.propagation_delay_s,
                                       "elevation_deg": candidate.elevation_deg,
                                       "data": dict(candidate.data)} for candidate in values]
                           for identity, values in reports.items()},
        })
    return {**scene_inputs(physical.scene), "format": "skyseed.physical-inspection/v1",
            "models": {"position": type(physical.position_model).__name__,
                       "candidate": type(physical.candidate_model).__name__},
            "frames": frames}


def write_inspection(data, output):
    return write_page(data, output, template=Path(__file__).with_name('viewer.html'),
                      data_name='physical')
