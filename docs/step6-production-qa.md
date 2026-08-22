# STEP 6 — Production Polish / Documentation Sync / Safari-Compatible QA

## Scope

STEP 6 is a production-quality gate. It does not alter Analysis Core v4 calculations.

It synchronizes public documentation with the corrected STEP 1〜5 state and adds an automated browser regression path using Playwright WebKit against the real corrected production export.

## Documentation synchronization

The gate requires README, release status, and correction-plan documents to state the current v4/v5 contract and rejects known stale wording, including:

- STEP 2〜4 still pending;
- STEP 4 recalculation waiting language;
- missing score components being removed and remaining weights renormalized.

STEP 5 production findings are recorded in `docs/step5-sensitivity-results.md`.

## Production browser input

The WebKit workflow does not trust the legacy committed public-data snapshot. It resolves the latest successful `main` push run of `routing-step2.yml`, downloads its `corrected-final-export` artifact, and verifies:

- `analysis_version = analysis-core-v4-corrected-public`;
- artifact source SHA equals the selected production run head SHA;
- target meshes = 1,090;
- complete routes = 1,062;
- route unavailable = 28;
- cross-border routes = 13.

The same downloaded data are then used by `npm run build` and the browser smoke test.

## WebKit smoke coverage

Two contexts are executed:

1. desktop WebKit at 1440×1000;
2. Playwright iPhone 13 device profile on WebKit.

Both require:

- application title rendered;
- analysis mode navigation rendered;
- KPI strip rendered;
- map panel rendered;
- municipality selector rendered and interactive;
- production KPI showing 35 over-capacity shelters;
- no fatal data state;
- either MapLibre canvas or the intentional WebGL fallback;
- no uncaught page errors;
- STEP 7 simulation remains disabled during STEP 6.

The iPhone-equivalent context additionally opens the mobile diagnostic panel and verifies its state change.

## Safari interpretation

Playwright WebKit is an automated compatibility proxy for Safari-family rendering and JavaScript behavior. It is **not** a claim that the site was tested in Apple Safari on macOS or on a physical iPhone.

Therefore STEP 6 distinguishes:

- automated WebKit production regression: CI gate;
- native Safari / iPhone Safari: manual-device QA item.

This avoids falsely marking native Safari verification as complete while still protecting the application from common WebKit regressions in CI.
