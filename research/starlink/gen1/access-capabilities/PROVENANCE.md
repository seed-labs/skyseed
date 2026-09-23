# Gen1 Gateway-access capability provenance

This record preserves the external evidence used for the S4
GroundStation/Satellite access-capacity abstraction. It is research evidence,
not runtime input and not a claim that every individual gateway site has the
same installed hardware.

## GroundStation-side policy

FCC Earth Station Applications Public Notice `SES-02767`, attachment
[`DOC-411304A1`](https://docs.fcc.gov/public/attachments/DOC-411304A1.pdf),
describes SpaceX's Blountsville modification (`SES-MOD-20230301-00235`) as 40
technically identical 1.85 m antennas that can simultaneously communicate with
up to eight Gen1 satellites. This supports the S4 model-wide value
`maximum_ground_station_access_count = 8`.

That public-notice statement does not establish that every one of the 355
reviewed S4 sites has the same antenna inventory or physical capability.
Accordingly, their per-site
`GroundStation.max_simultaneous_satellite_accesses` values remain `None`.

## Satellite-side capability

SpaceX's original Gen1 Technical Attachment to FCC file
`SAT-LOA-20161115-00118`, attachment key `1158350`, describes the Ka-band
gateway beams as independently steerable and states that each satellite
transmits two co-frequency beams, one RHCP and one LHCP. A preserved filing
copy is indexed as
[`Technical Attachment 1158350`](https://fcc.report/IBFS/SAT-LOA20161115-00118/1158350.pdf).
The corresponding 2018 modification technical attachment,
`SAT-MOD-20181108-00083`, attachment key `1569860`, repeats the independently
steerable gateway-beam description and the two-beam-per-satellite operating
shape.

S4 maps that evidence to
`Satellite.max_simultaneous_ground_station_accesses = 2`: at most two
simultaneous GroundStation-access Connections may terminate on one Satellite.
This is an evidence-informed modeling abstraction for the two independently
steerable Ka-band gateway beams. It is not a total Satellite-degree cap, ISL
cap, UT-beam count, or antenna count.

The evidence does not define one beam as active and the other as standby.
Skyseed therefore assigns no active/standby role and introduces no handover
state machine. Two simultaneous links merely make overlap representable.

## Model interpretation

Geometric eligibility remains governed by the existing range, elevation, and
Earth-line-of-sight rules. The model allocates eligible links subject to both
ends' capacities. It first maximizes the number of GroundStations receiving at
least one access, then maximizes additional access-link cardinality, and uses
the existing higher-elevation/stable-identity ordering for deterministic
preference and reassignment. Access slots advance from zero. Within one slot,
requests use descending structural GroundStation identity; candidate
Satellites use descending elevation then ascending stable identity;
full-Satellite occupants are considered by descending
`(slot, GroundStation identity)`.
