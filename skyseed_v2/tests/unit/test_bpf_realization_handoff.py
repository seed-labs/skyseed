from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from skyseed_v2.experiment._realization_handoff import (
    _PersistedRealizationSupplier,
    _read_persisted_realization_sequence,
    _write_realization_handoff,
)
from skyseed_v2.backend.bpf.compiled_realization import (
    BpfCompiledRealization,
    _IntendedState,
    _TopologySignature,
)
from skyseed_v2.shared import NodeId


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
NODE = NodeId(65001, "satellite", "sat-1")


def _realization(start: int, end: int) -> BpfCompiledRealization:
    return BpfCompiledRealization._create(
        start_slice_id=start,
        start_timestamp=None if start == 0 else START + timedelta(seconds=start),
        end_slice_id=end,
        end_timestamp=START + timedelta(seconds=end),
        resource_realizations=(),
        parameter_realizations=(),
        forwarding_realizations=(),
        zone_edge_realizations=(),
        constraints=(),
        intended_state=_IntendedState((), (), (), (), ()),
        topology_signature=_TopologySignature((NODE,), ()),
    )


def test_persisted_realization_supplier_streams_exact_sequence_and_terminal(
    tmp_path,
) -> None:
    values = (_realization(0, 4), _realization(4, 8))
    manifest = _write_realization_handoff(values, tmp_path / "realizations")
    supplier = _PersistedRealizationSupplier(manifest)

    assert supplier() == values[0]
    assert supplier.snapshot()["consumed_count"] == 1
    assert supplier() == values[1]
    assert supplier() is None
    assert supplier() is None
    assert supplier.snapshot() == {
        "manifest": str(manifest.resolve()),
        "realization_count": 2,
        "consumed_count": 2,
        "terminal_returned": True,
    }
    assert _read_persisted_realization_sequence(manifest) == values


def test_persisted_realization_supplier_fails_closed_on_payload_corruption(
    tmp_path,
) -> None:
    manifest = _write_realization_handoff(
        (_realization(0, 4),), tmp_path / "realizations"
    )
    value = json.loads(manifest.read_text(encoding="utf-8"))
    payload = manifest.parent / value["realizations"][0]["path"]
    payload.write_bytes(payload.read_bytes() + b"corrupt")

    with pytest.raises(ValueError, match="checksum differs"):
        _PersistedRealizationSupplier(manifest)()


def test_persisted_realization_manifest_rejects_noncontinuous_index_or_escape(
    tmp_path,
) -> None:
    manifest = _write_realization_handoff(
        (_realization(0, 4),), tmp_path / "realizations"
    )
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["realizations"][0]["path"] = "../outside.pickle.gz"
    manifest.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="sequence checksum|leaves its handoff root"):
        _PersistedRealizationSupplier(manifest)


def test_realization_handoff_rejects_noncontinuous_compiler_output(tmp_path) -> None:
    with pytest.raises(ValueError, match="continuous"):
        _write_realization_handoff(
            (_realization(0, 4), _realization(5, 8)),
            tmp_path / "realizations",
        )
