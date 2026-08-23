# STEP 10 — Unserved Demand Root-Cause Analysis

## Purpose

STEP 10 explains why the released STEP 8 area-weighted K=10 allocation leaves 4,923.9 people-equivalent unserved. It does not reinterpret that number as a single shelter-capacity shortage. Instead, it separates routing failure, missing official capacity data, candidate-list truncation and residual known-capacity shortage.

The canonical Analysis Core v4 remains unchanged. STEP 8/9 production remains K=10 and continues to be frozen by `tests/step89_production_baseline.json`.

## Baseline observation before K=30 sensitivity

At the released K=10 baseline, the 4,923.9 unserved total is composed of 4,628.2 people-equivalent on meshes whose reachable candidates are all capacity-unknown and 295.7 on route-unavailable/no-candidate meshes. No K=10 area-weighted shortage is attributed to a mesh with at least one known-capacity candidate. This is why STEP 9 capacity augmentation cannot reduce the primary scenario shortage at K=10.

This observation is diagnostic only. STEP 10 must still test whether a known-capacity shelter appears beyond rank 10.

## Candidate sensitivity

Routing is generated once at K=30 using the same GraphML, connector limits and walking-distance definition as canonical STEP 2. The K=30 rank-1 candidate must pass the same double-parity contract as STEP 8: 1,062 / 1,062 identity matches against both the same-GraphML STEP 2 rebuild and the pinned canonical artifact, with distance tolerance <= 1e-6 m.

The allocation solver then truncates the same candidate table at K=10, K=20 and K=30. K=10 must reproduce the frozen STEP 8 value exactly. Total unserved demand must be monotonic non-increasing as K expands.

K=30 is a sensitivity bound, not proof that no farther shelter exists. A result that still contains unknown-capacity-only demand at K=30 means only that no capacity-known candidate was found within the first 30 reachable shelters under the defined routing model.

## Exhaustive decomposition contract

The released K=10 unserved total is decomposed into four mutually exhaustive aggregate causes:

1. `route_unavailable` — K=30 still has no reachable shelter candidate. Capacity augmentation cannot solve this component.
2. `unknown_capacity_only` — K=30 has reachable shelters, but none of the first 30 candidates has a positive official capacity value. Missing capacity remains unknown and is never treated as zero.
3. `candidate_limit_recoverable` — the net reduction in unserved demand from K=10 to K=30. This measures candidate-truncation sensitivity; it is not a new-facility siting recommendation.
4. `known_capacity_saturation` — residual K=30 unserved demand on meshes with at least one known-capacity candidate.

The four amounts must sum to the frozen K=10 unserved total within 1e-6. The release fails if the decomposition does not close.

## Capacity-data gap ranking

For residual `unknown_capacity_only` meshes, STEP 10 ranks capacity-unknown shelters that appear among their K=30 candidates. `residual_unserved_exposure` is an influence metric and may double-count a mesh across multiple candidate shelters. `nearest_gap_unserved_demand` counts only meshes for which the shelter is rank 1 and is therefore easier to interpret as a first data-confirmation target.

The ranking does not impute a capacity and does not claim that confirming a capacity value would automatically serve the exposed demand. It identifies where official capacity-data collection can remove the greatest current uncertainty.

## Municipality summary

Each municipality receives K=10, K=20 and K=30 unserved totals plus the K=30 residual split into route-unavailable, unknown-capacity-only and known-capacity-saturation components. The difference between K=10 and K=30 is shown separately as candidate-limit recoverable demand.

## Public WebGIS

STEP 10 is exposed inside the existing capacity-planning interface. The UI shows the four-cause decomposition, K=10/20/30 sensitivity, top capacity-data gaps and municipality-level summaries. The public contract adds four files under `public/data/capacity-planning/`:

- `step10-summary.json`
- `step10-root-causes.json`
- `step10-capacity-data-gaps.json`
- `step10-municipality-summary.json`

All canonical public files remain byte-identical. The total additive capacity-planning contract grows from 8 to 12 files.

## Release gates

STEP 10 must pass:

- Python synthetic root-cause unit tests.
- 14/14 municipality K=30 candidate routing.
- K=30 rank-1 double parity against same-GraphML STEP 2 and the pinned canonical artifact.
- Frozen STEP 8/9 production regression at K=10.
- K10 >= K20 >= K30 unserved monotonicity.
- Exact four-cause decomposition closure.
- Missing capacity never imputed or coerced to zero.
- Canonical public byte identity.
- TypeScript/Vite production build.
- Desktop and iPhone-equivalent Playwright WebKit checks, including STEP 10 decomposition visibility and canonical-app health after interaction.
