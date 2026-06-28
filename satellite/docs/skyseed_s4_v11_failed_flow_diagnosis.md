# S4 v11 failed-flow diagnosis

Source artifacts: `/tmp/skyseed_s4_bpf_owned_event_timeline_bounded/s4-event-timeline-bpf-owned-strict-v11`.

## Finding

v11 did not fail because the final-validation flows were broadly absent from generated strict BPF scope. All 38 failed flows are present in the latest `bpf_owned_readiness.resolved_packet_route_entries`; all 38 have return/handoff entries; 36/38 have a target-matching forward entry in the final generation and canary scope.

The stronger blocker is validation timing/lifecycle: final validation attempted to read `/sys/fs/bpf/tc/globals/packet_counters` and got `No such file or directory` (`returncode=255`). That means the final validation packets were not observed through the same attached BPF map/program state that the later/standalone canary validated. With BPF absent from that validation path and Linux G/Z/MPLS fallback disabled, packets fell back to ordinary UT-edge routing semantics and the satellite-side UT access peer returned `Destination Net Unreachable`.

## Scope summary

- `failed_flow_count`: 38
- `present_in_bpf_owned_readiness_count`: 38
- `inside_generated_gz_fec_any_snapshot_count`: 38
- `inside_generated_gz_fec_final_plan_count`: 36
- `inside_bpf_forward_scope_for_target_count`: 36
- `inside_bpf_return_handoff_scope_count`: 38
- `inside_canary_forward_scope_for_target_count`: 36
- `classification_counts`: `{"covered_by_bpf_scope_but_final_validation_bpf_maps_absent": 36, "return_handoff_present_but_forward_target_missing_in_final_generation": 2}`
- `final_validation_counter_read`: returncode `255`, stdout `{"error":"bpf obj get (/sys/fs/bpf/tc/globals/packet_counters): No such file or directory"}`
- `canary_ok`: `True`, canary counters `{"canonical_fallback": 57, "drop": 0, "drop_bad_packet": 0, "drop_inactive_fec": 0, "drop_inactive_parameter": 0, "drop_loss": 0, "drop_missing_fec": 0, "drop_missing_generation": 0, "drop_missing_parameter": 0, "fec_hit": 6, "parameter_entry_hit": 6, "parameter_generation_hit": 6, "pass": 57, "redirect": 6, "return_handoff_hit": 4, "runtime_generation_hit": 6, "seen": 63}`

## Canary vs final validation

- Canary passed because it installed/read its own BPF state and proved aggregate BPF hits: `seen=63`, `redirect=6`, `return_handoff_hit=4`, with zero BPF drop/miss counters.
- Final validation failed because its own counter read found no pinned `packet_counters` map. Its packets therefore were not attributed to active BPF map/program state in the artifact.
- The two uncovered forward-target cases are override flows `ut2011 -> 10.160.0.72 via gw204` and `ut2025 -> 10.160.0.72 via gw204`; both still have return handoff entries, but no final-generation forward entry for that target.

## Destination Net Unreachable source

`10.97.x.1` is the satellite-side dynamic endpoint on each UT access slot, for example `ut2001-access-slot0` has UT `10.97.209.2/29` and satellite peer `10.97.209.1/29` on `stl-940094:ua-u2001s0`. It is the source of ICMP unreachable because, with BPF forwarding inactive/absent and Linux G/Z fallback disabled, the serving satellite receives the UT packet on the access interface but has no ordinary Linux route/FIB path to the Mini Internet target.

## Failed-flow table

| i | flow | src | target | gw | sat | icmp from | owner | fwd | ret | canary | class |
|---:|---|---|---|---|---|---|---|---:|---:|---:|---|
| 0 | `ut-internet-egress:ut2001` | `10.97.209.2` | `10.154.0.129` | `gw201` | `stl-940094` | `10.97.209.1` | `stl-940094` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 1 | `ut-internet-egress:ut2002` | `10.97.210.2` | `10.154.0.129` | `gw201` | `stl-940094` | `10.97.210.1` | `stl-940094` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 2 | `ut-internet-egress:ut2003` | `10.97.211.2` | `10.154.0.129` | `gw201` | `stl-940094` | `10.97.211.1` | `stl-940094` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 3 | `ut-internet-egress:ut2004` | `10.97.212.2` | `10.154.0.129` | `gw201` | `stl-940094` | `10.97.212.1` | `stl-940094` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 4 | `ut-internet-egress:ut2005` | `10.97.213.2` | `10.154.0.129` | `gw203` | `stl-940070` | `10.97.213.1` | `stl-940070` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 5 | `ut-internet-egress:ut2005:override:asn160_via_selected_secondary_gateway` | `10.97.213.2` | `10.160.0.72` | `gw204` | `stl-940070` | `10.97.213.1` | `stl-940070` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 6 | `ut-internet-egress:ut2006` | `10.97.214.2` | `10.154.0.129` | `gw203` | `stl-940070` | `10.97.214.1` | `stl-940070` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 7 | `ut-internet-egress:ut2006:override:asn160_via_selected_secondary_gateway` | `10.97.214.2` | `10.160.0.72` | `gw204` | `stl-940070` | `10.97.214.1` | `stl-940070` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 8 | `ut-internet-egress:ut2007` | `10.97.215.2` | `10.154.0.129` | `gw203` | `stl-940070` | `10.97.215.1` | `stl-940070` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 9 | `ut-internet-egress:ut2007:override:asn160_via_selected_secondary_gateway` | `10.97.215.2` | `10.160.0.72` | `gw204` | `stl-940070` | `10.97.215.1` | `stl-940070` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 10 | `ut-internet-egress:ut2008` | `10.97.216.2` | `10.154.0.129` | `gw203` | `stl-940070` | `10.97.216.1` | `stl-940070` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 11 | `ut-internet-egress:ut2008:override:asn160_via_selected_secondary_gateway` | `10.97.216.2` | `10.160.0.72` | `gw204` | `stl-940070` | `10.97.216.1` | `stl-940070` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 12 | `ut-internet-egress:ut2009` | `10.97.217.2` | `10.154.0.129` | `gw205` | `stl-940251` | `None` | `` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 13 | `ut-internet-egress:ut2010` | `10.97.218.2` | `10.154.0.129` | `gw205` | `stl-940251` | `None` | `` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 14 | `ut-internet-egress:ut2011` | `10.97.219.2` | `10.154.0.129` | `gw205` | `stl-940024` | `10.97.219.1` | `stl-940024` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 15 | `ut-internet-egress:ut2011:override:asn160_via_selected_secondary_gateway` | `10.97.219.2` | `10.160.0.72` | `gw204` | `stl-940024` | `10.97.219.1` | `stl-940024` | false | true | false | `return_handoff_present_but_forward_target_missing_in_final_generation` |
| 16 | `ut-internet-egress:ut2012` | `10.97.220.2` | `10.154.0.129` | `gw205` | `stl-940251` | `None` | `` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 17 | `ut-internet-egress:ut2013` | `10.97.221.2` | `10.154.0.129` | `gw207` | `stl-940047` | `10.97.221.1` | `stl-940047` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 18 | `ut-internet-egress:ut2014` | `10.97.222.2` | `10.154.0.129` | `gw207` | `stl-940047` | `10.97.222.1` | `stl-940047` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 19 | `ut-internet-egress:ut2015` | `10.97.223.2` | `10.154.0.129` | `gw207` | `stl-940047` | `10.97.223.1` | `stl-940047` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 20 | `ut-internet-egress:ut2016` | `10.97.224.2` | `10.154.0.129` | `gw207` | `stl-940047` | `10.97.224.1` | `stl-940047` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 21 | `ut-internet-egress:ut2017` | `10.97.225.2` | `10.154.0.129` | `gw209` | `stl-940001` | `10.97.225.1` | `stl-940001` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 22 | `ut-internet-egress:ut2018` | `10.97.226.2` | `10.154.0.129` | `gw209` | `stl-940001` | `10.97.226.1` | `stl-940001` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 23 | `ut-internet-egress:ut2019` | `10.97.227.2` | `10.154.0.129` | `gw209` | `stl-940001` | `10.97.227.1` | `stl-940001` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 24 | `ut-internet-egress:ut2020` | `10.97.228.2` | `10.154.0.129` | `gw209` | `stl-940001` | `10.97.228.1` | `stl-940001` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 25 | `ut-internet-egress:ut2021` | `10.97.229.2` | `10.154.0.129` | `gw211` | `stl-940117` | `10.97.229.1` | `stl-940117` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 26 | `ut-internet-egress:ut2022` | `10.97.230.2` | `10.154.0.129` | `gw211` | `stl-940117` | `10.97.230.1` | `stl-940117` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 27 | `ut-internet-egress:ut2023` | `10.97.231.2` | `10.154.0.129` | `gw211` | `stl-940117` | `10.97.231.1` | `stl-940117` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 28 | `ut-internet-egress:ut2024` | `10.97.232.2` | `10.154.0.129` | `gw211` | `stl-940117` | `10.97.232.1` | `stl-940117` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 29 | `ut-internet-egress:ut2025` | `10.97.233.2` | `10.154.0.129` | `gw205` | `stl-940024` | `10.97.233.1` | `stl-940024` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 30 | `ut-internet-egress:ut2025:override:asn160_via_selected_secondary_gateway` | `10.97.233.2` | `10.160.0.72` | `gw204` | `stl-940024` | `10.97.233.1` | `stl-940024` | false | true | false | `return_handoff_present_but_forward_target_missing_in_final_generation` |
| 31 | `ut-internet-egress:ut2026` | `10.97.234.2` | `10.154.0.129` | `gw205` | `stl-940251` | `None` | `` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 32 | `ut-internet-egress:ut2027` | `10.97.235.2` | `10.154.0.129` | `gw207` | `stl-940047` | `10.97.235.1` | `stl-940047` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 33 | `ut-internet-egress:ut2028` | `10.97.236.2` | `10.154.0.129` | `gw207` | `stl-940047` | `10.97.236.1` | `stl-940047` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 34 | `ut-internet-egress:ut2029` | `10.97.237.2` | `10.154.0.129` | `gw209` | `stl-940001` | `10.97.237.1` | `stl-940001` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 35 | `ut-internet-egress:ut2030` | `10.97.238.2` | `10.154.0.129` | `gw209` | `stl-940001` | `10.97.238.1` | `stl-940001` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 36 | `ut-internet-egress:ut2031` | `10.97.239.2` | `10.154.0.129` | `gw207` | `stl-940047` | `10.97.239.1` | `stl-940047` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |
| 37 | `ut-internet-egress:ut2032` | `10.97.240.2` | `10.154.0.129` | `gw207` | `stl-940047` | `10.97.240.1` | `stl-940047` | true | true | true | `covered_by_bpf_scope_but_final_validation_bpf_maps_absent` |

## Recommendation

Primary fix: fix generation timing/lifecycle so final validation runs while strict BPF-owned programs/maps for the active runtime generation remain attached and readable. Do not add Linux G/Z/MPLS fallback and do not weaken validation.

Secondary fix: repair BPF route generation for the two override target flows missing final-generation forward entries: `ut-internet-egress:ut2011:override:asn160_via_selected_secondary_gateway` and `ut-internet-egress:ut2025:override:asn160_via_selected_secondary_gateway`.

Not recommended as the primary fix: qdisc/TheaterQ work, Linux fallback, or hardcoded UT/target exceptions.
