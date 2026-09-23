# Gen1 orbital filing provenance

`fcc-sat-mod-20200417-00037-gen1.json` preserves all 4,408 individual satellite
records from the Schedule S technical data accompanying SpaceX filing
[`SAT-MOD-20200417-00037`](https://fccprod.servicenowservices.com/icfs?id=ibfs_filing&number=SAT-MOD-20200417-00037).

## Authoritative attachment

The source is `technical_parameters.mdb`, legacy FCC attachment key `2274623`:

- legacy official URL:
  `https://licensing.fcc.gov/myibfs/download.do?attachment_key=2274623`;
- byte size: `2,691,072`;
- SHA-256:
  `d234f13d42b03ca465fe938eaea33ae62abd84ee6d9a4e12762f0d8fa55a9e3c`;
- filing epoch: `2015-01-01T00:00:00Z`;
- reference body: Earth.

The attachment was decoded directly with `access-parser` 0.0.6 during the S4
provenance audit. The repository record groups the direct `Planes` and
`Satellites` values by source element and plane. Every published mean anomaly
is retained at its one-decimal precision; formulas are not substituted for the
individual records.

## Evidence classes

Explicit official filing values are the element and plane identifiers, RAAN,
inclination, apogee, perigee, period, argument of perigee, active arc, satellite
counts, and individual mean anomalies. Circular eccentricity is derived
unambiguously from equal apogee and perigee. Regular RAAN or anomaly steps are
derived summaries, not separately published exact formulas.

Jonathan McDowell's [filing-data index](https://planet4589.org/astro/starsim/fcc.html)
and the Richard Cole / Jonathan McDowell
[`Gen1m.csv`](https://planet4589.org/astro/starsim/docs/Gen1m.csv) conversion
were secondary preservation and cross-check aids. The CSV SHA-256 is
`7bde0b183c381e648fd6a0926a227369ad3ea0b49fd280472a32b046a4ccd1d3e`.
Neither secondary artifact supersedes the FCC attachment.

The 2015 filing epoch is evidence metadata. Skyseed's 2026 benchmark epoch is a
local re-anchoring and does not claim physical propagation from 2015. The S4
example keeps its smaller, reviewed six-plane reference record locally and does
not load this archive at runtime.
