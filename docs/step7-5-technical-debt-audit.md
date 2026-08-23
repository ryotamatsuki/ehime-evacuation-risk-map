# STEP 7.5 Technical Debt Audit / Safe Refactor Record

Baseline main: `af1a985cd0812675f25c6c84268e4d16b753c5c5`

## Scope and release rule

STEP 7.5 is a behavior-preserving maintenance stage. It does not redefine STEP 1-4 analytical methodology, does not change the canonical Analysis Core v4 score, and does not introduce a new policy assumption. A merge is allowed only after the production regression baseline, STEP 5 robustness results, STEP 7 counterfactual results, TypeScript/Vite build and desktop/iPhone-equivalent WebKit smoke all pass.

The corrected `main` Analysis Core push remains the authoritative production/deploy path. PR path filters may avoid expensive GIS recomputation for unrelated changes, but `push` to `main` for `routing-step2.yml` remains intentionally unfiltered so every merged release rebuilds the corrected export and Pages artifact.

## A. Inventory findings

1. Pre-v4 routing, route exposure, capacity, score and public-export scripts still coexisted with the corrected v2/STEP 3/STEP 4 pipeline. Their semantics and row universe differed from current Analysis Core v4.
2. `scripts/README.md` still described the retired pipeline and said missing score weights were renormalized, which contradicts the current complete-only five-component score contract.
3. `npm run test:data` referenced retired/nonexistent `route_exposure.csv` and `risk_mesh.csv` outputs.
4. The retired mixed-schema `validate_data.py` coupled source-data QA to old route/risk output semantics.
5. `AppCorrected.tsx` retained a disabled STEP 7 placeholder even though STEP 7 is now exposed through `AppWithSimulation` and `PolicySimulation`.
6. Primitive JSON loading, finite-number conversion, text conversion and formatting logic was duplicated across frontend modules.
7. STEP 7 had two masking fallbacks: reconstructing a missing canonical baseline pressure from demand/capacity, and clamping a negative score delta to zero without surfacing it.
8. Frontend build, production artifact resolution/download, provenance validation and WebKit QA were repeated across multiple workflows.
9. A legacy standalone Pages workflow remained even though corrected Pages deployment is owned by `routing-step2.yml` FINAL C.
10. STEP 1 and STEP 2-4 spatial workflows ran on PRs that changed only UI/docs.
11. TypeScript unused-local and unused-parameter diagnostics were disabled.
12. `AppCorrected.tsx` remains a large component and is architectural debt, but splitting it in this maintenance stage would expand the behavioral change surface too much.

## B. Safe refactor applied

- Removed retired pre-v4 analytical entrypoints. Historical implementations remain available in Git history; they are no longer callable as current pipeline scripts.
- Replaced the mixed-schema validator with `validate_source_data.py`, whose scope is stable committed source/intermediate data only.
- Rewrote `scripts/README.md` so the corrected v2/STEP 3/STEP 4/export pipeline is the only documented canonical path.
- Added `src/dataContract.ts` for primitive parsing/loading/formatting constants shared by the active UI and STEP 7.
- Removed the dead simulation mode/button/CSS hiding workaround from `AppCorrected.tsx`; the live STEP 7 launcher remains the single UI entrypoint.
- Changed STEP 7 to consume the canonical exported `capacity_pressure_area_weighted` only. Missing pressure is not reconstructed.
- Changed STEP 7 validation to fail if capacity augmentation would increase a complete-row score beyond floating-point tolerance rather than silently hiding the condition.
- Enabled `noUnusedLocals` and `noUnusedParameters` in the TypeScript production configuration.
- Consolidated frontend production regression + STEP 7 + WebKit work into `application-quality.yml`, resolving/downloading the corrected production artifact once per run.
- Removed redundant legacy/UI/browser workflows and standalone legacy Pages deployment.
- Scoped expensive STEP 1 and STEP 2-4 PR workflows to analytical inputs/code while leaving the main production push unfiltered.
- Added PR-only concurrency cancellation for long-running quality/spatial workflows. Main Analysis Core runs are never cancelled by this policy.

## C. Regression contract

`tests/production_regression_baseline.json` is the intentional release baseline. `scripts/verify_production_regression.py` must preserve:

- target meshes: 1,090
- complete routes: 1,062
- area-weighted over-capacity shelters: 35
- complete five-component scores: 813
- capacity-missing core-only rows: 128
- core-data-incomplete rows: 121
- route unavailable: 28
- cross-border routes: 13
- full-mesh over-capacity shelters: 72
- STEP 5 scenarios: 12
- STEP 5 robust top-decile across all scenarios: 69 meshes
- STEP 5 robust Top 50 across all scenarios: 42 meshes
- STEP 5 equal-weight Spearman vs baseline: 0.9894182482934234
- STEP 7 +100 people: 34 overloaded shelters, 1 resolved
- STEP 7 +500 people: 19 overloaded shelters, 16 resolved
- STEP 7 +1,000 people: 13 overloaded shelters, 22 resolved

STEP 7 total score-reduction values are also stored in the baseline fixture and compared numerically, so preserving only headline facility counts is not sufficient.

## Deliberately retained — not accidental debt removal

- `AppCorrected.tsx` is not split into many components in STEP 7.5. A future extraction should be done as an isolated UI-architecture task with screenshot/DOM regression coverage.
- Compatibility readers for historical walking-time / route fields in the frontend are retained unless production-data migration proves they are no longer needed. Removing a compatibility fallback is a data-contract migration, not cosmetic cleanup.
- Python analytical validators and TypeScript browser logic are not fully DRYed into one implementation. Independent implementations are useful cross-checks; sharing every formula would allow one defect to contaminate both implementation and verifier.
- STEP 5 remains a separate analytical workflow as well as being recomputed in the consolidated application-quality gate. Its independent analytical release purpose is distinct from browser/application regression.

## E. Merge gate

Before merge, the PR must show:

1. source/debt contract tests PASS;
2. production artifact provenance PASS;
3. cross-stage STEP 1-7 regression PASS;
4. `npm run build` PASS with v4 real-data projection contract;
5. desktop and iPhone-equivalent Playwright WebKit PASS with no uncaught page errors;
6. a fresh PR Analysis Core corrected-final-export that is semantically/numerically equal to the latest successful main production export, excluding run-specific provenance identifiers only.

Native Apple Safari / physical iPhone remains a manual device QA item; Playwright WebKit is not labeled as native Safari verification.
