"""Uncertain recorded arrivals remain gaps, never planned conformance claims."""
from tools.observation.traffic._spatial import _observed_steps


def test_missing_ingress_and_ambiguous_association_do_not_create_hops():
    packets=[{'node_id':'b','kernel_monotonic_ns':1,'observation':'ingress',
              'simulation_timestamp':'2026-01-01T00:00:00+00:00',
              'ingress_identity':{'peer_node_id':'a','validity':'exact-generation','connection_id':'ab'}}]
    transaction={'request_packet_indices':[0],'association_uncertainty':['identifier collision']}
    steps=_observed_steps(transaction,'request',packets,{'a':0,'b':1},[])
    assert not steps[0]['arrival_supported'] and 'ambiguous' in steps[0]['gap_before']
    transaction['association_uncertainty']=[]
    assert _observed_steps(transaction,'request',packets,{'a':0,'b':1},[])[0]['arrival_supported']
    packets[0]['ingress_identity']=None
    assert not _observed_steps(transaction,'request',packets,{'a':0,'b':1},[])[0]['arrival_supported']
