# STEP 2 — Cross-border Mesh → Shelter Routing Results

Validation date: 2026-08-22

Validation base main SHA: `e715a70934e3f106e9d993d84c2dd07f6313d222`

Verified GitHub Actions run: `32558665220`

Verified aggregate evidence artifact: `9472305747`

## Scope

STEP 2 routes only the 500 m meshes where `tsunami_inundation_ratio > 0`.
The source target count is exactly 1,090 meshes.

The legacy rule that restricted destination shelters to the mesh municipality was removed. A tsunami-compatible designated emergency evacuation place can be selected across a municipal border when it lies within the N03 municipality + 3 km routing AOI and is reachable on the OSM walking network.

## Verified result

- Target meshes: 1,090
- Unique output rows: 1,090
- Complete network routes: 1,062
- `no_network_path`: 26
- `network_coverage_gap`: 2
- Missing target meshes: 0
- Extra non-target meshes: 0
- Cross-border complete routes: 10
- Same-municipality restriction: disabled
- Release Gate: **PASS**

The two `network_coverage_gap` meshes are exactly the STEP 1 known gaps:

- `493253842`
- `493262112`

They remain in the denominator as explicit failures; they are not silently dropped or connected by a fabricated jump.

## Cross-border selections

Ten complete routes select a shelter outside the home municipality:

- 宇和島市 → 愛南町: 9 meshes
- 宇和島市 → 西予市: 1 mesh

This confirms that municipal boundaries must not be used as an evacuation-destination restriction.

## Distance accounting

For every complete route:

```text
total_walking_distance_m
  = origin_access_distance_m
  + network_path_distance_m
  + shelter_connector_distance_m
```

The maximum observed formula residual was `9.09e-13 m`, effectively zero.

Shelter-to-network connectors over 500 m are excluded before routing.

- Maximum allowed shelter connector: 500 m
- Maximum connector in a complete route: 388.36 m

`origin_access_distance_m` is the access cost from the mesh representative origin to the network seed. For edge-based origins it includes the off-network centroid-to-edge connector plus travel along the selected OSM edge to the seed node; it must not be interpreted solely as straight-line distance "to the road".

## Distance distribution for complete routes

- Median: 1,124.34 m
- P90: 3,363.03 m
- P95: 4,298.35 m
- Maximum: 9,717.88 m

Walking-time scenarios are generated at:

- 1.0 m/s
- 0.62 m/s
- 0.5 m/s

## Explicit route failures

`no_network_path` counts by municipality:

- 松山市: 18
- 今治市: 4
- 宇和島市: 2
- 西条市: 1
- 上島町: 1

`network_coverage_gap`:

- 宇和島市: 2

These statuses are retained for STEP 3/4 and must not be converted to zero-risk records.

## OSM acquisition resilience

The first 伊予市 OSM network acquisition failed transiently. The STEP 2 aggregate gate stopped as designed because one municipality artifact was missing. Re-running only the failed 伊予市 job succeeded with the same code and AOI, after which all 14 municipality artifacts were present and the 1,090-mesh aggregate gate passed.

The STEP 2 workflow now retries a failed municipality network acquisition up to three times and verifies both the GraphML file and municipality QA `status=complete` before routing starts. A failed network build therefore cannot fall through to the routing step as a misleading missing-file failure.

## Outputs

Permanent repository evidence:

- `data/qa/step2/step2_routing_summary.json`
- this document
- STEP 2 routing/release-gate scripts and tests

The full 1,090-route intermediate CSV is retained as the Actions aggregate artifact for this checkpoint. STEP 3 will regenerate/use the verified routing logic and publish route data after corrected tsunami-route exposure fields are attached. GraphML remains an unversioned ETL intermediate.

## STEP 3 readiness

**READY**

STEP 3 may calculate corrected route tsunami exposure for the 1,062 complete routes while retaining all 28 explicit routing failures in the 1,090-mesh analysis table.
