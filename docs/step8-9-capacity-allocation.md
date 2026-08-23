# STEP 8–9 Capacity-Constrained Evacuation Planning

## Status and invariants

This is a new analytical layer on top of canonical Analysis Core v4. It does not replace or mutate STEP 2–7 outputs. The production regression contract remains 1,090 target meshes, 1,062 complete canonical routes, 28 route-unavailable meshes, 13 cross-border canonical routes, 813 complete five-component scores, 128 capacity-missing core meshes, and 35 area-weighted fixed-assignment overloaded shelters. Any STEP 8/9 implementation that changes those canonical values fails release.

## STEP 8A — Model contract

STEP 8 introduces capacity-constrained allocation as a separate scenario. Each tsunami target mesh keeps multiple reachable tsunami-shelter candidates. Candidate ranking uses the same walking graph and distance accounting as STEP 2: origin access + network path + shelter connector. Candidate rank 1 must match canonical STEP 2 shelter identity and total distance for all 1,062 complete routes.

Shelter capacity is a hard upper bound only when a positive official capacity is available. Missing capacity is unknown, never zero. Unknown-capacity shelters remain visible in candidate diagnostics but are excluded from the strict known-capacity allocation scenario. Demand is conserved: every scaled unit is assigned to a known-capacity candidate or remains explicitly unserved. Area-weighted STEP 4 demand is primary and full-mesh demand remains a sensitivity scenario. Allocation may split one mesh demand among multiple shelters; this is a planning-flow approximation, not a claim about operational dispatch. Objective order is maximise served demand, then minimise walking distance. Administrative borders do not constrain allocation.

The deterministic solver uses integer min-cost flow with demand/capacity scaled to 0.1 person-equivalent. Capacity is floored after scaling so conversion can never exceed official capacity.

## STEP 8B — Multi-shelter candidate routing

A multi-label, multi-source Dijkstra retains up to 10 nearest distinct shelter identities. Capacity does not affect routing. Release gates require one candidate-status row per target mesh, exactly 1,062 complete and 28 unavailable, per-mesh status parity with STEP 2, rank-1 identity parity, rank-1 distance absolute difference <= 1e-6 m, and unique consecutive distance-ordered ranks.

Production allocation uses K=10 and also solves K=5 as truncation sensitivity. Since K=10 contains K=5, K=10 may not have higher unserved demand or a worse min-cost objective.

## STEP 8C/D — Capacity-constrained allocation and comparison

Outputs remain separate from Analysis Core v4 and include mesh, flow and shelter tables for area-weighted and full-mesh demand. Comparison measures include served/unserved demand, saturated known-capacity shelters, split meshes, destination changes, cross-border allocated demand, added walking distance, and K=5 versus K=10 sensitivity. Under a hard-capacity solver a known-capacity shelter cannot exceed capacity; shortage appears as rerouting or explicit unserved demand rather than an overloaded assigned shelter.

## STEP 8E/F — Public comparison and release

The WebGIS exposes STEP 8 as a clearly labelled scenario layer while canonical v4 remains default. Alternative route geometry is not invented; the comparison focuses on allocation state, destination changes and shelter utilisation using existing mesh and shelter geometries. Release requires Python unit tests, candidate parity, allocation conservation/capacity gates, TypeScript build, WebKit desktop/iPhone smoke and canonical v4 regression.

## STEP 9 — Added-capacity placement optimisation

STEP 9 optimises where a global added-capacity budget should be placed among existing shelters with known baseline capacity. A budget of +N means at most N additional capacity units may be placed across eligible existing shelters. A shared augmentation pool is added to the same min-cost-flow network, so network simplex returns the global optimum for the defined candidate graph, demand scenario, known capacities and budget.

Production budgets are +100, +500, +1,000, +2,000 and +5,000. Optimisation is run independently for area-weighted and full-mesh demand. Plan overlap and shared allocated capacity are robustness diagnostics.

STEP 9 is not a new-shelter siting model, construction-feasibility model, monetary cost-benefit model, or land/staffing/operations model. Without public project-cost and site-feasibility data those constraints must not be invented. New-facility siting, if added later, requires an explicit candidate-site dataset and cost assumptions.
