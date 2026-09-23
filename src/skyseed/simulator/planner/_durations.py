"""Duration rules used by the concrete default selection algorithms."""
from datetime import timedelta


def _validate_service_duration(service):
    if not isinstance(service, timedelta):
        raise TypeError("minimum_service_duration must be a timedelta")
    if service < timedelta(0):
        raise ValueError("minimum_service_duration must be non-negative")


def _required_contact_duration(acquisition_duration, observation_interval, service):
    return observation_interval * (
        (acquisition_duration + observation_interval - timedelta.resolution)
        // observation_interval) + service


def _has_useful_contact(candidate, identifier, state, service):
    if identifier in state.operational_connections:
        return True
    if candidate.contact_window is None:
        return True  # Current feasibility, with no claim about future coverage.
    end = state.selected_since.get(identifier, state.timestamp) + _required_contact_duration(
        state.acquisition_duration, state.observation_interval, service)
    return end <= state.timestamp or candidate.contact_window.covers(state.timestamp, end)
