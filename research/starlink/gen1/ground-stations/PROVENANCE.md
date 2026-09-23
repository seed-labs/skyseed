# Starlink gateway evidence snapshot — 2026-08-25

`gateway-evidence-2026-08-25.json` is a bounded, reusable research snapshot. It
contains 358 physical-site evidence records: 356 coordinate clusters plus two
Ofcom-granted UK sites whose specific coordinates were not extracted. It is not
a claim of worldwide completeness and is not an S4 runtime input.

## Collection and authority

The broad base is the 363-record `starlink.sx` gateway-map snapshot retrieved
on 2026-08-25 (182,395 bytes; SHA-256
`a660c7f7624ddf791e239d5a8d76ac6973ec5bba3ac42d327b08c7844bf0c6a4`).
It contributes class-D community coordinates, not regulator authority. Of its
records, 343 have Ka/E gateway assignments and supportable claims for this
snapshot. Four TT&C-only records, 14 records without a gateway-band assignment,
and two explicitly “reported, not verified” Philippine records are excluded.
The 343 gateway records collapse to 324 physical clusters at 500 metres.

Fifty exact-coordinate SpaceX FCC filing records were then added or overlaid.
They produce 48 class-A physical sites, one of which is the withdrawn-only
Centerville filing excluded by the proposal. FCC filing status and operational
status remain separate. Several 2026 STAs explicitly concern continuation of
operations; new applications are classified planned unless other evidence says
the site is already operating.

UK Ofcom records establish licensing or operator history for Goonhilly,
Chalfont, Morn Hill, Wherstead, Woodwalton, Fawley, Isle of Man, Bristol, and
Hoo. The 2026 Harlow and Mulberry Wharf licences are retained with null
coordinates rather than geocoded. New Zealand RSM material corroborates the
Hinds Starlink gateway near `-44.00, 171.57`, but the retained specific Hinds
coordinate remains class D. Australia's ACMA RRL and daily download were
investigated, but no Starlink-specific ACMA coordinate extraction was completed
in this bounded pass; Australian coordinates therefore remain class D.

Coordinate authority and operational evidence are intentionally independent:

- A: regulator/direct SpaceX filing with a specific site coordinate;
- B: other primary operator or site-host evidence;
- C: independently site-confirmed coordinate;
- D: community-maintained claimed gateway coordinate;
- E: approximate or ordinary geocoded location.

No class-E place-name coordinate is admitted to the proposal, and evidence is
not upgraded merely because a regulator confirms that a site or licence exists.

## Proposed frozen benchmark rule

As of 2026-08-25, include one physical site per evidence cluster of at most 500
metres when it has a valid coordinate and supported Ka- or E-band gateway
evidence in classes A-D. Include current, historical, and planned sites because
the qualification experiment needs broad geometry, not a live operational
network inventory. Exclude TT&C-only records, reported-not-verified or
unconfirmed records, withdrawn/superseded-only filings, and records without a
supportable specific coordinate.

The rule yields **355 sites**: 47 class A, one class C, and 307 class D; 202 are
current, 150 planned, and three historical according to the retained evidence.
It is count-independent: 355 is the result of the rule, not a target.

The historical 250 records match 230 legacy records to 223 physical evidence
sites. Ninety-eight legacy coordinates are confirmed within 500 m; 109 are
corrected approximate coordinates 0.5-25 km away; 23 are contradicted by a
matched site's coordinate more than 25 km away; and 20 remain removed or
unresolved. The proposed set contains 132 physical sites without a legacy-250
match. The JSON preserves every match and the unmatched list.

## Required spot checks

The legacy Aerzen coordinate `52.0489, 9.2683` is about 4.3 km from the
independently visually confirmed rooftop gateway at the Onlime teleport,
approximately `52.060989, 9.328231`. The legacy value is an approximate town
location, not a defensible antenna coordinate. The physical coordinate is class
C. A July 2026 visual report says the antennas were removed, so the record is
historical; that status does not reduce coordinate confidence.

The legacy Piti coordinate `13.4156, 144.6879` is not merged into Tumon. FCC
filing `SES-AMD-20220610-00634`, call sign `E220020`, places the separate gateway
at Tumon (`13.50280556, 144.80519444`) after moving the proposal from Apra
Heights. No matching SpaceX FCC Piti filing was found. Piti remains
contradicted-or-unresolved and excluded, while Tumon is a separate class-A site.

## Limits and future accumulation

US exact-coordinate coverage is strongest because the FCC filings were
machine-readable and spot-checked. UK identity/licence coverage is strong but
some retained coordinates remain community-derived. New Zealand has targeted
primary corroboration; Australia and most other countries retain community
coordinates. A complete regulator-by-regulator reconstruction, historical
licence lifecycle audit, and primary-coordinate recovery outside the US could
change the count, but no discovered gap creates a simulation-semantic blocker.
Those are continuing archive tasks with diminishing returns for the present S4
decision, not reasons to keep the physical-input phase open indefinitely.
