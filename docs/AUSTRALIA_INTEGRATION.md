> Historical integration notes. The current implementation, completed national build and independent test are documented in [HARMONIZATION_V3.md](HARMONIZATION_V3.md). The early missing-data/setup statements below describe the first import stage.

# Australia prototype integration

IGNIS-Revamp remains the application. Its React/Vite design, OpenStreetMap / MapLibre map, Natural Earth fallback, saved study outputs, and original scientific commands are retained. EmberCal-v1's national pipeline and analysis are imported separately.

## Running

Run `Setup.ps1`, then `Start.ps1` and open http://127.0.0.1:8010. Australia analysis is the default workspace. Saved study screens work before national outputs are built. `GET /api/analysis/readiness` reports missing dependencies and outputs without returning secrets.

To build the national record, run `Build-Australia.ps1`. This downloads Australia archives, harmonizes sensors, obtains climate drivers, and fits/back-tests forecasts. Initial downloads and model fitting can be substantial; no runtime or download-size promise is made. This integration did not run that national build.

For interrupted runs use `Build-Australia.ps1 -Step ingest`, `-Step harmonize`, `-Step drivers`, or `-Step forecast` in that order. A failed step stops the script. Restart the API after regenerating data because it caches datasets and area calculations.

Use `FIRMS_MAP_KEY` in the backend process environment or `backend/.env`. It is never passed to the frontend. With no key, public recent feeds do not fill the entire current year. Ingestion, climate requests and model training run from the command line, not from a browser request.

## Data and methods

The pipeline configuration contains Australia only: study extent [112, -44.5, 154.5, -9], monthly records from November 2000, 0.1-degree analysis cells and 0.5-degree forecast regions. This bounding extent is not an administrative boundary mask. Geometry queries are limited to that extent and weighted against Australian cells.

Outputs live in `data/processed`, separate from `data/demo`. The imported Sri Lanka files and historical logs are not copied. Existing demo CSVs and their provenance hashes are unchanged.

The national reference is VIIRS **S-NPP-equivalent**. The saved southeastern experiment uses **NOAA-20** and held-out day/night ratios. These series, confidence filters, baseline periods and validation results are kept distinct. Importing a method does not verify the original README's accuracy claims. Generated national evaluation reports are displayed only when those outputs exist.

The new workspace includes region presets, bounding-box coordinates, exact drawn polygon/circle geometry and point/radius AOIs; a monthly calendar with intervals; robust anomalies; baseline seasonality; complete-year annual totals, rankings and Mann–Kendall/Sen trends; 1–3 month experimental outlooks with predictive ranges, terciles and back-test skill; MODIS/VIIRS/harmonized comparison; climate anomalies and ENSO/IOD context; hotspot queries; calendar/hotspot CSV and panel PNG export.

Forecast skill selection was corrected: a small single-country AOI no longer inherits the country-level skill score. Nonpositive regional skill retains the climatology fallback. Forecasts always use the harmonized series even if a raw series is displayed elsewhere.

Recent regional observations retain the legacy short-date and query-volume limits; use a smaller Australian preset there. The national analysis uses preprocessed data and supports Australia-wide queries. The existing saved calendar is southeastern Australia, 2019–2024 only; it has not been relabelled as a national record. Existing Validation continues to disclose the failed day/night stress test.

## Verification and limits

`Verify.ps1` runs the preserved backend tests, integration tests, frontend calculation tests and production build. Integration tests use temporary synthetic fixtures, not fabricated deliverable results. A full national model build, external NASA requests, and scientific validation against independent ground truth require real national inputs and remain separate work.

The imported methods document is archived as `docs/EMBERCAL_METHODS_SOURCE.md`; its future country-expansion plan is historical source context, not this prototype's scope.
