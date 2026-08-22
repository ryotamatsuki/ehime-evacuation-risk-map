# QA outputs

- `shelter_join_report.csv`: current authoritative result from the 2026-07-27 prefectural workbook and the GSI CSV downloaded on 2026-08-22.
- `shelter_join_report_computed.csv`: duplicate explicit copy of the current computed result.

The earlier conversation checkpoint (unmatched 26, duplicate 2) is preserved in `data/metadata/progress_checkpoint.json`; it is not used as the current ETL result. Differences are retained rather than silently merged.

`network/` contains OSM pedestrian-network extraction QA by municipality and a cumulative summary. It records only QA metadata (footprint, counts, connectivity, attribution); GraphML network intermediates are deliberately excluded from version control and public delivery.
