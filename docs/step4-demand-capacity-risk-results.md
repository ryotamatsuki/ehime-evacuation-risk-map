# STEP 4 — Evacuation Demand, Shelter Capacity Pressure, and Exploratory Risk Results

Verified: 2026-08-22 JST  
Baseline main: `6d89cc68fd4e2ca435e70addf4677b8a7f98d596`  
Verified calculation head: `3ae22767767bc5349f6d755973d3bc20c890637f`  
GitHub Actions run: `32564613773`  
STEP 4 artifact: `analysis-step4` / artifact ID `9473850300`

## Release gate

**PASS** — no STEP 4 release-gate failures.

The same workflow run also re-ran and passed:

- STEP 2 unit tests and all 14 coastal-municipality route jobs;
- STEP 2 aggregate gate for exactly 1,090 tsunami-target meshes;
- STEP 3 unit tests and the 1,062-route tsunami-exposure gate;
- STEP 4 demand/capacity/risk unit tests.

## Routing population retained

- tsunami-target meshes: **1,090**
- complete routes: **1,062**
- `no_network_path`: **26**
- `network_coverage_gap`: **2**
- route-unavailable meshes retained in the denominator: **28**

Route-unavailable meshes are not force-assigned to a shelter and do not receive a numeric risk score.

## Evacuation-demand scenarios

The primary demand proxy is:

`mesh_evacuation_demand_area_weighted = total_population × tsunami_inundation_ratio`

This assumes that population is uniformly distributed inside each 500 m mesh. It is an analytical proxy, **not an evacuee forecast**.

A sensitivity scenario also retains the full 500 m mesh population.

### Primary area-weighted proxy

- total target demand: **144,255.716 people-equivalent**
- assigned to a reachable selected shelter: **143,959.994**
- unassigned because the route is unavailable: **295.722**

### Full-mesh sensitivity

- total target population: **306,220**
- assigned to a reachable selected shelter: **304,137**
- unassigned because the route is unavailable: **2,083**

Both scenarios passed demand-conservation checks: `assigned + unassigned = total`.

## Shelter capacity pressure

Demand is aggregated by the selected shelter **before** dividing by capacity. This replaces the legacy per-mesh calculation `mesh population / shelter capacity`.

- selected shelters receiving at least one mesh: **391**
- selected shelters with recorded capacity: **272**
- selected shelters with missing capacity: **119**
- assigned meshes whose selected shelter has missing capacity: **151**
- selected shelters above recorded capacity under area-weighted demand: **35**
- selected shelters above recorded capacity under full-mesh sensitivity: **72**
- maximum area-weighted capacity pressure: **64.0566×**
- maximum full-mesh capacity pressure: **73.2927×**
- ambiguous selected shelter identities: **0**

The maximum area-weighted pressure occurs at **グランフィールド松前町舎前**: 8 assigned meshes, area-weighted assigned demand about 7,878.964 people-equivalent, recorded capacity 123, yielding approximately 64.0566×. This is not a capacity-constrained allocation model: routing selects the reachable destination by the STEP 2 routing rule, then STEP 4 diagnoses the resulting capacity pressure. Overflow is not automatically reassigned to another shelter.

Missing capacity is retained as null. It is never interpreted as zero and never creates a numeric capacity-pressure value.

## Risk-score correction

The existing PoC weights are retained for continuity:

| Component | Weight |
|---|---:|
| Tsunami exposure | 25 |
| Vulnerable population | 20 |
| Walking accessibility | 25 |
| Route tsunami exposure | 15 |
| Shelter capacity pressure | 15 |

These weights are exploratory and **not an official policy standard**.

The corrected calculation produces two distinct score families:

1. `core_evacuation_difficulty_score`: four non-capacity components, for cases where the core data are complete;
2. `evacuation_difficulty_score`: all five components, only when every component including shelter capacity is available.

A missing capacity component is **not silently removed and the remaining four components are not reweighted into a five-component score**.

### Score availability

- complete five-component score: **813 meshes**
- core score only because shelter capacity is missing: **128 meshes**
- core data incomplete: **121 meshes**
- route unavailable: **28 meshes**
- core score non-null: **941 meshes**
- full five-component score non-null: **813 meshes**

Artifact inspection confirmed that all **121 `core_data_incomplete` meshes** lack the 65+ population-rate component; the walking, tsunami, and route-exposure components are present. This is therefore source-data completeness, not a routing/exposure calculation failure.

Of the 151 assigned meshes with missing capacity, 128 have otherwise complete core data; the remainder overlap the vulnerable-population data incompleteness above.

### Walking normalization

Walking distance is normalized from the 5th to 95th percentile of complete STEP 3 routes:

- P05: **226.659 m**
- P95: **4,298.348 m**

### Route-raster uncertainty

- routes with unknown raster coverage: **2**
- point score uses inundation share among classified coverage;
- lower bound treats unknown route coverage as non-inundated;
- upper bound treats unknown route coverage as inundated.

The lower / point / upper ordering is release-gated.

## Exploratory score distribution

From the verified artifact:

- core score: median **33.04**, P90 **55.26**, P95 **58.48**, max **77.71**
- full primary score: median **34.00**, P90 **59.76**, P95 **63.52**, max **79.05**
- full-mesh sensitivity score: median **36.35**, P90 **60.37**, P95 **63.95**, max **81.06**

These are analytical prioritization indices, not an official evacuation-risk classification and not probabilities.

## Interpretation constraints

- Primary demand uses area-weighted population and assumes within-mesh uniformity.
- Full-mesh population is a sensitivity scenario, not the preferred forecast.
- Selected-shelter capacity pressure is diagnostic. The routing engine is not capacity-constrained and does not redistribute overflow.
- Missing official capacity remains missing.
- Route tsunami exposure is raster exposure along the modeled route, not road-failure probability.
- The 25/20/25/15/15 composite is an exploratory PoC convention.
- The public GitHub Pages dataset is not considered corrected merely because STEP 4 passes. A separate final export/deployment stage must replace legacy published analysis data before public UI figures are treated as STEP 4 results.
