# STEP 8–9 Capacity-Constrained Evacuation Planning

## Status and invariants

This is a new analytical layer on top of canonical Analysis Core v4. It does not replace or mutate STEP 2–7 outputs. The production regression contract remains 1,090 target meshes, 1,062 complete canonical routes, 28 route-unavailable meshes, 13 cross-border canonical routes, 813 complete five-component scores, 128 capacity-missing core meshes, and 35 area-weighted fixed-assignment overloaded shelters. Any STEP 8/9 implementation that changes those canonical values fails release.

`tests/step89_production_baseline.json` is the numerical source of truth for released STEP 8/9 values. STEP 9.5 corrected stale intermediate numbers that had been copied into the merged PR description; the implementation, baseline fixture, this document and release CI were already on the values below. Release notes must be copied from the frozen fixture rather than recomputed or manually transcribed from intermediate runs.

## STEP 8A — Model contract

STEP 8 introduces capacity-constrained allocation as a separate scenario. Each tsunami target mesh keeps multiple reachable tsunami-shelter candidates. Candidate ranking uses the same walking graph and distance accounting as STEP 2: origin access + network path + shelter connector. Candidate rank 1 must match canonical STEP 2 shelter identity and total distance for all 1,062 complete routes.

Shelter capacity is a hard upper bound only when a positive official capacity is available. Missing capacity is unknown, never zero. Unknown-capacity shelters remain visible in candidate diagnostics but are excluded from the strict known-capacity allocation scenario. Demand is conserved: every scaled unit is assigned to a known-capacity candidate or remains explicitly unserved. Area-weighted STEP 4 demand is primary and full-mesh demand remains a sensitivity scenario. Allocation may split one mesh demand among multiple shelters; this is a planning-flow approximation, not a claim about operational dispatch. Objective order is maximise served demand, then minimise walking distance. Administrative borders do not constrain allocation.

The deterministic solver uses integer min-cost flow with demand/capacity scaled to 0.1 person-equivalent. Capacity is floored after scaling so conversion can never exceed official capacity.

## STEP 8B — Multi-shelter candidate routing

A multi-label, multi-source Dijkstra retains reachable shelter identities in walking-distance order. STEP 8 production allocation is fixed at K=10. STEP 10 may generate a wider K=30 candidate table, but the STEP 8/9 solver still truncates that table back to K=10, so the released production contract cannot drift merely because wider sensitivity candidates were computed. Capacity does not affect routing.

Release gates require one candidate-status row per target mesh, exactly 1,062 complete and 28 unavailable, per-mesh status parity with STEP 2, rank-1 identity parity, rank-1 distance absolute difference <= 1e-6 m, and unique consecutive distance-ordered ranks.

The release implementation applies a double parity gate. Candidate rank 1 must reproduce the same-GraphML STEP 2 result and the immutable canonical public artifact for all 1,062 complete routes. The observed maximum distance delta is 9.094947017729282e-13 m. Same-GraphML routing contains 10 raw cross-border rows; the canonical public contract contains 13 because three shelter municipality attributes are corrected from official address metadata. The canonical value 13 is the public contract.

Production allocation uses K=10 and also solves K=5 as truncation sensitivity. Since K=10 contains K=5, K=10 may not have higher unserved demand or a worse min-cost objective.

## STEP 8C/D — Capacity-constrained allocation and comparison

Outputs remain separate from Analysis Core v4 and include mesh, flow and shelter tables for area-weighted and full-mesh demand. Comparison measures include served/unserved demand, saturated known-capacity shelters, split meshes, destination changes, cross-border allocated demand, added walking distance, and K=5 versus K=10 sensitivity. Under a hard-capacity solver a known-capacity shelter cannot exceed capacity; shortage appears as rerouting or explicit unserved demand rather than an overloaded assigned shelter.

The production KPI baseline is frozen in `tests/step89_production_baseline.json`. For area-weighted demand, K=10 serves 139,333.2 of 144,257.1 scaled people-equivalent and leaves 4,923.9 unserved; 139 meshes have only unknown-capacity candidates, 44 meshes are split, 328 change dominant destination, 49 known-capacity shelters saturate, and no known-capacity shelter exceeds capacity. K=5 leaves 5,263.2 unserved, so K=10 serves 339.3 more people-equivalent.

For full-mesh demand, K=10 serves 290,411 of 306,220 and leaves 15,809 unserved; 144 meshes have only unknown-capacity candidates, 86 meshes split, 378 change dominant destination, and 99 known-capacity shelters saturate. K=5 leaves 23,212 unserved, so K=10 serves 7,403 more.

## STEP 8E/F — Public comparison and release

The WebGIS exposes STEP 8 as a clearly labelled scenario layer while canonical v4 remains default. Alternative route geometry is not invented; the comparison focuses on allocation state, destination changes and shelter utilisation using existing mesh and shelter geometries. Release requires Python unit tests, candidate double parity, allocation conservation/capacity gates, frozen production KPI regression, TypeScript build, WebKit desktop/iPhone smoke and canonical v4 regression.

Canonical-output preservation is byte-level: all 33 pre-existing `public/data` files must remain SHA-256 identical. STEP 8/9 created eight additive files under `capacity-planning/`. STEP 10 adds four more diagnostic files under the same additive prefix, for 12 capacity-planning files total. Any changed, missing, or unexpected file outside that prefix fails release.

## STEP 9 — Added-capacity placement optimisation

STEP 9 optimises where a global added-capacity budget should be placed among existing shelters with known baseline capacity. A budget of +N means at most N additional capacity units may be placed across eligible existing shelters. A shared augmentation pool is added to the same min-cost-flow network, so network simplex returns the global optimum for the defined candidate graph, demand scenario, known capacities and budget.

Production budgets are +100, +500, +1,000, +2,000 and +5,000. Optimisation is run independently for area-weighted and full-mesh demand. Plan overlap and shared allocated capacity are robustness diagnostics.

In the primary area-weighted scenario, the tested added-capacity budgets do not reduce the 4,923.9 unserved demand. They reduce walking cost within the served population instead. This is consistent with the diagnosed shortage being dominated by route/candidate availability and unknown-capacity-only candidate sets rather than insufficient known capacity at an otherwise reachable alternative.

In the full-mesh sensitivity scenario, +100 reduces unserved demand by 100; +500 and every larger tested budget reduce it by 226, after which additional capacity primarily reduces walking cost. The +1,000 area-weighted plan uses the full 1,000-unit budget at one existing known-capacity shelter and has zero unserved-demand reduction; the corresponding full-mesh plan uses the full budget across three shelters and reduces unserved demand by 226.

STEP 9 is not a new-shelter siting model, construction-feasibility model, monetary cost-benefit model, or land/staffing/operations model. Without public project-cost and site-feasibility data those constraints must not be invented. New-facility siting, if added later, requires an explicit candidate-site dataset and cost assumptions.
