# S4 560 km / 97.6° / 6×58 filing reference

This directory contains an example-local, non-runtime reference extraction for
the six 58-satellite planes in SpaceX FCC filing
[`SAT-MOD-20200417-00037`](https://fccprod.servicenowservices.com/icfs?id=ibfs_filing&number=SAT-MOD-20200417-00037).
It is historical/reference evidence, not a public Skyseed schema, a scenario
input, or an alternative source of runtime authority.

## Official attachment identity

The authoritative source is the filing's `technical_parameters.mdb` Schedule S
technical-data attachment:

- legacy FCC attachment key: `2274623`;
- legacy FCC URL:
  `https://licensing.fcc.gov/myibfs/download.do?attachment_key=2274623`;
- size: `2,691,072` bytes;
- SHA-256:
  `d234f13d42b03ca465fe938eaea33ae62abd84ee6d9a4e12762f0d8fa55a9e3c`.

The current legacy FCC download URL redirects to the replacement filing portal
instead of returning the database bytes. The extraction used Jonathan
McDowell's preserved `2274623.jet` copy after verifying that its byte count and
SHA-256 match the
[`technical_parameters.mdb` filing index](https://fcc.report/IBFS/SAT-MOD-20200417-00037/2050095a879aa610b986a7570cbb35b3).
The preserved bytes were decoded directly; the secondary CSV was not used as
the source of the values stored in the JSON record.

The filing's
[`Schedule S` PDF](https://fcc.report/IBFS/SAT-MOD-20200417-00037/f5b34d1e879aa610b986a7570cbb35fc.pdf?raw=1)
is only a representative report and says that the complete technical data is
in the attached database. FCC Order
[`FCC-21-48`](https://docs.fcc.gov/public/attachments/FCC-21-48A1.pdf)
independently identifies the relevant shell as 348 satellites at 560 km and
97.6 degrees, arranged as six planes with 58 satellites per plane.

## Extraction

The database was decoded with the pure-Python `access-parser` 0.0.6 package.
The record preserves these direct selections:

- `System`: `SYSTEM_NAME = SpaceX Constellation` and `ORBIT_TYPE = NGSO`;
- `Ngso_orbit`: filing epoch `2015-01-01 00:00:00` and reference body `Earth`;
- `Planes`: `SATELLITES = 58`, `INCLINATION = 97.6`, `APOGEE = 560`, and
  `PERIGEE = 560`, selecting source plane IDs 5 through 10;
- `Satellites`: `ALTITUDE = 560` and source plane IDs 5 through 10, ordered by
  `SAT_ID` 1 through 58 within each plane.

`mean_anomaly_deg_by_source_satellite_id_1_to_58` stores every selected
`PHASE_ANGLE` value. Array position 1 corresponds to `SAT_ID = 1`, and so on.
The Schedule S report labels this satellite angle as mean anomaly. All 348
published values have one-decimal-degree precision; they are not replaced by
an inferred spacing formula.

## Evidence classification

### 1. Explicit official filing values

The database explicitly records:

- filing epoch and reference body;
- plane IDs 5 through 10;
- 58 satellites per selected plane;
- RAAN values `63.7`, `123.7`, `183.7`, `243.7`, `303.7`, and `3.7` degrees;
- inclination `97.6` degrees;
- apogee and perigee `560` km;
- orbital period `5760` seconds;
- argument of perigee `0` degrees;
- active arc `0` through `360` degrees;
- all 348 one-decimal mean-anomaly values preserved in the JSON record.

### 2. Unambiguously derived values

Equal apogee and perigee imply eccentricity zero for this circular shell. The
explicit RAAN list has a cyclic 60-degree separation. At the filing's published
precision, all anomaly records are reproduced by an in-plane `360/58` spacing
and a cumulative adjacent-plane `360/348` offset. Those formulas are derived
summaries; the attachment stores the individual one-decimal anomaly records,
not the exact formulas.

### 3. Secondary decoding and cross-check evidence

Jonathan McDowell publishes a
[`Gen1m` filing-data index](https://planet4589.org/astro/starsim/fcc.html),
including the preserved
[`2274623.jet` Access database](https://planet4589.org/astro/starsim/docs/2274623.jet)
and a
[`Gen1m.csv` conversion](https://planet4589.org/astro/starsim/docs/Gen1m.csv)
credited to Richard Cole. The CSV was used only to cross-check the direct
database decode. Its SHA-256 is
`7bde0b183c381e648fd6a0926a227369ad3ea0b49fd280472a32b046a4ccd1d3e`.
Neither the conversion nor this repository-local extraction supersedes the FCC
attachment.

### 4. Skyseed benchmark re-anchoring and local assumptions

The checked-in S4 scenario keeps its benchmark epoch at
`2026-01-01T00:00:00Z` and preserves the official plane/phase pairing in
source-plane order: RAAN starts at `63.7` degrees with a 60-degree step, plane
phase starts at zero with a cumulative `360/348` step, and in-plane phase uses
the `360/58` step. The individual one-decimal filing records remain the
authoritative evidence; the steps are derived reconstruction formulas.

The earlier S4 input used RAAN start zero as a Skyseed-local global-orientation
normalization and alternated Orbit starts by `180/58`. That alternating offset
was inherited from historical Skyseed construction rather than the FCC records
and is superseded for S4 because it does not reproduce the filing's cumulative
per-Orbit progression. Alternating phasing remains a supported general policy
for scenarios such as the canonical Mini. PositionModel period semantics and
connection/routing behavior are unchanged by this reference.

## Epoch and use limits

The absolute filing RAANs and anomalies are attached to the 2015 filing epoch.
The JSON deliberately preserves them as filing records. The intended S4
experiment uses the FCC-derived orbital geometry and phase pattern re-anchored
at the 2026 Skyseed benchmark epoch; it does not claim to propagate the real
constellation from 2015 to 2026. No runtime loader consumes this directory, and
no shared reference-data API is introduced.
