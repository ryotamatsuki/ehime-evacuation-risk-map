# Analysis correction staged plan

The published WebGIS remains available, but the analysis core is being corrected in four release-gated steps. UI work is frozen until the corrected outputs are regenerated.

## STEP 1 — Routing foundation & spatial QA

- replace population-centroid convex hull AOIs with N03 municipal MultiPolygon AOIs
- buffer in EPSG:32653, not EPSG:3857
- allow neighboring-municipality shelter candidates inside the routing AOI
- measure origin and shelter snap distances
- review border cases and island/disconnected components
- do not regenerate production routing yet

## STEP 2 — Cross-border mesh-to-shelter routing

After STEP 1 is approved, regenerate production routes using all tsunami-compatible shelters reachable in the AOI rather than same-municipality shelters only. Preserve no-path and snap failures as explicit null states.

## STEP 3 — Route tsunami exposure

Recalculate route exposure from the corrected routes. Distinguish source-tile missing/palette-unmatched states from valid no-inundation. Preserve route length, inundated distance, ratio and maximum depth class.

## STEP 4 — Evacuation demand & shelter-level capacity pressure

Replace per-mesh `population / shelter capacity` with shelter-level aggregated demand. Publish two explicit demand scenarios: area-weighted proxy (`population × tsunami_inundation_ratio`) and full-mesh upper bound. Recalculate risk scores only after corrected capacity metrics exist.

Each step must be implemented, QA'd, committed, reviewed and merged independently. A failed release gate stops progression to the next step.
