# STEP 5 — Sensitivity / Robustness / Final Analytical QA Results

Verified: 2026-08-23 JST  
STEP 5 PR: #12  
Merged main SHA: `6f3367aae87b9dcf1e73217e41ef6fe53af444f3`  
STEP 5 production QA run: `32605144352`  
STEP 5 evidence artifact: `analysis-step5-sensitivity` / artifact ID `9483928972`  
Input corrected production run: `32598837155`  
Input analysis source SHA: `42736642f668ef35616ef26297941dabce73f002`

## Release gate

**PASS**

- STEP 5 unit tests: 4/4 PASS
- corrected production artifact provenance: PASS
- Analysis Core v4 baseline contract: PASS
- sensitivity analysis: PASS
- output artifact upload: PASS

No canonical STEP 4 score was overwritten.

## Baseline population

- tsunami-target rows: **1,090**
- complete five-component rows ranked: **813**
- core-only due to missing capacity: **128**
- core-data-incomplete: **121**
- route unavailable: **28**
- total rows excluded from weight-ranking sensitivity: **277**

Excluded rows remain explicit missing/unavailable states and are not interpreted as low risk.

## Weight sensitivity design

Twelve deterministic scenarios were compared:

1. baseline `25 / 20 / 25 / 15 / 15`;
2. each of the five weights individually multiplied by `0.8`;
3. each of the five weights individually multiplied by `1.2`;
4. equal weights `20 / 20 / 20 / 20 / 20`.

The baseline was recalculated from exported component scores and matched the canonical STEP 4 `evacuation_difficulty_score` within the release-gate tolerance.

## Robust high-priority group

The baseline top-decile size is **82 meshes**.

- remain in top decile under all 12 scenarios: **69 meshes**
- remain in Top 50 under all 12 scenarios: **42 meshes**

This means the highest-priority set is substantially more stable than any exact rank number.

## Rank movement

Across all 12 scenarios:

- median per-mesh rank range: **40 places**
- P90 rank range: **91 places**
- maximum rank range: **160 places**

Therefore, exact ordering should not be read as a precise policy league table. A more defensible interpretation is to emphasize meshes that remain in the high-priority group across plausible exploratory weights.

## Scenario comparison against baseline

Selected results:

| Scenario | Rank correlation | Top 10% overlap | Top 50 overlap |
|---|---:|---:|---:|
| tsunami weight -20% | 0.997956 | 78/82 (95.12%) | 46/50 (92%) |
| tsunami weight +20% | 0.998475 | 79/82 (96.34%) | 47/50 (94%) |
| vulnerable population -20% | 0.999185 | 81/82 (98.78%) | 48/50 (96%) |
| vulnerable population +20% | 0.999197 | 81/82 (98.78%) | 47/50 (94%) |
| walking accessibility -20% | 0.996894 | 78/82 (95.12%) | 47/50 (94%) |
| walking accessibility +20% | 0.997358 | 78/82 (95.12%) | 45/50 (90%) |
| route exposure -20% | 0.998962 | 80/82 (97.56%) | 47/50 (94%) |
| route exposure +20% | 0.999191 | 80/82 (97.56%) | 49/50 (98%) |
| capacity pressure -20% | 0.998478 | 80/82 (97.56%) | 48/50 (96%) |
| capacity pressure +20% | 0.998815 | 81/82 (98.78%) | 48/50 (96%) |
| equal weights | **0.989418** | **75/82 (91.46%)** | **46/50 (92%)** |

The equal-weight case is the largest tested structural departure from the baseline and still retains high rank correlation and more than 90% overlap in both high-priority sets.

## Demand sensitivity

- area-weighted demand: **144,255.716 people-equivalent**
- full-mesh population scenario: **306,220 people**
- full-mesh / area-weighted ratio: **2.1228×**
- area-weighted over-capacity shelters: **35**
- full-mesh over-capacity shelters: **72**
- additional over-capacity shelters under full-mesh sensitivity: **37**
- complete-score full-mesh sensitivity median: **36.3521**

Demand assumptions therefore have a materially larger effect on the number of diagnosed capacity-constrained shelters than the tested score-weight changes have on the identity of the highest-priority meshes.

## Policy interpretation

1. Do not over-interpret the exact baseline rank.
2. Give more weight to the **69 all-scenario robust top-decile meshes** and **42 all-scenario robust Top 50 meshes** when identifying persistent priorities.
3. Capacity conclusions should be shown with both the area-weighted and full-mesh sensitivity scenarios because the demand assumption changes the overloaded-shelter count from 35 to 72.
4. The 277 rows without a complete five-component score need separate data-completeness/action treatment rather than being placed below complete rows in a single ranking.
5. These outputs remain exploratory policy-analysis results, not official risk categories, forecasts, or probabilities.
