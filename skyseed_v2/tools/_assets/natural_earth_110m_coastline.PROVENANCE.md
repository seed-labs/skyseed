# Natural Earth coastline display reference

This adjacent JSON file is a frozen, Tool-private orientation reference. It is
not a Topology, Zone, membership, coverage, or GIS authority and is not exposed
as a public data API.

- Project: Natural Earth
- Theme: 1:110m physical-vector Coastline (major islands included)
- Theme version: 4.1.0
- Release snapshot used for conversion: `natural-earth-vector` tag `v5.1.2`
- Source GeoJSON: `https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_110m_coastline.geojson`
- Source GeoJSON SHA-256: `851f581ff5ffb844deed8ae1a9ce22e3c4bb3d74fa342cadb5d8e39b41ae7c3c`
- Frozen JSON SHA-256: `f28998a2a1d144098245d9877be7188a35a950c37232443a068fe53cd7e2ca9f`
- Frozen contents: 134 line strings / 5,128 coordinates
- Coordinate representation: `[latitude_deg, longitude_deg]`

The conversion discarded every feature property, bounding box, identifier,
country/border field, and other metadata, retained only the Coastline
LineString coordinates, reversed source `[longitude, latitude]` pairs into the
renderer-native order, and serialized compact JSON with one trailing newline.
No simplification or coordinate rounding was applied.

Natural Earth describes 1:110m as suitable for small global or locator maps and
states that all versions of its raster and vector data are public domain:

- `https://www.naturalearthdata.com/downloads/110m-physical-vectors/`
- `https://www.naturalearthdata.com/about/terms-of-use/`

Generated standalone Tool HTML embeds these frozen coordinates. Browsers make
no network request and load no GIS or JavaScript dependency.
