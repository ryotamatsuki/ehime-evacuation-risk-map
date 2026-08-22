# STEP 5 — Sensitivity / Robustness / Final Analytical QA

STEP 5 does not change the corrected Analysis Core v4 baseline. It tests whether the policy-priority ranking is stable when the exploratory assumptions are varied.

## Canonical baseline

The production baseline remains the STEP 4 five-component score:

- tsunami exposure: 25
- vulnerable population: 20
- walking accessibility: 25
- route tsunami exposure: 15
- selected-shelter capacity pressure: 15

The score is exploratory and is not an official policy standard. Only `score_status=complete` rows receive a five-component score. Missing shelter capacity, incomplete core data, and route failures remain explicit null/status states.

## Weight sensitivity

Twelve deterministic scenarios are evaluated:

1. canonical baseline;
2. each of the five component weights multiplied by 0.8, one component at a time;
3. each of the five component weights multiplied by 1.2, one component at a time;
4. equal weights 20/20/20/20/20.

Each scenario uses all five components and divides by the scenario's total weight. No missing component is filled or renormalized away.

For the complete baseline rows, STEP 5 records:

- Spearman-equivalent rank correlation against baseline (Pearson correlation of rank vectors);
- top-decile overlap with baseline;
- top-50 overlap with baseline;
- each mesh's best, worst, median, and range of rank across all scenarios;
- meshes that remain in the top decile under every tested scenario;
- meshes that remain in the top 50 under every tested scenario.

These outputs measure prioritization robustness within the tested weight envelope. They are not probabilities or uncertainty intervals for an actual disaster.

## Demand sensitivity

The two demand scenarios defined in STEP 4 are compared without introducing a new forecast:

- primary: `total_population × tsunami_inundation_ratio`;
- sensitivity upper bound: full 500 m mesh population.

STEP 5 compares total demand, over-capacity shelter counts, and the existing full-mesh sensitivity score. It does not reroute overflow to another shelter.

## Missing-data impact

STEP 5 separately reports:

- complete five-component rows;
- core-only rows with missing shelter capacity;
- rows with incomplete core data;
- route-unavailable rows.

Only complete rows enter the weight-ranking sensitivity analysis. Excluded rows are never interpreted as low risk.

## Release gate

The production STEP 5 workflow consumes the latest successful `main` corrected-final-export artifact and verifies the v4 provenance before analysis. The gate preserves the production contract:

- target meshes: 1,090;
- complete routes: 1,062;
- complete five-component scores: 813;
- capacity-missing core-complete rows: 128;
- core-data-incomplete rows: 121;
- route unavailable: 28;
- cross-border routes: 13;
- area-weighted over-capacity shelters: 35;
- full-mesh over-capacity shelters: 72.

The baseline score is recomputed from exported components and must match the STEP 4 canonical score to numerical tolerance. STEP 5 never overwrites the canonical production score.
