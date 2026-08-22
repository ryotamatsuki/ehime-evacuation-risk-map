# Analysis correction staged plan — CLOSED

The STEP 1〜4 correction program is complete. This document is retained as the methodological release plan and closure record.

## Closure baseline

- correction phase final UI/data-contract merge: `42736642f668ef35616ef26297941dabce73f002`
- verified corrected production/deploy run: `32598837155`
- production target: 1,090 tsunami-exposed 500m meshes
- complete routes: 1,062
- explicit route unavailable: 28
- resolved cross-border routes: 13
- complete five-component scores: 813
- capacity-missing core-complete rows: 128
- core-data-incomplete rows: 121
- area-weighted over-capacity shelters: 35

The original UI freeze is lifted. Production UI is bound to the corrected Analysis Core v4 contract and has a real-data regression gate.

## STEP 1 — Routing foundation & spatial QA — CLOSED

Implemented:

- replace population-centroid convex hull AOIs with N03 municipal MultiPolygon AOIs
- buffer in EPSG:32653 rather than EPSG:3857
- allow neighboring-municipality shelter candidates inside the routing AOI
- measure origin and shelter connector distances
- review border, island, and disconnected-component cases
- prohibit fabricated >500m network jumps

## STEP 2 — Cross-border mesh-to-shelter routing — CLOSED

Production routes were regenerated using tsunami-compatible shelters reachable in the routing AOI rather than same-municipality shelters only. No-path and network-coverage failures remain explicit null/status states and stay in the 1,090-row denominator.

## STEP 3 — Route tsunami exposure — CLOSED

Route exposure was recalculated from the corrected STEP 2 routes. Source-tile missing/palette-unmatched coverage is distinguished from valid no-inundation. Route length, inundated distance, classified ratio, unknown coverage, and maximum depth class are retained.

## STEP 4 — Evacuation demand & shelter-level capacity pressure — CLOSED

The legacy per-mesh `population / shelter capacity` calculation was replaced by selected-shelter-level aggregated demand before capacity division.

Two explicit demand scenarios are retained:

- primary area-weighted proxy: `population × tsunami_inundation_ratio`
- full-mesh population sensitivity scenario

Missing official shelter capacity remains null. Full five-component risk score is produced only when all five components are available; missing components are not removed and remaining weights are not redistributed.

## Post-correction stages

### STEP 5 — Sensitivity / Robustness / Final Analytical QA — COMPLETED

The canonical v4 score is kept unchanged while 12 deterministic weight scenarios test ranking stability. See `docs/step5-sensitivity-methodology.md` and `docs/step5-sensitivity-results.md`.

### STEP 6 — Production polish / document synchronization / Safari-compatible QA

Synchronize public documentation and add production browser regression using Playwright WebKit. WebKit CI is an automated Safari-compatibility proxy; native Safari/iPhone Safari remains a separate manual-device check.

### STEP 7 — Policy Simulation

After STEP 6 passes, implement an explicit policy-scenario layer. The first release should model hypothetical shelter-capacity augmentation without mutating the canonical v4 score and without claiming capacity-constrained rerouting.

Every post-correction STEP follows the same gate: dedicated branch → implementation → automated QA → PR → merge → next STEP.
