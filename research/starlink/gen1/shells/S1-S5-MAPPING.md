# FCC Gen1 to historical Skyseed S1-S5 mapping

The filing's four source elements map to the historical Skyseed shell names by
altitude, inclination, plane count, and satellites per plane. The filing values
are preserved in `fcc-sat-mod-20200417-00037-gen1.json`.

| Skyseed name | FCC source element / planes | Official shell shape | Historical Skyseed reconstruction |
| --- | --- | --- | --- |
| S1 | element 2, planes 1-72 | 550 km, 53.0°, 72×22; RAAN starts 0.0° with 5.0° cyclic spacing | Shape and normalized RAAN align; historical generated phase used alternating `180/22`, not the filing's cumulative explicit anomalies. |
| S2 | element 1, planes 1-72 | 540 km, 53.2°, 72×22; RAAN starts 2.5° with 5.0° cyclic spacing | Shape aligns; historical RAAN start 0.0° is a local global-orientation normalization, and historical phase used alternating `180/22`. |
| S3 | element 4, planes 1-36 | 570 km, 70.0°, 36×20; RAAN starts 0.0° with 10.0° cyclic spacing | Shape and RAAN align; historical phase used alternating `180/20`, not the filing's cumulative explicit anomalies. |
| S4 | element 3, planes 5-10 | 560 km, 97.6°, 6×58; RAAN `63.7, 123.7, 183.7, 243.7, 303.7, 3.7`° | v2 S4 uses the official source-plane ordering and clean `360/348` inter-plane plus `360/58` in-plane reconstruction, re-anchored at the 2026 benchmark epoch. |
| S5 | element 3, planes 1-4 | 560 km, 97.6°, four 43-satellite planes; RAAN `75.7, 87.7, 99.7, 117.7`° | Historical generated RAAN `0, 90, 180, 270`° and alternating `180/43` phase are local assumptions, not filing values. |

All listed altitude, inclination, counts, source RAANs, periods, arguments of
perigee, and one-decimal anomaly records are explicit filing values. Equal
apogee/perigee implying zero eccentricity and descriptions of regular spacing
are derived. The S1-S5 names, 2026 benchmark epoch, generated global orientation
normalizations, and alternating reconstruction are Skyseed-local conventions.
