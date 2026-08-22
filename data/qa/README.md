# QA outputs

- `shelter_join_report.csv`: current authoritative result from the 2026-07-27 prefectural workbook and the GSI CSV downloaded on 2026-08-22.
- `shelter_join_report_computed.csv`: duplicate explicit copy of the current computed result.
- `routes_qa.json`: municipality-level network assignment result and speed-scenario note.
- `route_exposure_qa.json`: route sampling spacing, coverage, and interpretation note.
- `capacity_qa.json`: capacity parsing, missing-capacity count, and hypothetical assignment note.
- `risk_score_qa.json`: normalization bounds, weights, and missing-data handling.
- `validation_report.json`: reproducible strict validation result. Warnings are retained for source/data coverage issues; they are not converted to zeros.

The earlier conversation checkpoint (unmatched 26, duplicate 2) is preserved in `data/metadata/progress_checkpoint.json`; it is not used as the current ETL result. Differences are retained rather than silently merged.

`network/` contains OSM pedestrian-network extraction QA by municipality and a cumulative summary. It records only QA metadata (footprint, counts, connectivity, attribution); GraphML network intermediates are deliberately excluded from version control and public delivery.

Current strict validation: failure 0; warnings 4. Warnings are prefectural common-ID duplicates (4 IDs / 8 records), 28 unmatched shelter coordinates, 282 missing tsunami-shelter capacities, and 57 meshes without a network path.
