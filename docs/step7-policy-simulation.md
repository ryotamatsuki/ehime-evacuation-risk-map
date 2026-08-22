# STEP 7 — Policy Simulation v1

## Purpose

STEP 7 changes the product from a diagnostic map into a limited policy-comparison tool while preserving the corrected Analysis Core v4 baseline.

The v1 simulator answers one explicit question:

> If the recorded capacity of each currently selected evacuation shelter were hypothetically increased by a fixed number of people, how would diagnosed shelter pressure and the capacity component of the exploratory score change?

It does **not** claim to optimize actual evacuation operations.

## Scenario controls

The UI provides three deterministic capacity-augmentation scenarios:

- +100 people per known-capacity selected shelter;
- +500 people;
- +1,000 people.

Only shelters with a positive published capacity and STEP 4 assigned area-weighted demand enter the simulator. Missing capacity remains unknown and is not filled with a hypothetical baseline.

## Held-fixed assumptions

For a capacity-augmentation scenario, the following remain exactly as Analysis Core v4 produced them:

- selected shelter;
- walking route;
- area-weighted evacuation demand;
- tsunami-exposure component;
- vulnerable-population component;
- walking-accessibility component;
- route-tsunami-exposure component.

The simulator does not reroute a mesh, does not move overflow to another shelter, does not create a new shelter location, and does not modify production JSON.

## Scenario calculation

For a shelter with known baseline capacity `C`, STEP 4 assigned area-weighted demand `D`, and hypothetical augmentation `ΔC`:

`simulated_capacity = C + ΔC`

`simulated_capacity_pressure = D / simulated_capacity`

The simulated capacity score component follows the exact STEP 4 component definition:

`simulated_capacity_component = min(simulated_capacity_pressure × 100, 100)`

For a mesh whose canonical `score_status` is `complete`, the scenario-only five-component score is:

`(tsunami×25 + vulnerable×20 + walking×25 + route×15 + simulated_capacity_component×15) / 100`

The canonical exported `evacuation_difficulty_score` remains read-only. Non-complete rows do not receive a hypothetical five-component score.

## Policy-effect ranking

Candidate shelters are ranked primarily by the sum of non-negative five-component score reductions across complete meshes currently assigned to the shelter. The UI also shows:

- baseline and augmented capacity;
- assigned area-weighted demand;
- baseline and simulated capacity pressure;
- number of affected complete meshes;
- whether a shelter moves from >100% pressure to ≤100%.

This is an exploratory comparison metric, not a cost-benefit analysis. It does not include construction cost, feasibility, ownership, staffing, seismic safety, vertical-evacuation suitability, access constraints, or behavioral response.

## Release gate

STEP 7 is release-gated against the latest successful corrected `main` production artifact. Automated QA requires:

- complete canonical scores = 813;
- non-complete rows = 277;
- known-capacity selected shelters = 272;
- baseline area-weighted over-capacity shelters = 35;
- each positive capacity augmentation must not increase the overloaded-shelter count;
- larger capacity augmentations must not reduce modeled aggregate score improvement;
- all 813 complete rows remain associated with a known-capacity scenario calculation;
- canonical STEP 4 scores and capacity values are not mutated;
- browser build continues to pass the v4 35/813/128/28/13 real-data regression contract.

The production WebKit smoke test also opens the simulator on desktop and iPhone-equivalent contexts, verifies a baseline of 35 over-capacity shelters, and checks monotonic behavior when switching to +1,000 people.

## Interpretation

The simulator should be described as **counterfactual diagnostic sensitivity**, not as an evacuation optimization engine. A later version may evaluate targeted shelter interventions or network improvements, but any rerouting or new-facility placement must be implemented as a separate analysis model with its own spatial release gate rather than inferred in the browser.
