# STEP 1 — Routing Foundation & Spatial QA results

Validated on 2026-08-22 against the corrected N03 MultiPolygon + 3 km walking-network AOIs.

## Release result

STEP 1 release gate: **PASS**.

- 14/14 coastal municipality walking networks rebuilt successfully.
- All 5,821 population meshes were checked.
- Analysis target: 1,090 meshes with `tsunami_inundation_ratio > 0`.
- All 1,090 target meshes received an explicit origin classification; unresolved = 0.
- 492 border-near meshes had an external-municipality shelter candidate closer than the same-municipality candidate in the spatial QA. This confirms that STEP 2 must not restrict shelters by municipality.

## Target origin classification

| Method | Target meshes |
| --- | ---: |
| walk node inside 500 m mesh | 1,039 |
| walk edge intersects mesh | 46 |
| nearest walk edge fallback (<=500 m) | 3 |
| explicit network coverage gap | 2 |
| **Total** | **1,090** |

The two explicit network coverage gaps are mesh IDs `493253842` and `493262112`, both in Uwajima. They remain in the analysis denominator. STEP 2 must return `route_status=network_coverage_gap` rather than fabricate a >500 m network jump.

The maximum connector distance among resolved tsunami-target meshes is 370.352 m.

## Why edge-aware origin selection was required

A node-only centroid snap initially reported 9 critical (>500 m) tsunami-target meshes. Independent edge review found that 7 of those meshes were crossed by an OSM walk edge even though no graph node lay inside the mesh. They are therefore represented by a virtual origin on the intersecting edge, not treated as network gaps.

For edge-based origins, STEP 2 must seed both edge endpoints with the cost of:

`centroid -> projected point on edge + along-edge distance to endpoint`

This avoids both false network gaps and free/zero-cost snapping.

## Shelter connector policy for STEP 2

One shelter/network-context record exceeded the 500 m connector threshold:

- `E3821400137201` 岩井避難場所, Seiyo: 857.618 m

STEP 2 must exclude a shelter candidate in a given network context when its connector exceeds 500 m. Such exclusions must be counted and reported; the facility must not be assigned through a fabricated connector.

## Island / disconnected component review

- Kamijima boundary: MultiPolygon, 52 parts; walking network 34 components; largest component ratio 0.6819; ferry-tagged walking edges 0.
- Imabari boundary: MultiPolygon, 369 parts; walking network 167 components; largest component ratio 0.9473; ferry-tagged walking edges 0.

Disconnected island components are expected. No synthetic inter-island edge is introduced.

## Reproducibility evidence

The passing aggregate QA was produced by GitHub Actions run `32554926671`, artifact `9471183746`. The canonical `Routing Foundation STEP 1` workflow rebuilds networks independently and enforces the same explicit-gap release gate.
