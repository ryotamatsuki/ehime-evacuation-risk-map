# Routing methodology — corrected foundation

## Why the legacy AOI is replaced

The original walking-network extractor used the convex hull of population mesh centroids plus a buffer. That is convenient for extraction but is not an appropriate policy-analysis boundary: a convex hull can span sea between islands and a small municipality-specific footprint can omit a closer shelter across an administrative border.

## AOI definition

STEP 1 uses the 2024 MLIT National Land Numerical Information N03 municipal boundary geometry. MultiPolygon structure is preserved. The geometry is projected to WGS84 / UTM zone 53N (`EPSG:32653`) for metric buffering, buffered by 3,000 m by default, and transformed back to WGS84 for OSMnx extraction.

The 3 km buffer is an analysis search margin, not an evacuation-distance threshold. STEP 1 records cross-border cases; STEP 2 may use adaptive expansion where the QA shows that 3 km is insufficient.

## Administrative borders

Municipality is metadata, not a hard routing constraint. A mesh may be assigned to a tsunami-compatible emergency evacuation site in a neighboring municipality if that site is reachable and closer by the pedestrian network.

## Snapping QA

Both mesh origins and tsunami-compatible shelters are snapped to the pedestrian graph and their snap distance is preserved.

- <=100 m: normal
- >100–250 m: review
- >250–500 m: warning
- >500 m: critical

These are QA classes, not grounds for automatic deletion or imputation. Critical origin records block STEP 2 until reviewed.

## Islands and disconnected components

Disconnected components are expected in island municipalities. The analysis never creates a synthetic edge between components. The QA explicitly records component sizes and checks for ferry-tagged edges so that ferry or inter-island behavior is not silently interpreted as ordinary walking.

## Data handling

GraphML and OSM cache data are local ETL intermediates and are not committed or served by GitHub Pages. Only QA summaries and later derived route metrics are published.
